"""Widget-aware probing and STT vocabulary biasing: password fields get
maximum protection, accepted hotwords bias REST providers, streaming
providers honestly ignore the bias, and screen text is never harvested."""

import asyncio
import json
from unittest import mock

import pytest

from voice_keyboard import dictionary
from voice_keyboard.config import _default_config_with_paths, validate_config
from voice_keyboard.daemon import Daemon
from voice_keyboard.focusprobe import ATSPI_PROBE_SCRIPT, FocusInfo, _probe_linux
from voice_keyboard.flow.registers import register_for_app
from voice_keyboard.stt import BufferedRESTSTTClient, ChunkedRESTAdapter, _bias_tokens


@pytest.fixture(autouse=True)
def state_dir(tmp_path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path))
    return tmp_path


@pytest.fixture(autouse=True)
def no_overlay(monkeypatch: pytest.MonkeyPatch):
    from voice_keyboard import client

    monkeypatch.setattr(client, "_show_overlay", mock.Mock())


def _valid_config() -> dict:
    cfg = _default_config_with_paths()
    cfg["xai"]["api_key"] = "test-api-key"
    return cfg


class TestProbeParsing:
    def _probe(self, payload: dict) -> FocusInfo:
        result = mock.Mock(returncode=0, stdout=json.dumps(payload))
        with mock.patch("voice_keyboard.focusprobe.subprocess.run", return_value=result):
            info = _probe_linux(1.0)
        assert info is not None
        return info

    def test_secret_field_parses(self) -> None:
        info = self._probe(
            {"x": 1, "y": 2, "app": "firefox", "role": "password text",
             "editable": True, "secret": True}
        )
        assert info.secret is True

    def test_missing_secret_defaults_false(self) -> None:
        info = self._probe({"x": 1, "y": 2, "app": "gedit", "role": "text", "editable": True})
        assert info.secret is False

    def test_probe_script_never_reads_widget_text(self) -> None:
        # Dictation is new thought, not a continuation of screen text: the
        # probe reports role/anchor/secret and must never harvest content.
        assert "get_text(" not in ATSPI_PROBE_SCRIPT


class TestPasswordRegister:
    def test_password_role_forces_verbatim(self) -> None:
        register = register_for_app("firefox", "password text")
        assert register.name == "verbatim"

    def test_password_beats_config_map(self) -> None:
        register = register_for_app(
            "firefox", "password text", config_map={"firefox": "terminal"}
        )
        assert register.name == "verbatim"

    def test_terminal_role_still_terminal(self) -> None:
        assert register_for_app("someapp", "terminal").name == "terminal"


class TestBiasTokens:
    def test_dedupes_and_caps(self) -> None:
        tokens = _bias_tokens("Seneschal, seneschal kairos a of Kairos")
        assert tokens == ["Seneschal", "kairos"]

    def test_short_tokens_dropped(self) -> None:
        assert _bias_tokens("go up in it") == []


class TestPayloadInjection:
    def _client(self, provider: str, model: str = "") -> BufferedRESTSTTClient:
        client = BufferedRESTSTTClient(provider=provider, api_key="k", model=model)
        client._session = mock.Mock()
        return client

    def _ok_response(self, payload: dict) -> mock.Mock:
        response = mock.Mock()
        response.raise_for_status = mock.Mock()
        response.json.return_value = payload
        return response

    def test_openai_prompt_injected(self) -> None:
        client = self._client("openai", model="whisper-1")
        client.bias_prompt = "Seneschal, kairos"
        client._session.post.return_value = self._ok_response({"text": "ok"})
        assert client._transcribe_openai_compatible("http://x", b"wav") == "ok"
        data = client._session.post.call_args.kwargs["data"]
        assert data["prompt"] == "Seneschal, kairos"

    def test_openai_no_bias_no_prompt_key(self) -> None:
        client = self._client("openai", model="whisper-1")
        client._session.post.return_value = self._ok_response({"text": "ok"})
        client._transcribe_openai_compatible("http://x", b"wav")
        assert "prompt" not in client._session.post.call_args.kwargs["data"]

    def test_deepgram_nova3_uses_keyterm(self) -> None:
        client = self._client("deepgram", model="nova-3")
        client.bias_prompt = "Seneschal kairos"
        client._session.post.return_value = self._ok_response(
            {"results": {"channels": [{"alternatives": [{"transcript": "ok"}]}]}}
        )
        assert client._transcribe_deepgram(b"wav") == "ok"
        params = client._session.post.call_args.kwargs["params"]
        assert params["keyterm"] == ["Seneschal", "kairos"]
        assert "keywords" not in params

    def test_deepgram_nova2_uses_keywords(self) -> None:
        client = self._client("deepgram", model="nova-2")
        client.bias_prompt = "Seneschal"
        client._session.post.return_value = self._ok_response(
            {"results": {"channels": [{"alternatives": [{"transcript": "ok"}]}]}}
        )
        client._transcribe_deepgram(b"wav")
        params = client._session.post.call_args.kwargs["params"]
        assert params["keywords"] == ["Seneschal"]

    def test_assemblyai_word_boost(self) -> None:
        client = self._client("assemblyai")
        client.bias_prompt = "Seneschal kairos"
        upload = self._ok_response({"upload_url": "http://u"})
        submit = self._ok_response({"id": "t1"})
        poll = self._ok_response({"status": "completed", "text": "ok"})
        client._session.post.side_effect = [upload, submit]
        client._session.get.return_value = poll
        assert client._transcribe_assemblyai(b"wav") == "ok"
        submitted = client._session.post.call_args_list[1].kwargs["json"]
        assert submitted["word_boost"] == ["Seneschal", "kairos"]

    def test_adapter_propagates_bias_to_both_clients(self) -> None:
        inner = self._client("openai")
        interim = self._client("openai")
        adapter = ChunkedRESTAdapter(inner, interim)
        adapter.bias_prompt = "Seneschal"
        assert inner.bias_prompt == "Seneschal"
        assert interim.bias_prompt == "Seneschal"
        assert adapter.bias_prompt == "Seneschal"


