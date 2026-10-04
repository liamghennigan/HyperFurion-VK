"""Native Win32 clipboard access (ctypes; no PowerShell round-trips).

Spawning PowerShell costs ~0.5 s per call and flashes nothing but still
burns a process; these calls take microseconds. Beyond text get/set this
offers a snapshot/restore of the clipboard (every memory-backed format —
text, HTML, RTF, images as DIB, file lists — plus enhanced metafiles) so
copying the user's selection to read it aloud can put their clipboard
back exactly; a snapshot says when it could NOT capture everything, so
callers can leave the clipboard alone instead.

A restore is tagged so Windows clipboard history (Win+V) and cloud sync
don't record it as a new entry. Content that password managers mark
private is recognized (is_sensitive()) and never read aloud.
"""

import ctypes
import logging
import time
from contextlib import contextmanager
from ctypes import wintypes
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)

CF_BITMAP = 2
CF_METAFILEPICT = 3
CF_DIB = 8
CF_PALETTE = 9
CF_UNICODETEXT = 13
CF_ENHMETAFILE = 14
CF_DIBV5 = 17
GMEM_MOVEABLE = 0x0002
# Handles Windows re-creates from a twin we DO capture: CF_BITMAP (and its
# palette) from CF_DIB/CF_DIBV5, CF_METAFILEPICT from CF_ENHMETAFILE.
_SYNTHESIZED_FROM = {
    CF_BITMAP: (CF_DIB, CF_DIBV5),
    CF_PALETTE: (CF_DIB, CF_DIBV5),
    CF_METAFILEPICT: (CF_ENHMETAFILE,),
}
# Device-independent bitmaps Windows converts between: saving the one the
# app put (listed first) is enough, the other is re-created on restore.
_DIB_TWINS = frozenset({CF_DIB, CF_DIBV5})
# Owner-drawn and app-private handles: never restorable.
_UNRESTORABLE_FORMATS = frozenset({0x80, 0x82, 0x83, 0x8E})
_PRIVATE_RANGE = range(0x200, 0x400)  # CF_PRIVATEFIRST..CF_GDIOBJLAST
# Room for a screenshot across three 4K monitors (~100 MB as a DIB).
SNAPSHOT_LIMIT_BYTES = 128 * 1024 * 1024
# Tags on a restore: keep it out of Win+V history and cloud sync (it is
# not new content). Deliberately NOT the stronger
# ExcludeClipboardContentFromMonitorProcessing, which password managers
# set and is_sensitive() honors.
_HISTORY_EXCLUSION_FORMATS = ("CanIncludeInClipboardHistory", "CanUploadToCloudClipboard")
# Markers password managers put next to a secret they copy.
_SENSITIVE_MARKERS = ("ExcludeClipboardContentFromMonitorProcessing", "Clipboard Viewer Ignore")


@dataclass
class Snapshot:
    """The clipboard's formats as bytes. `complete` is False when some
    format couldn't be captured (too large, or a handle that can't be
    copied), i.e. a restore would lose something."""

    formats: list = field(default_factory=list)
    complete: bool = True


class LASTINPUTINFO(ctypes.Structure):
    _fields_ = [("cbSize", ctypes.c_uint32), ("dwTime", ctypes.c_uint32)]


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
    user32.IsClipboardFormatAvailable.argtypes = [wintypes.UINT]
    user32.IsClipboardFormatAvailable.restype = wintypes.BOOL
    user32.GetClipboardOwner.restype = wintypes.HWND
    user32.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
    user32.GetWindowThreadProcessId.restype = wintypes.DWORD
    user32.GetLastInputInfo.argtypes = [ctypes.POINTER(LASTINPUTINFO)]
    user32.GetLastInputInfo.restype = wintypes.BOOL
    gdi32 = ctypes.WinDLL("gdi32", use_last_error=True)  # type: ignore[attr-defined]
    gdi32.GetEnhMetaFileBits.argtypes = [ctypes.c_void_p, wintypes.UINT, ctypes.c_void_p]
    gdi32.GetEnhMetaFileBits.restype = wintypes.UINT
    gdi32.SetEnhMetaFileBits.argtypes = [wintypes.UINT, ctypes.c_char_p]
    gdi32.SetEnhMetaFileBits.restype = ctypes.c_void_p
    gdi32.DeleteEnhMetaFile.argtypes = [ctypes.c_void_p]
    _api = (user32, kernel32, gdi32)
    return _api


@contextmanager
def _opened():  # pragma: no cover - requires Windows
    """Open the clipboard with a throwaway owner window (SetClipboardData
    fails when the clipboard is opened with a NULL owner and emptied).
    Retries briefly: another app may be holding it. Yields success."""
    user32 = _load()[0]
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
    kernel32 = _load()[1]
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
    user32, kernel32 = _load()[:2]
    handle = _alloc(data)
    if handle is None:
        return False
    if not user32.SetClipboardData(fmt, handle):
        kernel32.GlobalFree(handle)
        return False
    return True


