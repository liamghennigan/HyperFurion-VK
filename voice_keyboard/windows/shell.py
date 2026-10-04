"""The daemon's face on Windows: overlay pill, Kai orb, and tray icon.

The GNOME build draws these in a shell extension; Windows gets the same
instrument natively, in-process, with nothing but ctypes:

- the overlay pill — STARTING / LISTENING (with the live caption and
  level meter) / PROCESSING / INSERTED / NO SIGNAL / ERROR — drawn as a
  per-pixel-alpha layered window near the caret. It never takes focus and
  is click-through, so it can't disturb the app you are typing into;
- the Kai orb — a small always-on-top disc; click to summon Kai, drag to
  move. Clicking it does not steal focus (WS_EX_NOACTIVATE), so Kai still
  knows which app you were in. Its glow is a separate click-through
  window: only the disc itself takes clicks;
- a notification-area icon whose colour tracks the daemon (cyan idle, red
  recording, amber setup needed), with a menu for everything else.

All windows live on one UI thread; other threads talk to it through a
command queue plus a posted wake-up message, so every call here is
thread-safe and non-blocking.
"""

import ctypes
import json
import logging
import os
import subprocess
import threading
import time
from collections import deque
from ctypes import wintypes
from dataclasses import dataclass, field
from typing import Callable, Optional

from voice_keyboard.windows import render

logger = logging.getLogger(__name__)

# ------------------------------------------------------------- Win32 bits

WM_DESTROY = 0x0002
WM_CLOSE = 0x0010
WM_SETCURSOR = 0x0020
WM_MOUSEACTIVATE = 0x0021
WM_COMMAND = 0x0111
WM_TIMER = 0x0113
WM_MOUSEMOVE = 0x0200
WM_LBUTTONDOWN = 0x0201
WM_LBUTTONUP = 0x0202
WM_RBUTTONUP = 0x0205
WM_MOUSELEAVE = 0x02A3
WM_CONTEXTMENU = 0x007B
WM_SYSCOMMAND = 0x0112
SC_CLOSE = 0xF060
DWMWA_CLOAKED = 14
WM_NULL = 0x0000
WM_DPICHANGED = 0x02E0
WM_DISPLAYCHANGE = 0x007E
WM_APP = 0x8000
WM_APP_COMMAND = WM_APP + 1
WM_APP_TRAY = WM_APP + 2

WS_POPUP = 0x80000000
WS_EX_TOPMOST = 0x00000008
WS_EX_TRANSPARENT = 0x00000020
WS_EX_TOOLWINDOW = 0x00000080
WS_EX_LAYERED = 0x00080000
WS_EX_NOACTIVATE = 0x08000000

SW_HIDE = 0
SW_SHOWNOACTIVATE = 4
SWP_NOSIZE = 0x0001
SWP_NOMOVE = 0x0002
SWP_NOACTIVATE = 0x0010
SWP_SHOWWINDOW = 0x0040
HWND_TOPMOST = -1
MA_NOACTIVATE = 3
MSGFLT_ALLOW = 1
ULW_ALPHA = 0x00000002
AC_SRC_OVER = 0x00
AC_SRC_ALPHA = 0x01
DIB_RGB_COLORS = 0
BI_RGB = 0
TRANSPARENT = 1
FW_NORMAL = 400
FW_BOLD = 700
ANTIALIASED_QUALITY = 4  # grayscale: no ClearType fringes on the glass
DT_LEFT = 0x0000
DT_SINGLELINE = 0x0020
DT_NOPREFIX = 0x0800
DT_END_ELLIPSIS = 0x8000
TME_LEAVE = 0x00000002
IDC_HAND = 32649
IDC_ARROW = 32512
MONITOR_DEFAULTTONEAREST = 2
MONITOR_DEFAULTTOPRIMARY = 1
SM_CXSMICON = 49

NIM_ADD = 0
NIM_MODIFY = 1
NIM_DELETE = 2
NIM_SETVERSION = 4
NIF_MESSAGE = 0x01
NIF_ICON = 0x02
NIF_TIP = 0x04
NIF_INFO = 0x10
NIF_SHOWTIP = 0x80
NIIF_INFO = 0x01
NIIF_ERROR = 0x03
NIIF_NOSOUND = 0x10
NOTIFYICON_VERSION_4 = 4
NIN_SELECT = 0x0400  # WM_USER + 0
NIN_KEYSELECT = 0x0401

MF_STRING = 0x0000
MF_GRAYED = 0x0001
MF_CHECKED = 0x0008
MF_SEPARATOR = 0x0800
TPM_RIGHTBUTTON = 0x0002
TPM_RETURNCMD = 0x0100
TPM_NONOTIFY = 0x0080

TIMER_POLL = 1
TIMER_ANIM = 2
TIMER_HIDE = 3
TIMER_DEFERRED = 4
POLL_MS = 300
ANIM_MS = 130

ORB_SIZE = 46
OVERLAY_MIN_W = 250
OVERLAY_MAX_W = 560
# A LISTENING pill that outlives the session it describes (a dropped
# update) is cleared once the daemon has been idle this long.
STALE_LISTENING_S = 2.5

# Shell windows and the taskbar never count as "the app you were in".
_SHELL_CLASSES = {
    "shell_traywnd", "notifyiconoverflowwindow", "toplevelwindowforoverflowxamlisland",
    "shell_secondarytraywnd", "progman", "workerw", "xamlexplorerhostislandwindow",
}

LRESULT = ctypes.c_ssize_t
WNDPROC = getattr(ctypes, "WINFUNCTYPE", ctypes.CFUNCTYPE)(
    LRESULT, wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM
)


class WNDCLASSEXW(ctypes.Structure):
    _fields_ = [
        ("cbSize", wintypes.UINT),
        ("style", wintypes.UINT),
        ("lpfnWndProc", WNDPROC),
        ("cbClsExtra", ctypes.c_int),
        ("cbWndExtra", ctypes.c_int),
        ("hInstance", wintypes.HINSTANCE),
        ("hIcon", wintypes.HICON),
        ("hCursor", wintypes.HANDLE),
        ("hbrBackground", wintypes.HBRUSH),
        ("lpszMenuName", wintypes.LPCWSTR),
        ("lpszClassName", wintypes.LPCWSTR),
        ("hIconSm", wintypes.HICON),
    ]


class GUID(ctypes.Structure):
    _fields_ = [
        ("Data1", wintypes.DWORD),
        ("Data2", wintypes.WORD),
        ("Data3", wintypes.WORD),
        ("Data4", ctypes.c_ubyte * 8),
    ]


class NOTIFYICONDATAW(ctypes.Structure):
    _fields_ = [
        ("cbSize", wintypes.DWORD),
        ("hWnd", wintypes.HWND),
        ("uID", wintypes.UINT),
        ("uFlags", wintypes.UINT),
        ("uCallbackMessage", wintypes.UINT),
        ("hIcon", wintypes.HICON),
        ("szTip", wintypes.WCHAR * 128),
        ("dwState", wintypes.DWORD),
        ("dwStateMask", wintypes.DWORD),
        ("szInfo", wintypes.WCHAR * 256),
        ("uVersion", wintypes.UINT),
        ("szInfoTitle", wintypes.WCHAR * 64),
        ("dwInfoFlags", wintypes.DWORD),
        ("guidItem", GUID),
        ("hBalloonIcon", wintypes.HICON),
    ]


