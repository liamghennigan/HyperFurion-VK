"""Quartz keystroke injection (macOS).

CGEventKeyboardSetUnicodeString types arbitrary Unicode — accents, CJK,
emoji — which the Linux uinput backend cannot. Text is posted in small
chunks (the CGEvent Unicode buffer is bounded) with a breather between
posts so slow apps keep up.

Requires the hosting Python process to have Accessibility permission
(System Settings → Privacy & Security → Accessibility).
"""

import logging
import time

from voice_keyboard.injector import strip_line_breaks

logger = logging.getLogger(__name__)

# Practical per-event budget for CGEventKeyboardSetUnicodeString, counted
# in UTF-16 code units (the API's native unit).
CHUNK_UTF16_UNITS = 18

# Virtual keycode for the Delete (backspace) key on Apple keyboards.
KVK_DELETE = 51

# Named keys for press_combo (Carbon HIToolbox kVK_* values, ANSI layout):
# the modifiers, navigation and editing keys hands-free navigation needs,
# and the function keys. Letters, digits and punctuation come from the
# hotkey module's table.
MAC_KEY_NAMES = {
    "ctrl": 59, "control": 59, "leftctrl": 59, "rightctrl": 62,
    "shift": 56, "leftshift": 56, "rightshift": 60,
    "alt": 58, "option": 58, "leftalt": 58, "altgr": 61, "rightalt": 61,
    "super": 55, "meta": 55, "win": 55, "cmd": 55, "command": 55,
    "enter": 36, "return": 36, "kpenter": 76,
    "tab": 48, "esc": 53, "escape": 53, "space": 49, "spacebar": 49,
    "backspace": 51, "bksp": 51, "delete": 117, "del": 117,
    "up": 126, "down": 125, "left": 123, "right": 124,
    "home": 115, "end": 119, "pageup": 116, "pgup": 116, "pagedown": 121, "pgdn": 121,
    "capslock": 57, "insert": 114,
    "minus": 27, "equal": 24, "comma": 43, "period": 47, "dot": 47, "slash": 44,
    "backslash": 42, "semicolon": 41, "apostrophe": 39, "grave": 50,
    "f1": 122, "f2": 120, "f3": 99, "f4": 118, "f5": 96, "f6": 97, "f7": 98, "f8": 100,
    "f9": 101, "f10": 109, "f11": 103, "f12": 111,
}
# A chord's modifiers also travel as event flags: synthetic Quartz events
# are read by their flags, not by the modifier keys that preceded them.
MAC_MODIFIER_FLAGS = {
    59: 0x40000, 62: 0x40000,      # kCGEventFlagMaskControl
    56: 0x20000, 60: 0x20000,      # kCGEventFlagMaskShift
    58: 0x80000, 61: 0x80000,      # kCGEventFlagMaskAlternate
    55: 0x100000,                  # kCGEventFlagMaskCommand
}
KEY_NAMES = MAC_KEY_NAMES


def resolve_key(name: str) -> int:
    """A key name (or a single character) as a macOS virtual keycode."""
    from voice_keyboard.macos.hotkey import MAC_KEYCODES

    key = str(name).strip().lower()
    if key in MAC_KEY_NAMES:
        return MAC_KEY_NAMES[key]
    if key in MAC_KEYCODES:
        return MAC_KEYCODES[key]
    raise ValueError(f"unknown key {name!r}")


def _utf16_units(text: str) -> int:
    return len(text.encode("utf-16-le")) // 2


def chunk_text(text: str, budget: int = CHUNK_UTF16_UNITS) -> list[str]:
    """Split text into pieces of at most `budget` UTF-16 units, never
    splitting a surrogate pair (astral chars count as two units)."""
    chunks: list[str] = []
    current = ""
    used = 0
    for ch in text:
        units = _utf16_units(ch)
        if used + units > budget and current:
            chunks.append(current)
            current = ""
            used = 0
        current += ch
        used += units
    if current:
        chunks.append(current)
    return chunks



class MacTextInjector:
    """Drop-in for the Linux TextInjector: start() / stop() / type_text(),
    with the same suppress_enter contract."""

    def __init__(self):
        self._quartz = None
        # Interface parity with the Linux injector (the daemon sets it per
        # register); Unicode events never need the clipboard, so unused.
        self.paste_chord_shift = False
        # The intent channel and Kai's terminal route type commands but
        # must never run them: while set, a newline in the text becomes a
        # space — a typed "\n" is Return in Terminal.
        self.suppress_enter = False
        # A chat app: a line break is Shift+Return (plain Return sends).
        self.shift_newline = False

    def start(self) -> None:
        import Quartz  # pyobjc-framework-Quartz; darwin only

        self._quartz = Quartz
        logger.info("Quartz keyboard injector ready")

    def stop(self) -> None:
        self._quartz = None
        logger.info("Quartz keyboard injector released")

    def type_text(self, text: str) -> None:
        if self._quartz is None:
            raise RuntimeError("Injector not started")
        if self.suppress_enter:
            text = strip_line_breaks(text)
        elif self.shift_newline and "\n" in text:
            first, *rest = text.replace("\r\n", "\n").split("\n")
            self.type_text(first)
            for part in rest:
                self.press_combo(["shift", "return"])
                self.type_text(part)
            return
        q = self._quartz
        for chunk in chunk_text(text):
            units = _utf16_units(chunk)
            for is_down in (True, False):
                event = q.CGEventCreateKeyboardEvent(None, 0, is_down)
                q.CGEventKeyboardSetUnicodeString(event, units, chunk)
                q.CGEventPost(q.kCGHIDEventTap, event)
            time.sleep(0.005)

    def press_combo(self, names: list[str]) -> None:
        """Press a key chord (e.g. ['shift', 'alt', 'left']): modifiers go
        down in order, the key is posted with their flags set, then
        everything releases in reverse. Enter is NOT suppressed here (a
        combo is an explicit key request; the no-Enter guard is for
        dictated TEXT — and no navigation table ever names it)."""
        if self._quartz is None:
            raise RuntimeError("Injector not started")
        codes: list[int] = []
        for name in names:
            code = resolve_key(name)
            if code not in codes:
                codes.append(code)
        if not codes:
            return
        q = self._quartz
        flags = 0
        for code in codes:
            flags |= MAC_MODIFIER_FLAGS.get(code, 0)
            event = q.CGEventCreateKeyboardEvent(None, code, True)
            q.CGEventSetFlags(event, flags)
            q.CGEventPost(q.kCGHIDEventTap, event)
        for code in reversed(codes):
            # A modifier's key-up no longer carries its own flag, as a
            # hardware flagsChanged event would not.
            flags &= ~MAC_MODIFIER_FLAGS.get(code, 0)
            event = q.CGEventCreateKeyboardEvent(None, code, False)
            q.CGEventSetFlags(event, flags)
            q.CGEventPost(q.kCGHIDEventTap, event)
        time.sleep(0.005)

    def delete_chars(self, count: int) -> None:
        """Erase `count` characters before the caret via the Delete key."""
        if self._quartz is None:
            raise RuntimeError("Injector not started")
        q = self._quartz
        for index in range(max(0, count)):
            for is_down in (True, False):
                event = q.CGEventCreateKeyboardEvent(None, KVK_DELETE, is_down)
                q.CGEventPost(q.kCGHIDEventTap, event)
            if (index + 1) % 16 == 0:
                time.sleep(0.005)
        time.sleep(0.005)
