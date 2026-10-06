"""[flow] fillers: hesitation sounds a streaming recognizer writes down are
dropped from what is typed, with the commas that bracket them. (The
grammar's cases live in the parity corpus, tests/flow_corpus.json.)"""

import asyncio

import pytest
from fakes import FakeStreamingSTT, RecordingInjector, _make_daemon
from waiting import wait_until

from voice_keyboard.config import _default_config_with_paths, validate_config
from voice_keyboard.flow.engine import FlowConfig, FlowEngine
from voice_keyboard.flow.grammar import Grammar
from voice_keyboard.flow.registers import PROSE, VERBATIM


def finalize(text: str, register=PROSE, **grammar) -> str:
    return FlowEngine(FlowConfig(), Grammar(**grammar), register).finalize(text, now=1.0).text


def test_defaults_drop_hesitations_only() -> None:
    assert finalize("Um, so we should, uh, ship it.") == "So we should ship it."
    assert finalize("like, you know, hmm, well") == "Like, you know, hmm, well"


def test_custom_list_and_off() -> None:
    assert finalize("er, I mean, ah, yes", fillers=["er", "ah"]) == "I mean yes"
    assert finalize("we should, uh, ship it", fillers=[]) == "We should, uh, ship it"


def test_verbatim_keeps_everything() -> None:
    assert finalize("um, uh", VERBATIM, enabled=False) == "um, uh"


def test_config_validation() -> None:
    config = _default_config_with_paths()
    config["xai"]["api_key"] = "test-api-key"
    config["flow"]["fillers"] = ["um", "er"]
    validate_config(config)
    for bad in ("um", ["you know"], [""], [3]):
        config["flow"]["fillers"] = bad
        with pytest.raises(RuntimeError, match="flow.fillers"):
            validate_config(config)


def test_the_daemon_types_without_them(tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path))

    async def _to_thread(func, /, *args, **kwargs):
        await asyncio.sleep(0)
        return func(*args, **kwargs)

    monkeypatch.setattr(asyncio, "to_thread", _to_thread)
    monkeypatch.setattr("voice_keyboard.client._show_overlay", lambda *a, **k: None)
    injector = RecordingInjector()
    events = [{"type": "transcript.partial", "text": "Um, the build is, uh, green.", "is_final": True}]
    daemon = _make_daemon(FakeStreamingSTT(events), injector)
    daemon._config["flow"]["fillers"] = ["um", "uh"]

    async def run() -> str:
        with daemon._audio_patch, daemon._stt_patch, daemon._probe_patch:
            await daemon._start_recording()
            assert await wait_until(lambda: injector.screen.endswith("green."))
            return await daemon._stop_recording()

    assert asyncio.run(run()) == "The build is green."
    assert injector.screen == "The build is green."


def test_addresses_switch() -> None:
    assert finalize("mail liam at example dot com") == "Mail liam@example.com"
    assert finalize("mail liam at example dot com", addresses=False) == "Mail liam at example dot com"


def test_formatters_switch() -> None:
    assert finalize("call snake case user id") == "Call user_id"
    assert finalize("call snake case user id", formatters=False) == "Call snake case user id"
