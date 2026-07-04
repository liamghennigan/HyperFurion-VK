import logging
import sys
import time
from typing import Optional

try:
    from evdev import UInput, ecodes as e
except ImportError:  # non-Linux: create_injector() picks the mac backend
    UInput = None
    e = None

from voice_keyboard import clipboard

logger = logging.getLogger(__name__)

SHIFT_MAP = {
    "!": "1", "@": "2", "#": "3", "$": "4", "%": "5",
    "^": "6", "&": "7", "*": "8", "(": "9", ")": "0",
    "_": "-", "+": "=", "{": "[", "}": "]", "|": "\\",
    ":": ";", '"': "'", "<": ",", ">": ".", "?": "/",
    "~": "`",
}

CHAR_TO_KEY = {} if e is None else {
    "a": e.KEY_A, "b": e.KEY_B, "c": e.KEY_C, "d": e.KEY_D,
    "e": e.KEY_E, "f": e.KEY_F, "g": e.KEY_G, "h": e.KEY_H,
    "i": e.KEY_I, "j": e.KEY_J, "k": e.KEY_K, "l": e.KEY_L,
    "m": e.KEY_M, "n": e.KEY_N, "o": e.KEY_O, "p": e.KEY_P,
    "q": e.KEY_Q, "r": e.KEY_R, "s": e.KEY_S, "t": e.KEY_T,
    "u": e.KEY_U, "v": e.KEY_V, "w": e.KEY_W, "x": e.KEY_X,
    "y": e.KEY_Y, "z": e.KEY_Z,
    "0": e.KEY_0, "1": e.KEY_1, "2": e.KEY_2, "3": e.KEY_3,
    "4": e.KEY_4, "5": e.KEY_5, "6": e.KEY_6, "7": e.KEY_7,
    "8": e.KEY_8, "9": e.KEY_9,
    " ": e.KEY_SPACE, "-": e.KEY_MINUS, "=": e.KEY_EQUAL,
    "[": e.KEY_LEFTBRACE, "]": e.KEY_RIGHTBRACE,
    "\\": e.KEY_BACKSLASH, ";": e.KEY_SEMICOLON,
    "'": e.KEY_APOSTROPHE, ",": e.KEY_COMMA, ".": e.KEY_DOT,
    "/": e.KEY_SLASH, "`": e.KEY_GRAVE,
    "\n": e.KEY_ENTER, "\t": e.KEY_TAB,
}

# Named keys for combo injection (the `key` IPC command / press_combo). Modifiers
# + navigation + function keys an integrator (e.g. a computer-use agent) needs to
# drive apps — open a tab, focus the address bar, Tab through a form, arrow around.
# Letters/digits/punctuation fall through to CHAR_TO_KEY.
KEY_NAMES = {} if e is None else {
    "ctrl": e.KEY_LEFTCTRL, "control": e.KEY_LEFTCTRL, "leftctrl": e.KEY_LEFTCTRL,
    "rightctrl": e.KEY_RIGHTCTRL,
    "shift": e.KEY_LEFTSHIFT, "leftshift": e.KEY_LEFTSHIFT, "rightshift": e.KEY_RIGHTSHIFT,
    "alt": e.KEY_LEFTALT, "leftalt": e.KEY_LEFTALT, "altgr": e.KEY_RIGHTALT,
    "rightalt": e.KEY_RIGHTALT, "option": e.KEY_LEFTALT,
    "super": e.KEY_LEFTMETA, "meta": e.KEY_LEFTMETA, "win": e.KEY_LEFTMETA,
    "cmd": e.KEY_LEFTMETA, "command": e.KEY_LEFTMETA,
    "enter": e.KEY_ENTER, "return": e.KEY_ENTER, "kpenter": e.KEY_KPENTER,
    "tab": e.KEY_TAB, "esc": e.KEY_ESC, "escape": e.KEY_ESC,
    "space": e.KEY_SPACE, "spacebar": e.KEY_SPACE,
    "backspace": e.KEY_BACKSPACE, "bksp": e.KEY_BACKSPACE,
    "delete": e.KEY_DELETE, "del": e.KEY_DELETE, "insert": e.KEY_INSERT,
    "up": e.KEY_UP, "down": e.KEY_DOWN, "left": e.KEY_LEFT, "right": e.KEY_RIGHT,
    "home": e.KEY_HOME, "end": e.KEY_END,
    "pageup": e.KEY_PAGEUP, "pgup": e.KEY_PAGEUP,
    "pagedown": e.KEY_PAGEDOWN, "pgdn": e.KEY_PAGEDOWN,
    "capslock": e.KEY_CAPSLOCK,
    "minus": e.KEY_MINUS, "equal": e.KEY_EQUAL, "comma": e.KEY_COMMA,
    "period": e.KEY_DOT, "dot": e.KEY_DOT, "slash": e.KEY_SLASH,
    "backslash": e.KEY_BACKSLASH, "semicolon": e.KEY_SEMICOLON,
    "apostrophe": e.KEY_APOSTROPHE, "grave": e.KEY_GRAVE,
    "f1": e.KEY_F1, "f2": e.KEY_F2, "f3": e.KEY_F3, "f4": e.KEY_F4,
    "f5": e.KEY_F5, "f6": e.KEY_F6, "f7": e.KEY_F7, "f8": e.KEY_F8,
    "f9": e.KEY_F9, "f10": e.KEY_F10, "f11": e.KEY_F11, "f12": e.KEY_F12,
}

