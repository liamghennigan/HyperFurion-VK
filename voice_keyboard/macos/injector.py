"""Quartz keystroke injection (macOS).

CGEventKeyboardSetUnicodeString types arbitrary Unicode — accents, CJK,
emoji — which the Linux uinput backend cannot. Text is posted in small
chunks (the CGEvent Unicode buffer is bounded) with a breather between
posts so slow apps keep up.

A line break and a tab are pressed as the real Return and Tab keys, as on
Windows: a "\\n" smuggled inside a Unicode string means different things
to different apps (Terminal runs the line). That also makes the no-Enter
guard a rule about one key: while suppress_enter is set, Return is never
posted by any path of type_text.

Every event carries explicit modifier flags. Hold-to-talk keeps the
hotkey's Control and Option physically down while words land; without
explicit flags the typed text would inherit them and become shortcuts
(Option+Delete erases a whole word). Every event is also stamped with
INJECTED_EVENT_TAG so our own hotkey tap ignores it.

Chords resolve letters on the current keyboard layout (keylayout.py): the
Select All chord on AZERTY must press the key that types "a", which sits
where a US keyboard has Q.

Requires the process that runs HyperFurion VK to have Accessibility
permission (System Settings → Privacy & Security → Accessibility). Without
it macOS drops the events silently; start() says so.
"""

import logging
import time

from voice_keyboard.injector import strip_line_breaks
from voice_keyboard.macos.hotkey import INJECTED_EVENT_TAG

logger = logging.getLogger(__name__)

# Practical per-event budget for CGEventKeyboardSetUnicodeString, counted
# in UTF-16 code units (the API's native unit; it drops anything past 20).
CHUNK_UTF16_UNITS = 18

# Virtual keycodes (Carbon HIToolbox kVK_* values).
KVK_DELETE = 51          # Backspace ("delete" on Apple keyboards)
KVK_RETURN = 36
KVK_KEYPAD_ENTER = 76
KVK_TAB = 48
ENTER_CODES = frozenset({KVK_RETURN, KVK_KEYPAD_ENTER})
FLAG_SHIFT = 0x20000
_FIELD_SOURCE_USER_DATA = 42  # kCGEventSourceUserData

# Named keys for press_combo (Carbon HIToolbox kVK_* values, ANSI layout):
# the modifiers, navigation and editing keys hands-free navigation needs,
# and the function keys. Letters, digits and punctuation come from the
# current keyboard layout, else the hotkey module's ANSI table.
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


def resolve_key(name: str, layout=None) -> int:
    """A key name (or a single character) as a macOS virtual keycode. A
    character resolves on `layout` (a LayoutKeymap) first: the key that
    types it now, wherever the layout puts it."""
    from voice_keyboard.macos.hotkey import MAC_KEYCODES

    key = str(name).strip().lower()
    if key in MAC_KEY_NAMES:
        return MAC_KEY_NAMES[key]
    if len(key) == 1 and layout is not None:
        code = layout.code_for(key)
        if code is not None:
            return code
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


def split_keys(text: str) -> list[str]:
    """Text as runs to type and the single characters that are real keys:
    "a\\nb\\tc" -> ["a", "\\n", "b", "\\t", "c"]. CRLF and CR are one line
    break."""
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    parts: list[str] = []
    run = ""
    for ch in text:
        if ch in "\n\t":
            if run:
                parts.append(run)
                run = ""
            parts.append(ch)
        else:
            run += ch
    if run:
        parts.append(run)
    return parts


