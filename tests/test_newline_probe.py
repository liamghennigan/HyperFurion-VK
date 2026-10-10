"""The focus probe's side of the line-break policy: the field's line mode,
the window title and (Linux) the page address. The real AT-SPI probe
script runs here against a fake accessibility tree; the Windows probe
runs against fake user32/kernel32 (and, on Windows, a real Edit box)."""

import ctypes
import json
import sys
from types import ModuleType, SimpleNamespace
from unittest import mock

import pytest

from voice_keyboard import focusprobe
from voice_keyboard.focusprobe import (
    ATSPI_PROBE_SCRIPT,
    MAX_TITLE_CHARS,
    SELECTION_PROBE_SCRIPT,
    FocusInfo,
    _probe_linux,
    atspi_single_line,
)
from voice_keyboard.newline import ENTER, NONE, SHIFT_ENTER, choose_newline

FOCUSED, EDITABLE, SINGLE, MULTI = "focused", "editable", "single-line", "multi-line"


# ── a fake accessibility tree ─────────────────────────────────────────────

class Node:
    def __init__(self, role, name="", *children, states=(), attributes=None, document=None):
        self.role, self.name, self.children = role, name, list(children)
        self.parent = None
        for child in self.children:
            child.parent = self
        self.states = set(states)
        self.attributes = {} if attributes is None else attributes
        self.document = document or {}

    def get_state_set(self):
        return SimpleNamespace(contains=lambda state: state in self.states)

    def get_child_count(self):
        return len(self.children)

    def get_child_at_index(self, index):
        return self.children[index]

    def get_name(self):
        return self.name

    def get_role_name(self):
        return self.role

    def get_parent(self):
        return self.parent

    def get_application(self):
        node = self
        while node is not None and node.role != "application":
            node = node.parent
        if node is None:
            raise RuntimeError("no application")
        return node

    def get_attributes(self):
        if isinstance(self.attributes, Exception):
            raise self.attributes
        return self.attributes


def field(role="entry", *, states=(EDITABLE,), tag=None, **kw) -> Node:
    attributes = kw.pop("attributes", {"tag": tag} if tag else {})
    return Node(role, "", states=(FOCUSED, *states), attributes=attributes, **kw)


def browser(app: str, title: str, *content: Node) -> Node:
    """desktop > application > frame(title) > panel > content."""
    return Node("desktop frame", "main", Node("application", app, Node("frame", title, Node("panel", "", *content))))


def page(url: str, *children: Node, key="DocURL") -> Node:
    return Node("document web", "", *children, document={key: url})


def run_probe(desktop: Node, *, old_bindings=False, document_api="new") -> dict:
    class Document:
        pass

    def lookup(node, name):
        return node.document.get(name)

    if document_api == "new":
        Document.get_document_attribute_value = staticmethod(lookup)
    elif document_api == "old":
        Document.get_attribute_value = staticmethod(lookup)
    states = dict(FOCUSED=FOCUSED, EDITABLE=EDITABLE)
    if not old_bindings:
        states.update(SINGLE_LINE=SINGLE, MULTI_LINE=MULTI)
    atspi = SimpleNamespace(
        CoordType=SimpleNamespace(SCREEN=0),
        StateType=SimpleNamespace(**states),
        Text=SimpleNamespace(get_caret_offset=mock.Mock(side_effect=RuntimeError("no caret"))),
        Component=SimpleNamespace(get_extents=mock.Mock(side_effect=RuntimeError("no extents"))),
        get_desktop=lambda index: desktop,
    )
    if document_api is not None:
        atspi.Document = Document
    gi = ModuleType("gi")
    gi.require_version = lambda *a: None
    repository = ModuleType("gi.repository")
    repository.Atspi = atspi
    gi.repository = repository
    out: list[str] = []
    with mock.patch.dict(sys.modules, {"gi": gi, "gi.repository": repository}):
        exec(compile(ATSPI_PROBE_SCRIPT, "<probe>", "exec"), {"print": out.append, "__name__": "__main__"})
    return json.loads(out[0])


