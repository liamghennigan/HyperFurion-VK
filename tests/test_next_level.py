"""The 1-4-6-7 build: ask-any-app, total recall, the remote mic, and
procedural memory — routing, gating, and physics."""

import asyncio
import json
import queue
from unittest import mock

import pytest

from voice_keyboard import dictionary, recall
from voice_keyboard.config import _default_config_with_paths, validate_config
from voice_keyboard.daemon import Daemon
from voice_keyboard.llm import LLMClient
from voice_keyboard.remotemic import (
    PAGE_HTML,
    RemoteAudioSource,
    SILENCE_CHUNK,
    check_token,
    ensure_certificate,
)


def _valid_config() -> dict:
    cfg = _default_config_with_paths()
    cfg["xai"]["api_key"] = "test-api-key"
    return cfg


@pytest.fixture(autouse=True)
def state_dir(tmp_path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path))
    return tmp_path


@pytest.fixture(autouse=True)
def inline_to_thread(monkeypatch: pytest.MonkeyPatch):
    async def _to_thread(func, /, *args, **kwargs):
        return func(*args, **kwargs)

    monkeypatch.setattr(asyncio, "to_thread", _to_thread)


@pytest.fixture(autouse=True)
def no_overlay(monkeypatch: pytest.MonkeyPatch):
    from voice_keyboard import client

    monkeypatch.setattr(client, "_show_overlay", mock.Mock())


class RecordingInjector:
    def __init__(self):
        self.typed: list[str] = []
        self.suppress_enter = False
        self.paste_chord_shift = False
        self.flag_at_type: list[bool] = []

    def start(self) -> None:
        pass

    def stop(self) -> None:
        pass

    def type_text(self, text: str) -> None:
        self.flag_at_type.append(self.suppress_enter)
        self.typed.append(text)

    def delete_chars(self, count: int) -> None:
        pass


def _daemon(cfg: dict | None = None) -> Daemon:
    return Daemon(
        config=cfg or _valid_config(),
        injector=RecordingInjector(),
        ipc_server=mock.Mock(),
        tts_client=mock.Mock(),
    )


def _entry(ts: float, text: str, *, app: str = "editor", register: str = "prose") -> dict:
    return {"ts": ts, "app": app, "register": register, "text": text}


# ══ 7 — procedural memory ═══════════════════════════════════════════════


class TestMacroMining:
    def test_repeated_dictation_becomes_candidate(self) -> None:
        text = "co-authored by the whole flight deck crew"
        entries = [_entry(100.0 * i, text) for i in range(1, 4)]
        assert dictionary.mine_macros(entries) == [(text, 3)]

    def test_short_texts_never_qualify(self) -> None:
        entries = [_entry(100.0 * i, "yes ok") for i in range(1, 6)]
        assert dictionary.mine_macros(entries) == []

    def test_command_registers_are_skipped(self) -> None:
        text = "grep dash r n todo in the repo now"
        entries = [_entry(100.0 * i, text, register="intent") for i in range(1, 5)]
        assert dictionary.mine_macros(entries) == []

    def test_accept_and_reject_close_candidates(self) -> None:
        text = "co-authored by the whole flight deck crew"
        entries = [_entry(100.0 * i, text) for i in range(1, 4)]
        assert dictionary.open_macro_candidates(entries) == [(text, 3)]
        data = dictionary.load_dictionary()
        data["macros"]["trailer"] = text
        dictionary.save_dictionary(data)
        assert dictionary.open_macro_candidates(entries) == []

    def test_macro_text_lookup_is_forgiving(self) -> None:
        data = dictionary.load_dictionary()
        data["macros"]["trailer"] = "the saved body"
        dictionary.save_dictionary(data)
        assert dictionary.macro_text("Trailer.") == "the saved body"
        assert dictionary.macro_text("unknown") is None


