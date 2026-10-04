"""Live Win32 tests — real SendInput, clipboard, keyboard hook, and shell.

These run only on Windows (the Windows CI job, or Windows Python under
Wine) and skip themselves when there is no interactive desktop to type
into — unless HFVK_REQUIRE_DESKTOP=1 (CI sets it), where a missing
desktop is a failure: a crash must never pass as a skip. Physical key
presses can't be synthesized here (our own hook ignores injected input by
design), so the hook is checked for a clean install/uninstall; the
swallow logic is covered in tests/test_windows.py.
"""

import os
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
    user32.GetWindowLongW.argtypes = [wintypes.HWND, ctypes.c_int]
    user32.GetWindowRect.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.RECT)]
    user32.PostMessageW.argtypes = [wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
    user32.IsWindow.argtypes = [wintypes.HWND]
    user32.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.c_void_p]
    user32.WindowFromPoint.argtypes = [wintypes.POINT]
    user32.WindowFromPoint.restype = wintypes.HWND

REQUIRE_DESKTOP = os.environ.get("HFVK_REQUIRE_DESKTOP") == "1"


def no_desktop(reason: str) -> None:
    if REQUIRE_DESKTOP:
        pytest.fail(f"{reason} (HFVK_REQUIRE_DESKTOP=1)")
    pytest.skip(reason)


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
        no_desktop("no interactive desktop (cannot take the foreground)")
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

    def test_key_chords(self, edit_box, injector) -> None:
        # Ctrl+End, then Shift+Home selects the line (keys every EDIT control
        # has always handled; multi-line EDIT has no Ctrl+A of its own).
        edit_box.text = "select me"
        edit_box.run_while_pumping(lambda: (injector.press_combo(["ctrl", "end"]),
                                            injector.press_combo(["shift", "home"]),
                                            injector.type_text("replaced")))
        assert edit_box.text == "replaced"


class TestClipboard:
    def test_round_trip_and_restore(self, saved_clipboard) -> None:
        clip = saved_clipboard
        assert clip.set_text("clipboard ünïcode ✓")
        assert clip.get_text() == "clipboard ünïcode ✓"
        snap = clip.snapshot()
        assert snap is not None and snap.complete
        assert any(fmt == 13 for fmt, _ in snap.formats)  # CF_UNICODETEXT
        clip.set_text("temporary", transient=True)
        assert clip.get_text() == "temporary"
        assert clip.restore(snap)
        assert clip.get_text() == "clipboard ünïcode ✓"

    def test_metafile_pictures_survive_a_restore(self, saved_clipboard) -> None:
        # Office copies pictures as enhanced metafiles: GDI handles, not
        # memory — they must be saved by value or a restore drops them.
        clip = saved_clipboard
        gdi32 = ctypes.WinDLL("gdi32")
        gdi32.CreateEnhMetaFileW.restype = ctypes.c_void_p
        gdi32.CreateEnhMetaFileW.argtypes = [ctypes.c_void_p] * 4
        gdi32.CloseEnhMetaFile.restype = ctypes.c_void_p
        gdi32.CloseEnhMetaFile.argtypes = [ctypes.c_void_p]
        gdi32.Rectangle.argtypes = [ctypes.c_void_p] + [ctypes.c_int] * 4
        dc = gdi32.CreateEnhMetaFileW(None, None, None, None)
        gdi32.Rectangle(dc, 10, 10, 200, 120)
        metafile = gdi32.CloseEnhMetaFile(dc)
        user32_ = clip._load()[0]
        with clip._opened() as ok:
            assert ok
            user32_.EmptyClipboard()
            assert user32_.SetClipboardData(14, metafile)  # CF_ENHMETAFILE
            assert clip._put(13, "caption".encode("utf-16-le") + b"\x00\x00")
        snap = clip.snapshot()
        assert snap is not None and snap.complete
        bits = dict(snap.formats)[14]
        assert len(bits) > 0
        clip.set_text("something else")
        assert clip.restore(snap)
        again = clip.snapshot()
        assert dict(again.formats)[14] == bits
        assert clip.get_text() == "caption"

    def test_snapshot_too_large_is_incomplete(self, saved_clipboard, monkeypatch) -> None:
        clip = saved_clipboard
        clip.set_text("x" * 5000)
        monkeypatch.setattr(clip, "SNAPSHOT_LIMIT_BYTES", 1000)
        snap = clip.snapshot()
        assert snap is not None and not snap.complete

    def test_password_manager_markers(self, saved_clipboard) -> None:
        clip = saved_clipboard
        clip.set_text("ordinary")
        assert not clip.is_sensitive()
        user32_ = clip._load()[0]
        marker = user32_.RegisterClipboardFormatW("ExcludeClipboardContentFromMonitorProcessing")
        with clip._opened() as ok:
            assert ok
            user32_.EmptyClipboard()
            clip._put(13, "hunter2".encode("utf-16-le") + b"\x00\x00")
            clip._put(marker, b"\x00")
        assert clip.is_sensitive()
        # A restore keeps the marker (password managers' auto-clear relies on
        # it) and only adds the history/cloud opt-outs.
        snap = clip.snapshot()
        clip.set_text("other")
        clip.restore(snap)
        assert clip.is_sensitive()

    def test_copy_selection_restores_the_clipboard(self, edit_box, saved_clipboard) -> None:
        from voice_keyboard.windows.selection import copy_selection

        clip = saved_clipboard
        clip.set_text("what was there before")
        edit_box.text = "the selected words"
        edit_box.select_all()
        result = edit_box.run_while_pumping(
            lambda: copy_selection(should_skip=lambda: False), seconds=3.0
        )
        assert result == "the selected words"
        assert clip.get_text() == "what was there before"