# How long the focused app gets to read the clipboard after the paste
# chord before the previous clipboard contents are restored.
PASTE_SETTLE_S = 0.15


def strip_line_breaks(text: str) -> str:
    """Collapse newlines/carriage returns to spaces — nothing that could
    submit a command may reach the keycode or paste paths in no-Enter mode."""
    return text.replace("\r\n", " ").replace("\r", " ").replace("\n", " ")


def create_injector():
    """Platform factory: uinput on Linux, Quartz on macOS, SendInput on Windows."""
    if sys.platform == "darwin":
        from voice_keyboard.macos.injector import MacTextInjector

        return MacTextInjector()
    if sys.platform == "win32":
        from voice_keyboard.windows.injector import WinTextInjector

        return WinTextInjector()
    return TextInjector()


def _keyable(ch: str) -> bool:
    """True when the uinput key map can type `ch` directly."""
    if ch.isupper():
        return ch.lower() in CHAR_TO_KEY
    return ch in CHAR_TO_KEY or (ch in SHIFT_MAP and SHIFT_MAP[ch] in CHAR_TO_KEY)


def _split_runs(text: str) -> list[tuple[bool, str]]:
    """Partition text into (keyable, run) segments."""
    runs: list[tuple[bool, str]] = []
    for ch in text:
        keyable = _keyable(ch)
        if runs and runs[-1][0] == keyable:
            runs[-1] = (keyable, runs[-1][1] + ch)
        else:
            runs.append((keyable, ch))
    return runs


