"""Which key types a given character on the user's keyboard layout (macOS).

macOS virtual keycodes name key POSITIONS on an ANSI (US) keyboard. Typed
text never needs them — it goes as Unicode — but a chord does: the Select
All chord is Command plus the key that types "a" right now. On AZERTY that
key sits where a US keyboard has Q, so pressing the ANSI "a" position
there would be Command+Q, which quits the app. Dvorak, QWERTZ and the
Nordic layouts move letters around too.

UCKeyTranslate (Carbon, through ctypes) tells what each keycode types on
the current ASCII-capable layout — the one macOS itself matches shortcuts
against, also while a Russian or Japanese input source is active. Every
failure falls back to the ANSI table: on a US layout the answer is the
same either way.

The Text Input Sources calls must run on the MAIN thread: recent macOS
versions abort the whole process (a dispatch_assert_queue trap, not an
exception) when they are made from any other. So the map is built and
refreshed (every few seconds, as the user may switch layouts) only when
asked on the main thread — the daemon primes it there at start — and
other threads, such as the injection worker, read the cached map.
"""

import ctypes
import logging
import threading
import time
from typing import Callable, Optional

logger = logging.getLogger(__name__)

# Keypad keycodes type digits and operators too; a chord means the main
# block, so they never win.
KEYPAD_CODES = frozenset({65, 67, 69, 71, 75, 76, 78, 81, 82, 83, 84, 85, 86, 87, 88, 89, 91, 92})
# The keycodes that type characters (0..50 is the main block; the rest are
# function, navigation and keypad keys).
CHARACTER_CODES = tuple(range(0, 51))
REFRESH_S = 5.0

_CARBON = "/System/Library/Frameworks/Carbon.framework/Carbon"
_CORE_FOUNDATION = "/System/Library/Frameworks/CoreFoundation.framework/CoreFoundation"
_KUC_KEY_ACTION_DISPLAY = 3
_KUC_NO_DEAD_KEYS = 1  # 1 << kUCKeyTranslateNoDeadKeysBit


def build_char_map(translate: Callable[[int], str]) -> dict[str, int]:
    """char -> keycode for every key that types one character with no
    modifier held. `translate(keycode)` returns what that key types ("" for
    nothing). The lowest keycode wins a tie; the keypad never does."""
    found: dict[str, int] = {}
    for code in CHARACTER_CODES:
        if code in KEYPAD_CODES:
            continue
        try:
            text = translate(code)
        except Exception:
            continue
        if not text or len(text) != 1 or not text.isprintable() or text.isspace():
            continue
        char = text.lower()
        found.setdefault(char, code)
    return found


def _carbon_translator() -> Optional[Callable[[int], str]]:  # pragma: no cover - macOS only
    """A translate(keycode) function over the current ASCII-capable keyboard
    layout, or None when Carbon can't be asked (not macOS, a layout without
    Unicode data)."""
    try:
        carbon = ctypes.cdll.LoadLibrary(_CARBON)
        cf = ctypes.cdll.LoadLibrary(_CORE_FOUNDATION)
    except OSError:
        return None
    c_void_p = ctypes.c_void_p
    for name in ("TISCopyCurrentASCIICapableKeyboardLayoutInputSource",
                 "TISCopyCurrentKeyboardLayoutInputSource"):
        getattr(carbon, name).restype = c_void_p
    carbon.TISGetInputSourceProperty.argtypes = [c_void_p, c_void_p]
    carbon.TISGetInputSourceProperty.restype = c_void_p
    carbon.LMGetKbdType.restype = ctypes.c_uint8
    carbon.UCKeyTranslate.argtypes = [
        c_void_p, ctypes.c_uint16, ctypes.c_uint16, ctypes.c_uint32, ctypes.c_uint32,
        ctypes.c_uint32, ctypes.POINTER(ctypes.c_uint32), ctypes.c_ulong,
        ctypes.POINTER(ctypes.c_ulong), ctypes.POINTER(ctypes.c_uint16),
    ]
    carbon.UCKeyTranslate.restype = ctypes.c_int32
    cf.CFDataGetBytePtr.argtypes = [c_void_p]
    cf.CFDataGetBytePtr.restype = c_void_p
    cf.CFDataGetLength.argtypes = [c_void_p]
    cf.CFDataGetLength.restype = ctypes.c_long
    cf.CFRelease.argtypes = [c_void_p]
    prop = c_void_p.in_dll(carbon, "kTISPropertyUnicodeKeyLayoutData")

    source = None
    data = None
    for name in ("TISCopyCurrentASCIICapableKeyboardLayoutInputSource",
                 "TISCopyCurrentKeyboardLayoutInputSource"):
        source = getattr(carbon, name)()
        if source:
            data = carbon.TISGetInputSourceProperty(source, prop)
            if data:
                break
            cf.CFRelease(source)
            source = None
    if not source or not data:
        return None
    try:
        # Copy the layout bytes: the CFData belongs to the input source,
        # which is released before the translator is used.
        length = int(cf.CFDataGetLength(data))
        layout = ctypes.create_string_buffer(ctypes.string_at(cf.CFDataGetBytePtr(data), length), length)
    finally:
        cf.CFRelease(source)
    keyboard_type = int(carbon.LMGetKbdType())

    def translate(code: int) -> str:
        dead = ctypes.c_uint32(0)
        out_len = ctypes.c_ulong(0)
        out = (ctypes.c_uint16 * 4)()
        status = carbon.UCKeyTranslate(
            ctypes.cast(layout, c_void_p), code, _KUC_KEY_ACTION_DISPLAY, 0, keyboard_type,
            _KUC_NO_DEAD_KEYS, ctypes.byref(dead), 4, ctypes.byref(out_len), out,
        )
        if status != 0 or out_len.value == 0:
            return ""
        return "".join(chr(unit) for unit in out[: out_len.value])

    return translate


class LayoutKeymap:
    """char -> keycode on the current layout, refreshed every REFRESH_S.
    `translator_factory` returns a translate(keycode) function or None; it
    is injectable for tests."""

    def __init__(
        self,
        translator_factory=_carbon_translator,
        *,
        clock=time.monotonic,
        on_main_thread=lambda: threading.current_thread() is threading.main_thread(),
    ):
        self._factory = translator_factory
        self._clock = clock
        self._on_main_thread = on_main_thread
        self._map: dict[str, int] = {}
        self._built_at: Optional[float] = None
        self._lock = threading.Lock()

    def prime(self) -> None:
        """Build the map now (call on the main thread)."""
        self._current()

    def _current(self) -> dict[str, int]:
        now = self._clock()
        with self._lock:
            stale = self._built_at is None or now - self._built_at >= REFRESH_S
            if stale and self._on_main_thread():
                self._built_at = now
                try:
                    translate = self._factory()
                    self._map = build_char_map(translate) if translate else {}
                except Exception:
                    logger.debug("keyboard layout lookup failed", exc_info=True)
                    self._map = {}
            return self._map

    def code_for(self, char: str) -> Optional[int]:
        """The keycode that types `char` (a single character) on the
        current layout, or None when the layout has no such key."""
        if len(char) != 1:
            return None
        return self._current().get(char.lower())


_default: Optional[LayoutKeymap] = None


def default_keymap() -> LayoutKeymap:
    global _default
    if _default is None:
        _default = LayoutKeymap()
    return _default
