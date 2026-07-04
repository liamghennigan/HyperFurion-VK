"""Energy-based voice activity detection over 16-bit PCM chunks.

Pure stdlib (the array module parses int16 frames in C). Two consumers:
the overlay's live VU meter, and the optional auto-stop endpointer that
ends a recording after sustained silence — tap, speak, done.

The threshold adapts to the room: a slow noise-floor EMA tracks quiet
chunks, and speech must clear a multiple of that floor.
"""

import math
from array import array
from dataclasses import dataclass

_VU_GLYPHS = " ▁▂▃▄▅▆▇█"


def chunk_rms(chunk: bytes) -> float:
    """RMS level of an int16 mono PCM chunk, normalized to 0..1."""
    if len(chunk) < 2:
        return 0.0
    samples = array("h")
    samples.frombytes(chunk[: len(chunk) - (len(chunk) % 2)])
    if not samples:
        return 0.0
    acc = 0
    for sample in samples:
        acc += sample * sample
    return math.sqrt(acc / len(samples)) / 32768.0


def vu_glyph(level: float, *, gain: float = 12.0) -> str:
    """One block glyph for a 0..1 RMS level (speech RMS is small, so a
    gain factor spreads normal speaking volume across the glyph range)."""
    scaled = min(1.0, max(0.0, level * gain))
    index = min(len(_VU_GLYPHS) - 1, int(scaled * (len(_VU_GLYPHS) - 1) + 0.5))
    return _VU_GLYPHS[index]


def vu_bar(levels: list[float], *, width: int = 5) -> str:
    """A tiny bar-meter string from the most recent levels."""
    recent = levels[-width:]
    if not recent:
        return _VU_GLYPHS[0] * width
    pad = [0.0] * (width - len(recent))
    return "".join(vu_glyph(level) for level in pad + recent)


@dataclass
class SilenceGate:
    """Auto-stop endpointer. Feed (level, chunk_ms) per captured chunk;
    fires True once when speech has happened and then `auto_stop_ms` of
    silence has followed."""

    auto_stop_ms: int
    min_speech_ms: int = 240       # this much cumulative speech arms the gate
    floor: float = 0.004           # starting noise-floor estimate
    floor_alpha: float = 0.05      # EMA rate for the noise floor
    speech_ratio: float = 3.0      # speech must clear floor * ratio
    min_threshold: float = 0.010   # ...and this absolute minimum

    _speech_ms: float = 0.0
    _silence_ms: float = 0.0
    _fired: bool = False

    @property
    def armed(self) -> bool:
        return self._speech_ms >= self.min_speech_ms

    def feed(self, level: float, chunk_ms: float) -> bool:
        if self.auto_stop_ms <= 0 or self._fired:
            return False

        threshold = max(self.min_threshold, self.floor * self.speech_ratio)
        if level >= threshold:
            self._speech_ms += chunk_ms
            self._silence_ms = 0.0
        else:
            # Only quiet chunks teach the noise floor.
            self.floor += self.floor_alpha * (level - self.floor)
            if self.armed:
                self._silence_ms += chunk_ms
                if self._silence_ms >= self.auto_stop_ms:
                    self._fired = True
                    return True
        return False