class TextInjector:
    def __init__(self):
        self._ui: Optional["UInput"] = None
        # Terminals paste with ctrl+shift+v; the daemon sets this per
        # session from the resolved register.
        self.paste_chord_shift = False
        # The intent channel types commands but must never run them: while
        # set, Enter cannot be emitted by ANY path — keycode, newline in
        # text, or newline smuggled through a clipboard paste. Pressing
        # Enter stays a human act.
        self.suppress_enter = False
        self._warned_no_clipboard = False

    def start(self) -> None:
        if UInput is None:
            raise RuntimeError(
                "uinput injection is Linux-only; use create_injector() to get"
                " the platform backend"
            )
        caps = {
            e.EV_KEY: [
                e.KEY_A, e.KEY_B, e.KEY_C, e.KEY_D, e.KEY_E,
                e.KEY_F, e.KEY_G, e.KEY_H, e.KEY_I, e.KEY_J,
                e.KEY_K, e.KEY_L, e.KEY_M, e.KEY_N, e.KEY_O,
                e.KEY_P, e.KEY_Q, e.KEY_R, e.KEY_S, e.KEY_T,
                e.KEY_U, e.KEY_V, e.KEY_W, e.KEY_X, e.KEY_Y,
                e.KEY_Z,
                e.KEY_0, e.KEY_1, e.KEY_2, e.KEY_3, e.KEY_4,
                e.KEY_5, e.KEY_6, e.KEY_7, e.KEY_8, e.KEY_9,
                e.KEY_SPACE, e.KEY_MINUS, e.KEY_EQUAL,
                e.KEY_LEFTBRACE, e.KEY_RIGHTBRACE,
                e.KEY_BACKSLASH, e.KEY_SEMICOLON,
                e.KEY_APOSTROPHE, e.KEY_COMMA, e.KEY_DOT,
                e.KEY_SLASH, e.KEY_GRAVE,
                e.KEY_ENTER, e.KEY_TAB,
                e.KEY_LEFTSHIFT, e.KEY_RIGHTSHIFT,
                e.KEY_BACKSPACE, e.KEY_LEFTCTRL,
                # Extended set for combo injection (press_combo / the `key` IPC
                # command) — modifiers, navigation, editing, function keys.
                e.KEY_RIGHTCTRL, e.KEY_LEFTALT, e.KEY_RIGHTALT,
                e.KEY_LEFTMETA, e.KEY_RIGHTMETA, e.KEY_ESC, e.KEY_CAPSLOCK,
                e.KEY_KPENTER, e.KEY_DELETE, e.KEY_INSERT,
                e.KEY_UP, e.KEY_DOWN, e.KEY_LEFT, e.KEY_RIGHT,
                e.KEY_HOME, e.KEY_END, e.KEY_PAGEUP, e.KEY_PAGEDOWN,
                e.KEY_F1, e.KEY_F2, e.KEY_F3, e.KEY_F4, e.KEY_F5, e.KEY_F6,
                e.KEY_F7, e.KEY_F8, e.KEY_F9, e.KEY_F10, e.KEY_F11, e.KEY_F12,
            ],
        }
        self._ui = UInput(caps, name="voice-keyboard", version=0x1)
        logger.info("UInput virtual keyboard created")

    def stop(self) -> None:
        if self._ui is not None:
            self._ui.close()
            self._ui = None
            logger.info("UInput virtual keyboard closed")

    def _press_key(self, code: int, shift: bool = False) -> None:
        if self._ui is None:
            raise RuntimeError("Injector not started")
        if self.suppress_enter and code == e.KEY_ENTER:
            logger.warning("no-Enter mode: refused to press Enter")
            return
        if shift:
            self._ui.write(e.EV_KEY, e.KEY_LEFTSHIFT, 1)
            self._ui.syn()
        self._ui.write(e.EV_KEY, code, 1)
        self._ui.syn()
        time.sleep(0.002)
        self._ui.write(e.EV_KEY, code, 0)
        self._ui.syn()
        if shift:
            self._ui.write(e.EV_KEY, e.KEY_LEFTSHIFT, 0)
            self._ui.syn()
        time.sleep(0.002)

    def _resolve_key(self, name: str) -> int:
        key = str(name).strip().lower()
        if key in KEY_NAMES:
            return KEY_NAMES[key]
        if len(key) == 1 and key in CHAR_TO_KEY:
            return CHAR_TO_KEY[key]
        raise ValueError(f"unknown key {name!r}")

    def press_combo(self, names: list[str]) -> None:
        """Press a key chord (e.g. ['ctrl','t'] or ['alt','Tab']): press each key
        down in order, release in reverse — so modifiers wrap the terminal key.
        Enter is NOT suppressed here (a combo is an explicit key request; the
        no-Enter guard is for dictated TEXT, not integrator key events)."""
        if self._ui is None:
            raise RuntimeError("Injector not started")
        codes = [self._resolve_key(n) for n in names]
        if not codes:
            return
        for code in codes:
            self._ui.write(e.EV_KEY, code, 1)
            self._ui.syn()
            time.sleep(0.002)
        for code in reversed(codes):
            self._ui.write(e.EV_KEY, code, 0)
            self._ui.syn()
            time.sleep(0.002)

    def type_text(self, text: str) -> None:
        if self.suppress_enter:
            text = strip_line_breaks(text)
        for keyable, run in _split_runs(text):
            if keyable:
                self._type_keyable(run)
            else:
                self._paste_text(run)

    def delete_chars(self, count: int) -> None:
        """Erase `count` characters before the caret via Backspace."""
        for _ in range(max(0, count)):
            self._press_key(e.KEY_BACKSPACE)

    def _type_keyable(self, text: str) -> None:
        for ch in text:
            if ch.isupper():
                self._press_key(CHAR_TO_KEY[ch.lower()], shift=True)
            elif ch in SHIFT_MAP:
                self._press_key(CHAR_TO_KEY[SHIFT_MAP[ch]], shift=True)
            else:
                self._press_key(CHAR_TO_KEY[ch])

    def _paste_text(self, text: str) -> None:
        """Type beyond the uinput key map by pasting: put the run on the
        clipboard, press the paste chord, then restore the clipboard."""
        if self.suppress_enter:
            # Defense in depth: terminals execute pasted newlines.
            text = strip_line_breaks(text)
            if not text:
                return
        if not clipboard.available():
            if not self._warned_no_clipboard:
                self._warned_no_clipboard = True
                logger.warning(
                    "Unsupported characters need a clipboard tool for paste"
                    " injection; install wl-clipboard (Wayland) or xclip (X11)"
                )
            logger.warning("Unsupported characters dropped: %r", text)
            return

        previous = clipboard.get_text()
        if not clipboard.set_text(text):
            logger.warning("Unsupported characters dropped (clipboard write failed): %r", text)
            return
        try:
            self._press_paste_chord()
            # Give the focused app time to read the selection before the
            # previous clipboard contents come back.
            time.sleep(PASTE_SETTLE_S)
        finally:
            if previous is not None:
                clipboard.set_text(previous)

    def _press_paste_chord(self) -> None:
        if self._ui is None:
            raise RuntimeError("Injector not started")
        self._ui.write(e.EV_KEY, e.KEY_LEFTCTRL, 1)
        if self.paste_chord_shift:
            self._ui.write(e.EV_KEY, e.KEY_LEFTSHIFT, 1)
        self._ui.syn()
        time.sleep(0.002)
        self._ui.write(e.EV_KEY, e.KEY_V, 1)
        self._ui.syn()
        time.sleep(0.002)
        self._ui.write(e.EV_KEY, e.KEY_V, 0)
        self._ui.syn()
        time.sleep(0.002)
        if self.paste_chord_shift:
            self._ui.write(e.EV_KEY, e.KEY_LEFTSHIFT, 0)
        self._ui.write(e.EV_KEY, e.KEY_LEFTCTRL, 0)
        self._ui.syn()
        time.sleep(0.002)