class TestSession:
    def test_the_ipc_token_is_named_for_this_windows_session(self) -> None:
        # Never stubbed here: a broken lookup would quietly share one token
        # file between sessions.
        from voice_keyboard import ipc

        session = ipc._session_id()
        assert isinstance(session, int)
        assert ipc._token_path().name == f"ipc-token-{session}"


class TestFocusProbe:
    def test_our_own_window_is_not_the_app_being_dictated_to(self, edit_box) -> None:
        # The tray/orb menu brings our window to the front; the focus
        # watchdog must not read that as "the user switched apps".
        from voice_keyboard.focusprobe import probe_focus

        assert probe_focus() is None

    def test_probing_does_not_grow_ctypes_caches(self, edit_box) -> None:
        from voice_keyboard import focusprobe

        cache = getattr(ctypes, "_pointer_type_cache", None)
        if cache is None:
            pytest.skip("no ctypes pointer cache on this Python")
        thread_id = user32.GetWindowThreadProcessId(edit_box.frame, None)
        focusprobe._windows_caret_and_secret(focusprobe._windows_api()[0], thread_id)
        before = len(cache)
        for _ in range(50):
            focusprobe._windows_caret_and_secret(focusprobe._windows_api()[0], thread_id)
        assert len(cache) == before


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
        assert thread is not None
        time.sleep(0.2)
        if not thread.is_alive():
            # SetWindowsHookEx refused (a non-interactive service session).
            no_desktop("low-level keyboard hooks unavailable in this session")
        listener.stop()
        assert not thread.is_alive()

    def test_shell_lifecycle(self) -> None:
        from voice_keyboard.windows.shell import ShellCallbacks, WinShell

        closed = threading.Event()
        shell = WinShell(
            ShellCallbacks(status=lambda: {"recording": True}, quit=closed.set), version="test"
        )
        if not shell.start():
            no_desktop("no desktop for windows")
        try:
            shell.set_button(True)
            time.sleep(0.3)
            # The glow is click-through; only the disc-sized window takes clicks.
            user32.GetWindowLongW.restype = ctypes.c_long
            glow_style = user32.GetWindowLongW(shell._orb_glow_hwnd, -20)  # GWL_EXSTYLE
            assert glow_style & 0x20  # WS_EX_TRANSPARENT
            assert not user32.GetWindowLongW(shell._orb_hwnd, -20) & 0x20
            hit, glow = wintypes.RECT(), wintypes.RECT()
            user32.GetWindowRect(shell._orb_hwnd, ctypes.byref(hit))
            user32.GetWindowRect(shell._orb_glow_hwnd, ctypes.byref(glow))
            assert hit.right - hit.left < glow.right - glow.left
            # Clicks: the disc takes them, the halo around it doesn't.
            cx, cy = (hit.left + hit.right) // 2, (hit.top + hit.bottom) // 2
            assert user32.WindowFromPoint(wintypes.POINT(cx, cy)) == shell._orb_hwnd
            halo = wintypes.POINT(glow.left + 3, cy)  # inside the glow, outside the disc
            assert user32.WindowFromPoint(halo) not in (shell._orb_hwnd, shell._orb_glow_hwnd)
            # Alt+F4 never quits the app...
            user32.PostMessageW(shell._hwnd, 0x0112, 0xF060, 0)  # WM_SYSCOMMAND, SC_CLOSE
            time.sleep(0.3)
            assert not closed.is_set()
            # ...a taskkill (without /F) asks it to quit properly.
            user32.PostMessageW(shell._hwnd, 0x0010, 0, 0)  # WM_CLOSE
            assert closed.wait(2.0)
            assert user32.IsWindow(shell._hwnd)
            for state in ("starting", "listening", "processing", "inserted", "empty", "error"):
                shell.show(state, detail=f"{state} ▁▃▅▇ detail", anchor=(400, 300))
                time.sleep(0.15)
            shell.show("inserted", detail="auto-hide", timeout_ms=200)
            time.sleep(0.6)
            assert not shell._ov.visible
            # A timed pill turned persistent: the old timer must not hide it.
            shell.show("processing", detail="timed", timeout_ms=250)
            shell.show("processing", detail="now persistent")
            time.sleep(0.6)
            assert shell._ov.visible
            shell.hide()
            time.sleep(0.2)
            # Emoji are two UTF-16 units: measured (and drawn) in full.
            gdi32 = ctypes.WinDLL("gdi32")
            gdi32.CreateCompatibleDC.restype = wintypes.HDC
            gdi32.CreateCompatibleDC.argtypes = [wintypes.HDC]
            gdi32.DeleteDC.argtypes = [wintypes.HDC]
            dc = gdi32.CreateCompatibleDC(None)
            try:
                font = shell._font(96, 8.5, False)
                plain = shell._text_size(dc, font, "ab")[0]
                assert shell._text_size(dc, font, "ab\U0001F399\U0001F399")[0] > plain
            finally:
                gdi32.DeleteDC(dc)
            shell.notify("HyperFurion VK", "live test")
            shell.set_setup_mode("Add your speech provider API key to get started")
            time.sleep(0.3)
            assert not shell._orb.visible  # the orb hides while setup is needed
            shell.set_setup_mode("")
            time.sleep(0.3)
        finally:
            shell.stop()
        assert not shell._thread.is_alive()