class MacTextInjector:
    """Drop-in for the Linux TextInjector: start() / stop() / type_text(),
    with the same suppress_enter contract."""

    def __init__(self, layout=None):
        self._quartz = None
        self._layout = layout
        # Interface parity with the Linux injector (the daemon sets it per
        # register); Unicode events never need the clipboard, so unused.
        self.paste_chord_shift = False
        # The intent channel and Kai's terminal route type commands but
        # must never run them: while set, a line break in the text becomes
        # a space and Return is never pressed.
        self.suppress_enter = False
        # A chat app: a line break is Shift+Return (plain Return sends).
        self.shift_newline = False

    def start(self) -> None:
        import Quartz  # pyobjc-framework-Quartz; darwin only

        self._quartz = Quartz
        if self._layout is None:
            from voice_keyboard.macos.keylayout import default_keymap

            self._layout = default_keymap()
        # The daemon starts the injector on its main thread: the only place
        # the layout may be read (chords are pressed from a worker thread).
        prime = getattr(self._layout, "prime", None)
        if callable(prime):
            try:
                prime()
            except Exception:
                logger.debug("keyboard layout lookup failed", exc_info=True)
        logger.info("Quartz keyboard injector ready")
        try:
            from voice_keyboard.macos.permissions import (
                accessibility_granted,
                notify_missing,
                prompt_accessibility,
                responsible_app,
            )

            if accessibility_granted() is False:
                logger.warning(
                    "No Accessibility permission: macOS will drop every keystroke."
                    " System Settings → Privacy & Security → Accessibility → turn on"
                    " %s, then restart HyperFurion VK.", responsible_app(),
                )
                # macOS's own prompt also puts the app in that list.
                prompt_accessibility()
                notify_missing("typing")
        except Exception:
            logger.debug("Accessibility check failed", exc_info=True)

    def stop(self) -> None:
        self._quartz = None
        logger.info("Quartz keyboard injector released")

    # ------------------------------------------------------------ events

    def _post(self, code: int, down: bool, flags: int = 0, text: str = "") -> None:
        q = self._quartz
        event = q.CGEventCreateKeyboardEvent(None, code, down)
        if text:
            q.CGEventKeyboardSetUnicodeString(event, _utf16_units(text), text)
        # Explicit flags: a modifier the user is physically holding (the
        # hold-to-talk chord) must not reach the typed text.
        q.CGEventSetFlags(event, flags)
        q.CGEventSetIntegerValueField(
            event, getattr(q, "kCGEventSourceUserData", _FIELD_SOURCE_USER_DATA), INJECTED_EVENT_TAG
        )
        q.CGEventPost(q.kCGHIDEventTap, event)

    def _tap_key(self, code: int, flags: int = 0) -> None:
        if self.suppress_enter and code in ENTER_CODES:
            logger.warning("no-Enter mode: refused to press Return")
            return
        self._post(code, True, flags)
        self._post(code, False, flags)

    def type_text(self, text: str) -> None:
        if self._quartz is None:
            raise RuntimeError("Injector not started")
        if self.suppress_enter:
            text = strip_line_breaks(text)
        for part in split_keys(text):
            if part == "\n":
                self._press_newline()
            elif part == "\t":
                self._tap_key(KVK_TAB)
            else:
                for chunk in chunk_text(part):
                    self._post(0, True, 0, chunk)
                    self._post(0, False, 0, chunk)
                    time.sleep(0.005)

    def _press_newline(self) -> None:
        """A dictated line break: Return, or Shift+Return in a chat app
        (where Return sends). Never while suppress_enter is set."""
        if self.suppress_enter:
            logger.warning("no-Enter mode: refused to press Return")
            return
        self._tap_key(KVK_RETURN, FLAG_SHIFT if self.shift_newline else 0)
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
            code = resolve_key(name, self._layout)
            if code not in codes:
                codes.append(code)
        if not codes:
            return
        flags = 0
        for code in codes:
            flags |= MAC_MODIFIER_FLAGS.get(code, 0)
            self._post(code, True, flags)
        for code in reversed(codes):
            # A modifier's key-up no longer carries its own flag, as a
            # hardware flagsChanged event would not.
            flags &= ~MAC_MODIFIER_FLAGS.get(code, 0)
            self._post(code, False, flags)
        time.sleep(0.005)

    def delete_chars(self, count: int) -> None:
        """Erase `count` characters before the caret via the Delete key."""
        if self._quartz is None:
            raise RuntimeError("Injector not started")
        for index in range(max(0, count)):
            self._tap_key(KVK_DELETE)
            if (index + 1) % 16 == 0:
                time.sleep(0.005)
        time.sleep(0.005)
