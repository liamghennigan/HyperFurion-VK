"""The merge: the conversational MIND inside the keyboard daemon.

Unified memory (assistant + dictation ledger), the brain routing
(realtime default → local fallback), converse capture routing, and the
Phase-2 action gate — the brain drafts a command through the same
no-Enter chokepoint as the intent channel, and Enter is never pressed.
"""

import asyncio
from pathlib import Path
from unittest import mock

import pytest

from voice_keyboard import dictionary, history
from voice_keyboard.assistant.brain import Brain, create_brain
from voice_keyboard.assistant.context import ContextProvider
from voice_keyboard.assistant.memory import AssistantMemory, extract_memory_candidate
from voice_keyboard.assistant.prompting import build_prompt, split_action, ACTION_PREFIX
from voice_keyboard.assistant.realtime import create_realtime_client
from voice_keyboard.assistant.models import ContextChunk
from voice_keyboard.config import _default_config_with_paths, validate_config
from voice_keyboard.daemon import Daemon


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


def _config(**assistant) -> dict:
    cfg = _default_config_with_paths()
    cfg["xai"]["api_key"] = "test-api-key"
    cfg["assistant"].update(assistant)
    return cfg


class RecordingInjector:
    def __init__(self):
        self.typed: list[str] = []
        self.suppress_enter = False
        self.paste_chord_shift = False
        self.flag_at_type: list[bool] = []

    def start(self):
        pass

    def stop(self):
        pass

    def type_text(self, text: str) -> None:
        self.flag_at_type.append(self.suppress_enter)
        self.typed.append(text)

    def delete_chars(self, count: int) -> None:
        pass


def _daemon(cfg: dict) -> Daemon:
    return Daemon(
        config=cfg,
        injector=RecordingInjector(),
        ipc_server=mock.Mock(),
        tts_client=mock.Mock(),
    )


# ══ Phase 0 — unified memory ════════════════════════════════════════════


class TestUnifiedMemory:
    def test_remember_and_search(self) -> None:
        mem = AssistantMemory()
        mem.remember("Liam prefers concise spoken answers", kind="preference")
        hits = mem.search("concise answers")
        assert hits and "concise" in hits[0].text

    def test_db_is_mode_600(self) -> None:
        import os

        mem = AssistantMemory()
        mem.remember("x y z")
        mode = os.stat(mem.db_path).st_mode & 0o777
        assert mode == 0o600

    def test_relevant_chunks_fold_in_dictation_ledger(self) -> None:
        history.append_entry("the relay caps were re-derived at real prices", app="editor")
        mem = AssistantMemory()
        mem.remember("Liam is building HyperFurion")
        chunks = mem.relevant_chunks("relay caps", 5)
        kinds = {c.kind for c in chunks}
        texts = " ".join(c.text for c in chunks)
        assert "interaction" in kinds  # ledger folded in
        assert "relay caps" in texts

    def test_extract_memory_candidate(self) -> None:
        assert extract_memory_candidate("please remember that I like tea") == "I like tea"
        assert extract_memory_candidate("what time is it") is None


# ══ prompting ═══════════════════════════════════════════════════════════


class TestPrompting:
    def test_action_rule_only_when_can_act(self) -> None:
        chunks: list[ContextChunk] = []
        with_act = build_prompt("do it", context=chunks, can_act=True)
        without = build_prompt("do it", context=chunks, can_act=False)
        assert ACTION_PREFIX in with_act
        assert ACTION_PREFIX not in without

    def test_selection_and_memory_reach_prompt(self) -> None:
        chunks = [
            ContextChunk(kind="selection", title="sel", uri="s", text="def froznak():"),
            ContextChunk(kind="memory", title="m", uri="m", text="likes tea"),
        ]
        prompt = build_prompt("explain", context=chunks)
        assert "froznak" in prompt and "likes tea" in prompt

    def test_assistant_name_in_persona(self) -> None:
        prompt = build_prompt("hi", context=[], name="Kai")
        assert "You are Kai" in prompt
        other = build_prompt("hi", context=[], name="Nova")
        assert "You are Nova" in other

    def test_local_privacy_withholds_file_contents(self) -> None:
        chunks = [ContextChunk(kind="file", title="secret.txt", uri="/x", text="TOPSECRET")]
        prompt = build_prompt("read", context=chunks, privacy_mode="local")
        assert "TOPSECRET" not in prompt
        prompt_cloud = build_prompt("read", context=chunks, privacy_mode="cloud")
        assert "TOPSECRET" in prompt_cloud

    def test_split_action_last_line_only(self) -> None:
        assert split_action("talk about action items")[1] == ""
        spoken, cmd = split_action("Running it.\nACTION: ls -la")
        assert spoken == "Running it." and cmd == "ls -la"