def _mark_transient() -> None:  # pragma: no cover - requires Windows
    user32 = _load()[0]
    for name in _HISTORY_EXCLUSION_FORMATS:
        fmt = user32.RegisterClipboardFormatW(name)
        if fmt:
            _put(fmt, b"\x00\x00\x00\x00")


def is_sensitive() -> bool:  # pragma: no cover - requires Windows
    """True when the clipboard holds something a password manager marked
    private — never send it to a speech provider or read it aloud."""
    user32 = _load()[0]
    for name in _SENSITIVE_MARKERS:
        fmt = user32.RegisterClipboardFormatW(name)
        if fmt and user32.IsClipboardFormatAvailable(fmt):
            return True
    return False


def owner_pid():  # pragma: no cover - requires Windows
    """The process that last set the clipboard, or None when unknown (an
    app that opened the clipboard without a window)."""
    user32 = _load()[0]
    hwnd = user32.GetClipboardOwner()
    if not hwnd:
        return None
    pid = wintypes.DWORD()
    user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
    return int(pid.value) or None


def last_input_tick():  # pragma: no cover - requires Windows
    """When the user last pressed a key or moved the mouse (ms ticks)."""
    user32 = _load()[0]
    info = LASTINPUTINFO(ctypes.sizeof(LASTINPUTINFO), 0)
    if not user32.GetLastInputInfo(ctypes.byref(info)):
        return None
    return int(info.dwTime)


def sequence_number() -> int:  # pragma: no cover - requires Windows
    user32 = _load()[0]
    return int(user32.GetClipboardSequenceNumber())


def get_text(*, unless_sensitive: bool = False):  # pragma: no cover - requires Windows
    """Clipboard text; "" when the clipboard holds no text, None on failure
    — and None when `unless_sensitive` and a password manager marked it
    (checked in the same clipboard session as the read, so a writer can't
    add the marker in between)."""
    user32, kernel32 = _load()[:2]
    with _opened() as ok:
        if not ok:
            return None
        if unless_sensitive and is_sensitive():
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
    user32 = _load()[0]
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
    """The clipboard's contents as a Snapshot (empty for an empty clipboard),
    or None when it could not be opened."""
    user32, kernel32, gdi32 = _load()
    snap = Snapshot()
    present: set[int] = set()
    total = 0
    with _opened() as ok:
        if not ok:
            return None
        fmt = 0
        while True:
            fmt = user32.EnumClipboardFormats(fmt)
            if not fmt:
                break
            fmt = int(fmt)
            present.add(fmt)
            if fmt in _SYNTHESIZED_FROM:
                continue  # checked below, once every format is known
            if fmt in _DIB_TWINS and any(f in _DIB_TWINS for f, _ in snap.formats):
                continue  # re-created from the twin we saved
            if fmt in _UNRESTORABLE_FORMATS or fmt in _PRIVATE_RANGE:
                snap.complete = False
                continue
            handle = user32.GetClipboardData(fmt)
            if not handle:
                # Listed but not renderable for us (e.g. an OLE stream such
                # as FileContents): a restore would lose it.
                snap.complete = False
                continue
            if fmt == CF_ENHMETAFILE:
                size = gdi32.GetEnhMetaFileBits(handle, 0, None)
                if not size or total + size > SNAPSHOT_LIMIT_BYTES:
                    snap.complete = False
                    continue
                buffer = ctypes.create_string_buffer(size)
                if gdi32.GetEnhMetaFileBits(handle, size, buffer) != size:
                    snap.complete = False
                    continue
                snap.formats.append((fmt, buffer.raw))
                total += size
                continue
            size = kernel32.GlobalSize(handle)
            if not size or total + size > SNAPSHOT_LIMIT_BYTES:
                snap.complete = False
                continue
            pointer = kernel32.GlobalLock(handle)
            if not pointer:
                snap.complete = False
                continue
            try:
                snap.formats.append((fmt, ctypes.string_at(pointer, size)))
                total += size
            finally:
                kernel32.GlobalUnlock(handle)
    captured = {fmt for fmt, _ in snap.formats}
    for fmt, twins in _SYNTHESIZED_FROM.items():
        if fmt in present and not captured.intersection(twins):
            snap.complete = False
    if present & _DIB_TWINS and not captured & _DIB_TWINS:
        snap.complete = False
    return snap


def restore(saved) -> bool:  # pragma: no cover - requires Windows
    """Put a snapshot() back (an empty snapshot empties the clipboard).
    False when the clipboard couldn't be opened or any format failed."""
    user32, _, gdi32 = _load()
    formats = saved.formats if isinstance(saved, Snapshot) else saved
    with _opened() as ok:
        if not ok:
            return False
        user32.EmptyClipboard()
        complete = True
        for fmt, data in formats:
            if fmt == CF_ENHMETAFILE:
                metafile = gdi32.SetEnhMetaFileBits(len(data), data)
                if not metafile:
                    complete = False
                elif not user32.SetClipboardData(fmt, metafile):
                    gdi32.DeleteEnhMetaFile(metafile)
                    complete = False
                continue
            if not _put(fmt, data):
                complete = False
        if formats:
            _mark_transient()
        return complete
