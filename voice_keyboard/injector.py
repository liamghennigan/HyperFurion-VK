import logging
import time
from typing import Optional

from evdev import UInput, ecodes as e

logger = logging.getLogger(__name__)

SHIFT_MAP = {
    "!": "1", "@": "2", "#": "3", "$": "4", "%": "5",
    "^": "6", "&": "7", "*": "8", "(": "9", ")": "0",
    "_": "-", "+": "=", "{": "[", "}": "]", "|": "\\",
    ":": ";", '"': "'", "<": ",", ">": ".", "?": "/",
    "~": "`",
}

CHAR_TO_KEY = {
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


class TextInjector:
    def __init__(self):
        self._ui: Optional[UInput] = None

    def start(self) -> None:
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

    def type_text(self, text: str) -> None:
        for ch in text:
            if ch.isupper():
                lower = ch.lower()
                if lower in CHAR_TO_KEY:
                    self._press_key(CHAR_TO_KEY[lower], shift=True)
                else:
                    logger.warning("Unsupported character: %r", ch)
            elif ch in SHIFT_MAP:
                base = SHIFT_MAP[ch]
                if base in CHAR_TO_KEY:
                    self._press_key(CHAR_TO_KEY[base], shift=True)
                else:
                    logger.warning("Unsupported character: %r", ch)
            elif ch in CHAR_TO_KEY:
                self._press_key(CHAR_TO_KEY[ch])
            else:
                logger.warning("Unsupported character: %r", ch)
