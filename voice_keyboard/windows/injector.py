"""SendInput keystroke injection (Windows).

KEYEVENTF_UNICODE types arbitrary Unicode one UTF-16 unit at a time —
surrogate pairs are delivered as consecutive units, which Windows
reassembles — so like macOS this backend beats the Linux ASCII limit.
No special privileges are required.
"""

import ctypes
import logging
import time

logger = logging.getLogger(__name__)

INPUT_KEYBOARD = 1
KEYEVENTF_UNICODE = 0x0004
KEYEVENTF_KEYUP = 0x0002

# Units per SendInput batch; a breather between batches keeps slow apps fed.
BATCH_UTF16_UNITS = 16


class _KEYBDINPUT(ctypes.Structure):
    _fields_ = [
        ("wVk", ctypes.c_ushort),
        ("wScan", ctypes.c_ushort),
        ("dwFlags", ctypes.c_ulong),
        ("time", ctypes.c_ulong),
        ("dwExtraInfo", ctypes.POINTER(ctypes.c_ulong)),
    ]


class _INPUT(ctypes.Structure):
    class _U(ctypes.Union):
        _fields_ = [("ki", _KEYBDINPUT)]

    _anonymous_ = ("u",)
    _fields_ = [("type", ctypes.c_ulong), ("u", _U)]


def utf16_units(text: str) -> list[int]:
    """The UTF-16 code units of `text`, in order — what SendInput wants."""
    raw = text.encode("utf-16-le")
    return [int.from_bytes(raw[i : i + 2], "little") for i in range(0, len(raw), 2)]


class WinTextInjector:
    """Drop-in for the Linux TextInjector: start() / stop() / type_text()."""

    def __init__(self):
        self._user32 = None

    def start(self) -> None:
        self._user32 = ctypes.WinDLL("user32", use_last_error=True)  # type: ignore[attr-defined]
        logger.info("SendInput keyboard injector ready")

    def stop(self) -> None:
        self._user32 = None
        logger.info("SendInput keyboard injector released")

    def type_text(self, text: str) -> None:
        if self._user32 is None:
            raise RuntimeError("Injector not started")
        units = utf16_units(text)
        for start in range(0, len(units), BATCH_UTF16_UNITS):
            batch = units[start : start + BATCH_UTF16_UNITS]
            events = (_INPUT * (len(batch) * 2))()
            for i, unit in enumerate(batch):
                for j, flags in enumerate((KEYEVENTF_UNICODE, KEYEVENTF_UNICODE | KEYEVENTF_KEYUP)):
                    event = events[i * 2 + j]
                    event.type = INPUT_KEYBOARD
                    event.ki = _KEYBDINPUT(0, unit, flags, 0, None)
            sent = self._user32.SendInput(
                len(events), events, ctypes.sizeof(_INPUT)
            )
            if sent != len(events):
                logger.warning("SendInput delivered %d/%d events", sent, len(events))
            time.sleep(0.004)
