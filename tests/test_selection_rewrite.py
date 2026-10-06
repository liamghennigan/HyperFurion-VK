"""Rewrite the selection in any app: highlight text, say "VK, make this
shorter", and the answer is typed over it. The selection comes from the
focused widget itself (Linux accessibility) or the focused app's copy
(Windows) — never a stale PRIMARY selection — and never from a terminal
or a secret field."""

import asyncio
import json
import sys
from types import ModuleType, SimpleNamespace
from unittest import mock

import pytest
from fakes import FakeStreamingSTT, RecordingInjector, _make_daemon

from voice_keyboard import focusprobe
from voice_keyboard.focusprobe import (
    ATSPI_PROBE_SCRIPT,
    MAX_SELECTION_CHARS,
    SELECTION_PROBE_SCRIPT,
    FocusInfo,
)


# ── the probe script, run against a fake accessibility tree ───────────────

def _run_probe(text: str, start: int, end: int, *, role="text", editable=True) -> dict:
    focused_state, editable_state = object(), object()

    class Node:
        def __init__(self, children=(), focused=False):
            self.children, self.focused = list(children), focused

        def get_state_set(self):
            states = {focused_state} if self.focused else set()
            if self.focused and editable:
                states.add(editable_state)
            return SimpleNamespace(contains=lambda state: state in states)

        def get_child_count(self):
            return len(self.children)

        def get_child_at_index(self, index):
            return self.children[index]

        def get_name(self):
            return "doc"

        def get_role_name(self):
            return role if self.focused else "frame"

        def get_application(self):
            return SimpleNamespace(get_name=lambda: "gedit")

    field = Node(focused=True)
    desktop = Node([Node([field])])

    class Text:
        @staticmethod
        def get_caret_offset(acc):
            raise RuntimeError("no caret")

        @staticmethod
        def get_n_selections(acc):
            return 1 if end > start else 0

        @staticmethod
        def get_selection(acc, index):
            return SimpleNamespace(start_offset=start, end_offset=end)

        @staticmethod
        def get_text(acc, a, b):
            return text[a:b]

    atspi = SimpleNamespace(
        CoordType=SimpleNamespace(SCREEN=0),
        StateType=SimpleNamespace(FOCUSED=focused_state, EDITABLE=editable_state),
        Text=Text,
        Component=SimpleNamespace(get_extents=lambda *a: (_ for _ in ()).throw(RuntimeError())),
        get_desktop=lambda index: desktop,
    )
    gi = ModuleType("gi")
    gi.require_version = lambda *a: None
    repository = ModuleType("gi.repository")
    repository.Atspi = atspi
    gi.repository = repository
    out: list[str] = []
    with mock.patch.dict(sys.modules, {"gi": gi, "gi.repository": repository}):
        exec(
            compile(f"LIMIT = {MAX_SELECTION_CHARS}\n" + SELECTION_PROBE_SCRIPT, "<probe>", "exec"),
            {"print": out.append, "__name__": "__main__"},
        )
    return json.loads(out[0])


class TestProbe:
    def test_reads_the_focused_widgets_selection(self) -> None:
        assert _run_probe("teh quick fox jumps", 0, 13) == {"selection": "teh quick fox", "chars": 13}

    def test_no_selection(self) -> None:
        assert _run_probe("teh quick fox", 4, 4) == {"selection": "", "chars": 0}

    def test_never_in_a_password_field(self) -> None:
        assert _run_probe("hunter2", 0, 7, role="password text")["selection"] == ""

    def test_never_in_a_widget_that_cannot_be_typed_into(self) -> None:
        assert _run_probe("a web page paragraph", 0, 5, editable=False)["selection"] == ""

    def test_too_long_is_counted_not_read(self) -> None:
        n = MAX_SELECTION_CHARS + 10
        assert _run_probe("x" * n, 0, n) == {"selection": "", "chars": n}

    def test_the_always_on_probe_still_reads_no_text(self) -> None:
        assert "get_text(" not in ATSPI_PROBE_SCRIPT
        assert ATSPI_PROBE_SCRIPT.startswith(SELECTION_PROBE_SCRIPT[:200])  # same focus walk

    def test_payload_parsing(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(focusprobe.subprocess, "run", lambda *a, **k: SimpleNamespace(
            returncode=0, stdout=json.dumps({"selection": "abc", "chars": 3})))
        assert focusprobe.probe_selection(1.0) == ("abc", 3)
        monkeypatch.setattr(focusprobe.subprocess, "run", lambda *a, **k: SimpleNamespace(returncode=1, stdout=""))
        assert focusprobe.probe_selection(1.0) is None


# ── the daemon ────────────────────────────────────────────────────────────

class GuardedInjector(RecordingInjector):
    """Records whether Enter was refused while each piece was typed."""

    def __init__(self):
        super().__init__()
        self.suppress_enter = False
        self.guarded: list[bool] = []

    def type_text(self, text: str) -> None:
        self.guarded.append(self.suppress_enter)
        super().type_text(text.replace("\n", " ") if self.suppress_enter else text)


@pytest.fixture(autouse=True)
def inline(tmp_path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path))

    async def _to_thread(func, /, *args, **kwargs):
        await asyncio.sleep(0)
        return func(*args, **kwargs)

    monkeypatch.setattr(asyncio, "to_thread", _to_thread)
    monkeypatch.setattr("voice_keyboard.client._show_overlay", lambda *a, **k: None)
    monkeypatch.setattr(sys, "platform", "linux")


