"""SendInput keystroke injection (Windows).

Text goes in as KEYEVENTF_UNICODE packets, one UTF-16 unit at a time —
surrogate pairs are delivered as consecutive units, which Windows
reassembles — so like macOS this backend beats the Linux ASCII limit and
never needs the clipboard. Line breaks and tabs are real Enter/Tab
keystrokes (a VK_PACKET newline means nothing to most apps), which is why
the no-Enter guard lives here too: while `suppress_enter` is set, no path
can emit Enter.

Hold-to-talk means the hotkey's modifiers can still be physically down
when molten typing starts, and Ctrl+Backspace deletes a whole word. So
every injection first releases any held modifier (behind an inert "mask"
keystroke, so a lone Alt/Win release can't pop the menu bar or Start).
No special privileges are required — but Windows (UIPI) silently drops
input aimed at an elevated window from a non-elevated process.
"""

import ctypes
import logging
import time

from voice_keyboard.injector import strip_line_breaks

logger = logging.getLogger(__name__)

INPUT_KEYBOARD = 1
KEYEVENTF_EXTENDEDKEY = 0x0001
KEYEVENTF_KEYUP = 0x0002
KEYEVENTF_UNICODE = 0x0004
MAPVK_VK_TO_VSC = 0

VK_BACK = 0x08
VK_TAB = 0x09
VK_RETURN = 0x0D
VK_LSHIFT = 0xA0
VK_RSHIFT = 0xA1
VK_LCONTROL = 0xA2
VK_RCONTROL = 0xA3
VK_LMENU = 0xA4
VK_RMENU = 0xA5
VK_LWIN = 0x5B
VK_RWIN = 0x5C
# Unassigned virtual key: pressing it does nothing, but it counts as "another
# key" so releasing Alt or Win afterwards doesn't activate a menu (the same
# trick AutoHotkey uses for its menu mask key).
VK_MASK = 0xE8

HELD_MODIFIERS = (
    VK_LCONTROL, VK_RCONTROL, VK_LMENU, VK_RMENU,
    VK_LWIN, VK_RWIN, VK_LSHIFT, VK_RSHIFT,
)
MENU_MODIFIERS = frozenset({VK_LMENU, VK_RMENU, VK_LWIN, VK_RWIN})

# Keys that live on the extended (E0-prefixed) scan set. Without the flag,
# arrows land as numpad keys and Right Ctrl as Left Ctrl.
EXTENDED_VKS = frozenset({
    VK_RCONTROL, VK_RMENU, VK_LWIN, VK_RWIN,
    0x21, 0x22, 0x23, 0x24,  # PageUp, PageDown, End, Home
    0x25, 0x26, 0x27, 0x28,  # Left, Up, Right, Down
    0x2C, 0x2D, 0x2E,        # PrintScreen, Insert, Delete
    0x5D, 0x6F, 0x90,        # Apps, Numpad /, NumLock
})

# Named keys for combo injection (the `key` IPC command / press_combo) —
# the same vocabulary as the Linux injector's KEY_NAMES. Values are
# (virtual key, extended?).
KEY_NAMES: dict[str, tuple[int, bool]] = {
    "ctrl": (VK_LCONTROL, False), "control": (VK_LCONTROL, False),
    "leftctrl": (VK_LCONTROL, False), "rightctrl": (VK_RCONTROL, True),
    "shift": (VK_LSHIFT, False), "leftshift": (VK_LSHIFT, False),
    "rightshift": (VK_RSHIFT, False),
    "alt": (VK_LMENU, False), "leftalt": (VK_LMENU, False), "option": (VK_LMENU, False),
    "altgr": (VK_RMENU, True), "rightalt": (VK_RMENU, True),
    "super": (VK_LWIN, True), "meta": (VK_LWIN, True), "win": (VK_LWIN, True),
    "cmd": (VK_LWIN, True), "command": (VK_LWIN, True),
    "enter": (VK_RETURN, False), "return": (VK_RETURN, False),
    "kpenter": (VK_RETURN, True),
    "tab": (VK_TAB, False), "esc": (0x1B, False), "escape": (0x1B, False),
    "space": (0x20, False), "spacebar": (0x20, False),
    "backspace": (VK_BACK, False), "bksp": (VK_BACK, False),
    "delete": (0x2E, True), "del": (0x2E, True), "insert": (0x2D, True),
    "up": (0x26, True), "down": (0x28, True), "left": (0x25, True), "right": (0x27, True),
    "home": (0x24, True), "end": (0x23, True),
    "pageup": (0x21, True), "pgup": (0x21, True),
    "pagedown": (0x22, True), "pgdn": (0x22, True),
    "capslock": (0x14, False),
    "minus": (0xBD, False), "equal": (0xBB, False), "comma": (0xBC, False),
    "period": (0xBE, False), "dot": (0xBE, False), "slash": (0xBF, False),
    "backslash": (0xDC, False), "semicolon": (0xBA, False),
    "apostrophe": (0xDE, False), "grave": (0xC0, False),
    **{f"f{n}": (0x6F + n, False) for n in range(1, 25)},
}

