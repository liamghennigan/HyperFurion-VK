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

logger = logging.getLogger(__name__)

# Practical per-event budget for CGEventKeyboardSetUnicodeString, counted
# in UTF-16 code units (the API's native unit).
CHUNK_UTF16_UNITS = 18


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
    """Drop-in for the Linux TextInjector: start() / stop() / type_text()."""

    def __init__(self):
        self._quartz = None

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
        q = self._quartz
        for chunk in chunk_text(text):
            units = _utf16_units(chunk)
            for is_down in (True, False):
                event = q.CGEventCreateKeyboardEvent(None, 0, is_down)
                q.CGEventKeyboardSetUnicodeString(event, units, chunk)
                q.CGEventPost(q.kCGHIDEventTap, event)
            time.sleep(0.005)
