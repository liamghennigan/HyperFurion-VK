import asyncio
from unittest import mock

import pytest

from voice_keyboard.daemon import Daemon, _dedupe_repeated_transcript_text, _merge_transcript_text
from voice_keyboard.config import _default_config_with_paths


def _valid_config() -> dict:
    cfg = _default_config_with_paths()
    cfg["xai"]["api_key"] = "test-api-key"
    cfg["audio"]["sample_rate"] = 16000
    cfg["audio"]["chunk_ms"] = 100
    return cfg


class TestTranscriptMerge:
    def test_repeated_whole_transcript_is_collapsed_before_injection(self) -> None:
        text = (
            "Did you make a repo for this? Also it doubled when it printed the words. "
            "Did you make a repo for this? Also it doubled when it printed the words."
        )

        assert _dedupe_repeated_transcript_text(text) == (
            "Did you make a repo for this? Also it doubled when it printed the words."
        )

    def test_short_repetitions_are_preserved(self) -> None:
        assert _dedupe_repeated_transcript_text("No no") == "No no"

    def test_punctuation_corrected_final_transcript_replaces_accumulated_chunks(self) -> None:
        current = _merge_transcript_text(
            "Just to be clear it is not always doubling up",
            "but every once in a while it does",
        )
        final = _merge_transcript_text(
            current,
            "Just to be clear, it is not always doubling up, but every once in a while it does.",
        )

        assert final == (
            "Just to be clear, it is not always doubling up, "
            "but every once in a while it does."
        )

    def test_overlapping_chunks_merge_ignores_punctuation(self) -> None:
        assert _merge_transcript_text(
            "I was talking for a while, and then",
            "and then I kept going",
        ) == "I was talking for a while, and then I kept going"