class BITMAPINFOHEADER(ctypes.Structure):
    _fields_ = [
        ("biSize", wintypes.DWORD),
        ("biWidth", wintypes.LONG),
        ("biHeight", wintypes.LONG),
        ("biPlanes", wintypes.WORD),
        ("biBitCount", wintypes.WORD),
        ("biCompression", wintypes.DWORD),
        ("biSizeImage", wintypes.DWORD),
        ("biXPelsPerMeter", wintypes.LONG),
        ("biYPelsPerMeter", wintypes.LONG),
        ("biClrUsed", wintypes.DWORD),
        ("biClrImportant", wintypes.DWORD),
    ]


class BLENDFUNCTION(ctypes.Structure):
    _fields_ = [
        ("BlendOp", ctypes.c_ubyte),
        ("BlendFlags", ctypes.c_ubyte),
        ("SourceConstantAlpha", ctypes.c_ubyte),
        ("AlphaFormat", ctypes.c_ubyte),
    ]


class ICONINFO(ctypes.Structure):
    _fields_ = [
        ("fIcon", wintypes.BOOL),
        ("xHotspot", wintypes.DWORD),
        ("yHotspot", wintypes.DWORD),
        ("hbmMask", wintypes.HBITMAP),
        ("hbmColor", wintypes.HBITMAP),
    ]


class MONITORINFO(ctypes.Structure):
    _fields_ = [
        ("cbSize", wintypes.DWORD),
        ("rcMonitor", wintypes.RECT),
        ("rcWork", wintypes.RECT),
        ("dwFlags", wintypes.DWORD),
    ]


class TRACKMOUSEEVENT(ctypes.Structure):
    _fields_ = [
        ("cbSize", wintypes.DWORD),
        ("dwFlags", wintypes.DWORD),
        ("hwndTrack", wintypes.HWND),
        ("dwHoverTime", wintypes.DWORD),
    ]


_api = None


def _load_api():  # pragma: no cover - requires Windows
    global _api
    if _api is not None:
        return _api
    user32 = ctypes.WinDLL("user32", use_last_error=True)  # type: ignore[attr-defined]
    gdi32 = ctypes.WinDLL("gdi32", use_last_error=True)  # type: ignore[attr-defined]
    shell32 = ctypes.WinDLL("shell32", use_last_error=True)  # type: ignore[attr-defined]
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)  # type: ignore[attr-defined]

    def proto(fn, restype, *argtypes):
        fn.restype = restype
        fn.argtypes = list(argtypes)

    H, U, W, L = wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM
    HDC, HGDI = wintypes.HDC, wintypes.HGDIOBJ
    proto(user32.DefWindowProcW, LRESULT, H, U, W, L)
    proto(user32.RegisterClassExW, wintypes.ATOM, ctypes.POINTER(WNDCLASSEXW))
    proto(user32.CreateWindowExW, H, wintypes.DWORD, wintypes.LPCWSTR, wintypes.LPCWSTR,
          wintypes.DWORD, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int,
          H, wintypes.HMENU, wintypes.HINSTANCE, wintypes.LPVOID)
    proto(user32.DestroyWindow, wintypes.BOOL, H)
    proto(user32.ShowWindow, wintypes.BOOL, H, ctypes.c_int)
    proto(user32.SetWindowPos, wintypes.BOOL, H, H, ctypes.c_int, ctypes.c_int,
          ctypes.c_int, ctypes.c_int, U)
    proto(user32.PostMessageW, wintypes.BOOL, H, U, W, L)
    proto(user32.GetMessageW, wintypes.BOOL, ctypes.POINTER(wintypes.MSG), H, U, U)
    proto(user32.TranslateMessage, wintypes.BOOL, ctypes.POINTER(wintypes.MSG))
    proto(user32.DispatchMessageW, LRESULT, ctypes.POINTER(wintypes.MSG))
    proto(user32.PostQuitMessage, None, ctypes.c_int)
    proto(user32.SetTimer, ctypes.c_size_t, H, ctypes.c_size_t, U, ctypes.c_void_p)
    proto(user32.KillTimer, wintypes.BOOL, H, ctypes.c_size_t)
    proto(user32.UpdateLayeredWindow, wintypes.BOOL, H, HDC, ctypes.POINTER(wintypes.POINT),
          ctypes.POINTER(wintypes.SIZE), HDC, ctypes.POINTER(wintypes.POINT),
          wintypes.COLORREF, ctypes.POINTER(BLENDFUNCTION), wintypes.DWORD)
    proto(user32.GetDC, HDC, H)
    proto(user32.ReleaseDC, ctypes.c_int, H, HDC)
    proto(user32.LoadCursorW, wintypes.HANDLE, wintypes.HINSTANCE, wintypes.LPVOID)
    proto(user32.SetCursor, wintypes.HANDLE, wintypes.HANDLE)
    proto(user32.GetCursorPos, wintypes.BOOL, ctypes.POINTER(wintypes.POINT))
    proto(user32.SetCapture, H, H)
    proto(user32.ReleaseCapture, wintypes.BOOL)
    proto(user32.TrackMouseEvent, wintypes.BOOL, ctypes.POINTER(TRACKMOUSEEVENT))
    proto(user32.GetForegroundWindow, H)
    proto(user32.SetForegroundWindow, wintypes.BOOL, H)
    proto(user32.IsWindow, wintypes.BOOL, H)
    proto(user32.IsWindowVisible, wintypes.BOOL, H)
    proto(user32.GetClassNameW, ctypes.c_int, H, wintypes.LPWSTR, ctypes.c_int)
    proto(user32.GetWindowRect, wintypes.BOOL, H, ctypes.POINTER(wintypes.RECT))
    proto(user32.MonitorFromPoint, wintypes.HMONITOR, wintypes.POINT, wintypes.DWORD)
    proto(user32.MonitorFromWindow, wintypes.HMONITOR, H, wintypes.DWORD)
    proto(user32.GetMonitorInfoW, wintypes.BOOL, wintypes.HMONITOR, ctypes.POINTER(MONITORINFO))
    proto(user32.CreatePopupMenu, wintypes.HMENU)
    proto(user32.AppendMenuW, wintypes.BOOL, wintypes.HMENU, U, ctypes.c_size_t, wintypes.LPCWSTR)
    proto(user32.TrackPopupMenu, wintypes.BOOL, wintypes.HMENU, U, ctypes.c_int, ctypes.c_int,
          ctypes.c_int, H, wintypes.LPVOID)
    proto(user32.DestroyMenu, wintypes.BOOL, wintypes.HMENU)
    proto(user32.RegisterWindowMessageW, U, wintypes.LPCWSTR)
    proto(user32.CreateIconIndirect, wintypes.HICON, ctypes.POINTER(ICONINFO))
    proto(user32.DestroyIcon, wintypes.BOOL, wintypes.HICON)
    proto(user32.GetSystemMetrics, ctypes.c_int, ctypes.c_int)
    proto(user32.MessageBoxW, ctypes.c_int, H, wintypes.LPCWSTR, wintypes.LPCWSTR, U)
    proto(user32.DrawTextW, ctypes.c_int, HDC, wintypes.LPCWSTR, ctypes.c_int,
          ctypes.POINTER(wintypes.RECT), U)
    try:
        proto(user32.ChangeWindowMessageFilterEx, wintypes.BOOL, H, U, wintypes.DWORD,
              ctypes.c_void_p)
    except AttributeError:
        pass
    proto(gdi32.GdiFlush, wintypes.BOOL)
    proto(user32.IsIconic, wintypes.BOOL, H)
    try:
        dwmapi = ctypes.WinDLL("dwmapi")  # type: ignore[attr-defined]
        proto(dwmapi.DwmGetWindowAttribute, ctypes.c_long, H, wintypes.DWORD,
              ctypes.c_void_p, wintypes.DWORD)
        user32._dwm_cloaked = dwmapi.DwmGetWindowAttribute  # type: ignore[attr-defined]
    except (OSError, AttributeError):
        pass
    proto(gdi32.CreateCompatibleDC, HDC, HDC)
    proto(gdi32.DeleteDC, wintypes.BOOL, HDC)
    proto(gdi32.CreateDIBSection, wintypes.HBITMAP, HDC, ctypes.POINTER(BITMAPINFOHEADER),
          U, ctypes.POINTER(ctypes.c_void_p), wintypes.HANDLE, wintypes.DWORD)
    proto(gdi32.CreateBitmap, wintypes.HBITMAP, ctypes.c_int, ctypes.c_int, U, U, wintypes.LPVOID)
    proto(gdi32.SelectObject, HGDI, HDC, HGDI)
    proto(gdi32.DeleteObject, wintypes.BOOL, HGDI)
    proto(gdi32.SetBkMode, ctypes.c_int, HDC, ctypes.c_int)
    proto(gdi32.SetTextColor, wintypes.COLORREF, HDC, wintypes.COLORREF)
    proto(gdi32.SetTextCharacterExtra, ctypes.c_int, HDC, ctypes.c_int)
    proto(gdi32.CreateFontW, wintypes.HFONT, ctypes.c_int, ctypes.c_int, ctypes.c_int,
          ctypes.c_int, ctypes.c_int, wintypes.DWORD, wintypes.DWORD, wintypes.DWORD,
          wintypes.DWORD, wintypes.DWORD, wintypes.DWORD, wintypes.DWORD, wintypes.DWORD,
          wintypes.LPCWSTR)
    proto(gdi32.GetTextExtentPoint32W, wintypes.BOOL, HDC, wintypes.LPCWSTR, ctypes.c_int,
          ctypes.POINTER(wintypes.SIZE))
    proto(gdi32.GetTextFaceW, ctypes.c_int, HDC, ctypes.c_int, wintypes.LPWSTR)
    proto(shell32.Shell_NotifyIconW, wintypes.BOOL, wintypes.DWORD,
          ctypes.POINTER(NOTIFYICONDATAW))
    proto(kernel32.GetModuleHandleW, wintypes.HMODULE, wintypes.LPCWSTR)
    shcore = None
    try:
        shcore = ctypes.WinDLL("shcore")  # type: ignore[attr-defined]
        proto(shcore.GetDpiForMonitor, ctypes.c_long, wintypes.HMONITOR, ctypes.c_int,
              ctypes.POINTER(wintypes.UINT), ctypes.POINTER(wintypes.UINT))
    except (OSError, AttributeError):
        shcore = None
    _api = (user32, gdi32, shell32, kernel32, shcore)
    return _api


