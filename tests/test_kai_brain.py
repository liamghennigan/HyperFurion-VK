"""A brain is bound to the verdict it was built under (DESIGN A3, A4).

create_brain builds nothing while Kai is off, copies the settings it reads
(so a reload mid-turn can't redirect a question), keeps its KaiState, and
searches dictation history semantically only where the verdict allows it.
"""

import asyncio
from unittest import mock

import pytest

from voice_keyboard.assistant.brain import Brain, create_brain
from voice_keyboard.config import _default_config_with_paths


@pytest.fixture(autouse=True)
def state_dir(tmp_path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path))


@pytest.fixture(autouse=True)
def inline_to_thread(monkeypatch: pytest.MonkeyPatch):
    async def _to_thread(func, /, *args, **kwargs):
        return func(*args, **kwargs)

    monkeypatch.setattr(asyncio, "to_thread", _to_thread)


def _online(**assistant) -> dict:
    cfg = _default_config_with_paths()
    cfg["xai"]["api_key"] = "xai-test-key"
    cfg["assistant"].update(assistant)
    return cfg


def _local(**assistant) -> dict:
    cfg = _online(**assistant)
    cfg["providers"]["openai"]["base_url"] = "http://127.0.0.1:8000/v1"
    cfg["stt"]["provider"] = "openai"
    cfg["tts"]["provider"] = "openai"
    cfg["llm"].update(provider="openai", base_url="http://127.0.0.1:8080/v1", model="qwen")
    return cfg


def test_off_by_default_with_online_services() -> None:
    assert create_brain(_online()) is None


def test_on_by_default_when_everything_is_local() -> None:
    brain = create_brain(_local())
    assert isinstance(brain, Brain)
    assert brain.state.on and brain.state.local and brain.state.setting == "auto"
    assert brain.llm is not None and brain.llm._direct is True


def test_true_turns_it_on_with_online_services() -> None:
    brain = create_brain(_online(enabled=True))
    assert brain is not None and brain.state.setting == "on" and not brain.state.local


def test_false_keeps_it_off_even_when_local() -> None:
    assert create_brain(_local(enabled=False)) is None


def test_brain_keeps_its_own_copy_of_the_settings() -> None:
    cfg = _local()
    brain = create_brain(cfg)
    assert brain.built_from(cfg)
    # A reload changes the daemon's dicts in place: the brain doesn't see it.
    cfg["llm"]["base_url"] = "https://api.x.ai/v1"
    cfg["recall"].update(base_url="https://api.openai.com/v1", model="text-embedding-3-small")
    cfg["assistant"]["memory_enabled"] = False
    assert brain.llm._base_url == "http://127.0.0.1:8080/v1"
    assert brain._embedder is None
    assert brain._memory_enabled is True
    assert not brain.built_from(cfg)


def test_online_recall_is_keyword_only_under_auto() -> None:
    cfg = _local()
    cfg["recall"].update(base_url="https://api.openai.com/v1", model="text-embedding-3-small")
    brain = create_brain(cfg)
    assert brain is not None
    assert brain._embedder is None


def test_local_recall_is_semantic_under_auto() -> None:
    cfg = _local()
    cfg["recall"].update(base_url="http://localhost:11434/v1", model="nomic-embed-text")
    assert create_brain(cfg)._embedder is not None


def test_online_recall_is_used_once_you_turn_kai_on() -> None:
    cfg = _local(enabled=True)
    cfg["recall"].update(base_url="https://api.openai.com/v1", model="text-embedding-3-small")
    assert create_brain(cfg)._embedder is not None


def test_turn_sends_history_only_through_the_chosen_embedder() -> None:
    cfg = _local()
    cfg["recall"].update(base_url="https://api.openai.com/v1", model="text-embedding-3-small")
    brain = create_brain(cfg)
    brain._llm = mock.Mock()
    brain._llm.complete.return_value = "ok"
    with mock.patch.object(brain._memory, "relevant_chunks", return_value=[]) as chunks:
        asyncio.run(brain.respond("what did I say about the relay"))
    assert chunks.call_args.kwargs["embedder"] is None


def test_memory_off_skips_every_lookup() -> None:
    brain = create_brain(_local(memory_enabled=False))
    brain._llm = mock.Mock()
    brain._llm.complete.return_value = "ok"
    with mock.patch.object(brain._memory, "relevant_chunks") as chunks:
        asyncio.run(brain.respond("anything"))
    chunks.assert_not_called()
