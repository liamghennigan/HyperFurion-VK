"""Per-utterance latency: the timer's metrics, the ring's percentiles, the
onset detector, the opt-in JSONL log, and the daemon's marks end to end."""

import asyncio
import json
from array import array

import pytest
from fakes import FakeStreamingSTT, RecordingInjector, _make_daemon
from waiting import wait_until

from voice_keyboard import latency
from voice_keyboard.flow.vad import OnsetDetector


def _loud_chunk(amplitude: int = 8000, samples: int = 160) -> bytes:
    return array("h", [amplitude, -amplitude] * (samples // 2)).tobytes()


class FakeClock:
    def __init__(self):
        self.now = 0.0

    def __call__(self) -> float:
        return self.now


@pytest.fixture(autouse=True)
def state_dir(tmp_path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path))
    return tmp_path


class TestUtteranceTimer:
    def test_metrics_between_marks(self) -> None:
        clock = FakeClock()
        timer = latency.UtteranceTimer(clock)
        for name, at in [
            ("start", 0.0), ("onset", 0.10), ("first_partial", 0.40),
            ("first_key", 0.43), ("stop", 2.0), ("settled", 2.25),
        ]:
            clock.now = at
            timer.mark(name)
        assert timer.metrics() == {
            "speech_to_text": 300.0,
            "text_to_screen": 30.0,
            "speech_to_screen": 330.0,
            "stop_to_settled": 250.0,
        }

    def test_first_mark_wins_and_missing_marks_skip_metrics(self) -> None:
        clock = FakeClock()
        timer = latency.UtteranceTimer(clock)
        timer.mark("stop")
        clock.now = 1.0
        timer.mark("stop")
        timer.mark("settled")
        assert timer.metrics() == {"stop_to_settled": 1000.0}


class TestLatencyLog:
    def _timer(self, partial_to_key: float) -> latency.UtteranceTimer:
        clock = FakeClock()
        timer = latency.UtteranceTimer(clock)
        timer.mark("first_partial")
        clock.now = partial_to_key
        timer.mark("first_key")
        return timer

    def test_summary_percentiles(self) -> None:
        log = latency.LatencyLog()
        for ms in range(1, 101):
            log.record(self._timer(ms / 1000.0))
        row = log.summary()["text_to_screen"]
        assert row["count"] == 100
        assert row["p50"] == pytest.approx(50.5, abs=1)
        assert row["p95"] == pytest.approx(95, abs=1.5)
        assert row["max"] == 100.0

    def test_ring_is_bounded(self) -> None:
        log = latency.LatencyLog()
        for _ in range(latency.RING_SIZE + 5):
            log.record(self._timer(0.01))
        assert len(log.records()) == latency.RING_SIZE

    def test_empty_timer_is_not_recorded(self) -> None:
        log = latency.LatencyLog()
        assert log.record(latency.UtteranceTimer()) is None
        assert log.summary() == {}

    def test_persist_appends_numbers_never_text(self, state_dir) -> None:
        log = latency.LatencyLog(persist=True)
        log.record(self._timer(0.02), app="gedit", register="prose")
        lines = latency.latency_path().read_text().splitlines()
        assert len(lines) == 1
        entry = json.loads(lines[0])
        assert entry["app"] == "gedit"
        assert entry["text_to_screen"] == 20.0
        assert "text" not in entry

    def test_not_persisted_by_default(self) -> None:
        latency.LatencyLog().record(self._timer(0.02))
        assert not latency.latency_path().exists()

    def test_format_summary(self) -> None:
        log = latency.LatencyLog()
        log.record(self._timer(0.02))
        table = latency.format_summary(log.summary())
        assert "text_to_screen" in table and "20.0ms" in table
        assert latency.format_summary({}) == "No dictations timed yet."


class TestOnsetDetector:
    def test_fires_once_on_speech(self) -> None:
        detector = OnsetDetector()
        assert not detector.feed(0.001)
        assert detector.feed(0.2)
        assert not detector.feed(0.2)
        assert detector.fired

    def test_noise_floor_adapts(self) -> None:
        detector = OnsetDetector()
        for _ in range(200):
            assert not detector.feed(0.008)  # a steady hum, under 0.010
        # Speech must now clear ~3x the learned floor, not just the minimum.
        assert not detector.feed(0.012)
        assert detector.feed(0.05)


class TestDaemonMarks:
    @pytest.fixture(autouse=True)
    def inline_to_thread(self, monkeypatch: pytest.MonkeyPatch):
        async def _to_thread(func, /, *args, **kwargs):
            await asyncio.sleep(0)
            return func(*args, **kwargs)

        monkeypatch.setattr(asyncio, "to_thread", _to_thread)
        monkeypatch.setattr(
            "voice_keyboard.client._show_overlay", lambda *a, **k: None
        )

    def test_live_dictation_records_every_metric(self) -> None:
        async def run() -> None:
            events = [
                {"type": "transcript.partial", "text": "hello", "is_final": False},
                {"type": "transcript.partial", "text": "hello world", "is_final": False},
            ]
            injector = RecordingInjector()
            daemon = _make_daemon(FakeStreamingSTT(events), injector, chunk=_loud_chunk())
            with daemon._audio_patch, daemon._stt_patch, daemon._probe_patch:
                await daemon._start_recording()
                assert await wait_until(lambda: injector.screen)
                await daemon._stop_recording()
            records = daemon._latency.records()
            assert len(records) == 1
            assert set(latency.METRICS) <= set(records[0])
            assert records[0]["app"] == "test-editor"

        asyncio.run(run())

    def test_secret_field_is_not_recorded(self) -> None:
        async def run() -> None:
            events = [{"type": "transcript.partial", "text": "hunter2", "is_final": False}]
            injector = RecordingInjector()
            daemon = _make_daemon(FakeStreamingSTT(events), injector, chunk=_loud_chunk())
            with daemon._audio_patch, daemon._stt_patch, daemon._probe_patch:
                await daemon._start_recording()
                daemon._session_secret = True
                assert await wait_until(lambda: injector.screen)
                await daemon._stop_recording()
            assert daemon._latency.records() == []

        asyncio.run(run())