class TestMacroVoice:
    def _with_macro(self) -> Daemon:
        data = dictionary.load_dictionary()
        data["macros"]["trailer"] = "Signed-off-by: Liam\nReviewed: yes"
        dictionary.save_dictionary(data)
        return _daemon()

    def test_spoken_name_types_saved_body(self) -> None:
        daemon = self._with_macro()
        result = asyncio.run(daemon._transform_previous_or_report("trailer"))
        assert result == "Signed-off-by: Liam\nReviewed: yes"
        assert daemon._injector.typed == ["Signed-off-by: Liam\nReviewed: yes"]

    def test_macro_wins_over_transform(self) -> None:
        daemon = self._with_macro()
        daemon._run_transform = mock.AsyncMock()
        asyncio.run(daemon._transform_previous_or_report("trailer"))
        daemon._run_transform.assert_not_awaited()


# ══ 1 — ask any app ═════════════════════════════════════════════════════


class TestLLMAnswer:
    def _client(self) -> LLMClient:
        return LLMClient(base_url="https://api.x.ai/v1", api_key="k", model="m")

    def _response(self, content: str) -> mock.Mock:
        response = mock.Mock()
        response.raise_for_status = mock.Mock()
        response.json.return_value = {"choices": [{"message": {"content": content}}]}
        return response

    def test_context_reaches_the_prompt(self) -> None:
        with mock.patch(
            "voice_keyboard.llm.requests.post", return_value=self._response("because X")
        ) as post:
            answer = self._client().answer("why does this fail", "Traceback: boom")
        assert answer == "because X"
        user = post.call_args.kwargs["json"]["messages"][1]["content"]
        assert "Traceback: boom" in user and "why does this fail" in user

    def test_empty_answer_raises(self) -> None:
        with mock.patch(
            "voice_keyboard.llm.requests.post", return_value=self._response(" ")
        ):
            with pytest.raises(RuntimeError, match="ask"):
                self._client().answer("why", "")


class TestRunAsk:
    def test_say_mode_speaks_the_answer(self) -> None:
        daemon = _daemon()
        daemon._run_tts = mock.AsyncMock()
        llm = mock.Mock()
        llm.answer.return_value = "because the race"
        with mock.patch("voice_keyboard.daemon.create_llm_client", return_value=llm), \
             mock.patch(
                 "voice_keyboard.daemon.clipboard.get_primary_text",
                 return_value="the selected traceback",
             ):
            result = asyncio.run(daemon._run_ask("why does this fail"))
        assert result == "because the race"
        llm.answer.assert_called_once_with("why does this fail", "the selected traceback")
        daemon._run_tts.assert_awaited_once_with("because the race")
        assert daemon._injector.typed == []

    def test_type_mode_types_with_no_enter_armed(self) -> None:
        cfg = _valid_config()
        cfg["ask"]["mode"] = "type"
        daemon = _daemon(cfg)
        llm = mock.Mock()
        llm.answer.return_value = "line one\nline two"
        with mock.patch("voice_keyboard.daemon.create_llm_client", return_value=llm), \
             mock.patch(
                 "voice_keyboard.daemon.clipboard.get_primary_text", return_value=""
             ):
            asyncio.run(daemon._run_ask("summarize"))
        assert daemon._injector.flag_at_type == [True]
        assert daemon._injector.suppress_enter is False

    def test_voice_routing_strips_only_ask_verbs(self) -> None:
        cfg = _valid_config()
        cfg["ask"]["enabled"] = True
        cfg["ask"]["verbs"] = ["ask", "explain"]
        daemon = _daemon(cfg)
        daemon._run_ask = mock.AsyncMock(return_value="A")
        asyncio.run(daemon._transform_previous_or_report("ask why is this red"))
        daemon._run_ask.assert_awaited_once_with("why is this red")
        daemon._run_ask.reset_mock()
        asyncio.run(daemon._transform_previous_or_report("explain this thing"))
        daemon._run_ask.assert_awaited_once_with("explain this thing")

    def test_ask_last_refuses_while_recording(self) -> None:
        daemon = _daemon()
        daemon._recording = True
        with pytest.raises(RuntimeError, match="stop recording"):
            asyncio.run(daemon._ask_last("why"))


