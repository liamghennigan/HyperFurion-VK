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

For the line-break policy (voice_keyboard/newline.py) the probe also
reports whether the focused field takes a single line, and the window
title — on Linux also the web address (scheme, host and path; the query
and fragment never leave the probe) of the page holding the caret. They
choose between Enter, Shift+Enter and nothing for "new line", and are
never logged, stored or shown.
"""

import json
import logging
import os
import subprocess
import sys
from dataclasses import dataclass, field
from typing import Optional

logger = logging.getLogger(__name__)

PROBE_TIMEOUT_S = 1.2
# The longest selection read for "VK, make this …": a rewrite of more is a
# job for an editor, not a keyboard.
MAX_SELECTION_CHARS = 4000
# Longer window titles and web addresses are cut; what tells sites apart
# is near the start of an address and the end of a title.
MAX_TITLE_CHARS = 512
MAX_ADDRESS_CHARS = 1024


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
    # A one-line field (search box, form input, address bar): Enter there
    # submits, so a dictated line break presses nothing.
    single_line: bool = False
    # For the line-break policy only, and kept out of repr so no log line
    # can carry them: the toplevel window's title, and in a browser the
    # web address of the document holding the caret and of the tab's
    # page (scheme://host/path; they differ inside a frame).
    title: str = field(default="", repr=False)
    url: str = field(default="", repr=False)
    page_url: str = field(default="", repr=False)

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

# What a dictated line break should press here (voice_keyboard/newline.py):
# the field's line mode, the window title, and in a browser the address
# of the page. Names and addresses only — never the field's text.
from urllib.parse import urlsplit

SingleLine = getattr(Atspi.StateType, "SINGLE_LINE", None)
MultiLine = getattr(Atspi.StateType, "MULTI_LINE", None)


def has_state(accessible, state):
    return state is not None and state_contains(accessible, state)


def ancestors(accessible, limit=64):
    # (node, role) from the parent up to the application.
    chain = []
    node = accessible
    for _ in range(limit):
        try:
            node = node.get_parent()
        except Exception:
            break
        if node is None:
            break
        node_role = accessible_role(node)
        chain.append((node, node_role))
        if node_role in ("application", "desktop frame"):
            break
    return chain


def window_title(focused, chain):
    # The toplevel window is the application's child; a browser puts the
    # tab's title in its name ("Chat | Slack - Google Chrome").
    below = focused
    for node, node_role in chain:
        if node_role == "application":
            return accessible_name(below)
        below = node
    return ""


def object_attribute(accessible, name):
    try:
        attributes = accessible.get_attributes() or {}
    except Exception:
        return ""
    if isinstance(attributes, dict):
        return str(attributes.get(name, "") or "")
    for item in attributes:  # older bindings: ["name:value", ...]
        key, _, value = str(item).partition(":")
        if key == name:
            return value
    return ""


def web_address(url):
    try:
        parts = urlsplit(str(url).strip())
        host = parts.hostname or ""
    except Exception:
        return ""
    if parts.scheme.lower() not in ("http", "https") or not host:
        return ""
    return parts.scheme.lower() + "://" + host + (parts.path or "/")


def document_address(document):
    # Firefox names it DocURL, Chromium URI (Orca asks for both).
    interface = getattr(Atspi, "Document", None)
    if interface is None:
        return ""
    for name in ("DocURL", "URI"):
        for getter in ("get_document_attribute_value", "get_attribute_value"):
            try:
                value = getattr(interface, getter)(document, name)
            except Exception:
                continue
            if value:
                address = web_address(value)
                if address:
                    return address
    return ""


chain = ancestors(focused)
documents = [node for node, node_role in [(focused, role)] + chain if node_role == "document web"]
addresses = [a for a in (document_address(d) for d in documents[:6]) if a]
print(json.dumps({
    "x": anchor["x"],
    "y": anchor["y"],
    "app": application_name(focused),
    "role": role,
    "editable": state_contains(focused, Editable),
    "secret": role == "password text",
    "single_line": has_state(focused, SingleLine),
    "multi_line": has_state(focused, MultiLine),
    "tag": object_attribute(focused, "tag").strip().lower()[:32],
    "title": window_title(focused, chain)[:512],
    "url": addresses[0] if addresses else "",
    "page_url": addresses[-1] if addresses else "",
}))
"""

