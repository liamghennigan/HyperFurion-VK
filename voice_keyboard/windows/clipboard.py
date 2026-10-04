"""Native Win32 clipboard access (ctypes; no PowerShell round-trips).

Spawning PowerShell costs ~0.5 s per call and flashes nothing but still
burns a process; these calls take microseconds. Beyond text get/set this
offers a full snapshot/restore of the clipboard (every memory-backed
format — text, HTML, RTF, images as DIB, file lists) so copying the
user's selection to read it aloud can put their clipboard back exactly.

Anything written here only transiently (the selection copy, the restore)
is tagged so Windows clipboard history (Win+V) and cloud sync skip it.
"""

import ctypes
import logging
import time
from contextlib import contextmanager
from ctypes import wintypes

logger = logging.getLogger(__name__)

CF_UNICODETEXT = 13
GMEM_MOVEABLE = 0x0002
# Formats whose handle is a GDI object (or owner-drawn), not HGLOBAL memory
# — they cannot be copied byte-wise. Windows synthesizes HGLOBAL twins
# (CF_DIB for CF_BITMAP, ...) which ARE captured, so images survive.
_NON_HGLOBAL_FORMATS = frozenset({2, 3, 9, 14, 0x80, 0x82, 0x83, 0x8E})
_PRIVATE_RANGE = range(0x200, 0x400)  # CF_PRIVATEFIRST..CF_GDIOBJLAST
SNAPSHOT_LIMIT_BYTES = 64 * 1024 * 1024
_HISTORY_EXCLUSION_FORMATS = (
    "ExcludeClipboardContentFromMonitorProcessing",
    "CanIncludeInClipboardHistory",
    "CanUploadToCloudClipboard",
)

_api = None


def _load():  # pragma: no cover - requires Windows
    global _api
    if _api is not None:
        return _api
    user32 = ctypes.WinDLL("user32", use_last_error=True)  # type: ignore[attr-defined]
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)  # type: ignore[attr-defined]
    HGLOBAL = ctypes.c_void_p
    user32.OpenClipboard.argtypes = [wintypes.HWND]
    user32.OpenClipboard.restype = wintypes.BOOL
    user32.CloseClipboard.restype = wintypes.BOOL
    user32.EmptyClipboard.restype = wintypes.BOOL
    user32.GetClipboardData.argtypes = [wintypes.UINT]
    user32.GetClipboardData.restype = ctypes.c_void_p
    user32.SetClipboardData.argtypes = [wintypes.UINT, ctypes.c_void_p]
    user32.SetClipboardData.restype = ctypes.c_void_p
    user32.EnumClipboardFormats.argtypes = [wintypes.UINT]
    user32.EnumClipboardFormats.restype = wintypes.UINT
    user32.GetClipboardSequenceNumber.restype = wintypes.DWORD
    user32.RegisterClipboardFormatW.argtypes = [wintypes.LPCWSTR]
    user32.RegisterClipboardFormatW.restype = wintypes.UINT
    user32.CreateWindowExW.argtypes = [
        wintypes.DWORD, wintypes.LPCWSTR, wintypes.LPCWSTR, wintypes.DWORD,
        ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int,
        wintypes.HWND, wintypes.HMENU, wintypes.HINSTANCE, wintypes.LPVOID,
    ]
    user32.CreateWindowExW.restype = wintypes.HWND
    user32.DestroyWindow.argtypes = [wintypes.HWND]
    kernel32.GlobalAlloc.argtypes = [wintypes.UINT, ctypes.c_size_t]
    kernel32.GlobalAlloc.restype = HGLOBAL
    kernel32.GlobalLock.argtypes = [HGLOBAL]
    kernel32.GlobalLock.restype = ctypes.c_void_p
    kernel32.GlobalUnlock.argtypes = [HGLOBAL]
    kernel32.GlobalUnlock.restype = wintypes.BOOL
    kernel32.GlobalSize.argtypes = [HGLOBAL]
    kernel32.GlobalSize.restype = ctypes.c_size_t
    kernel32.GlobalFree.argtypes = [HGLOBAL]
    kernel32.GlobalFree.restype = HGLOBAL
    _api = (user32, kernel32)
    return _api


