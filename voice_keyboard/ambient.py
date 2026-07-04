"""Ambient containment: only machine-addressed speech reaches the engine.

The blocker for always-on dictation is trust — a mishearing keyboard
types garbage into reality. Molten rendering already gives text a pending
state; this gate adds the missing addressing layer: an utterance types
ONLY when it starts with the address word ("furion write ..."). Room
speech never reaches the flow engine at all, so it can never be typed,
never committed, never remembered. Unaddressed audio evaporates at each
segment boundary.

EXPERIMENTAL, off by default ([ambient] enabled). This switch does not
start continuous background capture — sessions still begin explicitly
(hotkey / CLI) and the hotkey remains the hard mute. What it changes is
what a long-running session is allowed to type.

Known v0 quirk: the address word is consumed by the gate, so an in-stream
wake-word instruction needs the word twice ("furion furion, make that
formal").
"""

import logging

logger = logging.getLogger(__name__)


class AmbientGate:
    """A pure filter between the merged transcript and the flow engine.

    Feed it the full merged transcript on every update; it returns only
    the machine-addressed portion. Segments are bounded by is_final
    updates: each new utterance must address the machine again.
    """

    def __init__(self, address_word: str):
        self._address = (address_word or "").strip().casefold()
        self._kept = ""       # addressed text, finalized
        self._final_len = 0   # chars of the raw transcript already finalized

    @staticmethod
    def _split_lead(text: str) -> tuple[str, str]:
        parts = text.split(None, 1)
        head = parts[0].strip(".,!?;:").casefold() if parts else ""
        rest = parts[1] if len(parts) > 1 else ""
        return head, rest

    def filter(self, merged: str, *, is_final: bool) -> str:
        """The engine-visible transcript: kept text plus the current tail
        if (and only if) the tail addresses the machine."""
        tail = merged[self._final_len:].strip()
        head, rest = self._split_lead(tail)
        addressed = bool(self._address) and head == self._address

        if is_final:
            self._final_len = len(merged)
            if addressed and rest:
                self._kept = f"{self._kept} {rest}".strip()
            elif tail and not addressed:
                logger.debug("Ambient gate: unaddressed segment contained")
            return self._kept

        if addressed and rest:
            return f"{self._kept} {rest}".strip()
        return self._kept
