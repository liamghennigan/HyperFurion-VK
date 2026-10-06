"""A recording that is only "scratch that" takes back the previous
dictation — when the caret is surely still right after it."""

import asyncio
from unittest import mock

import pytest
from fakes import FakeStreamingSTT, RecordingInjector, _make_daemon
from waiting import wait_until

from voice_keyboard import daemon as daemon_module
from voice_keyboard.focusprobe import FocusInfo


@pytest.fixture(autouse=True)
def inline(tmp_path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path))

    async def _to_thread(func, /, *args, **kwargs):
        await asyncio.sleep(0)
        return func(*args, **kwargs)

    monkeypatch.setattr(asyncio, "to_thread", _to_thread)
    monkeypatch.setattr("voice_keyboard.client._show_overlay", lambda *a, **k: None)


def _two(first: str, second: str, *, apps=("gedit", "gedit"), age=0.0):
    injector = RecordingInjector()
    fakes = [
        FakeStreamingSTT([{"type": "transcript.partial", "text": first, "is_final": True}]),
        FakeStreamingSTT([{"type": "transcript.partial", "text": second, "is_final": True}]),
    ]
    daemon = _make_daemon(fakes[0], injector)
    daemon._stt_patch = mock.patch("voice_keyboard.daemon.create_stt_client", side_effect=fakes)
    current = {"app": apps[0]}
    daemon._probe_patch = mock.patch(
        "voice_keyboard.daemon.probe_focus",
        side_effect=lambda *a, **k: FocusInfo(app=current["app"], role="text", x=1, y=1),
    )
    overlays: list = []

    async def overlay(state, **kwargs):
        overlays.append(kwargs.get("detail", ""))

    daemon._show_hotkey_overlay = overlay

    async def run() -> None:
        with daemon._audio_patch, daemon._stt_patch, daemon._probe_patch:
            await daemon._start_recording()
            assert await wait_until(lambda: injector.screen)
            await daemon._stop_recording()
            if age and daemon._landing:
                daemon._landing["when"] -= age
            current["app"] = apps[1]
            await daemon._start_recording()
            await asyncio.sleep(0.12)
            await daemon._stop_recording()

    asyncio.run(run())
    return injector.screen, overlays


def test_scratch_that_alone_takes_back_the_last_dictation() -> None:
    screen, overlays = _two("send it on Friday.", "scratch that")
    assert screen == ""
    assert any("scratched" in o for o in overlays)


@pytest.mark.parametrize("phrase", ["strike that", "undo that", "Scratch that."])
def test_synonyms(phrase) -> None:
    assert _two("send it on Friday.", phrase)[0] == ""


def test_not_in_another_app() -> None:
    screen, overlays = _two("send it on Friday.", "scratch that", apps=("gedit", "firefox"))
    assert screen == "Send it on Friday."
    assert any("another app" in o for o in overlays)


def test_not_when_too_old() -> None:
    screen, _ = _two("send it on Friday.", "scratch that", age=daemon_module.REJOIN_WINDOW_S + 1)
    assert screen == "Send it on Friday."


def test_not_across_complex_unicode() -> None:
    screen, overlays = _two("ship it 🚀", "scratch that")
    assert screen.endswith("🚀")
    assert any("safely" in o for o in overlays)


def test_a_scratch_inside_a_recording_stays_inside_it() -> None:
    screen, _ = _two("send it on Friday.", "oops scratch that")
    assert screen == "Send it on Friday."


def test_only_once() -> None:
    injector_screen, _ = _two("send it on Friday.", "scratch that scratch that")
    assert injector_screen == ""
