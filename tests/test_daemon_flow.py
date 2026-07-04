"""End-to-end molten dictation through the Daemon with a fake streaming
STT client: words are typed while "recording", repairs happen in place,
and stop reconciles the screen with the finalized transcript."""

import asyncio
from unittest import mock

import pytest

from voice_keyboard.config import _default_config_with_paths
from voice_keyboard.daemon import Daemon
from voice_keyboard.focusprobe import FocusInfo


def _flow_config() -> dict:
    cfg = _default_config_with_paths()
    cfg["xai"]["api_key"] = "test-api-key"
    cfg["flow"]["stability_ms"] = 10
    cfg["flow"]["stability_updates"] = 1
    cfg["flow"]["adaptive"] = False
    return cfg


class RecordingInjector:
    def __init__(self):
        self.screen = ""
        self.paste_chord_shift = False

    def start(self) -> None:
        pass

    def stop(self) -> None:
        pass

    def type_text(self, text: str) -> None:
        self.screen += text

    def delete_chars(self, count: int) -> None:
        self.screen = self.screen[: len(self.screen) - count]


class FakeStreamingSTT:
    """Speaks the streaming client interface; emits scripted events."""

    supports_streaming = True
    completion_timeout = 5.0

    def __init__(self, events: list[dict]):
        self._events = events
        self._done = asyncio.Event()

    async def connect(self, sample_rate: int) -> None:
        pass

    async def send_audio(self, data: bytes) -> None:
        pass

    async def send_audio_done(self) -> None:
        self._done.set()

    async def receive_events(self):
        for event in self._events:
            yield event
            await asyncio.sleep(0.02)
        await self._done.wait()
        yield {"type": "transcript.done", "text": self._events[-1]["text"]}

    async def close(self) -> None:
        self._done.set()


@pytest.fixture(autouse=True)
def inline_to_thread(monkeypatch: pytest.MonkeyPatch):
    async def _to_thread(func, /, *args, **kwargs):
        # Yield to the event loop first: the audio mock never raises, so
        # without this the _stream_audio loop would starve every other task.
        await asyncio.sleep(0)
        return func(*args, **kwargs)

    monkeypatch.setattr(asyncio, "to_thread", _to_thread)


@pytest.fixture(autouse=True)
def no_overlay(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(
        "voice_keyboard.client._show_overlay",
        lambda *args, **kwargs: None,
    )


def _make_daemon(stt_client, injector) -> Daemon:
    daemon = Daemon(
        config=_flow_config(),
        injector=injector,
        ipc_server=mock.Mock(),
        tts_client=mock.Mock(),
    )
    audio_capture = mock.Mock()
    audio_capture.read_chunk = mock.Mock(return_value=b"\x00" * 320)
    audio_capture.sample_rate = 16000
    audio_capture.running = True
    daemon._audio_patch = mock.patch(
        "voice_keyboard.daemon.AudioCapture", return_value=audio_capture
    )
    daemon._stt_patch = mock.patch(
        "voice_keyboard.daemon.create_stt_client", return_value=stt_client
    )
    daemon._probe_patch = mock.patch(
        "voice_keyboard.daemon.probe_focus",
        return_value=FocusInfo(app="test-editor", role="text", x=10, y=10),
    )
    return daemon


class TestLiveFlow:
    def test_words_stream_in_and_reconcile_at_stop(self) -> None:
        async def run() -> None:
            events = [
                {"type": "transcript.partial", "text": "hello", "is_final": False},
                {"type": "transcript.partial", "text": "hello world", "is_final": False},
                {
                    "type": "transcript.partial",
                    "text": "hello world how are you",
                    "is_final": False,
                },
            ]
            injector = RecordingInjector()
            stt = FakeStreamingSTT(events)
            daemon = _make_daemon(stt, injector)
            with daemon._audio_patch, daemon._stt_patch, daemon._probe_patch:
                await daemon._start_recording()
                assert daemon._flow_worker is not None, "live worker expected"
                # Let interims flow: something must be typed BEFORE stop.
                for _ in range(200):
                    if injector.screen:
                        break
                    await asyncio.sleep(0.01)
                assert injector.screen, "molten text should stream in while recording"

                final = await daemon._stop_recording()
                assert final == "Hello world how are you"
                assert injector.screen == final

        asyncio.run(run())

    def test_live_repair_converges_on_revision(self) -> None:
        async def run() -> None:
            events = [
                {"type": "transcript.partial", "text": "eye scream", "is_final": False},
                {"type": "transcript.partial", "text": "eye scream cone", "is_final": False},
                {"type": "transcript.partial", "text": "ice cream cone", "is_final": False},
            ]
            injector = RecordingInjector()
            stt = FakeStreamingSTT(events)
            daemon = _make_daemon(stt, injector)
            # A horizon longer than the event cadence: the revision lands
            # while the words are still molten, so it repairs in place.
            daemon._config["flow"]["stability_ms"] = 5000
            daemon._config["flow"]["stability_updates"] = 50
            with daemon._audio_patch, daemon._stt_patch, daemon._probe_patch:
                await daemon._start_recording()
                saw_wrong = saw_repaired = False
                for _ in range(400):
                    if injector.screen.startswith("Eye scream"):
                        saw_wrong = True
                    if saw_wrong and injector.screen == "Ice cream cone":
                        saw_repaired = True
                        break
                    await asyncio.sleep(0.005)
                assert saw_wrong, "misheard text should have been typed molten"
                assert saw_repaired, "revision should repair the typed text in place"

                final = await daemon._stop_recording()
                assert final == "Ice cream cone"
                assert injector.screen == final

        asyncio.run(run())

    def test_terminal_register_resolved_from_probe(self) -> None:
        async def run() -> None:
            events = [
                {"type": "transcript.partial", "text": "git status period", "is_final": False},
            ]
            injector = RecordingInjector()
            stt = FakeStreamingSTT(events)
            daemon = _make_daemon(stt, injector)
            daemon._probe_patch = mock.patch(
                "voice_keyboard.daemon.probe_focus",
                return_value=FocusInfo(app="kitty", role="terminal"),
            )
            with daemon._audio_patch, daemon._stt_patch, daemon._probe_patch:
                await daemon._start_recording()
                assert daemon._session_register.name == "terminal"
                assert injector.paste_chord_shift is True
                final = await daemon._stop_recording()
                # No prose capitalization in a terminal.
                assert final == "git status."

        asyncio.run(run())

    def test_flow_disabled_keeps_legacy_behavior(self) -> None:
        async def run() -> None:
            events = [
                {"type": "transcript.partial", "text": "hello there", "is_final": False},
            ]
            injector = RecordingInjector()
            stt = FakeStreamingSTT(events)
            daemon = _make_daemon(stt, injector)
            daemon._config["flow"]["enabled"] = False
            with daemon._audio_patch, daemon._stt_patch, daemon._probe_patch:
                await daemon._start_recording()
                assert daemon._flow_worker is None
                final = await daemon._stop_recording()
                assert final == "hello there"  # raw: no grammar, no caps
                assert injector.screen == final

        asyncio.run(run())
