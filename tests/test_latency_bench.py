"""Latency regression guard: VK's own pipeline overhead, end to end.

Drives the real Daemon (engine, worker, injector path) with a scripted
streaming recognizer and measures what VK itself adds — the recognizer's
own speed is not ours to test. Budgets are generous on purpose: they
catch a regression of an order of magnitude (a blocking call on the event
loop, a quadratic diff) on both the Linux and Windows CI runners, not
jitter. The numbers land in the CI step summary when GITHUB_STEP_SUMMARY
is set, so every run shows the trend.
"""

import asyncio
import os
import sys
from array import array

import pytest
from fakes import FakeStreamingSTT, RecordingInjector, _make_daemon
from waiting import wait_until

from voice_keyboard import latency

RUNS = 8
# p95 budgets in milliseconds.
BUDGET_TEXT_TO_SCREEN_MS = 150.0
BUDGET_STOP_TO_SETTLED_MS = 400.0

SENTENCE = (
    "the quick brown fox jumps over the lazy dog while the five boxing "
    "wizards jump quickly and pack my box with five dozen liquor jugs"
).split()


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path))

    async def _to_thread(func, /, *args, **kwargs):
        await asyncio.sleep(0)
        return func(*args, **kwargs)

    monkeypatch.setattr(asyncio, "to_thread", _to_thread)
    monkeypatch.setattr("voice_keyboard.client._show_overlay", lambda *a, **k: None)


def _events() -> list[dict]:
    return [
        {"type": "transcript.partial", "text": " ".join(SENTENCE[:n]), "is_final": False}
        for n in range(1, len(SENTENCE) + 1)
    ]


def _write_step_summary(summary: dict) -> None:
    path = os.environ.get("GITHUB_STEP_SUMMARY")
    if not path:
        return
    table = latency.format_summary(summary)
    with open(path, "a", encoding="utf-8") as f:
        f.write(
            f"### Dictation latency ({sys.platform}, py{sys.version_info.major}."
            f"{sys.version_info.minor}, {RUNS} runs)\n\n```\n{table}\n```\n"
        )


def test_pipeline_overhead_within_budget() -> None:
    loud = array("h", [8000, -8000] * 80).tobytes()
    summary_log = latency.LatencyLog()

    async def one_run() -> None:
        injector = RecordingInjector()
        daemon = _make_daemon(FakeStreamingSTT(_events()), injector, chunk=loud)
        with daemon._audio_patch, daemon._stt_patch, daemon._probe_patch:
            await daemon._start_recording()
            # Every scripted word is on screen (molten) before the stop, so
            # stop→settled times VK's reconcile, not the fake's pacing.
            assert await wait_until(lambda: injector.screen.endswith(SENTENCE[-1]))
            await daemon._stop_recording()
        assert injector.screen.lower().startswith("the quick brown fox")
        for record in daemon._latency.records():
            summary_log._records.append(record)

    for _ in range(RUNS):
        asyncio.run(one_run())

    summary = summary_log.summary()
    _write_step_summary(summary)
    print("\n" + latency.format_summary(summary))

    assert summary["text_to_screen"]["count"] == RUNS
    assert summary["text_to_screen"]["p95"] < BUDGET_TEXT_TO_SCREEN_MS, summary
    assert summary["stop_to_settled"]["p95"] < BUDGET_STOP_TO_SETTLED_MS, summary