# Keystrokes per SendInput batch; a breather between batches keeps slow
# apps (and Electron's input queue) fed.
BATCH_KEYSTROKES = 16
BATCH_PAUSE_S = 0.004


# Fixed-width types matching the Win32 ABI. DWORD/WORD are always 32/16-bit
# on Windows regardless of interpreter, and ULONG_PTR is pointer-width — so
# the struct layout (and sizeof) is correct on Windows AND deterministic when
# checked on any 64-bit host.
DWORD = ctypes.c_uint32
WORD = ctypes.c_uint16
LONG = ctypes.c_int32
ULONG_PTR = ctypes.c_size_t


class _KEYBDINPUT(ctypes.Structure):
    _fields_ = [
        ("wVk", WORD),
        ("wScan", WORD),
        ("dwFlags", DWORD),
        ("time", DWORD),
        ("dwExtraInfo", ULONG_PTR),
    ]


class _MOUSEINPUT(ctypes.Structure):
    # Not used for typing, but it is the LARGEST member of the INPUT union,
    # so it must be present for sizeof(_INPUT) to match the OS's INPUT
    # (40 bytes on 64-bit). Without it SendInput rejects cbSize and types
    # nothing.
    _fields_ = [
        ("dx", LONG),
        ("dy", LONG),
        ("mouseData", DWORD),
        ("dwFlags", DWORD),
        ("time", DWORD),
        ("dwExtraInfo", ULONG_PTR),
    ]


class _INPUT(ctypes.Structure):
    class _U(ctypes.Union):
        _fields_ = [("ki", _KEYBDINPUT), ("mi", _MOUSEINPUT)]

    _anonymous_ = ("u",)
    _fields_ = [("type", DWORD), ("u", _U)]


def utf16_units(text: str) -> list[int]:
    """The UTF-16 code units of `text`, in order — what SendInput wants."""
    raw = text.encode("utf-16-le")
    return [int.from_bytes(raw[i : i + 2], "little") for i in range(0, len(raw), 2)]



# A keystroke is (vk, scan_or_unit, flags) for the key-down; the key-up is
# derived by adding KEYEVENTF_KEYUP.
Keystroke = tuple[int, int, int]


