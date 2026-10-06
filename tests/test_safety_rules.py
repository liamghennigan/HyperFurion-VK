"""The hard rules, for paths added after the originals were tested: a
line break is never an Enter where a terminal can't be ruled out, a
model's line break is never an Enter, and a terminal is never sent a
paste for a character the grammar invented."""

import asyncio

import pytest
from fakes import FakeStreamingSTT, RecordingInjector, _make_daemon

from voice_keyboard import trial
from voice_keyboard.config import DEFAULT_CONFIG
from voice_keyboard.daemon import _surely_not_a_terminal, polish_plausible
from voice_keyboard.focusprobe import FocusInfo


@pytest.fixture(autouse=True)
def inline(tmp_path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path))

    async def _to_thread(func, /, *args, **kwargs):
        await asyncio.sleep(0)
        return func(*args, **kwargs)

    monkeypatch.setattr(asyncio, "to_thread", _to_thread)
    monkeypatch.setattr("voice_keyboard.client._show_overlay", lambda *a, **k: None)


@pytest.mark.parametrize("app, refused", [("gedit", False), ("", True), ("kitty", True)])
def test_enter_is_refused_wherever_a_terminal_cannot_be_ruled_out(app, refused) -> None:
    injector = RecordingInjector()
    injector.suppress_enter = False
    daemon = _make_daemon(FakeStreamingSTT([{"type": "transcript.partial", "text": "hi", "is_final": True}]),
                          injector, app=app)
    daemon._config["registers"] = {"map": {"kitty": "prose"}, "default": "prose"}
    seen = {}

    async def run() -> None:
        with daemon._audio_patch, daemon._stt_patch, daemon._probe_patch:
            await daemon._start_recording()
            seen["during"] = injector.suppress_enter
            await asyncio.sleep(0.05)
            await daemon._stop_recording()

    asyncio.run(run())
    assert seen["during"] is refused


def test_only_identified_non_terminals_are_sure() -> None:
    assert _surely_not_a_terminal(FocusInfo(app="gedit"))
    assert not _surely_not_a_terminal(None)
    assert not _surely_not_a_terminal(FocusInfo(app="kitty"))


def test_a_polish_never_adds_line_breaks() -> None:
    assert not polish_plausible("hey team send me the report", "Hey team,\nsend me the report?")
    assert polish_plausible("line one\nline two", "Line one.\nLine two.")


def test_terminals_and_code_get_no_pasted_marks() -> None:
    terminal = {**DEFAULT_CONFIG, "flow": {**DEFAULT_CONFIG["flow"], "language": "fr"}}
    assert trial.run(terminal, "terminal: echo emoji rocket".split()) == "echo emoji rocket"
    assert " " not in trial.run(terminal, "terminal: ls point virgule ls".split())
    assert trial.run(terminal, "ça va point d'interrogation".split()) == "Ça va ?"  # prose keeps French spacing
