"""The macOS Accessibility-API focus probe, driven through a fake AX tree.

voice_keyboard/macos/focus.py is plain logic over the small interface in
voice_keyboard/macos/ax.py; FakeAX implements that interface with dicts,
so every case — terminals by bundle id, VS Code's xterm.js terminal,
password fields, the caret, single/multi-line, the window title, the
Electron accessibility switch — runs on any platform.
"""

import sys
from dataclasses import dataclass, fields
from typing import Optional
from unittest import mock

import pytest

from voice_keyboard import focusprobe
from voice_keyboard.flow.registers import TERMINAL, VERBATIM, register_for_app
from voice_keyboard.focusprobe import FocusInfo
from voice_keyboard.macos import focus as macfocus


class Rect(tuple):
    pass


class Range(tuple):
    pass


class Point(tuple):
    pass


class Size(tuple):
    pass


class FakeAX:
    """Elements are dicts of attribute -> value. AXBoundsForRange is a
    function of the (location, length) asked for."""

    def __init__(self, focused_app=None, *, pid=4242, name="TextEdit", bundle="com.apple.TextEdit"):
        self.system = {"AXFocusedApplication": focused_app}
        self.pids = {id(focused_app): pid} if focused_app is not None else {}
        self.apps = {pid: (name, bundle)}
        self.settable_attrs: set = set()
        self.sets: list = []
        self.timeouts: list = []
        # What the app exposes after AXManualAccessibility is switched on.
        self.after_manual: Optional[dict] = None

    def system_wide(self):
        return self.system

    def set_timeout(self, element, seconds):
        self.timeouts.append(seconds)

    def attribute(self, element, name):
        if element is None:
            return None
        return element.get(name)

    def parameterized(self, element, name, parameter):
        handler = element.get(f"param:{name}") if element is not None else None
        return handler(tuple(parameter)) if handler else None

    def settable(self, element, name):
        return (id(element), name) in self.settable_attrs

    def set_attribute(self, element, name, value):
        self.sets.append((name, value))
        if name == "AXManualAccessibility" and self.after_manual is not None:
            element["AXFocusedUIElement"] = self.after_manual
            return True
        return False

    def pid(self, element):
        return self.pids.get(id(element))

    def app_info(self, pid):
        return self.apps.get(pid, ("", ""))

    def rect(self, value):
        return tuple(value) if isinstance(value, Rect) else None

    def point(self, value):
        return tuple(value) if isinstance(value, Point) else None

    def size(self, value):
        return tuple(value) if isinstance(value, Size) else None

    def text_range(self, value):
        return tuple(value) if isinstance(value, Range) else None

    def make_range(self, location, length):
        return Range((location, length))


def app(field=None, title="Untitled"):
    return {"AXFocusedUIElement": field, "AXFocusedWindow": {"AXTitle": title}}


def text_field(role="AXTextField", caret=5, **extra):
    field = {
        "AXRole": role,
        "AXSelectedTextRange": Range((caret, 0)),
        "param:AXBoundsForRange": lambda span: Rect((100 + 7 * span[0], 200, 0 if span[1] == 0 else 7, 18)),
        "AXPosition": Point((80, 190)),
        "AXSize": Size((400, 30)),
    }
    field.update(extra)
    return field


@pytest.fixture(autouse=True)
def fresh_electron_cache():
    macfocus._manual_accessibility_pids.clear()
    yield
    macfocus._manual_accessibility_pids.clear()


def probe(ax):
    return macfocus.probe(ax, own_pid=1)


