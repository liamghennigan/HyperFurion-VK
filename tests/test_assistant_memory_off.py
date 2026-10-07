"""Kai's memory switch: memory_enabled = false means nothing is stored.

Every spoken Kai turn (realtime voice agent or the local brain) is written
to the interactions log in exactly one place, Brain.remember_interaction,
which honours [assistant] memory_enabled. The realtime path used to log the
transcript a second time on its own, ignoring the switch.
"""

import asyncio
import sqlite3
from pathlib import Path
from unittest import mock

import pytest

from voice_keyboard.assistant.brain import Brain
from voice_keyboard.assistant.context import ContextProvider
from voice_keyboard.assistant.memory import AssistantMemory
from voice_keyboard.config import _default_config_with_paths
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


def _rows(memory: AssistantMemory, table: str) -> list[tuple]:
    with sqlite3.connect(memory.db_path) as conn:
        return conn.execute(f"SELECT * FROM {table}").fetchall()  # noqa: S608


def _realtime_agent(answer: str = "Paris.") -> mock.Mock:
    realtime = mock.Mock()
    realtime.ask_audio = mock.AsyncMock(
        return_value=mock.Mock(transcript=answer, audio=b"SPOKEN")
    )
    return realtime


def _local_llm(answer: str = "Paris.") -> mock.Mock:
    llm = mock.Mock()
    llm.complete.return_value = answer
    return llm


def _brain(cfg: dict, memory: AssistantMemory, *, realtime=None, llm=None) -> Brain:
    with mock.patch(
        "voice_keyboard.assistant.brain.create_realtime_client", return_value=realtime
    ), mock.patch("voice_keyboard.assistant.brain.create_llm_client", return_value=llm):
        return Brain(
            config=cfg,
            memory=memory,
            context_provider=ContextProvider(home_root=Path.home()),
        )


def _daemon_with(cfg: dict, brain: Brain) -> Daemon:
    from voice_keyboard.flow.registers import resolve_register
    from voice_keyboard.focusprobe import FocusInfo

    daemon = Daemon(
        config=cfg,
        injector=mock.Mock(),
        ipc_server=mock.Mock(),
        tts_client=mock.Mock(),
    )
    daemon._run_tts = mock.AsyncMock()
    # A known, non-terminal app: Kai answers aloud and types nothing.
    daemon._session_register = resolve_register("prose")
    daemon._session_focus = FocusInfo(app="gedit")
    daemon._brain = brain
    return daemon


@pytest.mark.parametrize("path", ["realtime", "local"])
def test_memory_off_stores_nothing(path: str) -> None:
    cfg = _config(enabled=True, brain="auto", memory_enabled=False)
    memory = AssistantMemory()
    if path == "realtime":
        brain = _brain(cfg, memory, realtime=_realtime_agent(), llm=_local_llm())
    else:
        brain = _brain(cfg, memory, realtime=None, llm=_local_llm())
    daemon = _daemon_with(cfg, brain)

    answer = asyncio.run(daemon._run_converse_audio(b"pcm", "capital of France"))

    assert answer == "Paris."
    # Not even an empty database file: nothing was written.
    assert not memory.db_path.exists()


@pytest.mark.parametrize("path", ["realtime", "local"])
def test_memory_on_logs_each_turn_once(path: str) -> None:
    cfg = _config(enabled=True, brain="auto", memory_enabled=True)
    memory = AssistantMemory()
    if path == "realtime":
        brain = _brain(cfg, memory, realtime=_realtime_agent(), llm=_local_llm())
    else:
        brain = _brain(cfg, memory, realtime=None, llm=_local_llm())
    daemon = _daemon_with(cfg, brain)

    asyncio.run(daemon._run_converse_audio(b"pcm", "capital of France"))

    rows = _rows(memory, "interactions")
    assert len(rows) == 1  # one turn, one record (no double logging)
    _id, user_text, assistant_text, _created = rows[0]
    assert user_text == "capital of France"
    assert assistant_text == "Paris."


@pytest.mark.parametrize("memory_enabled", [True, False])
def test_realtime_brain_alone_writes_nothing(memory_enabled: bool) -> None:
    # respond_audio itself never writes, memory on or off: the daemon owns
    # the single, switch-aware log call.
    cfg = _config(enabled=True, brain="realtime", memory_enabled=memory_enabled)
    memory = AssistantMemory()
    brain = _brain(cfg, memory, realtime=_realtime_agent(), llm=None)

    result = asyncio.run(brain.respond_audio(b"pcm", transcript_hint="capital of France"))

    assert result.brain == "realtime"
    assert not memory.db_path.exists()


def test_maybe_remember_respects_switch() -> None:
    cfg = _config(enabled=True, brain="local", memory_enabled=False)
    memory = AssistantMemory()
    brain = _brain(cfg, memory, llm=_local_llm())

    assert brain.maybe_remember("remember that I prefer short answers") is False
    assert not memory.db_path.exists()


def test_reads_before_any_write_create_nothing() -> None:
    memory = AssistantMemory()
    assert memory.search("anything at all") == []
    assert memory.list_recent() == []
    assert not memory.db_path.exists()
    memory.log_interaction("q", "a")
    assert memory.db_path.exists()
    assert len(_rows(memory, "interactions")) == 1