def parsed(payload: dict) -> FocusInfo:
    result = mock.Mock(returncode=0, stdout=json.dumps(payload))
    with mock.patch("voice_keyboard.focusprobe.subprocess.run", return_value=result):
        info = _probe_linux(1.0)
    assert info is not None
    return info


class TestAtspiProbeScript:
    def test_firefox_whatsapp_composer(self) -> None:
        # An ARIA textbox (a contenteditable div) that Firefox reports as
        # SINGLE_LINE: still a place where Shift+Enter breaks lines.
        composer = field("entry", states=(EDITABLE, SINGLE), tag="DIV")
        desktop = browser("Firefox", "WhatsApp — Mozilla Firefox",
                          page("https://web.whatsapp.com/?token=s3cret#chat", Node("section", "", composer)))
        payload = run_probe(desktop)
        assert payload["app"] == "Firefox" and payload["title"] == "WhatsApp — Mozilla Firefox"
        # Query and fragment never leave the probe.
        assert payload["url"] == payload["page_url"] == "https://web.whatsapp.com/"
        assert (payload["single_line"], payload["multi_line"], payload["tag"]) == (True, False, "div")
        info = parsed(payload)
        assert info.single_line is False
        assert choose_newline(info).key == SHIFT_ENTER

    def test_chromium_google_docs_in_its_editing_frame(self) -> None:
        # Docs types into a contenteditable inside an about:blank frame;
        # Chromium names the address "URI". The frame is skipped.
        editor = field("document web", states=(EDITABLE, MULTI), tag="body",
                       document={"URI": "about:blank"})
        desktop = browser("Google Chrome", "Plan - Google Docs - Google Chrome",
                          page("https://docs.google.com/document/d/1/edit?usp=sharing",
                               Node("internal frame", "", editor), key="URI"))
        payload = run_probe(desktop)
        assert payload["url"] == payload["page_url"] == "https://docs.google.com/document/d/1/edit"
        info = parsed(payload)
        assert choose_newline(info).key == ENTER

    def test_a_frame_with_its_own_web_address(self) -> None:
        box = field("entry", states=(EDITABLE, MULTI), tag="div")
        desktop = browser("Google Chrome", "Plan | Notion - Google Chrome",
                          page("https://www.notion.so/Plan",
                               Node("internal frame", "", page("https://widget.intercom.io/frame", box))))
        payload = run_probe(desktop)
        assert payload["url"] == "https://widget.intercom.io/frame"
        assert payload["page_url"] == "https://www.notion.so/Plan"
        assert choose_newline(parsed(payload)).key == SHIFT_ENTER  # not the document

    def test_a_web_form_input_is_one_line(self) -> None:
        search = field("entry", states=(EDITABLE, SINGLE), tag="input")
        payload = run_probe(browser("Google Chrome", "Search - Google Chrome", page("https://example.com/", search)))
        info = parsed(payload)
        assert info.single_line is True and choose_newline(info).key == NONE

    def test_native_fields(self) -> None:
        entry = run_probe(browser("gedit", "Find", field("text", states=(EDITABLE, SINGLE))))
        view = run_probe(browser("gedit", "notes.txt - gedit", field("text", states=(EDITABLE, MULTI))))
        assert parsed(entry).single_line is True and parsed(view).single_line is False
        assert entry["url"] == "" and view["title"] == "notes.txt - gedit"
        assert choose_newline(parsed(view)).key == ENTER

    def test_older_bindings_and_missing_interfaces_degrade_quietly(self) -> None:
        box = field("text", states=(EDITABLE,), attributes=RuntimeError("no attributes"))
        desktop = browser("Firefox", "Chat — Mozilla Firefox", page("https://example.com/", box))
        payload = run_probe(desktop, old_bindings=True, document_api=None)
        assert (payload["single_line"], payload["multi_line"], payload["tag"], payload["url"]) == (False, False, "", "")
        assert payload["title"] == "Chat — Mozilla Firefox"

    def test_the_older_document_call_and_list_attributes(self) -> None:
        box = field("entry", states=(EDITABLE, SINGLE), attributes=["xml-roles:textbox", "tag:input"])
        payload = run_probe(browser("Firefox", "Login — Mozilla Firefox", page("https://example.com/login", box)),
                            document_api="old")
        assert payload["url"] == "https://example.com/login" and payload["tag"] == "input"

    def test_a_tree_without_an_application_has_no_title(self) -> None:
        box = field("text", states=(EDITABLE, MULTI))
        payload = run_probe(Node("desktop frame", "main", Node("frame", "Orphan", box)))
        assert payload["title"] == ""

    def test_the_selection_probe_is_unchanged_and_nothing_reads_text(self) -> None:
        assert "get_text(" not in ATSPI_PROBE_SCRIPT
        assert "DocURL" not in SELECTION_PROBE_SCRIPT and "window_title" not in SELECTION_PROBE_SCRIPT