class TestProbe:
    def test_nothing_focused(self) -> None:
        assert probe(FakeAX(None)) is None

    def test_a_text_field(self) -> None:
        ax = FakeAX(app(text_field(), title="Notes — draft.txt"))
        found = probe(ax)
        assert found.app == "TextEdit" and found.bundle_id == "com.apple.TextEdit"
        assert found.ax_role == "AXTextField" and found.role == "entry"
        assert found.editable and not found.secret and not found.terminal
        assert found.multiline is False
        assert found.window_title == "Notes — draft.txt"
        assert (found.x, found.y) == (135, 200)  # the empty range at the caret
        assert ax.timeouts == [macfocus.MESSAGING_TIMEOUT_S]  # never hang on a stuck app

    def test_a_text_area_is_multi_line(self) -> None:
        found = probe(FakeAX(app(text_field("AXTextArea"))))
        assert found.multiline is True and found.role == "text"

    def test_caret_falls_back_to_the_character_before(self) -> None:
        field = text_field(caret=3)
        field["param:AXBoundsForRange"] = lambda span: Rect((50, 60, 0, 0)) if span[1] == 0 else Rect(
            (100 + 7 * span[0], 200, 7, 18))
        found = probe(FakeAX(app(field)))
        assert (found.x, found.y) == (100 + 7 * 2 + 7, 200)  # right edge of char 2

    def test_no_caret_uses_the_field(self) -> None:
        field = text_field()
        del field["AXSelectedTextRange"]
        found = probe(FakeAX(app(field)))
        assert (found.x, found.y) == (80 + 200, 190)

    def test_password_field(self) -> None:
        field = text_field("AXTextField", AXSubrole="AXSecureTextField")
        found = probe(FakeAX(app(field)))
        assert found.secret and found.role == "password text" and found.multiline is False
        # No caret bounds from a secret field (they would reveal its length).
        assert (found.x, found.y) == (80 + 200, 190)
        assert register_for_app(found.app, found.role) is VERBATIM

    def test_secure_text_field_role(self) -> None:
        found = probe(FakeAX(app(text_field("AXSecureTextField"))))
        assert found.secret and found.role == "password text"

    @pytest.mark.parametrize("name, bundle", [
        ("Terminal", "com.apple.Terminal"),
        ("iTerm2", "com.googlecode.iterm2"),
        ("Warp", "dev.warp.Warp-Stable"),
        ("Ghostty", "com.mitchellh.ghostty"),
        ("kitty", "net.kovidgoyal.kitty"),
        ("Alacritty", "org.alacritty"),
        ("WezTerm", "com.github.wez.wezterm"),
        ("My Renamed Term", "com.github.wez.wezterm"),
    ])
    def test_terminals_by_bundle_id(self, name, bundle) -> None:
        found = probe(FakeAX(app(text_field("AXTextArea")), name=name, bundle=bundle))
        assert found.terminal and found.role == "terminal"
        assert register_for_app(found.app, found.role) is TERMINAL

    def test_vs_code_terminal_by_its_xterm_textarea(self) -> None:
        field = text_field("AXTextArea", AXDOMClassList=["xterm-helper-textarea"])
        found = probe(FakeAX(app(field), name="Code", bundle="com.microsoft.VSCode"))
        assert found.terminal and found.role == "terminal"

    def test_cursor_terminal_by_its_label(self) -> None:
        field = text_field("AXTextArea", AXDescription="Terminal 1, zsh")
        found = probe(FakeAX(app(field), name="Cursor", bundle="com.todesktop.230313mzl4w4u92"))
        assert found.terminal

    def test_vs_code_editor_is_not_a_terminal(self) -> None:
        field = text_field("AXTextArea", AXDescription="The editor is not accessible at this time.")
        found = probe(FakeAX(app(field), name="Code", bundle="com.microsoft.VSCode"))
        assert not found.terminal and found.role == "text"

    def test_a_terminal_label_outside_an_ide_is_just_a_field(self) -> None:
        field = text_field(AXDescription="Terminal ID")
        found = probe(FakeAX(app(field), name="Safari", bundle="com.apple.Safari"))
        assert not found.terminal

    def test_xterm_in_a_browser_is_a_terminal(self) -> None:
        field = text_field("AXTextArea", AXDOMClassList=["xterm-helper-textarea"])
        found = probe(FakeAX(app(field), name="Google Chrome", bundle="com.google.Chrome"))
        assert found.terminal

    def test_electron_app_is_asked_for_its_tree_once(self) -> None:
        application = app(None)
        ax = FakeAX(application, name="Slack", bundle="com.tinyspeck.slackmacgap")
        ax.after_manual = text_field("AXTextArea")
        found = probe(ax)
        assert ax.sets == [("AXManualAccessibility", True)]
        assert found.ax_role == "AXTextArea" and found.multiline is True
        probe(ax)
        assert len(ax.sets) == 1  # once per process

    def test_non_electron_apps_are_never_written_to(self) -> None:
        ax = FakeAX(app(None), name="TextEdit", bundle="com.apple.TextEdit")
        found = probe(ax)
        assert ax.sets == []
        assert found.app == "TextEdit" and found.role == "" and not found.editable

    def test_unknown_role_but_settable_value_is_editable(self) -> None:
        field = {"AXRole": "AXWebArea"}
        ax = FakeAX(app(field), name="Safari", bundle="com.apple.Safari")
        ax.settable_attrs.add((id(field), "AXValue"))
        found = probe(ax)
        assert found.editable and found.multiline is None and found.role == "document web"

    def test_our_own_process_is_not_a_target(self) -> None:
        assert macfocus.probe(FakeAX(app(text_field()), pid=77), own_pid=77) is None

    def test_name_falls_back_to_the_app_title(self) -> None:
        application = app(text_field())
        application["AXTitle"] = "Mail"
        ax = FakeAX(application, name="", bundle="")
        assert probe(ax).app == "Mail"


