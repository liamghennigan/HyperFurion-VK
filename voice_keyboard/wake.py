"""Wake word: summon Kai hands-free by saying her name.

openWakeWord runs a tiny local model over a rolling mic buffer — no
transcription, nothing leaves the box — and fires a callback the instant it
hears the wake word. STT and the network engage only AFTER the fire, when
the normal converse capture begins.

Opt-in and default OFF. This is the one summon path that keeps the mic warm,
so it lives behind an explicit switch: the hotkey stays the hard mute.
"""

import logging
import threading
import time
from typing import Callable, Optional

from voice_keyboard.audio_capture import AudioCapture

logger = logging.getLogger(__name__)

WAKE_SAMPLE_RATE = 16000
# openWakeWord expects 80 ms frames (1280 samples @ 16 kHz).
WAKE_CHUNK_MS = 80


def wake_enabled(config: dict) -> bool:
    return bool(config.get("wake", {}).get("enabled", False))


class WakeListener:
    """Daemon thread: score the mic for the wake word, fire on_wake."""

    def __init__(
        self,
        *,
        config: dict,
        on_wake: Callable[[], None],
        is_busy: Callable[[], bool],
    ):
        wake_cfg = config.get("wake", {})
        self._word = str(wake_cfg.get("word", "kai")).strip().lower() or "kai"
        self._model_path = str(wake_cfg.get("model_path", "")).strip()
        self._threshold = float(wake_cfg.get("threshold", 0.5))
        self._cooldown_s = float(wake_cfg.get("cooldown_s", 2.0))
        self._device = str(wake_cfg.get("mic_device", "")).strip() or "default"
        self._on_wake = on_wake
        self._is_busy = is_busy
        self._model = None
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._last_fire = 0.0

    def _load_model(self):
        from openwakeword.model import Model  # lazy: optional [wake] extra

        if self._model_path:
            return Model(wakeword_models=[self._model_path])
        # No custom "Kai" model yet — fall back to openWakeWord's bundled
        # pretrained words so wake can be exercised before training. Loud so
        # it is never mistaken for a real "Kai" detector.
        logger.warning(
            "wake: no [wake] model_path set — using openWakeWord's bundled "
            "models. Train a %r model with scripts/train_kai_wakeword.py.",
            self._word,
        )
        return Model()

    def start(self) -> None:
        try:
            self._model = self._load_model()
        except Exception:
            logger.exception(
                "wake: could not load openWakeWord — is it installed? "
                "(pip install 'hyperfurion-vk[wake]')"
            )
            return
        self._thread = threading.Thread(target=self._run, name="vk-wake", daemon=True)
        self._thread.start()
        logger.info(
            "Wake word listening for %r (threshold %.2f)", self._word, self._threshold
        )

    def stop(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=2.0)
            self._thread = None

    def _ready(self, now: float) -> bool:
        return (now - self._last_fire) >= self._cooldown_s

    def score(self, samples) -> float:
        """Highest wake-word confidence for a frame (0..1). Prefers a model
        whose name matches the wake word; else the max over all heads."""
        preds = self._model.predict(samples)
        if not preds:
            return 0.0
        for name, val in preds.items():
            if self._word in str(name).lower():
                return float(val)
        return float(max(preds.values()))

    def _run(self) -> None:
        import numpy as np

        capture = AudioCapture(
            sample_rate=WAKE_SAMPLE_RATE,
            chunk_ms=WAKE_CHUNK_MS,
            device_name=self._device,
        )
        try:
            capture.start()
        except Exception:
            logger.exception("wake: could not open the microphone")
            return
        try:
            while not self._stop.is_set():
                try:
                    chunk = capture.read_chunk()
                except Exception:
                    break
                if not chunk or self._is_busy():
                    continue  # never wake into a live session
                samples = np.frombuffer(chunk, dtype=np.int16)
                now = time.monotonic()
                if self.score(samples) >= self._threshold and self._ready(now):
                    self._last_fire = now
                    logger.info("Wake: %r", self._word)
                    try:
                        self._on_wake()
                    except Exception:
                        logger.exception("wake: on_wake callback failed")
        finally:
            try:
                capture.stop()
            except Exception:
                pass