# ══ 4 — total recall ════════════════════════════════════════════════════


class TestRecallSearch:
    def test_keyword_search_ranks_phrase_hits_first(self) -> None:
        entries = [
            _entry(1.0, "the relay caps were re-derived at real prices"),
            _entry(2.0, "lunch plans for friday"),
            _entry(3.0, "caps lock is a different thing"),
        ]
        hits = recall.search(entries, "relay caps")
        assert hits and hits[0]["text"].startswith("the relay caps")

    def test_zero_score_entries_are_dropped(self) -> None:
        entries = [_entry(1.0, "completely unrelated words")]
        assert recall.search(entries, "relay caps") == []

    def test_intent_entries_are_not_memories(self) -> None:
        entries = [_entry(1.0, "grep relay caps", register="intent")]
        assert recall.search(entries, "relay caps") == []

    def test_semantic_path_uses_embeddings(self) -> None:
        entries = [_entry(1.0, "alpha"), _entry(2.0, "beta")]
        embedder = mock.Mock()
        embedder.embed.return_value = [[1.0, 0.0], [0.1, 0.9], [1.0, 0.0]]
        hits = recall.search(entries, "anything", embedder=embedder)
        assert hits[0]["text"] == "beta"

    def test_embedding_failure_falls_back_to_keywords(self) -> None:
        entries = [_entry(1.0, "the relay caps story")]
        embedder = mock.Mock()
        embedder.embed.side_effect = RuntimeError("down")
        hits = recall.search(entries, "relay caps", embedder=embedder)
        assert hits and "relay caps" in hits[0]["text"]

    def test_create_embedder_needs_url_and_model(self) -> None:
        cfg = _valid_config()
        assert recall.create_embedder(cfg) is None
        cfg["recall"]["base_url"] = "http://localhost:11434/v1"
        cfg["recall"]["model"] = "nomic-embed-text"
        assert recall.create_embedder(cfg) is not None

    def test_embedder_parses_openai_shape(self) -> None:
        embedder = recall.Embedder(
            base_url="http://localhost:11434/v1", model="nomic-embed-text"
        )
        response = mock.Mock()
        response.raise_for_status = mock.Mock()
        response.json.return_value = {
            "data": [{"embedding": [0.1, 0.2]}, {"embedding": [0.3, 0.4]}]
        }
        with mock.patch("voice_keyboard.recall.requests.post", return_value=response):
            assert embedder.embed(["a", "b"]) == [[0.1, 0.2], [0.3, 0.4]]


class TestRunRecall:
    def test_best_hit_is_spoken(self) -> None:
        daemon = _daemon()
        daemon._run_tts = mock.AsyncMock()
        entries = [_entry(1.0, "the relay caps were re-derived")]
        with mock.patch("voice_keyboard.daemon.history.last_entries", return_value=entries):
            result = asyncio.run(daemon._run_recall("relay caps"))
        assert result == "the relay caps were re-derived"
        daemon._run_tts.assert_awaited_once()

    def test_empty_ledger_raises_helpfully(self) -> None:
        daemon = _daemon()
        with mock.patch("voice_keyboard.daemon.history.last_entries", return_value=[]):
            with pytest.raises(RuntimeError, match="ledger is empty"):
                asyncio.run(daemon._run_recall("anything"))

    def test_voice_routing_reaches_recall(self) -> None:
        cfg = _valid_config()
        cfg["recall"]["enabled"] = True
        daemon = _daemon(cfg)
        daemon._run_recall = mock.AsyncMock(return_value="R")
        asyncio.run(daemon._transform_previous_or_report("recall the relay caps"))
        daemon._run_recall.assert_awaited_once_with("the relay caps")


