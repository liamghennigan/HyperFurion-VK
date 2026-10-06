"""Per-utterance latency: how fast speech becomes keystrokes.

Each dictation gets an `UtteranceTimer`; the daemon marks the moments that
matter as they happen:

    start          recording began (hotkey down / IPC start)
    onset          the first chunk loud enough to be speech
    first_partial  the provider's first transcript for this utterance
    first_key      the first characters landed in the focused app
    stop           recording ended (hotkey up / IPC stop)
    settled        the screen converged to the final text

`perf_counter` everywhere: Windows' monotonic clock ticks every ~15.6ms,
far too coarse for the numbers this module exists to watch.

The last `RING_SIZE` utterances stay in memory for `voice-keyboard stats`.
With [flow] latency_log = true they are also appended to latency.jsonl in
the state dir (numbers, app and register only — never text).
"""

import json
import logging
import os
import statistics
import time
from collections import deque
from pathlib import Path
from typing import Callable, Optional

from voice_keyboard import paths

logger = logging.getLogger(__name__)

RING_SIZE = 200

MARKS = ("start", "onset", "first_partial", "first_key", "stop", "settled")

# metric -> (from mark, to mark); milliseconds between the two.
METRICS: dict[str, tuple[str, str]] = {
    "speech_to_text": ("onset", "first_partial"),     # the recognizer
    "text_to_screen": ("first_partial", "first_key"),  # VK's own pipeline
    "speech_to_screen": ("onset", "first_key"),        # what the user feels
    "stop_to_settled": ("stop", "settled"),            # the final repair
}


class UtteranceTimer:
    """Marks for one dictation; each mark keeps its first timestamp."""

    def __init__(self, clock: Callable[[], float] = time.perf_counter):
        self._clock = clock
        self.marks: dict[str, float] = {}

    def mark(self, name: str) -> None:
        if name not in self.marks:
            self.marks[name] = self._clock()

    def metrics(self) -> dict[str, float]:
        out: dict[str, float] = {}
        for metric, (begin, end) in METRICS.items():
            if begin in self.marks and end in self.marks:
                out[metric] = round(
                    max(0.0, (self.marks[end] - self.marks[begin]) * 1000.0), 1
                )
        return out


def latency_path() -> Path:
    return paths.state_dir() / "latency.jsonl"


def _percentile(values: list[float], pct: float) -> float:
    if len(values) == 1:
        return values[0]
    cuts = statistics.quantiles(values, n=100, method="inclusive")
    return cuts[max(0, min(98, int(pct) - 1))]


def summarize(records: list[dict]) -> dict[str, dict[str, float]]:
    """metric -> {count, p50, p95, max} over the records that have it."""
    summary: dict[str, dict[str, float]] = {}
    for metric in METRICS:
        values = sorted(
            float(record[metric])
            for record in records
            if isinstance(record.get(metric), (int, float))
        )
        if not values:
            continue
        summary[metric] = {
            "count": len(values),
            "p50": round(_percentile(values, 50), 1),
            "p95": round(_percentile(values, 95), 1),
            "max": round(values[-1], 1),
        }
    return summary


class LatencyLog:
    def __init__(self, *, persist: bool = False):
        self.persist = persist
        self._records: deque[dict] = deque(maxlen=RING_SIZE)

    def record(
        self, timer: UtteranceTimer, *, app: str = "", register: str = ""
    ) -> Optional[dict]:
        metrics = timer.metrics()
        if not metrics:
            return None
        entry = {"ts": round(time.time(), 3), "app": app, "register": register}
        entry.update(metrics)
        self._records.append(entry)
        logger.info(
            "Latency: %s",
            " ".join(f"{name}={value:.0f}ms" for name, value in metrics.items()),
        )
        if self.persist:
            self._append(entry)
        return entry

    def records(self) -> list[dict]:
        return list(self._records)

    def summary(self) -> dict[str, dict[str, float]]:
        return summarize(self.records())

    @staticmethod
    def _append(entry: dict) -> None:
        """Best-effort; never raises into the dictation path."""
        try:
            path = latency_path()
            path.parent.mkdir(parents=True, mode=0o700, exist_ok=True)
            with open(path, "a", encoding="utf-8") as f:
                f.write(json.dumps(entry) + "\n")
            os.chmod(path, 0o600)
        except OSError:
            logger.exception("Could not append to the latency log")


def format_summary(summary: dict[str, dict[str, float]]) -> str:
    """A small fixed-width table for the CLI (and CI step summaries)."""
    if not summary:
        return "No dictations timed yet."
    lines = [f"{'metric':<18}{'n':>5}{'p50':>9}{'p95':>9}{'max':>9}"]
    for metric in METRICS:
        row = summary.get(metric)
        if row is None:
            continue
        lines.append(
            f"{metric:<18}{int(row['count']):>5}"
            f"{row['p50']:>7.1f}ms{row['p95']:>7.1f}ms{row['max']:>7.1f}ms"
        )
    return "\n".join(lines)