class TestDaemonStateTransitions:
    @pytest.fixture(autouse=True)
    def inline_to_thread(self, monkeypatch: pytest.MonkeyPatch):
        async def _to_thread(func, /, *args, **kwargs):
            return func(*args, **kwargs)

        monkeypatch.setattr(asyncio, "to_thread", _to_thread)

    @pytest.fixture
    def daemon(self):
        cfg = _valid_config()
        injector = mock.Mock()
        injector.start = mock.Mock()
        injector.stop = mock.Mock()
        injector.type_text = mock.Mock()
        ipc_server = mock.Mock()
        tts_client = mock.Mock()
        d = Daemon(config=cfg, injector=injector, ipc_server=ipc_server, tts_client=tts_client)
        return d

    def test_start_stop_recording_with_mocked_stt(self, daemon: Daemon) -> None:
        async def run() -> None:
            async def fake_receive_events():
                yield {"type": "transcript.done", "text": "hello world"}

            stt_client = mock.AsyncMock()
            stt_client.receive_events = fake_receive_events
            audio_capture = mock.Mock()
            audio_capture.read_chunk = mock.Mock(side_effect=[b"audio", RuntimeError("stream closed")])
            audio_capture.sample_rate = 16000
            audio_capture.running = True

            with mock.patch("voice_keyboard.daemon.AudioCapture", return_value=audio_capture), \
                 mock.patch("voice_keyboard.daemon.create_stt_client", return_value=stt_client):
                await daemon._start_recording()
                assert daemon._recording is True
                stt_client.connect.assert_awaited_once_with(16000)

                # Wait for the receive task to populate _final_text.
                for _ in range(100):
                    if daemon._final_text:
                        break
                    await asyncio.sleep(0.005)
                assert daemon._final_text == "hello world"

                result = await daemon._stop_recording()

                assert daemon._recording is False
                assert result == "hello world"
                daemon._injector.type_text.assert_called_once_with("hello world")

        asyncio.run(run())

    def test_stop_without_start_is_noop(self, daemon: Daemon) -> None:
        result = asyncio.run(daemon._stop_recording())
        assert result == ""
        daemon._injector.type_text.assert_not_called()

    def test_done_event_replaces_partial_transcript(self, daemon: Daemon) -> None:
        async def run() -> None:
            async def fake_receive_events():
                yield {"type": "transcript.partial", "text": "hello", "is_final": False}
                yield {"type": "transcript.done", "text": "hello world"}

            stt_client = mock.AsyncMock()
            stt_client.receive_events = fake_receive_events
            audio_capture = mock.Mock()
            audio_capture.read_chunk = mock.Mock(side_effect=[b"audio", RuntimeError("done")])
            audio_capture.sample_rate = 16000
            audio_capture.running = True

            with mock.patch("voice_keyboard.daemon.AudioCapture", return_value=audio_capture), \
                 mock.patch("voice_keyboard.daemon.create_stt_client", return_value=stt_client):
                await daemon._start_recording()
                for _ in range(100):
                    if daemon._final_text == "hello world":
                        break
                    await asyncio.sleep(0.005)
                await daemon._stop_recording()
                daemon._injector.type_text.assert_called_once_with("hello world")

        asyncio.run(run())

    def test_final_partial_segments_are_accumulated(self, daemon: Daemon) -> None:
        async def run() -> None:
            async def fake_receive_events():
                yield {"type": "transcript.partial", "text": "I started with this part", "is_final": True}
                yield {"type": "transcript.partial", "text": "and kept talking after that", "is_final": True}

            stt_client = mock.AsyncMock()
            stt_client.receive_events = fake_receive_events
            audio_capture = mock.Mock()
            audio_capture.read_chunk = mock.Mock(side_effect=[b"audio", RuntimeError("done")])
            audio_capture.sample_rate = 16000
            audio_capture.running = True

            with mock.patch("voice_keyboard.daemon.AudioCapture", return_value=audio_capture), \
                 mock.patch("voice_keyboard.daemon.create_stt_client", return_value=stt_client):
                await daemon._start_recording()
                for _ in range(100):
                    if daemon._final_text == "I started with this part and kept talking after that":
                        break
                    await asyncio.sleep(0.005)
                result = await daemon._stop_recording()

                assert result == "I started with this part and kept talking after that"
                daemon._injector.type_text.assert_called_once_with(result)

        asyncio.run(run())

    def test_done_segment_appends_to_previous_final_segments(self, daemon: Daemon) -> None:
        async def run() -> None:
            async def fake_receive_events():
                yield {"type": "transcript.partial", "text": "This was already finalized", "is_final": True}
                yield {"type": "transcript.done", "text": "and this arrived at the end"}

            stt_client = mock.AsyncMock()
            stt_client.receive_events = fake_receive_events
            audio_capture = mock.Mock()
            audio_capture.read_chunk = mock.Mock(side_effect=[b"audio", RuntimeError("done")])
            audio_capture.sample_rate = 16000
            audio_capture.running = True

            with mock.patch("voice_keyboard.daemon.AudioCapture", return_value=audio_capture), \
                 mock.patch("voice_keyboard.daemon.create_stt_client", return_value=stt_client):
                await daemon._start_recording()
                for _ in range(100):
                    if daemon._final_text == "This was already finalized and this arrived at the end":
                        break
                    await asyncio.sleep(0.005)
                result = await daemon._stop_recording()

                assert result == "This was already finalized and this arrived at the end"
                daemon._injector.type_text.assert_called_once_with(result)

        asyncio.run(run())

    def test_done_full_transcript_with_punctuation_does_not_duplicate(
        self,
        daemon: Daemon,
    ) -> None:
        async def run() -> None:
            async def fake_receive_events():
                yield {
                    "type": "transcript.partial",
                    "text": "Just to be clear it is not always doubling up",
                    "is_final": True,
                }
                yield {
                    "type": "transcript.partial",
                    "text": "but every once in a while it does",
                    "is_final": True,
                }
                yield {
                    "type": "transcript.done",
                    "text": (
                        "Just to be clear, it is not always doubling up, "
                        "but every once in a while it does."
                    ),
                }

            stt_client = mock.AsyncMock()
            stt_client.receive_events = fake_receive_events
            audio_capture = mock.Mock()
            audio_capture.read_chunk = mock.Mock(side_effect=[b"audio", RuntimeError("done")])
            audio_capture.sample_rate = 16000
            audio_capture.running = True

            with mock.patch("voice_keyboard.daemon.AudioCapture", return_value=audio_capture), \
                 mock.patch("voice_keyboard.daemon.create_stt_client", return_value=stt_client):
                await daemon._start_recording()
                for _ in range(100):
                    if daemon._final_text.endswith("it does."):
                        break
                    await asyncio.sleep(0.005)
                result = await daemon._stop_recording()

                assert result == (
                    "Just to be clear, it is not always doubling up, "
                    "but every once in a while it does."
                )
                daemon._injector.type_text.assert_called_once_with(result)

        asyncio.run(run())

    def test_doubled_done_transcript_is_collapsed_before_injection(
        self,
        daemon: Daemon,
    ) -> None:
        async def run() -> None:
            final_text = (
                "Did you make a repo for this? Also it doubled when it printed the words. "
                "Did you make a repo for this? Also it doubled when it printed the words."
            )
            expected = (
                "Did you make a repo for this? "
                "Also it doubled when it printed the words."
            )

            async def fake_receive_events():
                yield {"type": "transcript.done", "text": final_text}

            stt_client = mock.AsyncMock()
            stt_client.receive_events = fake_receive_events
            audio_capture = mock.Mock()
            audio_capture.read_chunk = mock.Mock(side_effect=[b"audio", RuntimeError("done")])
            audio_capture.sample_rate = 16000
            audio_capture.running = True

            with mock.patch("voice_keyboard.daemon.AudioCapture", return_value=audio_capture), \
                 mock.patch("voice_keyboard.daemon.create_stt_client", return_value=stt_client):
                await daemon._start_recording()
                for _ in range(100):
                    if daemon._final_text == final_text:
                        break
                    await asyncio.sleep(0.005)
                result = await daemon._stop_recording()

                assert result == expected
                daemon._injector.type_text.assert_called_once_with(expected)

        asyncio.run(run())

    def test_stt_error_event_raises_and_does_not_inject(self, daemon: Daemon) -> None:
        async def run() -> None:
            async def fake_receive_events():
                yield {"type": "error", "message": "openai STT transcription failed: bad key"}

            stt_client = mock.AsyncMock()
            stt_client.receive_events = fake_receive_events
            audio_capture = mock.Mock()
            audio_capture.read_chunk = mock.Mock(side_effect=[b"audio", RuntimeError("done")])
            audio_capture.sample_rate = 16000
            audio_capture.running = True

            with mock.patch("voice_keyboard.daemon.AudioCapture", return_value=audio_capture), \
                 mock.patch("voice_keyboard.daemon.create_stt_client", return_value=stt_client):
                await daemon._start_recording()
                for _ in range(100):
                    if daemon._stt_error:
                        break
                    await asyncio.sleep(0.005)

                with pytest.raises(RuntimeError, match="bad key"):
                    await daemon._stop_recording()

                daemon._injector.type_text.assert_not_called()

        asyncio.run(run())

    def test_stop_recording_timeout_raises_and_does_not_inject(self, daemon: Daemon) -> None:
        async def run() -> None:
            async def fake_receive_events():
                await asyncio.sleep(10)
                yield {"type": "transcript.done", "text": "late text"}

            stt_client = mock.AsyncMock()
            stt_client.receive_events = fake_receive_events
            audio_capture = mock.Mock()
            audio_capture.read_chunk = mock.Mock(side_effect=[b"audio", RuntimeError("done")])
            audio_capture.sample_rate = 16000
            audio_capture.running = True

            with mock.patch("voice_keyboard.daemon.AudioCapture", return_value=audio_capture), \
                 mock.patch("voice_keyboard.daemon.create_stt_client", return_value=stt_client), \
                 mock.patch.object(daemon, "_stt_completion_timeout", return_value=0.01):
                await daemon._start_recording()

                with pytest.raises(RuntimeError, match="timed out waiting"):
                    await daemon._stop_recording()

                daemon._injector.type_text.assert_not_called()

        asyncio.run(run())

    def test_tts_calls_synthesize_and_play(self, daemon: Daemon) -> None:
        daemon._tts_client.synthesize_and_play = mock.Mock()
        asyncio.run(daemon._run_tts("read this"))
        daemon._tts_client.synthesize_and_play.assert_called_once_with("read this")

    def test_start_recording_without_api_key_raises(self, daemon: Daemon) -> None:
        daemon._config["xai"]["api_key"] = ""
        with pytest.raises(RuntimeError, match="providers.xai.api_key is not configured"):
            asyncio.run(daemon._start_recording())

    def test_start_recording_cleans_up_on_stt_connect_failure(self, daemon: Daemon) -> None:
        """If STT connect raises, audio capture must be released and not leaked."""
        async def run() -> None:
            audio_capture = mock.Mock()
            audio_capture.sample_rate = 16000
            audio_capture.running = True

            stt_client = mock.AsyncMock()
            stt_client.connect = mock.AsyncMock(side_effect=RuntimeError("STT down"))

            with mock.patch("voice_keyboard.daemon.AudioCapture", return_value=audio_capture), \
                 mock.patch("voice_keyboard.daemon.create_stt_client", return_value=stt_client):
                with pytest.raises(RuntimeError, match="STT down"):
                    await daemon._start_recording()

            # Audio capture handle must have been stopped and cleared.
            audio_capture.stop.assert_called_once()
            assert daemon._audio_capture is None
            assert daemon._stt_client is None
            assert daemon._recording is False

        asyncio.run(run())

    def test_cleanup_after_failed_start_is_idempotent(self, daemon: Daemon) -> None:
        """Calling cleanup multiple times must not raise."""
        daemon._audio_capture = mock.Mock()
        daemon._stt_client = mock.AsyncMock()
        asyncio.run(daemon._cleanup_after_failed_start())
        asyncio.run(daemon._cleanup_after_failed_start())  # second call must not raise
        assert daemon._audio_capture is None
        assert daemon._stt_client is None

    def test_stop_recording_ipc_timeout_tracks_stt_completion_timeout(
        self,
        daemon: Daemon,
    ) -> None:
        daemon._stt_client = mock.Mock(completion_timeout=65.0)
        assert daemon._stop_recording_ipc_timeout() == 75.0

    def test_shutdown_stops_recording_and_cleanup(self, daemon: Daemon) -> None:
        daemon._recording = True
        daemon._audio_capture = mock.Mock()
        daemon._stt_client = mock.AsyncMock()
        daemon._send_task = mock.Mock()
        daemon._receive_task = mock.Mock()
        with mock.patch.object(daemon, "_stop_recording", new=mock.AsyncMock()) as mock_stop:
            asyncio.run(daemon._shutdown())
            mock_stop.assert_awaited_once()
        daemon._injector.stop.assert_called_once()
        daemon._ipc_server.stop.assert_called_once()
