"""Earcons: two short generated tones — the eyes-free "Kai is listening /
got it" cue.

A voice UI's biggest smoothness win is knowing, without looking, the exact
moment the mic goes live and the moment your words are captured. These tones
are synthesized in-code (no asset, no network) and played on a throwaway
thread so the summon path never blocks or fails on them.
"""

import logging
import threading

logger = logging.getLogger(__name__)

SAMPLE_RATE = 44100


def _tone(freq_start: float, freq_end: float, ms: int, volume: float = 0.22):
    """A short glide from freq_start to freq_end with click-free fades."""
    import numpy as np

    n = max(1, int(SAMPLE_RATE * ms / 1000))
    t = np.linspace(0.0, ms / 1000.0, n, endpoint=False)
    freq = np.linspace(freq_start, freq_end, n)
    wave = np.sin(2 * np.pi * freq * t)
    fade = min(int(SAMPLE_RATE * 0.008), n // 2) or 1
    env = np.ones(n)
    env[:fade] = np.linspace(0.0, 1.0, fade)
    env[-fade:] = np.linspace(1.0, 0.0, fade)
    return (wave * env * volume).astype("float32")


def play_earcon(kind: str) -> None:
    """Play the "listen" (rising) or "captured" (falling) cue, off-thread."""

    def _run() -> None:
        try:
            import sounddevice as sd

            if kind == "listen":
                data = _tone(660.0, 990.0, 90)
            else:
                data = _tone(880.0, 590.0, 110)
            sd.play(data, SAMPLE_RATE)
            sd.wait()
        except Exception as exc:  # missing device, no numpy/sounddevice, etc.
            logger.debug("earcon playback failed: %s", exc)

    threading.Thread(target=_run, name="vk-earcon", daemon=True).start()