# ══ brain routing ═══════════════════════════════════════════════════════


class TestBrainRouting:
    def _brain(self, cfg: dict, *, realtime=None, llm=None) -> Brain:
        with mock.patch(
            "voice_keyboard.assistant.brain.create_realtime_client", return_value=realtime
        ), mock.patch(
            "voice_keyboard.assistant.brain.create_llm_client", return_value=llm
        ):
            return Brain(
                config=cfg,
                memory=AssistantMemory(),
                context_provider=ContextProvider(home_root=Path.home()),
            )

    def test_typed_text_always_uses_local(self) -> None:
        # The voice agent can't take text; respond() is local-only even
        # when a realtime agent is configured.
        realtime = mock.Mock()
        realtime.ask_text = mock.AsyncMock()
        llm = mock.Mock()
        llm.complete.return_value = "local answer"
        brain = self._brain(_config(brain="auto"), realtime=realtime, llm=llm)
        result = asyncio.run(brain.respond("hello"))
        assert result.text == "local answer"
        assert result.brain == "local"
        realtime.ask_text.assert_not_called()

    def test_spoken_turn_uses_voice_agent(self) -> None:
        realtime = mock.Mock()
        realtime.ask_audio = mock.AsyncMock(
            return_value=mock.Mock(transcript="spoken answer", audio=b"AUD")
        )
        brain = self._brain(_config(brain="auto"), realtime=realtime, llm=mock.Mock())
        result = asyncio.run(brain.respond_audio(b"pcmpcm", transcript_hint="hi"))
        assert result.text == "spoken answer"
        assert result.audio == b"AUD"
        assert result.brain == "realtime"

    def test_spoken_turn_falls_back_to_local_on_agent_failure(self) -> None:
        realtime = mock.Mock()
        realtime.ask_audio = mock.AsyncMock(side_effect=RuntimeError("ws down"))
        llm = mock.Mock()
        llm.complete.return_value = "local heard you"
        brain = self._brain(_config(brain="auto"), realtime=realtime, llm=llm)
        result = asyncio.run(brain.respond_audio(b"pcm", transcript_hint="two plus two"))
        assert result.text == "local heard you"
        assert result.brain == "local"

    def test_forced_realtime_audio_does_not_fall_back(self) -> None:
        realtime = mock.Mock()
        realtime.ask_audio = mock.AsyncMock(side_effect=RuntimeError("ws down"))
        brain = self._brain(_config(brain="realtime"), realtime=realtime, llm=mock.Mock())
        with pytest.raises(RuntimeError, match="ws down"):
            asyncio.run(brain.respond_audio(b"pcm", transcript_hint="hi"))

    def test_spoken_turn_local_when_no_agent(self) -> None:
        llm = mock.Mock()
        llm.complete.return_value = "local voice"
        brain = self._brain(_config(brain="local"), realtime=None, llm=llm)
        result = asyncio.run(brain.respond_audio(b"pcm", transcript_hint="hello there"))
        assert result.text == "local voice" and result.brain == "local"

    def test_typed_without_local_brain_raises(self) -> None:
        brain = self._brain(_config(brain="local"), realtime=None, llm=None)
        with pytest.raises(RuntimeError, match="local brain"):
            asyncio.run(brain.respond("hello"))

    def test_action_dropped_when_cannot_act(self) -> None:
        llm = mock.Mock()
        llm.complete.return_value = "Sure.\nACTION: rm -rf /"
        brain = self._brain(_config(brain="local", can_act=False), llm=llm)
        result = asyncio.run(brain.respond("delete everything"))
        assert result.action == ""
        assert "rm -rf" not in result.text

    def test_action_kept_when_can_act(self) -> None:
        llm = mock.Mock()
        llm.complete.return_value = "On it.\nACTION: grep -rn TODO ."
        brain = self._brain(_config(brain="local", can_act=True), llm=llm)
        result = asyncio.run(brain.respond("find todos"))
        assert result.action == "grep -rn TODO ."


class TestRealtimeFactory:
    def test_none_without_agent(self) -> None:
        assert create_realtime_client(_config()) is None

    def test_built_with_agent_and_key(self) -> None:
        cfg = _config(agent_id="agent_123")
        client = create_realtime_client(cfg)
        assert client is not None and client.agent_id == "agent_123"


# ══ daemon integration + the action gate ════════════════════════════════


