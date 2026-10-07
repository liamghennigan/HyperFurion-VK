"""Under the default ("auto"), every request a Kai turn makes goes to a local
server, directly — including when those servers fail (DESIGN A18, L10).

Every HTTP request (requests' Session.request, which requests.post uses
too) and every websocket connect is recorded while a whole spoken turn runs:
the mic, speech-to-text, the language model, history search and the spoken
answer. Nothing silently falls back to a cloud service.
"""

import asyncio
import json
import threading
import time
from unittest import mock
from urllib.parse import urlsplit

import pytest
import requests

from voice_keyboard import client, history
from voice_keyboard.config import _default_config_with_paths, _is_local_endpoint
from voice_keyboard.daemon import Daemon
from voice_keyboard.focusprobe import FocusInfo
from voice_keyboard.netpolicy import NO_PROXIES
from voice_keyboard.tts import create_tts_client


class Network:
    """A fake network: records each request and answers like a local
    OpenAI-compatible server (or fails, for the paths in `down`)."""

    def __init__(self, transcript: str, chat: str = "Paris.", down: tuple = ()):
        self.transcript = transcript
        self.chat = chat
        self.down = down
        self.requests: list[tuple[str, dict]] = []
        self.sockets: list[str] = []
        self._lock = threading.Lock()

    def request(self, session, method, url, **kwargs):
        with self._lock:
            self.requests.append((url, kwargs))
        path = urlsplit(url).path
        if any(path.endswith(d) for d in self.down):
            raise requests.ConnectionError(f"{url} is down")
        if path.endswith("/audio/transcriptions"):
            body = {"text": self.transcript}
        elif path.endswith("/chat/completions"):
            body = {"choices": [{"message": {"content": self.chat}}]}
        elif path.endswith("/embeddings"):
            texts = (kwargs.get("json") or {}).get("input", [])
            body = {"data": [{"embedding": [1.0, 0.0]} for _ in texts]}
        elif path.endswith("/audio/speech"):
            response = requests.models.Response()
            response.status_code = 200
            response._content = b"MP3"
            return response
        else:
            raise AssertionError(f"unexpected request to {url}")
        response = requests.models.Response()
        response.status_code = 200
        response._content = json.dumps(body).encode()
        response.headers["Content-Type"] = "application/json"
        return response

    def connect(self, url, *args, **kwargs):
        with self._lock:
            self.sockets.append(url)
        raise OSError("no websockets in this test")

    def assert_all_local_and_direct(self) -> None:
        assert self.sockets == []
        for url, kwargs in self.requests:
            assert _is_local_endpoint(url), url
            assert kwargs.get("proxies") == NO_PROXIES, url
            assert kwargs.get("allow_redirects") is False, url


def _local_config(**changes) -> dict:
    cfg = _default_config_with_paths()
    cfg["providers"]["openai"]["base_url"] = "http://127.0.0.1:8000/v1"
    cfg["stt"].update(provider="openai", model="whisper")
    cfg["tts"].update(provider="openai", model="kokoro", voice_id="af_heart")
    cfg["llm"].update(provider="openai", base_url="http://localhost:8080/v1", model="qwen", api_key="")
    cfg["flow"]["history"] = True
    for section, values in changes.items():
        cfg[section].update(values)
    return cfg


class FakeMic:
    sample_rate = 16000

    def __init__(self, *args, **kwargs):
        pass

    def start(self):
        pass

    def stop(self):
        pass

    def read_chunk(self):
        time.sleep(0.005)
        return b"\x10\x00" * 160


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))
    monkeypatch.setattr(client, "_show_overlay", mock.Mock())
    monkeypatch.setattr(client, "_set_overlay_button", mock.Mock())
    monkeypatch.setattr("voice_keyboard.earcon.play_earcon", lambda kind: None)
    # A proxy in the environment must never see a local request.
    monkeypatch.setenv("HTTPS_PROXY", "http://127.0.0.1:9")
    monkeypatch.setenv("ALL_PROXY", "http://127.0.0.1:9")