class WinTextInjector:
    """Drop-in for the Linux TextInjector: start() / stop() / type_text() /
    delete_chars() / press_combo(), with the same suppress_enter contract."""

    def __init__(self):
        self._user32 = None
        # Interface parity with the Linux injector (the daemon sets it per
        # register). Unicode packets never need the clipboard, so unused.
        self.paste_chord_shift = False
        # The intent channel types commands but must never run them: while
        # set, no newline in the text can become an Enter keystroke.
        self.suppress_enter = False
        self._warned_short_send = False

    def start(self) -> None:
        user32 = ctypes.WinDLL("user32", use_last_error=True)  # type: ignore[attr-defined]
        # Prototype every call so ctypes marshals 64-bit pointers and the
        # return values without truncation.
        user32.SendInput.argtypes = [ctypes.c_uint, ctypes.POINTER(_INPUT), ctypes.c_int]
        user32.SendInput.restype = ctypes.c_uint
        user32.GetAsyncKeyState.argtypes = [ctypes.c_int]
        user32.GetAsyncKeyState.restype = ctypes.c_short
        user32.MapVirtualKeyW.argtypes = [ctypes.c_uint, ctypes.c_uint]
        user32.MapVirtualKeyW.restype = ctypes.c_uint
        user32.VkKeyScanW.argtypes = [ctypes.c_wchar]
        user32.VkKeyScanW.restype = ctypes.c_short
        self._user32 = user32
        logger.info("SendInput keyboard injector ready")

    def stop(self) -> None:
        self._user32 = None
        logger.info("SendInput keyboard injector released")

    # ---------------------------------------------------------------- text

    def type_text(self, text: str) -> None:
        self._require_started()
        if self.suppress_enter:
            text = strip_line_breaks(text)
        else:
            text = text.replace("\r\n", "\n").replace("\r", "\n")
        strokes: list[Keystroke] = []
        for ch in text:
            if ch == "\n":
                strokes.append(self._vk_stroke(VK_RETURN))
            elif ch == "\t":
                strokes.append(self._vk_stroke(VK_TAB))
            else:
                for unit in utf16_units(ch):
                    strokes.append((0, unit, KEYEVENTF_UNICODE))
        self._send_strokes(strokes)

    def delete_chars(self, count: int) -> None:
        """Erase `count` characters before the caret via Backspace."""
        self._require_started()
        self._send_strokes([self._vk_stroke(VK_BACK)] * max(0, count))

    # ---------------------------------------------------------------- keys

    def _resolve_key(self, name: str) -> list[tuple[int, bool]]:
        """A key name -> the (vk, extended) keys to hold for it. Usually one
        key; a shifted punctuation character on this layout adds Shift."""
        key = str(name).strip().lower()
        if key in KEY_NAMES:
            return [KEY_NAMES[key]]
        if len(key) == 1:
            if key.isascii() and key.isalnum():
                return [(ord(key.upper()), False)]
            if self._user32 is not None:
                scan = int(self._user32.VkKeyScanW(key))
                if scan != -1 and (scan & 0xFF) != 0xFF:
                    vk, shift_state = scan & 0xFF, (scan >> 8) & 0xFF
                    if shift_state & ~1:
                        raise ValueError(f"key {name!r} needs Ctrl/Alt on this layout")
                    keys = [(vk, vk in EXTENDED_VKS)]
                    if shift_state & 1:
                        keys.insert(0, (VK_LSHIFT, False))
                    return keys
        raise ValueError(f"unknown key {name!r}")

    def press_combo(self, names: list[str]) -> None:
        """Press a key chord (e.g. ['ctrl','t'] or ['alt','Tab']): press each
        key down in order, release in reverse — so modifiers wrap the final
        key. Enter is NOT suppressed here (a combo is an explicit key
        request; the no-Enter guard is for dictated TEXT)."""
        self._require_started()
        keys: list[tuple[int, bool]] = []
        for name in names:
            for key in self._resolve_key(name):
                if key not in keys:
                    keys.append(key)
        if not keys:
            return
        events = self._release_held_modifiers()
        for vk, extended in keys:
            events.append(self._key_event(vk, extended, up=False))
        for vk, extended in reversed(keys):
            events.append(self._key_event(vk, extended, up=True))
        self._send(events)

    # ------------------------------------------------------------ plumbing

    def _require_started(self) -> None:
        if self._user32 is None:
            raise RuntimeError("Injector not started")

    def _scan_code(self, vk: int) -> int:
        try:
            return int(self._user32.MapVirtualKeyW(vk, MAPVK_VK_TO_VSC)) & 0xFFFF
        except Exception:
            return 0

    def _vk_stroke(self, vk: int) -> Keystroke:
        flags = KEYEVENTF_EXTENDEDKEY if vk in EXTENDED_VKS else 0
        return (vk, self._scan_code(vk), flags)

    def _key_event(self, vk: int, extended: bool, *, up: bool) -> Keystroke:
        flags = (KEYEVENTF_EXTENDEDKEY if extended else 0) | (KEYEVENTF_KEYUP if up else 0)
        return (vk, self._scan_code(vk), flags)

    def _release_held_modifiers(self) -> list[Keystroke]:
        """Key-ups for any modifier currently down (hold-to-talk leaves the
        hotkey's Ctrl/Alt held). Not restored afterwards: re-pressing a key
        the user has since let go of would leave it stuck."""
        held = []
        for vk in HELD_MODIFIERS:
            try:
                if int(self._user32.GetAsyncKeyState(vk)) & 0x8000:
                    held.append(vk)
            except Exception:
                return []
        if not held:
            return []
        events: list[Keystroke] = []
        if MENU_MODIFIERS.intersection(held):
            events.append((VK_MASK, 0, 0))
            events.append((VK_MASK, 0, KEYEVENTF_KEYUP))
        for vk in held:
            events.append(self._key_event(vk, vk in EXTENDED_VKS, up=True))
        return events

    def _send_strokes(self, strokes: list[Keystroke]) -> None:
        """Press+release each keystroke, in batches with a short pause."""
        if not strokes:
            return
        prefix = self._release_held_modifiers()
        for start in range(0, len(strokes), BATCH_KEYSTROKES):
            events = prefix
            prefix = []
            for vk, scan, flags in strokes[start : start + BATCH_KEYSTROKES]:
                events.append((vk, scan, flags))
                events.append((vk, scan, flags | KEYEVENTF_KEYUP))
            self._send(events)
            time.sleep(BATCH_PAUSE_S)

    def _send(self, events: list[Keystroke]) -> None:
        if not events:
            return
        array = (_INPUT * len(events))()
        for slot, (vk, scan, flags) in zip(array, events):
            slot.type = INPUT_KEYBOARD
            slot.ki = _KEYBDINPUT(vk, scan, flags, 0, 0)
        sent = self._user32.SendInput(len(events), array, ctypes.sizeof(_INPUT))
        if sent != len(events) and not self._warned_short_send:
            self._warned_short_send = True
            logger.warning(
                "SendInput delivered %d/%d events (secure desktop or an"
                " elevated window in front?)",
                sent,
                len(events),
            )