# On demand only — never part of the probe above, which runs at every
# recording start and must not read what is on screen. This one runs when
# the user has asked to rewrite their selection ("VK, make this shorter"
# with nothing dictated): it reads the focused widget's own selection,
# which is exactly what typing will replace. Editable widgets only, never
# a password field.
SELECTION_PROBE_SCRIPT = ATSPI_PROBE_SCRIPT[:ATSPI_PROBE_SCRIPT.index("role = accessible_role(focused)")] + r"""
result = {"selection": "", "chars": 0}
role = accessible_role(focused)
if state_contains(focused, Editable) and role != "password text":
    try:
        count = Atspi.Text.get_n_selections(focused)
    except Exception:
        count = 0
    if count >= 1:
        try:
            span = Atspi.Text.get_selection(focused, 0)
            start, end = int(span.start_offset), int(span.end_offset)
        except Exception:
            start = end = 0
        if end > start:
            result["chars"] = end - start
            if end - start <= LIMIT:
                try:
                    result["selection"] = Atspi.Text.get_text(focused, start, end) or ""
                except Exception:
                    result["chars"] = 0
print(json.dumps(result))
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
        role = str(payload.get("role", ""))
        return FocusInfo(
            app=str(payload.get("app", "")),
            role=role,
            x=int(payload.get("x", -1)),
            y=int(payload.get("y", -1)),
            editable=bool(payload.get("editable", False)),
            secret=bool(payload.get("secret", False)),
            single_line=atspi_single_line(
                role,
                single=bool(payload.get("single_line", False)),
                multi=bool(payload.get("multi_line", False)),
                tag=str(payload.get("tag", "") or ""),
            ),
            title=str(payload.get("title", "") or "")[:MAX_TITLE_CHARS],
            url=str(payload.get("url", "") or "")[:MAX_ADDRESS_CHARS],
            page_url=str(payload.get("page_url", "") or "")[:MAX_ADDRESS_CHARS],
        )
    except (AttributeError, KeyError, TypeError, ValueError):
        return None


# Roles that are one-line text fields whatever states they report.
ONE_LINE_ROLES = frozenset({"entry", "password text", "spin button"})
# A field that picks or searches (a search box with suggestions, such as
# Google's, which is a <textarea role="combobox">): Enter picks or
# submits there, even when the field reports MULTI_LINE.
PICKER_ROLES = frozenset({"combo box", "autocomplete"})


def atspi_single_line(role: str, *, single: bool, multi: bool, tag: str = "") -> bool:
    """Does the focused AT-SPI widget take only one line?

    A combo box always does. Otherwise MULTI_LINE always means no. In web
    content (the browser exposes the element's HTML tag) only an <input>
    counts: Firefox reports an ARIA textbox without aria-multiline, which
    is how many chat composers are built, as SINGLE_LINE although
    Shift+Enter breaks lines in it. Native widgets: the SINGLE_LINE
    state, or an entry role without MULTI_LINE."""
    role = (role or "").strip().lower()
    if role in PICKER_ROLES:
        return True
    if multi:
        return False
    tag = (tag or "").strip().lower()
    if tag:
        return tag == "input"
    return single or role in ONE_LINE_ROLES


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


ES_MULTILINE = 0x0004
ES_PASSWORD = 0x0020
GWL_STYLE = -16
PROCESS_QUERY_LIMITED_INFORMATION = 0x1000

_win_api = None


def _windows_api():
    """user32/kernel32 with prototypes, and the GUITHREADINFO type — built
    once: ctypes caches every POINTER() type forever, so a structure class
    defined per call leaks on every probe."""
    global _win_api
    if _win_api is not None:
        return _win_api
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

    user32 = ctypes.WinDLL("user32", use_last_error=True)  # type: ignore[attr-defined]
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)  # type: ignore[attr-defined]
    user32.GetForegroundWindow.restype = wintypes.HWND
    user32.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
    user32.GetWindowThreadProcessId.restype = wintypes.DWORD
    user32.GetGUIThreadInfo.argtypes = [wintypes.DWORD, ctypes.POINTER(GUITHREADINFO)]
    user32.GetGUIThreadInfo.restype = wintypes.BOOL
    user32.GetClassNameW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
    user32.GetWindowTextW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
    user32.GetWindowTextW.restype = ctypes.c_int
    user32.GetWindowLongW.argtypes = [wintypes.HWND, ctypes.c_int]
    user32.GetWindowLongW.restype = ctypes.c_long
    user32.ClientToScreen.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.POINT)]
    user32.ClientToScreen.restype = wintypes.BOOL
    kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    kernel32.OpenProcess.restype = wintypes.HANDLE
    kernel32.QueryFullProcessImageNameW.argtypes = [
        wintypes.HANDLE, wintypes.DWORD, wintypes.LPWSTR, ctypes.POINTER(wintypes.DWORD)
    ]
    kernel32.QueryFullProcessImageNameW.restype = wintypes.BOOL
    kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
    try:  # Windows 10 1607+
        user32.SetThreadDpiAwarenessContext.argtypes = [ctypes.c_void_p]
        user32.SetThreadDpiAwarenessContext.restype = ctypes.c_void_p
    except AttributeError:
        pass
    _win_api = (user32, kernel32, GUITHREADINFO)
    return _win_api


DPI_AWARENESS_CONTEXT_PER_MONITOR_AWARE_V2 = -4


def _probe_windows() -> Optional[FocusInfo]:
    """Foreground window -> exe basename, plus (when the app exposes a
    system caret, as Win32/WinForms/most Chromium apps do) the caret's
    screen position for the overlay, a classic Edit control's ES_PASSWORD
    and ES_MULTILINE styles as the secret and one-line flags, and the
    window title (a browser's names the tab). None while our own window is in
    front (the tray or orb menu): that is not the app being dictated to."""
    try:
        import ctypes

        user32 = _windows_api()[0]
    except Exception:
        logger.debug("Windows focus probe unavailable", exc_info=True)
        return None
    # Physical pixels whoever asks: the app is per-monitor DPI aware, but
    # the CLI is not, and its caret position is forwarded to the app.
    set_dpi = getattr(user32, "SetThreadDpiAwarenessContext", None)
    previous = None
    if set_dpi is not None:
        previous = set_dpi(ctypes.c_void_p(DPI_AWARENESS_CONTEXT_PER_MONITOR_AWARE_V2))
    try:
        return _probe_windows_foreground()
    finally:
        if previous:
            set_dpi(ctypes.c_void_p(previous))


def _probe_windows_foreground() -> Optional[FocusInfo]:
    try:
        import ctypes
        from ctypes import wintypes

        user32, kernel32, _ = _windows_api()
        hwnd = user32.GetForegroundWindow()
        if not hwnd:
            return None
        pid = wintypes.DWORD()
        thread_id = user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        if not pid.value or pid.value == os.getpid():
            return None
        handle = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid.value)
        if not handle:
            return None
        try:
            size = wintypes.DWORD(1024)
            buffer = ctypes.create_unicode_buffer(size.value)
            if not kernel32.QueryFullProcessImageNameW(handle, 0, buffer, ctypes.byref(size)):
                return None
            image = buffer.value
        finally:
            kernel32.CloseHandle(handle)
        basename = image.replace("/", "\\").rsplit("\\", 1)[-1]
        x, y, secret, single_line = _windows_focus_details(user32, thread_id)
        return FocusInfo(
            app=basename,
            role="password text" if secret else "",
            x=x,
            y=y,
            secret=secret,
            single_line=single_line,
            title=_window_title(user32, hwnd),
        )
    except Exception:
        logger.debug("Windows focus probe failed", exc_info=True)
        return None


def _window_title(user32, hwnd) -> str:
    """The window's title bar text ("Chat | Slack - Google Chrome"); a
    browser puts the tab's title there. "" when it can't be read. One
    fixed buffer size: ctypes caches an array type per length."""
    import ctypes

    try:
        buffer = ctypes.create_unicode_buffer(MAX_TITLE_CHARS + 1)
        if user32.GetWindowTextW(hwnd, buffer, MAX_TITLE_CHARS + 1) <= 0:
            return ""
        return buffer.value
    except Exception:
        return ""


def _classic_edit_class(class_name: str) -> bool:
    """Window classes whose ES_MULTILINE style bit really means "takes
    several lines": the system Edit and RichEdit controls, and the
    WinForms and VCL text boxes built on them. Any other class's low
    style bits are its own business."""
    name = class_name.strip().lower()
    return (
        name in ("edit", "tedit")
        or name.startswith("richedit")
        or name.startswith("windowsforms10.edit.")
        or name.startswith("windowsforms10.richedit")  # RichTextBox
    )


def _windows_focus_details(user32, thread_id: int) -> tuple[int, int, bool, bool]:
    """(caret_x, caret_y, is_password_field, is_single_line_field) for the
    foreground thread. The caret is (-1, -1) when the app draws its own
    (UWP, some editors); the field flags are known only for classic Edit
    controls, and False otherwise."""
    import ctypes
    from ctypes import wintypes

    guithreadinfo = _windows_api()[2]
    info = guithreadinfo(cbSize=ctypes.sizeof(guithreadinfo))
    if not user32.GetGUIThreadInfo(thread_id, ctypes.byref(info)):
        return -1, -1, False, False

    secret = single_line = False
    if info.hwndFocus:
        name = ctypes.create_unicode_buffer(64)
        if user32.GetClassNameW(info.hwndFocus, name, 64) and "edit" in name.value.lower():
            style = user32.GetWindowLongW(info.hwndFocus, GWL_STYLE)
            secret = bool(style & ES_PASSWORD)
            single_line = _classic_edit_class(name.value) and not (style & ES_MULTILINE)

    if not info.hwndCaret:
        return -1, -1, secret, single_line
    point = wintypes.POINT(info.rcCaret.left, info.rcCaret.bottom)
    if not user32.ClientToScreen(info.hwndCaret, ctypes.byref(point)):
        return -1, -1, secret, single_line
    return int(point.x), int(point.y), secret, single_line


def _windows_caret_and_secret(user32, thread_id: int) -> tuple[int, int, bool]:
    """(caret_x, caret_y, is_password_field) for the foreground thread;
    (-1, -1, False) when the app draws its own caret (UWP, some editors)."""
    return _windows_focus_details(user32, thread_id)[:3]


def probe_selection(timeout: float = PROBE_TIMEOUT_S) -> Optional[tuple[str, int]]:
    """Linux, on demand: the focused editable widget's selection and its
    length in characters — ("", n) when it is longer than
    MAX_SELECTION_CHARS, ("", 0) when there is none; None when the
    accessibility tree can't be asked."""
    try:
        result = subprocess.run(
            ["/usr/bin/python3", "-c", f"LIMIT = {MAX_SELECTION_CHARS}\n" + SELECTION_PROBE_SCRIPT],
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
        return str(payload.get("selection", "") or "")[:MAX_SELECTION_CHARS], int(payload.get("chars", 0) or 0)
    except (TypeError, ValueError):
        return None


def probe_focus(timeout: float = PROBE_TIMEOUT_S) -> Optional[FocusInfo]:
    """Best-effort probe of the currently focused app; None if unknown."""
    if sys.platform == "darwin":
        return _probe_macos()
    if sys.platform == "win32":
        return _probe_windows()
    return _probe_linux(timeout)