def _rgb(color: tuple[int, int, int]) -> int:
    """COLORREF is 0x00BBGGRR."""
    r, g, b = color
    return (b << 16) | (g << 8) | r


@dataclass
class ShellCallbacks:
    """What the tray menu, orb, and status poll drive. Every callable is
    invoked on the UI thread and must not block."""

    toggle_dictation: Callable[[], None] = lambda: None
    summon: Callable[[], None] = lambda: None
    read_clipboard: Callable[[], None] = lambda: None
    open_settings: Callable[[], None] = lambda: None
    open_logs: Callable[[], None] = lambda: None
    sign_in: Callable[[], None] = lambda: None
    restart: Callable[[], None] = lambda: None
    quit: Callable[[], None] = lambda: None
    get_autostart: Callable[[], bool] = lambda: False
    set_autostart: Callable[[bool], None] = lambda enabled: None
    open_help: Callable[[], None] = lambda: None
    status: Callable[[], dict] = lambda: {}


@dataclass
class _Overlay:
    state: str = ""
    detail: str = ""
    anchor: Optional[tuple[int, int]] = None
    window_rect: Optional[tuple[int, int, int, int]] = None
    visible: bool = False
    phase: Optional[int] = None
    shown_at: float = 0.0
    idle_since: float = 0.0


@dataclass
class _Orb:
    visible: bool = False
    want: bool = True
    hover: bool = False
    tracking: bool = False
    pressed: bool = False
    dragging: bool = False
    press_pt: tuple[int, int] = (0, 0)
    press_pos: tuple[int, int] = (0, 0)
    pos: Optional[tuple[int, int]] = None
    accent: tuple[int, int, int] = render.ORB_CYAN
    margin: int = 0
    canvas: int = 0


@dataclass
class _Tray:
    added: bool = False
    accent: tuple[int, int, int] = render.ORB_CYAN
    tip: str = ""
    icons: dict = field(default_factory=dict)


