"""Focused-app probing on all three platforms.

Answers "what app is dictation about to type into?" so the daemon can
pick a context register (terminal / prose / verbatim) and detect focus
changes mid-dictation. Also supplies the caret anchor the overlay uses.

Linux: an AT-SPI walk in a subprocess under /usr/bin/python3 (the system
interpreter has the gi/Atspi bindings; the venv usually does not).
macOS: the frontmost layer-0 window via Quartz (already a dependency).
Windows: GetForegroundWindow -> process image name via ctypes.

Every path is best-effort: a None result means "unknown", and callers
treat the probe as advisory.
"""

import json
import logging
import subprocess
import sys
from dataclasses import dataclass
from typing import Optional

logger = logging.getLogger(__name__)

PROBE_TIMEOUT_S = 1.2


@dataclass(frozen=True)
class FocusInfo:
    app: str = ""
    role: str = ""
    x: int = -1
    y: int = -1
    editable: bool = False
    # A password/secret widget: never remember what was typed, render
    # verbatim, never contribute to STT biasing.
    secret: bool = False

    @property
    def identity(self) -> str:
        """Stable-enough key for "did focus move to another app?"."""
        return self.app.strip().lower()


# The AT-SPI walk: finds the focused accessible, reports its application
# name, role, editability, and a caret/component anchor for the overlay.
ATSPI_PROBE_SCRIPT = r"""
import json

try:
    import gi
    gi.require_version("Atspi", "2.0")
    from gi.repository import Atspi
except Exception:
    raise SystemExit(1)

CoordType = Atspi.CoordType.SCREEN
Focused = Atspi.StateType.FOCUSED
Editable = Atspi.StateType.EDITABLE


def state_contains(accessible, state):
    try:
        return accessible.get_state_set().contains(state)
    except Exception:
        return False


def accessible_name(accessible):
    try:
        return accessible.get_name() or ""
    except Exception:
        return ""


def accessible_role(accessible):
    try:
        return accessible.get_role_name() or ""
    except Exception:
        return ""


def application_name(accessible):
    try:
        app = accessible.get_application()
        return app.get_name() or ""
    except Exception:
        return ""


def is_shell_chrome(accessible):
    return (
        application_name(accessible) == "gnome-shell"
        and accessible_name(accessible) == "Main stage"
        and accessible_role(accessible) == "window"
    )


def rect_tuple(rect):
    return int(rect.x), int(rect.y), int(rect.width), int(rect.height)


def usable_rect(rect):
    x, y, width, height = rect_tuple(rect)
    return width > 0 and height > 0 and x > -30000 and y > -30000


def find_focused(accessible, depth=0, max_depth=14, seen=None):
    if seen is None:
        seen = set()
    if depth > max_depth:
        return None
    ident = id(accessible)
    if ident in seen:
        return None
    seen.add(ident)

    best = (
        accessible
        if state_contains(accessible, Focused) and not is_shell_chrome(accessible)
        else None
    )
    try:
        child_count = accessible.get_child_count()
    except Exception:
        return best

    for index in range(child_count):
        try:
            child = accessible.get_child_at_index(index)
        except Exception:
            continue
        found = find_focused(child, depth + 1, max_depth, seen)
        if found is not None:
            best = found
    return best


def caret_anchor(accessible):
    try:
        offset = Atspi.Text.get_caret_offset(accessible)
    except Exception:
        return None
    for candidate in [offset, offset - 1, 0]:
        if candidate < 0:
            continue
        try:
            rect = Atspi.Text.get_character_extents(accessible, candidate, CoordType)
        except Exception:
            continue
        if usable_rect(rect):
            x, y, width, height = rect_tuple(rect)
            anchor_x = x if candidate == offset else x + width
            return {"x": anchor_x, "y": y}
    return None


def component_anchor(accessible):
    try:
        rect = Atspi.Component.get_extents(accessible, CoordType)
    except Exception:
        return None
    if not usable_rect(rect):
        return None
    x, y, width, height = rect_tuple(rect)
    return {"x": x + max(width // 2, 1), "y": y}


focused = find_focused(Atspi.get_desktop(0))
if focused is None:
    raise SystemExit(1)

role = accessible_role(focused)
anchor = caret_anchor(focused) or component_anchor(focused) or {"x": -1, "y": -1}
print(json.dumps({
    "x": anchor["x"],
    "y": anchor["y"],
    "app": application_name(focused),
    "role": role,
    "editable": state_contains(focused, Editable),
    "secret": role == "password text",
}))
"""