class TestSelection:
    def test_reads_the_selection(self) -> None:
        field = text_field("AXTextArea", AXSelectedTextRange=Range((4, 5)), AXSelectedText="hello")
        assert macfocus.selected_text(FakeAX(app(field)), 4000) == ("hello", 5)

    def test_too_long_reports_the_length_only(self) -> None:
        field = text_field("AXTextArea", AXSelectedTextRange=Range((0, 9000)), AXSelectedText="x" * 9000)
        assert macfocus.selected_text(FakeAX(app(field)), 4000) == ("", 9000)

    def test_nothing_selected(self) -> None:
        assert macfocus.selected_text(FakeAX(app(text_field())), 4000) == ("", 0)

    def test_never_a_password_field(self) -> None:
        field = text_field(AXSubrole="AXSecureTextField", AXSelectedTextRange=Range((0, 6)),
                           AXSelectedText="hunter")
        assert macfocus.selected_text(FakeAX(app(field)), 4000) == ("", 0)
        assert macfocus.selected_text(FakeAX(app(field)), 4000, editable_only=False,
                                      allow_terminal=True) == ("", 0)

    def test_terminal_only_for_reading_aloud(self) -> None:
        field = text_field("AXTextArea", AXSelectedTextRange=Range((0, 2)), AXSelectedText="ls")
        ax = FakeAX(app(field), name="Terminal", bundle="com.apple.Terminal")
        assert macfocus.selected_text(ax, 4000) == ("", 0)  # a rewrite: typing won't replace it
        assert macfocus.selected_text(ax, 4000, editable_only=False, allow_terminal=True) == ("ls", 2)

    def test_web_page_text_only_for_reading_aloud(self) -> None:
        page = {"AXRole": "AXWebArea", "AXSelectedTextRange": Range((0, 4)), "AXSelectedText": "news"}
        ax = FakeAX(app(page), name="Safari", bundle="com.apple.Safari")
        assert macfocus.selected_text(ax, 4000) == ("", 0)
        assert macfocus.selected_text(ax, 4000, editable_only=False) == ("news", 4)

    def test_no_app(self) -> None:
        assert macfocus.selected_text(FakeAX(None), 4000) is None