class WinShell:
    """Overlay + orb + tray on a dedicated UI thread. Thread-safe API:
    show(), hide(), set_button(), notify(), set_setup_mode(), stop()."""

    def __init__(
        self,
        callbacks: ShellCallbacks,
        *,
        app_name: str = "HyperFurion VK",
        version: str = "",
        dictation_hotkey: str = "Ctrl+Alt+V",
        assistant_hotkey: str = "Right Ctrl",
        read_hotkey: str = "",
        orb_state_path: Optional[str] = None,
    ):
        self._cb = callbacks
        self._app_name = app_name
        self._version = version
        self._dictation_hotkey = dictation_hotkey
        self._assistant_hotkey = assistant_hotkey
        self._read_hotkey = read_hotkey
        self._orb_state_path = orb_state_path
        self._queue: deque = deque()
        self._lock = threading.Lock()
        self._ready = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._hwnd = None
        self._overlay_hwnd = None
        self._orb_hwnd = None  # the disc: takes clicks, draws nothing visible
        self._orb_glow_hwnd = None  # the visible orb and halo: click-through
        self._procs: list = []
        self._ov = _Overlay()
        self._orb = _Orb()
        self._tray = _Tray()
        self._setup_message = ""
        self._last_target = None
        self._deferred: Optional[Callable[[], None]] = None
        self._fonts: dict = {}
        self._mono_face: Optional[str] = None
        self._taskbar_created = 0
        self._running = False

    # ------------------------------------------------------- public API

    def start(self, timeout: float = 5.0) -> bool:
        self._thread = threading.Thread(target=self._run, name="vk-shell", daemon=True)
        self._thread.start()
        return self._ready.wait(timeout) and self._hwnd is not None

    def stop(self) -> None:
        self._post(("quit",))
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=3.0)

    def show(self, state: str, *, detail: str = "", timeout_ms: int = 0,
             anchor: Optional[tuple[int, int]] = None) -> None:
        self._post(("show", state, detail, int(timeout_ms or 0), anchor))

    def hide(self) -> None:
        self._post(("hide",))

    def set_button(self, visible: bool) -> None:
        self._post(("button", bool(visible)))

    def notify(self, title: str, body: str = "", *, error: bool = False) -> None:
        self._post(("notify", title, body, error))

    def set_labels(self, *, dictation: str, assistant: str, read: str) -> None:
        """Hotkey names shown in the menu and tooltip (refreshed whenever the
        daemon (re)starts, so edited bindings show up after Restart)."""
        self._post(("labels", dictation, assistant, read))

    def set_setup_mode(self, message: str) -> None:
        """Non-empty: the daemon can't start yet (e.g. no API key) — amber
        icon, a setup-first menu. Empty: back to normal."""
        self._post(("setup", message))

    # ------------------------------------------------------ queue plumbing

    def _post(self, command: tuple) -> None:
        with self._lock:
            self._queue.append(command)
        hwnd = self._hwnd
        if hwnd:
            try:
                _load_api()[0].PostMessageW(hwnd, WM_APP_COMMAND, 0, 0)
            except Exception:
                pass

    def _drain(self) -> None:
        while True:
            with self._lock:
                if not self._queue:
                    return
                command = self._queue.popleft()
            try:
                self._handle(command)
            except Exception:
                logger.exception("shell command failed: %s", command[0])

    def _handle(self, command: tuple) -> None:
        kind = command[0]
        if kind == "show":
            _, state, detail, timeout_ms, anchor = command
            self._show_overlay(state, detail, timeout_ms, anchor)
        elif kind == "hide":
            self._hide_overlay()
        elif kind == "button":
            self._orb.want = command[1]
            self._sync_orb()
        elif kind == "notify":
            _, title, body, error = command
            self._balloon(title, body, error)
        elif kind == "setup":
            self._setup_message = command[1]
            self._refresh_tray()
            self._sync_orb()
        elif kind == "labels":
            _, self._dictation_hotkey, self._assistant_hotkey, self._read_hotkey = command
            self._refresh_tray()
        elif kind == "quit":
            self._teardown()

    # ----------------------------------------------------------- UI thread

    def _run(self) -> None:  # pragma: no cover - requires Windows
        try:
            user32, _gdi32, _shell32, kernel32, _ = _load_api()
            self._hinstance = kernel32.GetModuleHandleW(None)
            self._taskbar_created = user32.RegisterWindowMessageW("TaskbarCreated")
            self._hwnd = self._create_window(
                "HyperFurionVK.Main", self._main_proc, ex_style=WS_EX_TOOLWINDOW, style=WS_POPUP
            )
            self._overlay_hwnd = self._create_window(
                "HyperFurionVK.Overlay", self._overlay_proc,
                ex_style=WS_EX_LAYERED | WS_EX_TOPMOST | WS_EX_TOOLWINDOW
                | WS_EX_NOACTIVATE | WS_EX_TRANSPARENT,
                style=WS_POPUP,
            )
            self._orb_glow_hwnd = self._create_window(
                "HyperFurionVK.OrbGlow", self._overlay_proc,
                ex_style=WS_EX_LAYERED | WS_EX_TOPMOST | WS_EX_TOOLWINDOW
                | WS_EX_NOACTIVATE | WS_EX_TRANSPARENT,
                style=WS_POPUP,
            )
            self._orb_hwnd = self._create_window(
                "HyperFurionVK.Orb", self._orb_proc,
                ex_style=WS_EX_LAYERED | WS_EX_TOPMOST | WS_EX_TOOLWINDOW | WS_EX_NOACTIVATE,
                style=WS_POPUP,
            )
            self._allow_explorer_messages()
            self._load_orb_position()
            self._add_tray()
            user32.SetTimer(self._hwnd, TIMER_POLL, POLL_MS, None)
            self._running = True
        except Exception:
            logger.exception("Windows shell failed to start")
            self._hwnd = None
            self._ready.set()
            return
        self._ready.set()
        self._drain()
        msg = wintypes.MSG()
        while user32.GetMessageW(ctypes.byref(msg), None, 0, 0) > 0:
            user32.TranslateMessage(ctypes.byref(msg))
            user32.DispatchMessageW(ctypes.byref(msg))
        self._running = False

    def _create_window(self, class_name, proc, *, ex_style, style):  # pragma: no cover
        user32 = _load_api()[0]
        wndproc = WNDPROC(proc)
        self._procs.append(wndproc)  # keep the thunk alive
        wc = WNDCLASSEXW()
        wc.cbSize = ctypes.sizeof(WNDCLASSEXW)
        wc.lpfnWndProc = wndproc
        wc.hInstance = self._hinstance
        wc.hCursor = user32.LoadCursorW(None, ctypes.c_void_p(IDC_ARROW))
        wc.lpszClassName = class_name
        user32.RegisterClassExW(ctypes.byref(wc))
        hwnd = user32.CreateWindowExW(
            ex_style, class_name, self._app_name, style,
            0, 0, 1, 1, None, None, self._hinstance, None,
        )
        if not hwnd:
            raise OSError(f"CreateWindowExW({class_name}) failed: {ctypes.get_last_error()}")
        return hwnd

    def _allow_explorer_messages(self) -> None:  # pragma: no cover - requires Windows
        """Run as administrator, our window would never hear from Explorer
        (UIPI drops messages from lower integrity): no tray clicks, and no
        TaskbarCreated to bring the icon back after Explorer restarts."""
        user32 = _load_api()[0]
        allow = getattr(user32, "ChangeWindowMessageFilterEx", None)
        if allow is None:
            return
        for message in (self._taskbar_created, WM_APP_TRAY):
            if message and not allow(self._hwnd, message, MSGFLT_ALLOW, None):
                logger.debug("ChangeWindowMessageFilterEx(%#x) failed", message)

    def _ours(self, hwnd) -> bool:
        return bool(hwnd) and hwnd in (
            self._hwnd, self._overlay_hwnd, self._orb_hwnd, self._orb_glow_hwnd
        )

    def _teardown(self) -> None:  # pragma: no cover - requires Windows
        user32, _, shell32, _, _ = _load_api()
        if self._tray.added:
            nid = self._nid()
            shell32.Shell_NotifyIconW(NIM_DELETE, ctypes.byref(nid))
            self._tray.added = False
        for hicon in self._tray.icons.values():
            user32.DestroyIcon(hicon)
        self._tray.icons.clear()
        for hwnd in (self._overlay_hwnd, self._orb_hwnd, self._orb_glow_hwnd, self._hwnd):
            if hwnd:
                user32.DestroyWindow(hwnd)
        self._overlay_hwnd = self._orb_hwnd = self._orb_glow_hwnd = None
        self._hwnd = None
        for font in self._fonts.values():
            _load_api()[1].DeleteObject(font)
        self._fonts.clear()
        user32.PostQuitMessage(0)

    # ------------------------------------------------------- window procs

    def _main_proc(self, hwnd, msg, wparam, lparam):  # pragma: no cover
        user32 = _load_api()[0]
        try:
            if msg == WM_APP_COMMAND:
                self._drain()
                return 0
            if msg == WM_TIMER:
                if wparam == TIMER_POLL:
                    self._poll()
                elif wparam == TIMER_ANIM:
                    self._animate()
                elif wparam == TIMER_HIDE:
                    user32.KillTimer(hwnd, TIMER_HIDE)
                    self._hide_overlay()
                elif wparam == TIMER_DEFERRED:
                    user32.KillTimer(hwnd, TIMER_DEFERRED)
                    action, self._deferred = self._deferred, None
                    if action is not None:
                        action()
                return 0
            if msg == WM_APP_TRAY:
                # NOTIFYICON_VERSION_4: LOWORD(lParam) is the event. A click
                # also sends raw button messages; act only on the semantic
                # ones so nothing fires twice.
                event = lparam & 0xFFFF
                if event == WM_CONTEXTMENU:
                    self._show_menu()
                elif event in (NIN_SELECT, NIN_KEYSELECT):
                    if self._setup_message:
                        self._show_menu()
                    else:
                        self._with_focus_restored(self._cb.toggle_dictation)
                return 0
            if msg == self._taskbar_created and msg:
                # Explorer restarted: the icon is gone until re-added.
                self._tray.added = False
                self._add_tray()
                return 0
            if msg == WM_SYSCOMMAND and (wparam & 0xFFF0) == SC_CLOSE:
                return 0  # Alt+F4 meant for something else: never quits us
            if msg == WM_CLOSE:
                # taskkill (without /F): quit properly rather than lose the
                # window and keep running.
                self._cb.quit()
                return 0
            if msg in (WM_DISPLAYCHANGE, WM_DPICHANGED):
                self._sync_orb(reposition=True)
        except Exception:
            logger.exception("main window message %#x failed", msg)
        return user32.DefWindowProcW(hwnd, msg, wparam, lparam)

    def _overlay_proc(self, hwnd, msg, wparam, lparam):  # pragma: no cover
        if msg == WM_MOUSEACTIVATE:
            return MA_NOACTIVATE
        if msg == WM_CLOSE:
            return 0  # only the main window answers a close (by quitting)
        return _load_api()[0].DefWindowProcW(hwnd, msg, wparam, lparam)

    def _orb_proc(self, hwnd, msg, wparam, lparam):  # pragma: no cover
        user32 = _load_api()[0]
        try:
            if msg == WM_MOUSEACTIVATE:
                return MA_NOACTIVATE  # never steal focus from the user's app
            if msg == WM_CLOSE:
                return 0
            if msg == WM_SETCURSOR:
                user32.SetCursor(user32.LoadCursorW(None, ctypes.c_void_p(IDC_HAND)))
                return 1
            if msg == WM_LBUTTONDOWN:
                point = wintypes.POINT()
                user32.GetCursorPos(ctypes.byref(point))
                self._orb.pressed = True
                self._orb.dragging = False
                self._orb.press_pt = (point.x, point.y)
                self._orb.press_pos = self._orb.pos or (0, 0)
                user32.SetCapture(hwnd)
                return 0
            if msg == WM_MOUSEMOVE:
                if not self._orb.tracking:
                    tme = TRACKMOUSEEVENT(ctypes.sizeof(TRACKMOUSEEVENT), TME_LEAVE, hwnd, 0)
                    user32.TrackMouseEvent(ctypes.byref(tme))
                    self._orb.tracking = True
                if not self._orb.hover:
                    self._orb.hover = True
                    self._draw_orb()
                if self._orb.pressed:
                    point = wintypes.POINT()
                    user32.GetCursorPos(ctypes.byref(point))
                    dx = point.x - self._orb.press_pt[0]
                    dy = point.y - self._orb.press_pt[1]
                    if self._orb.dragging or abs(dx) + abs(dy) > 5:
                        self._orb.dragging = True
                        self._orb.pos = (self._orb.press_pos[0] + dx, self._orb.press_pos[1] + dy)
                        self._draw_orb()
                return 0
            if msg == WM_MOUSELEAVE:
                self._orb.tracking = False
                if self._orb.hover:
                    self._orb.hover = False
                    self._draw_orb()
                return 0
            if msg == WM_LBUTTONUP:
                user32.ReleaseCapture()
                was_drag = self._orb.dragging
                pressed = self._orb.pressed
                self._orb.pressed = self._orb.dragging = False
                if was_drag:
                    self._save_orb_position()
                elif pressed:
                    self._cb.summon()
                return 0
            if msg == WM_RBUTTONUP:
                self._show_menu()
                return 0
        except Exception:
            logger.exception("orb message %#x failed", msg)
        return user32.DefWindowProcW(hwnd, msg, wparam, lparam)

    # --------------------------------------------------------------- poll

    def _poll(self) -> None:  # pragma: no cover - requires Windows
        """Track the app the user was last in (so tray actions can hand
        focus back to it), and follow daemon status: tray colour, orb
        colour, and clearing a LISTENING pill that outlived its session."""
        user32 = _load_api()[0]
        fg = self._app_window(user32.GetForegroundWindow())
        if fg:
            self._last_target = fg
        try:
            status = self._cb.status() or {}
        except Exception:
            status = {}
        recording = bool(status.get("recording"))
        conversing = bool(status.get("conversing"))
        if self._setup_message:
            accent = render.STATE_STYLES["processing"].accent
        elif recording or conversing:
            accent = render.STATE_STYLES["listening"].accent
        else:
            accent = render.ORB_CYAN
        if accent != self._tray.accent:
            self._tray.accent = accent
            self._refresh_tray()
        orb_accent = (
            render.STATE_STYLES["listening"].accent if conversing else render.ORB_CYAN
        )
        if orb_accent != self._orb.accent:
            self._orb.accent = orb_accent
            self._draw_orb()
        now = time.monotonic()
        if self._ov.visible and self._ov.state == "listening":
            if recording or conversing:
                self._ov.idle_since = 0.0
            elif not self._ov.idle_since:
                self._ov.idle_since = now
            elif now - self._ov.idle_since > STALE_LISTENING_S:
                self._hide_overlay()

    def _focusable(self, hwnd) -> bool:  # pragma: no cover - requires Windows
        """Can take the focus back sensibly: still exists, not minimized
        (typing would go nowhere visible), not on another virtual desktop."""
        user32 = _load_api()[0]
        if not hwnd or not user32.IsWindow(hwnd) or user32.IsIconic(hwnd):
            return False
        cloaked_query = getattr(user32, "_dwm_cloaked", None)
        if cloaked_query is not None:
            cloaked = wintypes.DWORD()
            if cloaked_query(hwnd, DWMWA_CLOAKED, ctypes.byref(cloaked),
                             ctypes.sizeof(cloaked)) == 0 and cloaked.value:
                return False
        return True

    def _app_window(self, hwnd):  # pragma: no cover - requires Windows
        """hwnd when it is an app the user works in; None for our own
        windows and the taskbar/desktop."""
        if not hwnd or self._ours(hwnd):
            return None
        name = ctypes.create_unicode_buffer(64)
        _load_api()[0].GetClassNameW(hwnd, name, 64)
        return None if name.value.lower() in _SHELL_CLASSES else hwnd

    def _with_focus_restored(self, action: Callable[[], None]) -> None:  # pragma: no cover
        """Clicking the tray hands focus to the taskbar; give it back to the
        app the user was in before acting, so dictation types THERE."""
        user32 = _load_api()[0]
        target = self._last_target
        if target and self._focusable(target):
            user32.SetForegroundWindow(target)
            self._deferred = action
            user32.SetTimer(self._hwnd, TIMER_DEFERRED, 150, None)
        else:
            action()

    # ------------------------------------------------------------ overlay

    def _show_overlay(self, state, detail, timeout_ms, anchor) -> None:  # pragma: no cover
        user32 = _load_api()[0]
        style = render.style_for(state)
        same = self._ov.visible and self._ov.state == state
        self._ov.state = state
        self._ov.detail = detail or style.detail
        if anchor is not None:
            self._ov.anchor = anchor
        elif not same:
            self._ov.anchor = None
        if not same:
            self._ov.window_rect = self._foreground_rect()
            self._ov.shown_at = time.monotonic()
            self._ov.idle_since = 0.0
            user32.KillTimer(self._hwnd, TIMER_HIDE)
            if style.active:
                self._ov.phase = 0
                user32.SetTimer(self._hwnd, TIMER_ANIM, ANIM_MS, None)
            else:
                self._ov.phase = None
                user32.KillTimer(self._hwnd, TIMER_ANIM)
        if timeout_ms > 0:
            user32.SetTimer(self._hwnd, TIMER_HIDE, timeout_ms, None)
        else:
            # Persistent now: an earlier update's timer must not hide it.
            user32.KillTimer(self._hwnd, TIMER_HIDE)
        self._ov.visible = True
        self._draw_overlay()

    def _hide_overlay(self) -> None:  # pragma: no cover - requires Windows
        user32 = _load_api()[0]
        user32.KillTimer(self._hwnd, TIMER_ANIM)
        user32.KillTimer(self._hwnd, TIMER_HIDE)
        self._ov.visible = False
        self._ov.state = ""
        if self._overlay_hwnd:
            user32.ShowWindow(self._overlay_hwnd, SW_HIDE)

    def _animate(self) -> None:  # pragma: no cover - requires Windows
        if not self._ov.visible or self._ov.phase is None:
            _load_api()[0].KillTimer(self._hwnd, TIMER_ANIM)
            return
        self._ov.phase = (self._ov.phase + 1) % len(render.BAR_WAVE)
        self._draw_overlay()

    def _foreground_rect(self):  # pragma: no cover - requires Windows
        user32 = _load_api()[0]
        fg = user32.GetForegroundWindow()
        if not fg or self._ours(fg):
            fg = self._last_target
        if not fg:
            return None
        rect = wintypes.RECT()
        if not user32.GetWindowRect(fg, ctypes.byref(rect)):
            return None
        if rect.right - rect.left < 120 or rect.bottom - rect.top < 80:
            return None
        return (rect.left, rect.top, rect.right, rect.bottom)

    def _monitor_for(self, x: int, y: int):  # pragma: no cover - requires Windows
        user32, _, _, _, shcore = _load_api()
        monitor = user32.MonitorFromPoint(wintypes.POINT(x, y), MONITOR_DEFAULTTONEAREST)
        info = MONITORINFO()
        info.cbSize = ctypes.sizeof(MONITORINFO)
        user32.GetMonitorInfoW(monitor, ctypes.byref(info))
        work = (info.rcWork.left, info.rcWork.top, info.rcWork.right, info.rcWork.bottom)
        dpi = 96
        if shcore is not None:
            dx, dy = wintypes.UINT(), wintypes.UINT()
            if shcore.GetDpiForMonitor(monitor, 0, ctypes.byref(dx), ctypes.byref(dy)) == 0:
                dpi = int(dx.value) or 96
        return work, dpi

    def _font(self, dpi: int, points: float, bold: bool, face: Optional[str] = None):  # pragma: no cover
        gdi32 = _load_api()[1]
        face = face or self._mono()
        key = (dpi, points, bold, face)
        font = self._fonts.get(key)
        if font is None:
            height = -int(round(points * dpi / 72.0))
            font = gdi32.CreateFontW(
                height, 0, 0, 0, FW_BOLD if bold else FW_NORMAL, 0, 0, 0, 1, 0, 0,
                ANTIALIASED_QUALITY, 0, face,
            )
            self._fonts[key] = font
        return font

    def _mono(self) -> str:  # pragma: no cover - requires Windows
        """Cascadia Mono where installed (Windows 11), else Consolas."""
        if self._mono_face is None:
            user32, gdi32, _, _, _ = _load_api()
            screen = user32.GetDC(None)
            dc = gdi32.CreateCompatibleDC(screen)
            probe = gdi32.CreateFontW(-12, 0, 0, 0, FW_NORMAL, 0, 0, 0, 1, 0, 0, 0, 0,
                                      "Cascadia Mono")
            old = gdi32.SelectObject(dc, probe)
            buffer = ctypes.create_unicode_buffer(64)
            gdi32.GetTextFaceW(dc, 64, buffer)
            gdi32.SelectObject(dc, old)
            gdi32.DeleteObject(probe)
            gdi32.DeleteDC(dc)
            user32.ReleaseDC(None, screen)
            self._mono_face = "Cascadia Mono" if buffer.value == "Cascadia Mono" else "Consolas"
        return self._mono_face

    def _text_size(self, dc, font, text: str, extra: int = 0) -> tuple[int, int]:  # pragma: no cover
        gdi32 = _load_api()[1]
        gdi32.SelectObject(dc, font)
        gdi32.SetTextCharacterExtra(dc, extra)
        size = wintypes.SIZE()
        # The count is in UTF-16 units: an emoji is two.
        gdi32.GetTextExtentPoint32W(dc, text, len(text.encode("utf-16-le")) // 2,
                                    ctypes.byref(size))
        return int(size.cx), int(size.cy)

    def _draw_overlay(self) -> None:  # pragma: no cover - requires Windows
        user32, gdi32, _, _, _ = _load_api()
        if not self._overlay_hwnd or not self._ov.visible:
            return
        style = render.style_for(self._ov.state)
        anchor = self._ov.anchor
        probe_x, probe_y = anchor if render.anchor_known(anchor) else self._rect_center()
        work, dpi = self._monitor_for(probe_x, probe_y)
        scale = dpi / 96.0
        s = lambda v: int(round(v * scale))  # noqa: E731

        title_font = self._font(dpi, 7.5, True)
        label_font = self._font(dpi, 13.0, True)
        detail_font = self._font(dpi, 8.5, False)
        screen = user32.GetDC(None)
        dc = gdi32.CreateCompatibleDC(screen)
        try:
            title = "VOICE KEYBOARD"
            tw, th = self._text_size(dc, title_font, title, s(2))
            lw, lh = self._text_size(dc, label_font, style.label, s(1))
            detail = self._ov.detail
            dw, dh = self._text_size(dc, detail_font, detail) if detail else (0, 0)
            pad_x, pad_y = s(18), s(12)
            bars_w = 4 * max(2, s(3)) + 3 * max(2, s(3))
            text_x = pad_x + bars_w + s(16)
            content_w = max(tw, lw, dw)
            width = max(s(OVERLAY_MIN_W), min(s(OVERLAY_MAX_W), text_x + content_w + pad_x))
            height = pad_y + th + s(1) + lh + ((s(3) + dh) if detail else 0) + pad_y
            height = max(height, render.BAR_SLOT * scale + 2 * pad_y)
            height = int(height)
            margin = s(22)
            canvas_w, canvas_h = width + 2 * margin, height + 2 * margin
            x, y = render.place_pill(width, height, anchor, self._ov.window_rect, work,
                                     scale=scale)

            rgb, alpha = render.pill_layers(
                canvas_w, canvas_h, margin=margin, width=width, height=height, scale=scale,
                accent=style.accent, glow_alpha=style.glow,
                bars=render.bar_heights(self._ov.phase), bars_x=pad_x,
            )
            bits, bitmap = self._dib(dc, canvas_w, canvas_h)
            old_bitmap = gdi32.SelectObject(dc, bitmap)
            import numpy as np

            view = np.ctypeslib.as_array(
                ctypes.cast(bits, ctypes.POINTER(ctypes.c_uint8)),
                shape=(canvas_h, canvas_w, 4),
            )
            view[..., 0] = np.clip(rgb[..., 2], 0, 255)
            view[..., 1] = np.clip(rgb[..., 1], 0, 255)
            view[..., 2] = np.clip(rgb[..., 0], 0, 255)
            gdi32.SetBkMode(dc, TRANSPARENT)
            top = margin + (height - (th + s(1) + lh + ((s(3) + dh) if detail else 0))) // 2
            left = margin + text_x
            right = margin + width - pad_x
            self._draw_text(dc, title_font, title, render.TITLE_GREY, left, top, right, th, s(2))
            top += th + s(1)
            self._draw_text(dc, label_font, style.label, style.accent, left, top, right, lh, s(1))
            if detail:
                top += lh + s(3)
                self._draw_text(dc, detail_font, detail, render.DETAIL_INK, left, top, right, dh)
            gdi32.GdiFlush()  # GDI may batch the text; read the bits after it lands
            drawn = view[..., :3][..., ::-1].astype(np.float32)
            view[...] = render.to_bgra_premultiplied(drawn, alpha)
            self._update_layered(self._overlay_hwnd, dc, x - margin, y - margin,
                                 canvas_w, canvas_h)
            gdi32.SelectObject(dc, old_bitmap)
            gdi32.DeleteObject(bitmap)
        finally:
            gdi32.DeleteDC(dc)
            user32.ReleaseDC(None, screen)
        user32.SetWindowPos(self._overlay_hwnd, HWND_TOPMOST, 0, 0, 0, 0,
                            SWP_NOMOVE | SWP_NOSIZE | SWP_NOACTIVATE | SWP_SHOWWINDOW)

    def _rect_center(self) -> tuple[int, int]:
        rect = self._ov.window_rect
        if rect:
            return ((rect[0] + rect[2]) // 2, (rect[1] + rect[3]) // 2)
        return (0, 0)

    def _draw_text(self, dc, font, text, color, left, top, right, height, extra=0):  # pragma: no cover
        user32, gdi32, _, _, _ = _load_api()
        gdi32.SelectObject(dc, font)
        gdi32.SetTextCharacterExtra(dc, extra)
        gdi32.SetTextColor(dc, _rgb(color))
        rect = wintypes.RECT(int(left), int(top), int(right), int(top + height + 2))
        # -1: NUL-terminated, so text with emoji (two UTF-16 units each) is
        # never cut short by a code-point count.
        user32.DrawTextW(dc, text, -1, ctypes.byref(rect),
                         DT_LEFT | DT_SINGLELINE | DT_NOPREFIX | DT_END_ELLIPSIS)

    def _dib(self, dc, width: int, height: int):  # pragma: no cover - requires Windows
        gdi32 = _load_api()[1]
        header = BITMAPINFOHEADER()
        header.biSize = ctypes.sizeof(BITMAPINFOHEADER)
        header.biWidth = width
        header.biHeight = -height  # top-down rows
        header.biPlanes = 1
        header.biBitCount = 32
        header.biCompression = BI_RGB
        bits = ctypes.c_void_p()
        bitmap = gdi32.CreateDIBSection(dc, ctypes.byref(header), DIB_RGB_COLORS,
                                        ctypes.byref(bits), None, 0)
        if not bitmap or not bits:
            raise OSError("CreateDIBSection failed")
        return bits, bitmap

    def _update_layered(self, hwnd, dc, x, y, width, height) -> None:  # pragma: no cover
        user32 = _load_api()[0]
        screen = user32.GetDC(None)
        try:
            blend = BLENDFUNCTION(AC_SRC_OVER, 0, 255, AC_SRC_ALPHA)
            user32.UpdateLayeredWindow(
                hwnd, screen, ctypes.byref(wintypes.POINT(int(x), int(y))),
                ctypes.byref(wintypes.SIZE(int(width), int(height))), dc,
                ctypes.byref(wintypes.POINT(0, 0)), 0, ctypes.byref(blend), ULW_ALPHA,
            )
        finally:
            user32.ReleaseDC(None, screen)

    # ----------------------------------------------------------------- orb

    def _sync_orb(self, reposition: bool = False) -> None:  # pragma: no cover
        user32 = _load_api()[0]
        want = self._orb.want and not self._setup_message
        if reposition:
            self._orb.pos = self._clamp_orb(self._orb.pos)
        if want:
            self._orb.visible = True
            self._draw_orb()
            # The click target goes above the glow it sits in.
            for hwnd in (self._orb_glow_hwnd, self._orb_hwnd):
                user32.SetWindowPos(hwnd, HWND_TOPMOST, 0, 0, 0, 0,
                                    SWP_NOMOVE | SWP_NOSIZE | SWP_NOACTIVATE | SWP_SHOWWINDOW)
        elif self._orb.visible:
            self._orb.visible = False
            for hwnd in (self._orb_hwnd, self._orb_glow_hwnd):
                user32.ShowWindow(hwnd, SW_HIDE)

    def _default_orb_pos(self, size: int) -> tuple[int, int]:  # pragma: no cover
        user32 = _load_api()[0]
        monitor = user32.MonitorFromPoint(wintypes.POINT(0, 0), MONITOR_DEFAULTTOPRIMARY)
        info = MONITORINFO()
        info.cbSize = ctypes.sizeof(MONITORINFO)
        user32.GetMonitorInfoW(monitor, ctypes.byref(info))
        margin = 22
        return (info.rcWork.right - size - margin, info.rcWork.bottom - size - margin)

    def _clamp_orb(self, pos):  # pragma: no cover - requires Windows
        if pos is None:
            return None
        work, _ = self._monitor_for(pos[0], pos[1])
        left, top, right, bottom = work
        size = self._orb.canvas - 2 * self._orb.margin if self._orb.canvas else ORB_SIZE
        return (max(left, min(pos[0], right - size)), max(top, min(pos[1], bottom - size)))

    def _draw_orb(self) -> None:  # pragma: no cover - requires Windows
        if not self._orb_hwnd or not self._orb.visible:
            return
        user32, gdi32, _, _, _ = _load_api()
        pos = self._orb.pos
        _, dpi = self._monitor_for(*(pos or (0, 0)))
        scale = dpi / 96.0
        size = int(round(ORB_SIZE * scale))
        margin = int(round(16 * scale))
        if pos is None:
            pos = self._orb.pos = self._default_orb_pos(size)
        hot = self._orb.hover
        accent = self._orb.accent
        ring = render.ORB_CYAN_HOT if hot and accent == render.ORB_CYAN else accent
        mark = (234, 252, 255) if hot else ring
        rgb, alpha = render.orb_layers(size, margin=margin, scale=scale, ring_rgb=ring,
                                       glow_alpha=0.42 if hot else 0.26, mark_rgb=mark)
        canvas = size + 2 * margin
        self._orb.margin, self._orb.canvas = margin, canvas
        screen = user32.GetDC(None)
        dc = gdi32.CreateCompatibleDC(screen)
        try:
            import numpy as np

            for hwnd, pixels, x, y in (
                (self._orb_glow_hwnd, render.to_bgra_premultiplied(rgb, alpha),
                 pos[0] - margin, pos[1] - margin),
                (self._orb_hwnd, render.orb_hit_bgra(size), pos[0], pos[1]),
            ):
                side = pixels.shape[0]
                bits, bitmap = self._dib(dc, side, side)
                old_bitmap = gdi32.SelectObject(dc, bitmap)
                view = np.ctypeslib.as_array(
                    ctypes.cast(bits, ctypes.POINTER(ctypes.c_uint8)), shape=(side, side, 4)
                )
                view[...] = pixels
                self._update_layered(hwnd, dc, x, y, side, side)
                gdi32.SelectObject(dc, old_bitmap)
                gdi32.DeleteObject(bitmap)
        finally:
            gdi32.DeleteDC(dc)
            user32.ReleaseDC(None, screen)

    def _load_orb_position(self) -> None:
        if not self._orb_state_path:
            return
        try:
            with open(self._orb_state_path, encoding="utf-8") as f:
                data = json.load(f)
            x, y = int(data["orb"][0]), int(data["orb"][1])
            self._orb.pos = (x, y)
            self._orb.pos = self._clamp_orb(self._orb.pos)
        except (OSError, ValueError, KeyError, TypeError, IndexError):
            pass

    def _save_orb_position(self) -> None:
        if not self._orb_state_path or self._orb.pos is None:
            return
        try:
            os.makedirs(os.path.dirname(self._orb_state_path), exist_ok=True)
            with open(self._orb_state_path, "w", encoding="utf-8") as f:
                json.dump({"orb": list(self._orb.pos)}, f)
        except OSError:
            logger.debug("could not save orb position", exc_info=True)

    # ---------------------------------------------------------------- tray

    def _nid(self) -> NOTIFYICONDATAW:
        nid = NOTIFYICONDATAW()
        nid.cbSize = ctypes.sizeof(NOTIFYICONDATAW)
        nid.hWnd = self._hwnd
        nid.uID = 1
        return nid

    def _icon(self, accent: tuple[int, int, int]):  # pragma: no cover - requires Windows
        hicon = self._tray.icons.get(accent)
        if hicon:
            return hicon
        user32, gdi32, _, _, _ = _load_api()
        size = user32.GetSystemMetrics(SM_CXSMICON) or 16
        rgba = render.icon_rgba(size, accent)
        screen = user32.GetDC(None)
        try:
            bits, color = self._dib(screen, size, size)
            bgra = rgba[..., [2, 1, 0, 3]].copy()
            ctypes.memmove(bits, bgra.ctypes.data, bgra.nbytes)
            mask = gdi32.CreateBitmap(size, size, 1, 1, None)
            info = ICONINFO(True, 0, 0, mask, color)
            hicon = user32.CreateIconIndirect(ctypes.byref(info))
            gdi32.DeleteObject(mask)
            gdi32.DeleteObject(color)
        finally:
            user32.ReleaseDC(None, screen)
        self._tray.icons[accent] = hicon
        return hicon

    def _tip_text(self) -> str:
        if self._setup_message:
            return f"{self._app_name} — setup needed"
        return f"{self._app_name} — {self._dictation_hotkey} to dictate"

    def _add_tray(self) -> None:  # pragma: no cover - requires Windows
        shell32 = _load_api()[2]
        nid = self._nid()
        nid.uFlags = NIF_MESSAGE | NIF_ICON | NIF_TIP | NIF_SHOWTIP
        nid.uCallbackMessage = WM_APP_TRAY
        nid.hIcon = self._icon(self._tray.accent)
        nid.szTip = self._tip_text()[:127]
        if shell32.Shell_NotifyIconW(NIM_ADD, ctypes.byref(nid)):
            nid.uVersion = NOTIFYICON_VERSION_4
            shell32.Shell_NotifyIconW(NIM_SETVERSION, ctypes.byref(nid))
            self._tray.added = True
        else:
            logger.warning("Could not add the notification-area icon")

    def _refresh_tray(self) -> None:  # pragma: no cover - requires Windows
        if not self._tray.added:
            return
        shell32 = _load_api()[2]
        nid = self._nid()
        nid.uFlags = NIF_ICON | NIF_TIP | NIF_SHOWTIP
        nid.hIcon = self._icon(self._tray.accent)
        nid.szTip = self._tip_text()[:127]
        shell32.Shell_NotifyIconW(NIM_MODIFY, ctypes.byref(nid))

    def _balloon(self, title: str, body: str, error: bool) -> None:  # pragma: no cover
        if not self._tray.added:
            return
        shell32 = _load_api()[2]
        nid = self._nid()
        nid.uFlags = NIF_INFO
        nid.szInfoTitle = (title or self._app_name)[:63]
        nid.szInfo = (body or " ")[:255]
        nid.dwInfoFlags = (NIIF_ERROR if error else NIIF_INFO) | NIIF_NOSOUND
        shell32.Shell_NotifyIconW(NIM_MODIFY, ctypes.byref(nid))

    def _show_menu(self) -> None:  # pragma: no cover - requires Windows
        user32 = _load_api()[0]
        status = {}
        try:
            status = self._cb.status() or {}
        except Exception:
            pass
        menu = user32.CreatePopupMenu()
        items: dict[int, Callable[[], None]] = {}
        # Items that open nothing hand focus straight back to the app the
        # user was in (so does dismissing the menu); the rest open a window
        # of their own, or restore focus themselves.
        refocus_ids: set[int] = set()

        def add(text: str, action: Optional[Callable[[], None]] = None, *,
                checked: bool = False, enabled: bool = True, refocus: bool = False) -> None:
            item_id = len(items) + 1
            flags = MF_STRING | (MF_CHECKED if checked else 0) | (0 if enabled else MF_GRAYED)
            user32.AppendMenuW(menu, flags, item_id, text)
            items[item_id] = action or (lambda: None)
            if refocus:
                refocus_ids.add(item_id)

        def separator() -> None:
            user32.AppendMenuW(menu, MF_SEPARATOR, 0, None)

        title = f"{self._app_name} {self._version}".strip()
        add(title, enabled=False)
        separator()
        if self._setup_message:
            add(self._setup_message[:80], enabled=False)
            add("Sign in to the hosted service…", self._cb.sign_in)
            add("Open settings file…", self._cb.open_settings)
        else:
            recording = bool(status.get("recording"))
            add(
                ("Stop dictation" if recording else "Start dictation")
                + f"\t{self._dictation_hotkey}",
                lambda: self._with_focus_restored(self._cb.toggle_dictation),
            )
            if status.get("assistant", True):
                add(f"Ask Kai\tHold {self._assistant_hotkey}",
                    lambda: self._with_focus_restored(self._cb.summon))
            read_label = "Read clipboard aloud"
            if self._read_hotkey:
                read_label += f"\t(selection: {self._read_hotkey})"
            add(read_label, self._cb.read_clipboard, refocus=True)
            separator()
            add("Show Kai orb", self._toggle_orb, checked=self._orb.want, refocus=True)
            add("Open settings file…", self._cb.open_settings)
        add("Open logs folder", self._cb.open_logs)
        add("Start with Windows", self._toggle_autostart, checked=self._safe_autostart(),
            refocus=True)
        add("Help", self._cb.open_help)
        separator()
        if not self._setup_message:
            add("Restart", self._cb.restart, refocus=True)
        add("Quit", self._cb.quit, refocus=True)

        # Whatever really had the focus (the taskbar, for a tray click; the
        # app or the desktop, for the orb, which never takes it).
        previous = user32.GetForegroundWindow()
        if self._ours(previous):
            previous = None
        point = wintypes.POINT()
        user32.GetCursorPos(ctypes.byref(point))
        # Required for the menu to close when clicking elsewhere.
        user32.SetForegroundWindow(self._hwnd)
        chosen = int(user32.TrackPopupMenu(
            menu, TPM_RETURNCMD | TPM_NONOTIFY | TPM_RIGHTBUTTON,
            point.x, point.y, 0, self._hwnd, None,
        ))
        user32.PostMessageW(self._hwnd, WM_NULL, 0, 0)
        user32.DestroyMenu(menu)
        if (
            (not chosen or chosen in refocus_ids)
            and user32.GetForegroundWindow() == self._hwnd  # not clicked away
            and self._focusable(previous)
        ):
            # Our hidden window must not keep the focus: typing would go
            # nowhere.
            user32.SetForegroundWindow(previous)
        action = items.get(chosen)
        if action is not None:
            try:
                action()
            except Exception:
                logger.exception("tray action failed")

    def _toggle_orb(self) -> None:
        self._orb.want = not self._orb.want
        self._sync_orb()

    def _safe_autostart(self) -> bool:
        try:
            return bool(self._cb.get_autostart())
        except Exception:
            return False

    def _toggle_autostart(self) -> None:
        try:
            self._cb.set_autostart(not self._safe_autostart())
        except Exception:
            logger.exception("could not change start-with-Windows")


def open_in_editor(path: str) -> None:  # pragma: no cover - requires Windows
    """Open a text file in Notepad (always present; .toml has no default
    handler on a fresh Windows)."""
    subprocess.Popen(["notepad.exe", path], close_fds=True)


def open_folder(path: str) -> None:  # pragma: no cover - requires Windows
    os.makedirs(path, exist_ok=True)
    os.startfile(path)  # type: ignore[attr-defined]