# ══ 6 — the multiplayer keyboard ════════════════════════════════════════


class TestRemoteMicPieces:
    def test_token_check(self) -> None:
        assert check_token("/?t=abc123", "abc123") is True
        assert check_token("/mic?t=abc123", "abc123") is True
        assert check_token("/?t=wrong", "abc123") is False
        assert check_token("/", "abc123") is False
        assert check_token("/?t=", "") is False

    def test_source_roundtrip_and_silence(self) -> None:
        source = RemoteAudioSource()
        source.start()
        source.push(b"\x01\x02")
        assert source.read_chunk() == b"\x01\x02"
        source.stop()
        assert source.read_chunk() == SILENCE_CHUNK

    def test_page_is_a_real_mic_page(self) -> None:
        assert "getUserMedia" in PAGE_HTML
        assert "AudioWorklet" in PAGE_HTML or "audioWorklet" in PAGE_HTML
        assert "wss://" in PAGE_HTML

    def test_certificate_is_cached(self) -> None:
        from voice_keyboard.remotemic import _state_dir as state

        state().mkdir(parents=True, exist_ok=True)
        (state() / "remote-mic-cert.pem").write_text("cert")
        (state() / "remote-mic-key.pem").write_text("key")
        with mock.patch("voice_keyboard.remotemic.subprocess.run") as run:
            cert, key = ensure_certificate()
        run.assert_not_called()
        assert cert.exists() and key.exists()


class FiniteRemoteSource(RemoteAudioSource):
    """A remote source whose stream ends after one frame — the inline
    to_thread test fixture would otherwise let the infinite-silence loop
    monopolize the event loop (real threads make that a non-issue)."""

    def __init__(self):
        super().__init__()
        self.reads = 0

    def read_chunk(self) -> bytes:
        self.reads += 1
        if self.reads == 1:
            return b"\x01\x00\x02\x00"
        raise RuntimeError("stream closed")


class TestRemoteSession:
    def test_override_source_replaces_pyaudio(self) -> None:
        cfg = _valid_config()
        cfg["flow"]["enabled"] = False
        daemon = _daemon(cfg)
        source = FiniteRemoteSource()
        daemon._audio_source_override = source

        async def run() -> None:
            async def fake_receive_events():
                yield {"type": "transcript.done", "text": "from the phone"}

            stt = mock.AsyncMock()
            stt.receive_events = fake_receive_events
            with mock.patch("voice_keyboard.daemon.create_stt_client", return_value=stt), \
                 mock.patch("voice_keyboard.daemon.AudioCapture") as pyaudio_cls:
                await daemon._start_recording()
                assert daemon._audio_capture is source
                assert source.running is True
                pyaudio_cls.assert_not_called()
                for _ in range(200):
                    if daemon._final_text:
                        break
                    await asyncio.sleep(0.005)
                result = await daemon._stop_recording()
                assert result == "from the phone"

        asyncio.run(run())
        assert daemon._injector.typed == ["from the phone"]
        assert source.running is False  # _stop_recording stopped the source


class TestNewConfigValidation:
    def test_defaults_validate(self) -> None:
        validate_config(_valid_config())

    def test_ask_mode_enum(self) -> None:
        cfg = _valid_config()
        cfg["ask"]["mode"] = "shout"
        with pytest.raises(RuntimeError, match="ask.mode"):
            validate_config(cfg)

    def test_recall_verbs_type(self) -> None:
        cfg = _valid_config()
        cfg["recall"]["verbs"] = "recall"
        with pytest.raises(RuntimeError, match="recall.verbs"):
            validate_config(cfg)

    def test_remote_mic_port_range(self) -> None:
        cfg = _valid_config()
        cfg["remote_mic"]["port"] = 99999
        with pytest.raises(RuntimeError, match="remote_mic.port"):
            validate_config(cfg)