def _session(focus: FocusInfo, rewrite: str, *, selection=("", 0), said="vk make it correct", last_typed=""):
    injector = GuardedInjector()
    daemon = _make_daemon(
        FakeStreamingSTT([{"type": "transcript.partial", "text": said, "is_final": True}]), injector
    )
    daemon._probe_patch = mock.patch("voice_keyboard.daemon.probe_focus", return_value=focus)
    selection_probe = mock.Mock(return_value=selection)
    daemon._last_typed = last_typed
    llm = mock.Mock()
    llm.rewrite = mock.Mock(return_value=rewrite)
    overlays: list = []

    async def overlay(state, **kwargs):
        overlays.append((state, kwargs.get("detail", "")))

    daemon._show_hotkey_overlay = overlay

    async def run() -> str:
        with daemon._audio_patch, daemon._stt_patch, daemon._probe_patch, \
                mock.patch("voice_keyboard.daemon.probe_selection", selection_probe), \
                mock.patch("voice_keyboard.daemon.create_llm_client", return_value=llm):
            await daemon._start_recording()
            await asyncio.sleep(0.15)
            return await daemon._stop_recording()

    result = asyncio.run(run())
    return SimpleNamespace(result=result, injector=injector, llm=llm, overlays=overlays, probe=selection_probe)


EDITOR = dict(app="gedit", role="text", x=10, y=10, editable=True)


def test_the_selection_is_rewritten_in_place() -> None:
    run = _session(FocusInfo(**EDITOR), "The quick fox.", selection=("teh quick fox", 13))
    run.llm.rewrite.assert_called_once_with("teh quick fox", "make it correct")
    assert run.injector.screen == "The quick fox."
    assert run.injector.guarded == [True]  # a single-line selection never gains an Enter
    assert run.result == "The quick fox."


def test_a_multiline_selection_is_refused() -> None:
    # Typing its line breaks would press Enter: in a chat box, that sends.
    run = _session(FocusInfo(**EDITOR), "One.\nTwo.", selection=("one\ntwo", 7))
    run.llm.rewrite.assert_not_called()
    assert run.injector.screen == ""
    assert any(state == "error" and "multi-line" in detail for state, detail in run.overlays)


def test_windows_ignores_an_editors_whole_line_copy(monkeypatch: pytest.MonkeyPatch) -> None:
    from voice_keyboard import clipboard
    from voice_keyboard.flow.registers import PROSE

    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.setattr(clipboard, "selection_text", lambda **k: "    return x\r\n")
    daemon = _make_daemon(FakeStreamingSTT([]), RecordingInjector())
    daemon._session_focus, daemon._session_secret, daemon._session_register = FocusInfo(app="Code.exe"), False, PROSE
    assert asyncio.run(daemon._focused_selection()) == ""


def test_a_single_line_rewrite_cannot_press_enter() -> None:
    run = _session(FocusInfo(**EDITOR), "Hi\nthere", selection=("hi there", 8))
    assert "\n" not in run.injector.screen


def test_without_a_selection_the_last_dictation_is_rewritten() -> None:
    run = _session(FocusInfo(**EDITOR), "Hello, world.", last_typed="hello world")
    run.llm.rewrite.assert_called_once_with("hello world", "make it correct")


@pytest.mark.parametrize(
    "focus",
    [
        FocusInfo(app="kitty", role="terminal", editable=True),
        FocusInfo(app="firefox", role="password text", editable=True, secret=True),
    ],
    ids=["terminal", "secret"],
)
def test_never_from_these(focus) -> None:
    run = _session(focus, "rewritten", selection=("ls -la", 6))
    run.probe.assert_not_called()  # not even read
    assert "rewritten" not in run.injector.screen


def test_ordinary_dictation_never_reads_the_selection() -> None:
    run = _session(FocusInfo(**EDITOR), "x", selection=("secret plans", 12), said="hello world")
    run.probe.assert_not_called()
    assert run.injector.screen == "Hello world"


def test_too_long_says_so_and_types_nothing() -> None:
    run = _session(FocusInfo(**EDITOR), "never", selection=("", MAX_SELECTION_CHARS + 1), last_typed="earlier text")
    run.llm.rewrite.assert_not_called()
    assert run.injector.screen == ""
    assert any(state == "error" and "too long" in detail for state, detail in run.overlays)


def test_an_unchanged_rewrite_types_nothing() -> None:
    run = _session(FocusInfo(**EDITOR), "Fine as is.", selection=("Fine as is.", 11))
    assert run.injector.screen == ""


def test_windows_copies_the_focused_selection(monkeypatch: pytest.MonkeyPatch) -> None:
    from voice_keyboard import clipboard

    monkeypatch.setattr(sys, "platform", "win32")
    seen = {}

    def selection_text(**kwargs):
        seen.update(kwargs)
        return "teh fox"

    monkeypatch.setattr(clipboard, "selection_text", selection_text)
    daemon = _make_daemon(FakeStreamingSTT([]), RecordingInjector())
    daemon._session_focus = FocusInfo(app="notepad.exe")
    daemon._session_secret = False
    from voice_keyboard.flow.registers import PROSE

    daemon._session_register = PROSE
    assert asyncio.run(daemon._focused_selection()) == "teh fox"
    assert seen["clipboard_fallback"] is False  # never the clipboard standing in


def test_macos_has_no_reliable_selection(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(sys, "platform", "darwin")
    daemon = _make_daemon(FakeStreamingSTT([]), RecordingInjector())
    daemon._session_focus = FocusInfo(app="TextEdit", editable=True)
    daemon._session_secret = False
    from voice_keyboard.flow.registers import PROSE

    daemon._session_register = PROSE
    assert asyncio.run(daemon._focused_selection()) == ""