class TestFocusInfoBridge:
    FOUND = macfocus.MacFocus(
        app="Slack", bundle_id="com.tinyspeck.slackmacgap", pid=9, role="text",
        ax_role="AXTextArea", x=10, y=20, editable=True, secret=False,
        multiline=True, window_title="general — Acme", terminal=False,
    )

    def test_today_focus_info(self) -> None:
        info = macfocus.to_focus_info(self.FOUND, FocusInfo)
        assert (info.app, info.role, info.x, info.y, info.editable, info.secret) == (
            "Slack", "text", 10, 20, True, False)

    def test_richer_focus_info_gets_the_extras(self) -> None:
        # The newline policy may grow FocusInfo: the bridge fills fields it
        # knows by name, whichever spelling lands.
        @dataclass(frozen=True)
        class Richer:
            app: str = ""
            role: str = ""
            x: int = -1
            y: int = -1
            editable: bool = False
            secret: bool = False
            title: str = ""
            single_line: Optional[bool] = None
            bundle_id: str = ""

        info = macfocus.to_focus_info(self.FOUND, Richer)
        assert info.title == "general — Acme" and info.single_line is False
        assert info.bundle_id == "com.tinyspeck.slackmacgap"
        assert {f.name for f in fields(Richer)} >= {"title", "single_line"}

    def test_unknown_lines_leave_the_default(self) -> None:
        @dataclass(frozen=True)
        class WithMultiline:
            app: str = ""
            multiline: Optional[bool] = None

        found = macfocus.MacFocus(app="X", multiline=None)
        assert macfocus.to_focus_info(found, WithMultiline).multiline is None


class TestFocusprobeDispatch:
    def test_darwin_uses_the_accessibility_probe(self, monkeypatch) -> None:
        monkeypatch.setattr(sys, "platform", "darwin")
        expected = FocusInfo(app="Notes", role="text", editable=True)
        with mock.patch("voice_keyboard.macos.focus.probe_focus_info", return_value=expected):
            assert focusprobe.probe_focus() is expected

    def test_darwin_falls_back_to_the_window_list(self, monkeypatch) -> None:
        # No Accessibility permission yet: the app name still comes through.
        from types import ModuleType

        monkeypatch.setattr(sys, "platform", "darwin")
        quartz = ModuleType("Quartz")
        quartz.kCGWindowListOptionOnScreenOnly = 1
        quartz.kCGWindowListExcludeDesktopElements = 16
        quartz.kCGNullWindowID = 0
        quartz.CGWindowListCopyWindowInfo = lambda *_: [
            {"kCGWindowLayer": 25, "kCGWindowOwnerName": "Control Center"},
            {"kCGWindowLayer": 0, "kCGWindowOwnerName": "iTerm2"},
        ]
        with mock.patch("voice_keyboard.macos.focus.probe_focus_info", return_value=None), \
                mock.patch.dict(sys.modules, {"Quartz": quartz}):
            info = focusprobe.probe_focus()
        assert info.app == "iTerm2"
        assert register_for_app(info.app, info.role) is TERMINAL

    def test_darwin_selection(self, monkeypatch) -> None:
        monkeypatch.setattr(sys, "platform", "darwin")
        with mock.patch("voice_keyboard.macos.focus.probe_selection", return_value=("abc", 3)) as read:
            assert focusprobe.probe_selection() == ("abc", 3)
        read.assert_called_once_with(focusprobe.MAX_SELECTION_CHARS)

    def test_probe_focus_info_never_raises(self) -> None:
        with mock.patch("voice_keyboard.macos.ax.AXBackend.create", side_effect=RuntimeError("boom")):
            assert macfocus.probe_focus_info(FocusInfo) is None
        with mock.patch("voice_keyboard.macos.ax.AXBackend.create", return_value=None):
            assert macfocus.probe_focus_info(FocusInfo) is None
            assert macfocus.probe_selection(10) is None

    def test_read_aloud_selection_uses_accessibility_first(self, monkeypatch) -> None:
        from voice_keyboard import clipboard

        monkeypatch.setattr(sys, "platform", "darwin")
        with mock.patch("voice_keyboard.macos.focus.probe_selection", return_value=("page text", 9)) as read, \
                mock.patch.object(clipboard, "get_text", return_value="old clip"):
            assert clipboard.selection_text() == "page text"
        assert read.call_args.kwargs == {"editable_only": False, "allow_terminal": True}
        with mock.patch("voice_keyboard.macos.focus.probe_selection", return_value=("", 0)), \
                mock.patch.object(clipboard, "get_text", return_value="old clip"):
            assert clipboard.selection_text() == "old clip"  # nothing selected: the clipboard
            assert clipboard.selection_text(clipboard_fallback=False) == ""
