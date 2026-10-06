"""Only a hand sends: in a terminal a line break is Enter, and Enter runs
the line. Dictating "new line" into a terminal must never press it —
neither through the grammar nor through the injector."""

import asyncio

import pytest
from fakes import FakeStreamingSTT, RecordingInjector, _make_daemon
from waiting import wait_until


class EnterInjector(RecordingInjector):
    """Like the real injectors: a newline is the Enter key unless Enter is
    being refused, in which case it becomes a space."""

    def __init__(self):
        super().__init__()
        self.suppress_enter = False
        self.enters = 0
        self.armed: list[bool] = []

    def type_text(self, text: str) -> None:
        self.armed.append(self.suppress_enter)
        if self.suppress_enter:
            text = text.replace("\n", " ")
        self.enters += text.count("\n")
        super().type_text(text)


@pytest.fixture(autouse=True)
def inline(tmp_path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path))

    async def _to_thread(func, /, *args, **kwargs):
        await asyncio.sleep(0)
        return func(*args, **kwargs)

    monkeypatch.setattr(asyncio, "to_thread", _to_thread)
    monkeypatch.setattr("voice_keyboard.client._show_overlay", lambda *a, **k: None)


def _dictate(said: str, app: str) -> EnterInjector:
    injector = EnterInjector()
    daemon = _make_daemon(
        FakeStreamingSTT([{"type": "transcript.partial", "text": said, "is_final": True}]), injector, app=app
    )

    async def run() -> None:
        with daemon._audio_patch, daemon._stt_patch, daemon._probe_patch:
            await daemon._start_recording()
            assert await wait_until(lambda: injector.screen)
            assert injector.suppress_enter is (app == "kitty")  # armed for the whole session
            await daemon._stop_recording()

    asyncio.run(run())
    return injector


def test_a_terminal_never_gets_enter() -> None:
    injector = _dictate("rm dash rf build new line", "kitty")
    assert injector.enters == 0
    assert "\n" not in injector.screen
    assert all(injector.armed)
    assert injector.suppress_enter is False  # released after the session


def test_prose_keeps_its_line_breaks() -> None:
    injector = _dictate("dear team new line thanks", "gedit")
    assert injector.screen == "Dear team\nThanks"
    assert injector.enters == 1
