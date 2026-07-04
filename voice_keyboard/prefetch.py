"""Speculative TTS: synthesize the selection while it is being made.

Reading a selection aloud pays the full synthesis round-trip after the
user asks — but the drag gesture telegraphs intent seconds earlier, and
the selection already IS the payload. When enabled, a watcher follows
the primary selection; once it holds still, the audio is synthesized
into a one-entry cache, and `voice-keyboard tts` plays it instantly on
a text match.

Cost-gated like flow.live_rest: "auto" prefetches only against a LOCAL
TTS endpoint (re-synthesis is free there); "always" opts in cloud
providers — which spends tokens on selections that are never played and
sends selection text to the provider BEFORE you ask; "off" (default)
changes nothing.
"""

import logging
import threading
import time
from typing import Callable, Optional

from voice_keyboard import clipboard

logger = logging.getLogger(__name__)

POLL_S = 0.4
STABLE_S = 0.8
MAX_PREFETCH_CHARS = 800
FAILURE_BACKOFF_S = 30.0


def prefetch_enabled(config: dict) -> bool:
    """Resolve [tts] prefetch: always | auto (local endpoint only) | off."""
    tts_cfg = config.get("tts", {})
    mode = str(tts_cfg.get("prefetch", "off")).strip().lower()
    if mode == "always":
        return True
    if mode != "auto":
        return False
    if str(tts_cfg.get("provider", "xai")).lower() != "openai":
        return False
    base_url = str(
        config.get("providers", {}).get("openai", {}).get("base_url", "")
    ).strip()
    if not base_url:
        return False
    from voice_keyboard.config import _is_local_endpoint  # lazy: avoid cycle

    return _is_local_endpoint(base_url)


class PrefetchGate:
    """Pure stability gate: fire a selection once it has held still.

    Feed it the current primary selection on every poll; it returns the
    text exactly once when the selection has been non-empty and unchanged
    for `stable_s` — mid-drag churn never fires, and a selection never
    synthesizes twice.
    """

    def __init__(
        self,
        stable_s: float = STABLE_S,
        max_chars: int = MAX_PREFETCH_CHARS,
    ):
        self._stable_s = stable_s
        self._max_chars = max_chars
        self._candidate = ""
        self._since = 0.0
        self._fired = ""

    def feed(self, text: Optional[str], now: float) -> Optional[str]:
        text = (text or "").strip()
        if not text:
            self._candidate = ""
            return None
        if text == self._fired:
            return None
        if text != self._candidate:
            self._candidate = text
            self._since = now
            return None
        if now - self._since < self._stable_s:
            return None
        # Mark fired before the size check so an oversize selection does
        # not re-trigger on every poll.
        self._fired = text
        if len(text) > self._max_chars:
            logger.debug("Selection too long to prefetch (%d chars)", len(text))
            return None
        return text


class SelectionWatcher:
    """Daemon thread: poll the primary selection, prefetch stable ones."""

    def __init__(
        self,
        *,
        tts_client,
        store: Callable[[str, bytes], None],
        is_busy: Callable[[], bool],
        gate: Optional[PrefetchGate] = None,
    ):
        self._tts = tts_client
        self._store = store
        self._busy = is_busy
        self._gate = gate or PrefetchGate()
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._backoff_until = 0.0

    def start(self) -> None:
        if not clipboard.available():
            logger.warning("TTS prefetch enabled but no clipboard tool found")
            return
        self._thread = threading.Thread(
            target=self._run, name="voice-keyboard-tts-prefetch", daemon=True
        )
        self._thread.start()
        logger.info("TTS prefetch watcher started")

    def stop(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=2.0)
            self._thread = None

    def step(self, now: float) -> None:
        """One poll iteration; separated from the loop for testability."""
        if self._busy() or now < self._backoff_until:
            return
        try:
            text = clipboard.get_primary_text()
        except Exception:
            return
        to_synthesize = self._gate.feed(text, now)
        if not to_synthesize:
            return
        try:
            audio = self._tts.synthesize(to_synthesize)
        except Exception as exc:
            logger.debug("TTS prefetch failed: %s", exc)
            self._backoff_until = time.monotonic() + FAILURE_BACKOFF_S
            return
        self._store(to_synthesize, audio)
        logger.info("TTS prefetch ready (%d chars)", len(to_synthesize))

    def _run(self) -> None:
        while not self._stop.wait(POLL_S):
            self.step(time.monotonic())
