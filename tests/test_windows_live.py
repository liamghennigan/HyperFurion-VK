"""Live Win32 tests — real SendInput, clipboard, keyboard hook, and shell.

These run only on Windows (the Windows CI job, or Windows Python under
Wine) and skip themselves when there is no interactive desktop to type
into. Physical key presses can't be synthesized here (our own hook
ignores injected input by design), so the hook is checked for a clean
install/uninstall; the swallow logic is covered in tests/test_windows.py.
"""

import sys
import threading
import time

import pytest

pytestmark = pytest.mark.skipif(sys.platform != "win32", reason="needs Windows")

if sys.platform == "win32":
    import ctypes
    from ctypes import wintypes

    user32 = ctypes.WinDLL("user32", use_last_error=True)
    user32.CreateWindowExW.restype = wintypes.HWND
    user32.CreateWindowExW.argtypes = [
        wintypes.DWORD, wintypes.LPCWSTR, wintypes.LPCWSTR, wintypes.DWORD,
        ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int,
        wintypes.HWND, wintypes.HMENU, wintypes.HINSTANCE, wintypes.LPVOID,
    ]
    user32.SendMessageW.argtypes = [wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
    user32.SendMessageW.restype = ctypes.c_ssize_t
    user32.SetForegroundWindow.argtypes = [wintypes.HWND]
    user32.SetFocus.argtypes = [wintypes.HWND]
    user32.GetForegroundWindow.restype = wintypes.HWND
    user32.DestroyWindow.argtypes = [wintypes.HWND]
    user32.PeekMessageW.argtypes = [
        ctypes.POINTER(wintypes.MSG), wintypes.HWND, wintypes.UINT, wintypes.UINT, wintypes.UINT
    ]

WS_VISIBLE = 0x10000000
WS_CHILD = 0x40000000
WS_OVERLAPPEDWINDOW = 0x00CF0000
ES_MULTILINE = 0x0004
ES_WANTRETURN = 0x1000
WM_SETTEXT = 0x000C
WM_GETTEXT = 0x000D
EM_SETSEL = 0x00B1
PM_REMOVE = 0x0001


class EditBox:
    """A focused multi-line EDIT control in a top-level window, with a
    message pump — what dictation types into."""

    def __init__(self):
        self.frame = user32.CreateWindowExW(
            0, "STATIC", "vk-live-test", WS_VISIBLE | WS_OVERLAPPEDWINDOW,
            100, 100, 600, 300, None, None, None, None,
        )
        self.edit = user32.CreateWindowExW(
            0, "EDIT", "", WS_VISIBLE | WS_CHILD | ES_MULTILINE | ES_WANTRETURN,
            10, 10, 560, 240, self.frame, None, None, None,
        )
        self._buffers = []
        self.focus()

    def focus(self) -> bool:
        for _ in range(20):
            user32.SetForegroundWindow(self.frame)
            user32.SetFocus(self.edit)
            self.pump(0.05)
            if user32.GetForegroundWindow() == self.frame:
                return True
        return False

    def pump(self, seconds: float) -> None:
        end = time.monotonic() + seconds
        msg = wintypes.MSG()
        while time.monotonic() < end:
            while user32.PeekMessageW(ctypes.byref(msg), None, 0, 0, PM_REMOVE):
                user32.TranslateMessage(ctypes.byref(msg))
                user32.DispatchMessageW(ctypes.byref(msg))
            time.sleep(0.01)

    def run_while_pumping(self, action, seconds: float = 2.0):
        box = {}

        def worker():
            time.sleep(0.2)
            try:
                box["result"] = action()
            except BaseException as exc:  # surfaced below
                box["error"] = exc

        thread = threading.Thread(target=worker)
        thread.start()
        deadline = time.monotonic() + seconds
        while thread.is_alive() and time.monotonic() < deadline:
            self.pump(0.05)
        self.pump(0.3)
        thread.join(timeout=1)
        if "error" in box:
            raise box["error"]
        return box.get("result")

    @property
    def text(self) -> str:
        buf = ctypes.create_unicode_buffer(4096)
        user32.SendMessageW(self.edit, WM_GETTEXT, 4096, ctypes.addressof(buf))
        return buf.value

    @text.setter
    def text(self, value: str) -> None:
        buf = ctypes.create_unicode_buffer(value)
        self._buffers.append(buf)
        user32.SendMessageW(self.edit, WM_SETTEXT, 0, ctypes.addressof(buf))

    def select_all(self) -> None:
        user32.SendMessageW(self.edit, EM_SETSEL, 0, -1)

    def close(self) -> None:
        user32.DestroyWindow(self.frame)


@pytest.fixture
def edit_box():
    box = EditBox()
    if not box.focus():
        box.close()
        pytest.skip("no interactive desktop (cannot take the foreground)")
    yield box
    box.close()


@pytest.fixture
def injector():
    from voice_keyboard.windows.injector import WinTextInjector

    inj = WinTextInjector()
    inj.start()
    yield inj
    inj.stop()


@pytest.fixture
def saved_clipboard():
    from voice_keyboard.windows import clipboard

    saved = clipboard.snapshot()
    yield clipboard
    if saved is not None:
        clipboard.restore(saved)


class TestSendInput:
    def test_unicode_enter_and_tab(self, edit_box, injector) -> None:
        edit_box.run_while_pumping(lambda: injector.type_text("héllo wörld 🎙\nnext\tline"))
        assert edit_box.text == "héllo wörld 🎙\r\nnext\tline"

    def test_no_enter_mode_and_backspace(self, edit_box, injector) -> None:
        def run():
            injector.suppress_enter = True
            try:
                injector.type_text("git status\nrm -rf /")
            finally:
                injector.suppress_enter = False
            injector.delete_chars(2)

        edit_box.run_while_pumping(run)
        assert edit_box.text == "git status rm -rf"

    def test_key_chord(self, edit_box, injector) -> None:
        edit_box.text = "select me"
        edit_box.run_while_pumping(lambda: (injector.press_combo(["ctrl", "a"]),
                                            injector.type_text("replaced")))
        assert edit_box.text == "replaced"


class TestClipboard:
    def test_round_trip_and_restore(self, saved_clipboard) -> None:
        clip = saved_clipboard
        assert clip.set_text("clipboard ünïcode ✓")
        assert clip.get_text() == "clipboard ünïcode ✓"
        snap = clip.snapshot()
        assert snap and any(fmt == 13 for fmt, _ in snap)  # CF_UNICODETEXT
        clip.set_text("temporary", transient=True)
        assert clip.get_text() == "temporary"
        assert clip.restore(snap)
        assert clip.get_text() == "clipboard ünïcode ✓"

    def test_copy_selection_restores_the_clipboard(self, edit_box, saved_clipboard) -> None:
        from voice_keyboard.windows.selection import copy_selection

        clip = saved_clipboard
        clip.set_text("what was there before")
        edit_box.text = "the selected words"
        edit_box.select_all()
        result = edit_box.run_while_pumping(
            lambda: copy_selection(is_terminal=lambda: False), seconds=3.0
        )
        assert result == "the selected words"
        assert clip.get_text() == "what was there before"


class TestHookAndShell:
    def test_hotkey_hook_installs_and_stops(self) -> None:
        from voice_keyboard.windows.hotkey import WinHotkeyListener

        listener = WinHotkeyListener(
            {"key": "control+alt+v", "mode": "toggle"},
            on_toggle=lambda: None, on_hold_start=lambda: None, on_hold_stop=lambda: None,
        )
        listener.start()
        for _ in range(100):
            if listener._thread_id is not None:
                break
            time.sleep(0.02)
        thread = listener._thread
        assert thread is not None and thread.is_alive()
        listener.stop()
        assert not thread.is_alive()

    def test_shell_lifecycle(self) -> None:
        from voice_keyboard.windows.shell import ShellCallbacks, WinShell

        shell = WinShell(ShellCallbacks(status=lambda: {"recording": True}), version="test")
        if not shell.start():
            pytest.skip("no desktop for windows")
        try:
            shell.set_button(True)
            for state in ("starting", "listening", "processing", "inserted", "empty", "error"):
                shell.show(state, detail=f"{state} ▁▃▅▇ detail", anchor=(400, 300))
                time.sleep(0.15)
            shell.show("inserted", detail="auto-hide", timeout_ms=200)
            time.sleep(0.6)
            assert not shell._ov.visible
            shell.notify("HyperFurion VK", "live test")
            shell.set_setup_mode("Add your speech provider API key to get started")
            time.sleep(0.3)
            assert not shell._orb.visible  # the orb hides while setup is needed
            shell.set_setup_mode("")
            time.sleep(0.3)
        finally:
            shell.stop()
        assert not shell._thread.is_alive()