class TestSessionBiasWiring:
    @pytest.fixture(autouse=True)
    def inline_to_thread(self, monkeypatch: pytest.MonkeyPatch):
        async def _to_thread(func, /, *args, **kwargs):
            return func(*args, **kwargs)

        monkeypatch.setattr(asyncio, "to_thread", _to_thread)

    def _daemon(self, cfg: dict) -> Daemon:
        return Daemon(
            config=cfg,
            injector=mock.Mock(),
            ipc_server=mock.Mock(),
            tts_client=mock.Mock(),
        )

    def _setup(self, daemon: Daemon, focus: FocusInfo) -> None:
        daemon._stt_client = mock.Mock(supports_streaming=False, bias_prompt="")

        async def run() -> None:
            async def fake_probe() -> FocusInfo:
                return focus

            task = asyncio.create_task(fake_probe())
            await daemon._setup_flow_session(task)

        asyncio.run(run())

    def _accept_hotword(self, word: str) -> None:
        data = dictionary.load_dictionary()
        data["hotwords"].append(word)
        dictionary.save_dictionary(data)

    def test_bias_is_accepted_hotwords_only(self) -> None:
        self._accept_hotword("Seneschal")
        cfg = _valid_config()
        cfg["stt"]["hotword_bias"] = True
        daemon = self._daemon(cfg)
        self._setup(daemon, FocusInfo(app="gedit", role="text"))
        assert daemon._stt_client.bias_prompt == "Seneschal"

    def test_no_accepted_hotwords_means_no_bias(self) -> None:
        cfg = _valid_config()
        cfg["stt"]["hotword_bias"] = True
        daemon = self._daemon(cfg)
        self._setup(daemon, FocusInfo(app="gedit", role="text"))
        assert daemon._stt_client.bias_prompt == ""

    def test_bias_off_by_default(self) -> None:
        self._accept_hotword("Seneschal")
        daemon = self._daemon(_valid_config())
        self._setup(daemon, FocusInfo(app="gedit", role="text"))
        assert daemon._stt_client.bias_prompt == ""

    def test_secret_field_gets_no_bias_and_verbatim(self) -> None:
        self._accept_hotword("Seneschal")
        cfg = _valid_config()
        cfg["stt"]["hotword_bias"] = True
        daemon = self._daemon(cfg)
        self._setup(
            daemon,
            FocusInfo(app="firefox", role="password text", secret=True),
        )
        assert daemon._stt_client.bias_prompt == ""
        assert daemon._session_register.name == "verbatim"
        assert daemon._session_secret is True

    def test_secret_session_never_remembered(self) -> None:
        daemon = self._daemon(_valid_config())
        daemon._session_secret = True
        daemon._config["flow"]["history"] = True
        with mock.patch("voice_keyboard.daemon.history.append_entry") as append:
            daemon._remember_typed("hunter2")
        append.assert_not_called()
        assert daemon._last_typed == ""

    def test_hotword_bias_must_be_bool(self) -> None:
        cfg = _valid_config()
        cfg["stt"]["hotword_bias"] = "yes"
        with pytest.raises(RuntimeError, match="stt.hotword_bias"):
            validate_config(cfg)
