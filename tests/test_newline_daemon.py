"""The daemon puts the line-break policy to work: every session (and
every `voice-keyboard type` / `transform` outside one) arms the injector
for what a line break is in the focused field, and puts it back after.
Driven end to end with fake focus, a recording injector, and the real
Linux and Windows injectors' key output."""

import asyncio
from types import SimpleNamespace
from unittest import mock

import pytest
from fakes import FakeStreamingSTT, RecordingInjector, _make_daemon
from waiting import wait_until

from voice_keyboard.config import DEFAULT_CONFIG, _default_config_with_paths, load_config, validate_config
from voice_keyboard.daemon import Daemon
from voice_keyboard.focusprobe import FocusInfo
from voice_keyboard.newline import ENTER, NONE, SHIFT_ENTER, NewlineChoice


class NewlineInjector(RecordingInjector):
    """Like the real injectors: a newline is Enter, Shift+Enter while
    shift_newline is set, and a space while Enter is refused."""

    def __init__(self):
        super().__init__()
        self.suppress_enter = False
        self.shift_newline = False
        self.enters = 0
        self.shift_enters = 0
        self.armed: list[tuple[bool, bool]] = []

    def type_text(self, text: str) -> None:
        self.armed.append((self.suppress_enter, self.shift_newline))
        if self.suppress_enter:
            text = text.replace("\n", " ")
        elif self.shift_newline:
            self.shift_enters += text.count("\n")
        else:
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


def dictate(said: str, focus, *, newline=None, chat_apps=None):
    injector = NewlineInjector()
    daemon = _make_daemon(FakeStreamingSTT([{"type": "transcript.partial", "text": said, "is_final": True}]), injector)
    daemon._probe_patch = mock.patch("voice_keyboard.daemon.probe_focus", return_value=focus)
    if newline is not None:
        daemon._config["registers"]["newline"] = newline
    if chat_apps is not None:
        daemon._config["registers"]["chat_apps"] = chat_apps
    overlays: list[str] = []
    real_overlay = daemon._show_hotkey_overlay

    async def overlay(state, detail="", **kw):
        overlays.append(detail)
        return await real_overlay(state, detail=detail, **kw)

    daemon._show_hotkey_overlay = overlay
    seen = {}

    async def run() -> None:
        with daemon._audio_patch, daemon._stt_patch, daemon._probe_patch:
            await daemon._start_recording()
            assert await wait_until(lambda: injector.screen)
            seen["during"] = (injector.suppress_enter, injector.shift_newline)
            seen["status"] = daemon._status_response()["newline"]
            await daemon._stop_recording()

    asyncio.run(run())
    return SimpleNamespace(
        injector=injector, during=seen["during"], status=seen["status"], overlays=overlays,
        choice=daemon._session_newline,
    )


WHATSAPP_FIREFOX = FocusInfo(app="Firefox", role="entry", title="WhatsApp — Mozilla Firefox")
GOOGLE_DOC = FocusInfo(app="Google Chrome", role="entry", url="https://docs.google.com/document/d/1/edit",
                       page_url="https://docs.google.com/document/d/1/edit", title="Plan - Google Docs - Google Chrome")