class TestParsing:
    @pytest.mark.parametrize("role, single, multi, tag, expected", [
        ("text", True, False, "", True),
        ("text", False, True, "", False),
        ("text", True, True, "", False),       # multi-line wins
        ("entry", False, False, "", True),     # an entry role without MULTI_LINE
        ("spin button", False, False, "", True),
        ("text", False, False, "", False),     # nothing known: not one-line
        ("entry", True, False, "input", True),
        ("entry", True, False, "div", False),  # an ARIA textbox
        ("entry", False, False, "textarea", False),
        ("section", True, False, "p", False),
    ])
    def test_single_line(self, role, single, multi, tag, expected) -> None:
        assert atspi_single_line(role, single=single, multi=multi, tag=tag) is expected

    def test_an_old_payload_still_parses(self) -> None:
        info = parsed({"x": 1, "y": 2, "app": "gedit", "role": "text", "editable": True})
        assert (info.single_line, info.title, info.url, info.page_url) == (False, "", "", "")

    def test_long_titles_are_cut(self) -> None:
        assert len(parsed({"app": "firefox", "title": "x" * 5000}).title) == MAX_TITLE_CHARS

    def test_titles_and_addresses_stay_out_of_logs(self) -> None:
        info = FocusInfo(app="firefox", title="Diagnosis results", url="https://clinic.example/r/1",
                         page_url="https://clinic.example/r/1")
        assert "Diagnosis" not in repr(info) and "clinic" not in repr(info)
        assert info.identity == "firefox"  # a tab switch is not a focus change


# ── Windows: fake user32 / kernel32 ───────────────────────────────────────

def _guithreadinfo_type():
    from ctypes import wintypes

    class GUITHREADINFO(ctypes.Structure):
        _fields_ = [
            ("cbSize", wintypes.DWORD), ("flags", wintypes.DWORD),
            ("hwndActive", wintypes.HWND), ("hwndFocus", wintypes.HWND),
            ("hwndCapture", wintypes.HWND), ("hwndMenuOwner", wintypes.HWND),
            ("hwndMoveSize", wintypes.HWND), ("hwndCaret", wintypes.HWND),
            ("rcCaret", wintypes.RECT),
        ]

    return GUITHREADINFO


class FakeUser32:
    def __init__(self, *, class_name="Edit", style=0, title="WhatsApp - Google Chrome", focus=7):
        self.class_name, self.style, self.title, self.focus = class_name, style, title, focus

    def GetForegroundWindow(self):
        return 100

    def GetWindowThreadProcessId(self, hwnd, pid_ref):
        pid_ref._obj.value = 4242
        return 77

    def GetGUIThreadInfo(self, thread_id, info_ref):
        info_ref._obj.hwndFocus = self.focus
        info_ref._obj.hwndCaret = None
        return True

    def GetClassNameW(self, hwnd, buffer, size):
        buffer.value = self.class_name
        return len(self.class_name)

    def GetWindowLongW(self, hwnd, index):
        assert index == focusprobe.GWL_STYLE
        return self.style

    def GetWindowTextW(self, hwnd, buffer, size):
        assert hwnd == 100 and size == MAX_TITLE_CHARS + 1
        buffer.value = self.title[: size - 1]
        return len(buffer.value)