def _probe_linux(timeout: float) -> Optional[FocusInfo]:
    try:
        result = subprocess.run(
            ["/usr/bin/python3", "-c", ATSPI_PROBE_SCRIPT],
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
        return None
    if result.returncode != 0:
        return None
    try:
        payload = json.loads(result.stdout)
        return FocusInfo(
            app=str(payload.get("app", "")),
            role=str(payload.get("role", "")),
            x=int(payload.get("x", -1)),
            y=int(payload.get("y", -1)),
            editable=bool(payload.get("editable", False)),
            secret=bool(payload.get("secret", False)),
        )
    except (KeyError, TypeError, ValueError):
        return None


def _probe_macos() -> Optional[FocusInfo]:
    try:
        import Quartz  # pyobjc-framework-Quartz; darwin only

        options = (
            Quartz.kCGWindowListOptionOnScreenOnly
            | Quartz.kCGWindowListExcludeDesktopElements
        )
        windows = Quartz.CGWindowListCopyWindowInfo(options, Quartz.kCGNullWindowID)
        for window in windows or []:
            if window.get("kCGWindowLayer", 1) == 0:
                owner = str(window.get("kCGWindowOwnerName") or "")
                if owner:
                    return FocusInfo(app=owner)
        return None
    except Exception:
        logger.debug("macOS focus probe failed", exc_info=True)
        return None


ES_PASSWORD = 0x0020
GWL_STYLE = -16


def _probe_windows() -> Optional[FocusInfo]:
    """Foreground window -> exe basename, plus (when the app exposes a
    system caret, as Win32/WinForms/most Chromium apps do) the caret's
    screen position for the overlay, and a classic Edit control's
    ES_PASSWORD style as the secret flag."""
    try:
        import ctypes
        from ctypes import wintypes

        user32 = ctypes.WinDLL("user32", use_last_error=True)  # type: ignore[attr-defined]
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)  # type: ignore[attr-defined]
        user32.GetForegroundWindow.restype = wintypes.HWND
        user32.GetWindowThreadProcessId.argtypes = [
            wintypes.HWND, ctypes.POINTER(wintypes.DWORD)
        ]
        user32.GetWindowThreadProcessId.restype = wintypes.DWORD
        kernel32.OpenProcess.restype = wintypes.HANDLE
        kernel32.QueryFullProcessImageNameW.argtypes = [
            wintypes.HANDLE, wintypes.DWORD, wintypes.LPWSTR, ctypes.POINTER(wintypes.DWORD)
        ]
        kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
        hwnd = user32.GetForegroundWindow()
        if not hwnd:
            return None
        pid = wintypes.DWORD()
        thread_id = user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        if not pid.value:
            return None
        PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
        handle = kernel32.OpenProcess(
            PROCESS_QUERY_LIMITED_INFORMATION, False, pid.value
        )
        if not handle:
            return None
        try:
            size = wintypes.DWORD(1024)
            buffer = ctypes.create_unicode_buffer(size.value)
            if not kernel32.QueryFullProcessImageNameW(
                handle, 0, buffer, ctypes.byref(size)
            ):
                return None
            image = buffer.value
        finally:
            kernel32.CloseHandle(handle)
        basename = image.replace("/", "\\").rsplit("\\", 1)[-1]
        x, y, secret = _windows_caret_and_secret(user32, thread_id)
        return FocusInfo(
            app=basename,
            role="password text" if secret else "",
            x=x,
            y=y,
            secret=secret,
        )
    except Exception:
        logger.debug("Windows focus probe failed", exc_info=True)
        return None


def _windows_caret_and_secret(user32, thread_id: int) -> tuple[int, int, bool]:
    """(caret_x, caret_y, is_password_field) for the foreground thread;
    (-1, -1, False) when the app draws its own caret (UWP, some editors)."""
    import ctypes
    from ctypes import wintypes

    class GUITHREADINFO(ctypes.Structure):
        _fields_ = [
            ("cbSize", wintypes.DWORD),
            ("flags", wintypes.DWORD),
            ("hwndActive", wintypes.HWND),
            ("hwndFocus", wintypes.HWND),
            ("hwndCapture", wintypes.HWND),
            ("hwndMenuOwner", wintypes.HWND),
            ("hwndMoveSize", wintypes.HWND),
            ("hwndCaret", wintypes.HWND),
            ("rcCaret", wintypes.RECT),
        ]

    info = GUITHREADINFO(cbSize=ctypes.sizeof(GUITHREADINFO))
    user32.GetGUIThreadInfo.argtypes = [wintypes.DWORD, ctypes.POINTER(GUITHREADINFO)]
    user32.GetGUIThreadInfo.restype = wintypes.BOOL
    if not user32.GetGUIThreadInfo(thread_id, ctypes.byref(info)):
        return -1, -1, False

    secret = False
    if info.hwndFocus:
        name = ctypes.create_unicode_buffer(64)
        user32.GetClassNameW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
        user32.GetWindowLongW.argtypes = [wintypes.HWND, ctypes.c_int]
        user32.GetWindowLongW.restype = ctypes.c_long
        if user32.GetClassNameW(info.hwndFocus, name, 64) and "edit" in name.value.lower():
            style = user32.GetWindowLongW(info.hwndFocus, GWL_STYLE)
            secret = bool(style & ES_PASSWORD)

    if not info.hwndCaret:
        return -1, -1, secret
    point = wintypes.POINT(info.rcCaret.left, info.rcCaret.bottom)
    user32.ClientToScreen.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.POINT)]
    user32.ClientToScreen.restype = wintypes.BOOL
    if not user32.ClientToScreen(info.hwndCaret, ctypes.byref(point)):
        return -1, -1, secret
    return int(point.x), int(point.y), secret


def probe_focus(timeout: float = PROBE_TIMEOUT_S) -> Optional[FocusInfo]:
    """Best-effort probe of the currently focused app; None if unknown."""
    if sys.platform == "darwin":
        return _probe_macos()
    if sys.platform == "win32":
        return _probe_windows()
    return _probe_linux(timeout)