class TestSessions:
    def test_a_web_chat_gets_shift_enter_and_never_sends(self) -> None:
        run = dictate("dear team new line thanks", WHATSAPP_FIREFOX)
        assert run.injector.screen == "Dear team\nThanks"
        assert (run.injector.shift_enters, run.injector.enters) == (1, 0)
        assert run.during == (False, True) and run.status == SHIFT_ENTER
        assert run.choice == NewlineChoice(SHIFT_ENTER, "chat-site", "WhatsApp")
        # Put back after the session.
        assert (run.injector.suppress_enter, run.injector.shift_newline) == (False, False)

    def test_a_document_editor_in_a_browser_keeps_real_paragraphs(self) -> None:
        run = dictate("dear team new paragraph thanks", GOOGLE_DOC)
        assert run.injector.screen == "Dear team\n\nThanks"
        assert (run.injector.enters, run.injector.shift_enters) == (2, 0)
        assert run.during == (False, False) and run.status == ENTER

    def test_an_unknown_web_page_gets_shift_enter(self) -> None:
        run = dictate("one new line two", FocusInfo(app="chrome.exe", title="Contact us - Google Chrome"))
        assert (run.injector.shift_enters, run.injector.enters) == (1, 0)

    def test_a_one_line_field_types_a_space_and_says_why(self) -> None:
        run = dictate("dear team new line thanks", FocusInfo(app="gedit", role="text", single_line=True))
        assert run.injector.screen == "Dear team Thanks"
        assert (run.injector.enters, run.injector.shift_enters) == (0, 0)
        assert run.during == (True, False) and run.status == NONE
        assert "Line break typed as a space: a one-line field, where Enter would submit it" in run.overlays

    def test_unknown_focus_is_unchanged(self) -> None:
        run = dictate("dear team new line thanks", None)
        assert run.injector.screen == "Dear team Thanks" and run.during == (True, False)
        assert "Line break typed as a space: this app couldn't be identified" in run.overlays

    def test_other_apps_keep_enter(self) -> None:
        run = dictate("dear team new line thanks", FocusInfo(app="gedit", role="text"))
        assert run.injector.enters == 1 and run.during == (False, False)
        assert not any("typed as a space" in o for o in run.overlays)

    def test_a_site_rule_from_config(self) -> None:
        run = dictate("one new line two", WHATSAPP_FIREFOX, newline={"whatsapp": "none"})
        assert run.injector.screen == "One Two" and run.during == (True, False)
        assert '[registers.newline] says none for "whatsapp"' in " ".join(run.overlays)

    def test_an_app_rule_from_config(self) -> None:
        run = dictate("one new line two", FocusInfo(app="obsidian", role="text"), newline={"Obsidian": "shift+enter"})
        assert run.injector.shift_enters == 1

    def test_chat_apps_still_work_and_reach_browser_tabs(self) -> None:
        run = dictate("one new line two", FocusInfo(app="mychat", role="text"), chat_apps=["mychat"])
        assert run.injector.shift_enters == 1
        run = dictate("one new line two", GOOGLE_DOC, chat_apps=["plan"])
        assert run.injector.shift_enters == 1  # the title segment "Plan"


def type_outside(focus, text="line one\nline two", *, config=None):
    injector = NewlineInjector()
    daemon = _make_daemon(FakeStreamingSTT([]), injector)
    if config:
        daemon._config["registers"].update(config)
    with mock.patch("voice_keyboard.daemon.probe_focus", return_value=focus):
        asyncio.run(daemon._type_text(text))
    return injector


class TestOutsideASession:
    def test_a_web_chat(self) -> None:
        injector = type_outside(WHATSAPP_FIREFOX)
        assert injector.armed == [(False, True)] and injector.shift_enters == 1
        assert (injector.suppress_enter, injector.shift_newline) == (False, False)  # restored

    def test_a_one_line_field(self) -> None:
        injector = type_outside(FocusInfo(app="gedit", single_line=True))
        assert injector.screen == "line one line two" and injector.armed == [(True, False)]
        assert injector.suppress_enter is False

    def test_unidentified_focus_never_gets_enter(self) -> None:
        # It may be a terminal the probe can't see (Wayland without
        # AT-SPI): a typed newline there would run the line.
        injector = type_outside(None)
        assert injector.enters == 0 and injector.screen == "line one line two"
        assert injector.armed == [(True, False)] and injector.suppress_enter is False  # restored

    def test_unidentified_focus_with_a_terminal_default_is_refused(self) -> None:
        assert type_outside(None, config={"default": "terminal"}).enters == 0

    def test_a_terminal_mapped_to_prose_is_still_a_terminal(self) -> None:
        injector = type_outside(FocusInfo(app="kitty"), config={"map": {"kitty": "prose"}})
        assert injector.enters == 0 and injector.armed == [(True, False)]