class TestConverseIntegration:
    def test_disabled_by_default(self) -> None:
        daemon = _daemon(_config())
        assert daemon._brain is None
        assert daemon._status_response()["assistant"] is False

    def _kai(self, cfg: dict, register: str) -> Daemon:
        from voice_keyboard.flow.registers import resolve_register

        daemon = _daemon(cfg)
        daemon._run_tts = mock.AsyncMock()
        daemon._session_register = resolve_register(register)
        return daemon

    def test_non_terminal_query_is_answered_by_voice(self) -> None:
        # Focused somewhere that is NOT a terminal → Kai answers aloud;
        # nothing is typed into the app.
        daemon = self._kai(_config(enabled=True, brain="auto"), register="prose")
        brain = mock.Mock()
        brain.remember_interaction = mock.Mock()
        brain.respond_audio = mock.AsyncMock(
            return_value=mock.Mock(text="Paris.", audio=b"SPOKEN", brain="realtime")
        )
        daemon._brain = brain
        result = asyncio.run(daemon._run_converse_audio(b"pcm", "capital of France"))
        assert result == "Paris."
        daemon._tts_client.play_audio.assert_called_once_with(b"SPOKEN")
        assert daemon._injector.typed == []  # answers never type into the app

    def test_terminal_command_query_is_typed_no_enter(self) -> None:
        # In a terminal AND a runnable request → compiled command typed at
        # the prompt, Enter never pressed.
        daemon = self._kai(_config(enabled=True, brain="auto"), register="terminal")
        daemon._brain = mock.Mock()
        daemon._focus_changed_since_session = mock.AsyncMock(return_value=False)
        llm = mock.Mock()
        llm.route_terminal_request.return_value = "git status"
        with mock.patch("voice_keyboard.daemon.create_llm_client", return_value=llm):
            result = asyncio.run(daemon._run_converse_audio(b"pcm", "show me the git status"))
        assert result == "git status"
        assert daemon._injector.typed == ["git status"]
        assert daemon._injector.flag_at_type == [True]
        assert daemon._injector.suppress_enter is False

    def test_terminal_question_is_answered_not_typed(self) -> None:
        # In a terminal but it's a QUESTION → the router returns None, so
        # Kai answers aloud and types nothing into the shell.
        daemon = self._kai(_config(enabled=True, brain="auto"), register="terminal")
        brain = mock.Mock()
        brain.remember_interaction = mock.Mock()
        brain.respond_audio = mock.AsyncMock(
            return_value=mock.Mock(text="Paris.", audio=b"SPOKEN", brain="realtime")
        )
        daemon._brain = brain
        llm = mock.Mock()
        llm.route_terminal_request.return_value = None  # it's a question
        with mock.patch("voice_keyboard.daemon.create_llm_client", return_value=llm):
            result = asyncio.run(
                daemon._run_converse_audio(b"pcm", "what is the capital of France")
            )
        assert result == "Paris."
        assert daemon._injector.typed == []  # nothing typed into the terminal
        daemon._tts_client.play_audio.assert_called_once_with(b"SPOKEN")

    def test_type_no_enter_helper_arms_and_restores(self) -> None:
        daemon = _daemon(_config())
        asyncio.run(daemon._type_no_enter("echo hi"))
        assert daemon._injector.typed == ["echo hi"]
        assert daemon._injector.flag_at_type == [True]
        assert daemon._injector.suppress_enter is False

    def test_converse_capture_resolves_terminal_focus(self) -> None:
        cfg = _config(enabled=True, brain="local")
        daemon = _daemon(cfg)
        daemon._converse_capture = True
        daemon._stt_client = mock.Mock(supports_streaming=False, bias_prompt="")

        async def run() -> None:
            async def probe():
                from voice_keyboard.focusprobe import FocusInfo
                return FocusInfo(app="kitty", role="terminal")
            await daemon._setup_flow_session(asyncio.create_task(probe()))
            # converse capture => no engine, but focus/register resolved so
            # Kai knows it is in a terminal.
            assert daemon._flow_engine is None
            assert daemon._session_register.name == "terminal"

        asyncio.run(run())


class TestAssistantConfigValidation:  # noqa: E301
    def test_defaults_validate(self) -> None:
        validate_config(_config())

    def test_brain_enum(self) -> None:
        with pytest.raises(RuntimeError, match="assistant.brain"):
            validate_config(_config(brain="genius"))

    def test_privacy_enum(self) -> None:
        with pytest.raises(RuntimeError, match="assistant.privacy_mode"):
            validate_config(_config(privacy_mode="public"))

    def test_can_act_bool(self) -> None:
        with pytest.raises(RuntimeError, match="assistant.can_act"):
            validate_config(_config(can_act="sure"))

    def test_hotkey_must_differ_from_dictation(self) -> None:
        cfg = _config(enabled=True, hotkey="control+alt+v")
        with pytest.raises(RuntimeError, match="differ from the dictation"):
            validate_config(cfg)

    def test_create_brain_gated_on_enabled(self) -> None:
        assert create_brain(_config(enabled=False)) is None
        assert create_brain(_config(enabled=True, brain="local")) is not None