class FakeKernel32:
    def OpenProcess(self, access, inherit, pid):
        return 5

    def QueryFullProcessImageNameW(self, handle, flags, buffer, size_ref):
        buffer.value = "C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe"
        return True

    def CloseHandle(self, handle):
        return True


@pytest.fixture
def windows_probe(monkeypatch: pytest.MonkeyPatch):
    def probe(**user32_kw) -> FocusInfo:
        monkeypatch.setattr(focusprobe, "_win_api", (FakeUser32(**user32_kw), FakeKernel32(), _guithreadinfo_type()))
        info = focusprobe._probe_windows_foreground()
        assert info is not None
        return info

    return probe


class TestWindowsProbe:
    def test_a_browser_window_reports_its_title(self, windows_probe) -> None:
        info = windows_probe(class_name="Chrome_RenderWidgetHostHWND")
        assert info.app == "chrome.exe" and info.title == "WhatsApp - Google Chrome"
        assert info.single_line is False
        assert choose_newline(info).key == SHIFT_ENTER

    @pytest.mark.parametrize("class_name, style, single", [
        ("Edit", 0, True),
        ("Edit", focusprobe.ES_MULTILINE, False),
        ("RICHEDIT50W", 0, True),
        ("RichEditD2DPT", focusprobe.ES_MULTILINE, False),
        ("WindowsForms10.EDIT.app.0.141b42a_r6_ad1", 0, True),
        ("TEdit", 0, True),
        ("MyEditorView", 0, False),  # not an Edit control: its style bits mean something else
        ("Chrome_RenderWidgetHostHWND", 0, False),
    ])
    def test_classic_edit_line_mode(self, windows_probe, class_name, style, single) -> None:
        assert windows_probe(class_name=class_name, style=style).single_line is single

    def test_a_password_edit_is_secret_and_one_line(self, windows_probe) -> None:
        info = windows_probe(class_name="Edit", style=focusprobe.ES_PASSWORD)
        assert info.secret and info.single_line and info.role == "password text"

    def test_long_titles_are_cut_and_no_focus_is_fine(self, windows_probe) -> None:
        info = windows_probe(title="t" * 2000, focus=None)
        assert info.title == "t" * MAX_TITLE_CHARS and info.single_line is False

    def test_the_old_helper_keeps_its_shape(self, monkeypatch: pytest.MonkeyPatch) -> None:
        user32 = FakeUser32(class_name="Edit", style=focusprobe.ES_PASSWORD)
        monkeypatch.setattr(focusprobe, "_win_api", (user32, FakeKernel32(), _guithreadinfo_type()))
        assert focusprobe._windows_caret_and_secret(user32, 77) == (-1, -1, True)


# ── Windows, live: a real Edit control ────────────────────────────────────

@pytest.fixture
def edit_box():
    from test_windows_live import EditBox, no_desktop

    box = EditBox()
    if not box.focus():
        box.close()
        no_desktop("no interactive desktop (cannot take the foreground)")
    yield box
    box.close()


@pytest.mark.skipif(sys.platform != "win32", reason="needs Windows")
class TestWindowsProbeLive:
    def test_edit_line_mode_and_window_title(self, edit_box) -> None:
        import test_windows_live as live

        api = focusprobe._windows_api()[0]
        thread_id = live.user32.GetWindowThreadProcessId(edit_box.frame, None)
        assert focusprobe._windows_focus_details(api, thread_id)[3] is False  # the multi-line EDIT
        one_line = live.user32.CreateWindowExW(
            0, "EDIT", "", live.WS_VISIBLE | live.WS_CHILD, 10, 255, 200, 24, edit_box.frame, None, None, None,
        )
        try:
            live.user32.SetFocus(one_line)
            edit_box.pump(0.1)
            assert focusprobe._windows_focus_details(api, thread_id)[3] is True
        finally:
            live.user32.DestroyWindow(one_line)
        assert focusprobe._window_title(api, edit_box.frame) == "vk-live-test"