class TestConfig:
    def test_default_is_empty(self) -> None:
        assert DEFAULT_CONFIG["registers"]["newline"] == {}

    def _validate(self, newline) -> None:
        cfg = _default_config_with_paths()
        cfg["xai"]["api_key"] = "k"
        cfg["registers"]["newline"] = newline
        validate_config(cfg)

    def test_valid_rules(self) -> None:
        self._validate({"web.whatsapp.com": "shift+enter", "obsidian": "Enter", "docs.google.com/spreadsheets": "none"})

    @pytest.mark.parametrize("newline, message", [
        ({"slack": "send"}, "registers.newline"),
        ({"slack": True}, "registers.newline"),
        ({" ": "enter"}, "empty"),
        (["slack"], "must be a table"),
    ])
    def test_invalid_rules(self, newline, message) -> None:
        with pytest.raises(RuntimeError, match=message):
            self._validate(newline)

    def test_loaded_from_toml(self, tmp_path) -> None:
        path = tmp_path / "config.toml"
        path.write_text(
            '[registers]\nchat_apps = ["mychat"]\n\n[registers.newline]\n"web.whatsapp.com" = "none"\nobsidian = "shift+enter"\n',
            encoding="utf-8",
        )
        cfg = load_config(path)
        assert cfg["registers"]["newline"] == {"web.whatsapp.com": "none", "obsidian": "shift+enter"}
        assert cfg["registers"]["chat_apps"] == ["mychat"] and cfg["registers"]["probe"] is True


# ── the real injectors press what the choice says ─────────────────────────

def armed(injector, key: str):
    Daemon._arm_newline(SimpleNamespace(_injector=injector), NewlineChoice(key, "test"))
    return injector


class TestLinuxInjectorKeys:
    @pytest.fixture
    def writes(self):
        from voice_keyboard.injector import TextInjector

        def run(key: str) -> list:
            with mock.patch("voice_keyboard.injector.UInput") as uinput, mock.patch("voice_keyboard.injector.time.sleep"):
                injector = TextInjector()
                injector.start()
                armed(injector, key).type_text("a\nb")
                return [call.args[1:] for call in uinput.return_value.write.call_args_list]

        return run

    def test_enter(self, writes) -> None:
        from evdev import ecodes as e

        events = writes(ENTER)
        assert (e.KEY_ENTER, 1) in events and (e.KEY_LEFTSHIFT, 1) not in events

    def test_shift_enter(self, writes) -> None:
        from evdev import ecodes as e

        events = writes(SHIFT_ENTER)
        start = events.index((e.KEY_LEFTSHIFT, 1))
        assert events[start:start + 4] == [(e.KEY_LEFTSHIFT, 1), (e.KEY_ENTER, 1), (e.KEY_ENTER, 0), (e.KEY_LEFTSHIFT, 0)]

    def test_none(self, writes) -> None:
        from evdev import ecodes as e

        events = writes(NONE)
        assert (e.KEY_ENTER, 1) not in events and (e.KEY_SPACE, 1) in events


class FakeSendInput:
    def __init__(self):
        self.events: list[tuple[int, int]] = []

    def SendInput(self, count, array, size):
        self.events += [(int(e.ki.wVk), int(e.ki.dwFlags)) for e in array[:count]]
        return count

    def GetAsyncKeyState(self, vk):
        return 0

    def MapVirtualKeyW(self, vk, kind):
        return 0


class TestWindowsInjectorKeys:
    def run(self, key: str) -> list:
        from voice_keyboard.windows import injector as win

        user32 = FakeSendInput()
        injector = win.WinTextInjector()
        injector._user32 = user32
        with mock.patch.object(win.time, "sleep"):
            armed(injector, key).type_text("a\nb")
        return [vk for vk, flags in user32.events if not flags & win.KEYEVENTF_KEYUP]

    def test_enter(self) -> None:
        from voice_keyboard.windows.injector import VK_LSHIFT, VK_RETURN

        downs = self.run(ENTER)
        assert VK_RETURN in downs and VK_LSHIFT not in downs

    def test_shift_enter(self) -> None:
        from voice_keyboard.windows.injector import VK_LSHIFT, VK_RETURN

        downs = self.run(SHIFT_ENTER)
        assert downs[downs.index(VK_LSHIFT) + 1] == VK_RETURN

    def test_none(self) -> None:
        from voice_keyboard.windows.injector import VK_RETURN

        assert VK_RETURN not in self.run(NONE)