@contextmanager
def _opened():  # pragma: no cover - requires Windows
    """Open the clipboard with a throwaway owner window (SetClipboardData
    fails when the clipboard is opened with a NULL owner and emptied).
    Retries briefly: another app may be holding it. Yields success."""
    user32, _ = _load()
    hwnd = user32.CreateWindowExW(0, "STATIC", None, 0, 0, 0, 0, 0, None, None, None, None)
    opened = False
    try:
        for _ in range(25):
            if user32.OpenClipboard(hwnd):
                opened = True
                break
            time.sleep(0.01)
        if not opened:
            logger.debug("clipboard busy (error %d)", ctypes.get_last_error())
        yield opened
    finally:
        if opened:
            user32.CloseClipboard()
        if hwnd:
            user32.DestroyWindow(hwnd)


def _alloc(data: bytes):  # pragma: no cover - requires Windows
    _, kernel32 = _load()
    handle = kernel32.GlobalAlloc(GMEM_MOVEABLE, max(1, len(data)))
    if not handle:
        return None
    pointer = kernel32.GlobalLock(handle)
    if not pointer:
        kernel32.GlobalFree(handle)
        return None
    ctypes.memmove(pointer, data, len(data))
    kernel32.GlobalUnlock(handle)
    return handle


def _put(fmt: int, data: bytes) -> bool:  # pragma: no cover - requires Windows
    """SetClipboardData with a fresh HGLOBAL copy (clipboard must be open
    and emptied). The system owns the memory on success."""
    user32, kernel32 = _load()
    handle = _alloc(data)
    if handle is None:
        return False
    if not user32.SetClipboardData(fmt, handle):
        kernel32.GlobalFree(handle)
        return False
    return True


def _mark_transient() -> None:  # pragma: no cover - requires Windows
    user32, _ = _load()
    for name in _HISTORY_EXCLUSION_FORMATS:
        fmt = user32.RegisterClipboardFormatW(name)
        if fmt:
            _put(fmt, b"\x00\x00\x00\x00")


def sequence_number() -> int:  # pragma: no cover - requires Windows
    user32, _ = _load()
    return int(user32.GetClipboardSequenceNumber())


def get_text():  # pragma: no cover - requires Windows
    """Clipboard text; "" when the clipboard holds no text, None on failure."""
    user32, kernel32 = _load()
    with _opened() as ok:
        if not ok:
            return None
        handle = user32.GetClipboardData(CF_UNICODETEXT)
        if not handle:
            return ""
        pointer = kernel32.GlobalLock(handle)
        if not pointer:
            return None
        try:
            return ctypes.wstring_at(pointer)
        finally:
            kernel32.GlobalUnlock(handle)


def set_text(text: str, *, transient: bool = False) -> bool:  # pragma: no cover
    user32, _ = _load()
    data = text.encode("utf-16-le") + b"\x00\x00"
    with _opened() as ok:
        if not ok:
            return False
        user32.EmptyClipboard()
        if not _put(CF_UNICODETEXT, data):
            return False
        if transient:
            _mark_transient()
        return True


def snapshot():  # pragma: no cover - requires Windows
    """Every memory-backed format on the clipboard as [(format, bytes)];
    [] for an empty clipboard, None when it could not be opened."""
    user32, kernel32 = _load()
    saved: list[tuple[int, bytes]] = []
    total = 0
    with _opened() as ok:
        if not ok:
            return None
        fmt = 0
        while True:
            fmt = user32.EnumClipboardFormats(fmt)
            if not fmt:
                break
            if fmt in _NON_HGLOBAL_FORMATS or fmt in _PRIVATE_RANGE:
                continue
            handle = user32.GetClipboardData(fmt)
            if not handle:
                continue
            size = kernel32.GlobalSize(handle)
            if not size or total + size > SNAPSHOT_LIMIT_BYTES:
                continue
            pointer = kernel32.GlobalLock(handle)
            if not pointer:
                continue
            try:
                saved.append((int(fmt), ctypes.string_at(pointer, size)))
                total += size
            finally:
                kernel32.GlobalUnlock(handle)
    return saved


def restore(saved) -> bool:  # pragma: no cover - requires Windows
    """Put a snapshot() back (an empty snapshot empties the clipboard)."""
    user32, _ = _load()
    with _opened() as ok:
        if not ok:
            return False
        user32.EmptyClipboard()
        for fmt, data in saved:
            _put(fmt, data)
        if saved:
            _mark_transient()
        return True
