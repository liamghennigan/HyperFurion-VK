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


class WinTextInjector:
    """Drop-in for the Linux TextInjector: start() / stop() / type_text()."""

    def __init__(self):
        self._user32 = None

    def start(self) -> None:
        self._user32 = ctypes.WinDLL("user32", use_last_error=True)  # type: ignore[attr-defined]
        # Prototype SendInput so ctypes marshals the 64-bit pointer arg and
        # the return value without truncation.
        self._user32.SendInput.argtypes = [
            ctypes.c_uint, ctypes.POINTER(_INPUT), ctypes.c_int
        ]
        self._user32.SendInput.restype = ctypes.c_uint
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
                    event.ki = _KEYBDINPUT(0, unit, flags, 0, 0)
            sent = self._user32.SendInput(
                len(events), events, ctypes.sizeof(_INPUT)
            )
            if sent != len(events):
                logger.warning("SendInput delivered %d/%d events", sent, len(events))
            time.sleep(0.004)