def _turn(cfg: dict, network: Network, focus: FocusInfo) -> Daemon:
    """One whole spoken Kai turn: hold Right Ctrl, speak, release."""
    tts = create_tts_client(cfg)
    tts.play_audio = mock.Mock()
    tts.play_pcm = mock.Mock()
    injector = mock.Mock()
    daemon = Daemon(config=cfg, injector=injector, ipc_server=mock.Mock(), tts_client=tts)
    assert daemon._brain is not None, "everything is local, so Kai is on"
    daemon._focus_changed_since_session = mock.AsyncMock(return_value=False)

    async def scenario() -> None:
        await daemon._handle_hotkey_action("converse_start")
        assert daemon._recording
        await asyncio.sleep(0.05)
        await daemon._handle_hotkey_action("converse_stop")
        task = daemon._converse_task
        if task is not None:
            await task

    def request(session, method, url, **kwargs):
        return network.request(session, method, url, **kwargs)

    with mock.patch("requests.sessions.Session.request", request), \
            mock.patch("websockets.connect", network.connect), \
            mock.patch("voice_keyboard.daemon.AudioCapture", FakeMic), \
            mock.patch("voice_keyboard.daemon.probe_focus", return_value=focus):
        asyncio.run(scenario())
    return daemon


PROSE = FocusInfo(app="gedit")
TERMINAL = FocusInfo(app="kitty", role="terminal")


def test_a_spoken_answer_stays_local() -> None:
    history.append_entry("the capital question came up yesterday", app="editor")
    network = Network("what is the capital of france")
    daemon = _turn(_local_config(), network, PROSE)
    paths = [urlsplit(url).path for url, _ in network.requests]
    assert "/v1/audio/transcriptions" in paths
    assert "/v1/chat/completions" in paths
    assert "/v1/audio/speech" in paths
    assert daemon._last_answer == "Paris."
    network.assert_all_local_and_direct()


def test_a_terminal_command_stays_local() -> None:
    network = Network("show me the git status", chat="git status")
    daemon = _turn(_local_config(), network, TERMINAL)
    daemon._injector.type_text.assert_called_with("git status")
    network.assert_all_local_and_direct()


def test_a_failing_language_model_falls_back_to_nothing() -> None:
    network = Network("what is the capital of france", down=("/chat/completions",))
    _turn(_local_config(), network, PROSE)
    assert any(url.endswith("/chat/completions") for url, _ in network.requests)
    network.assert_all_local_and_direct()


def test_a_failing_voice_falls_back_to_nothing() -> None:
    network = Network("what is the capital of france", down=("/audio/speech",))
    _turn(_local_config(), network, PROSE)
    network.assert_all_local_and_direct()


def test_failing_speech_to_text_falls_back_to_nothing() -> None:
    network = Network("", down=("/audio/transcriptions",))
    _turn(_local_config(), network, PROSE)
    network.assert_all_local_and_direct()


def test_an_online_recall_is_never_asked() -> None:
    for i in range(5):
        history.append_entry(f"the capital of france came up, note {i}", app="editor")
    cfg = _local_config(recall={"base_url": "https://api.openai.com/v1",
                                "model": "text-embedding-3-small", "api_key": "k"})
    network = Network("what is the capital of france")
    _turn(cfg, network, PROSE)
    assert not any("openai.com" in url for url, _ in network.requests)
    network.assert_all_local_and_direct()


def test_a_local_recall_is_asked_directly() -> None:
    for i in range(3):
        history.append_entry(f"the capital of france came up, note {i}", app="editor")
    cfg = _local_config(recall={"base_url": "http://127.0.0.1:11434/v1", "model": "nomic"})
    network = Network("what is the capital of france")
    _turn(cfg, network, PROSE)
    assert any(url.endswith("/embeddings") for url, _ in network.requests)
    network.assert_all_local_and_direct()
