"""Shared fakes for driving the real Daemon end to end: a recording
injector, a scripted streaming STT client, and a daemon factory that
patches out the microphone, the provider, and the focus probe."""

import asyncio
from unittest import mock

from voice_keyboard.config import _default_config_with_paths
from voice_keyboard.daemon import Daemon
from voice_keyboard.focusprobe import FocusInfo


def _flow_config() -> dict:
    cfg = _default_config_with_paths()
    cfg["xai"]["api_key"] = "test-api-key"
    cfg["flow"]["stability_ms"] = 10
    cfg["flow"]["stability_updates"] = 1
    cfg["flow"]["adaptive"] = False
    # The test key would make [llm] look usable: no real pause reviews.
    cfg["flow"]["pause_review"] = "rules"
    return cfg


class RecordingInjector:
    def __init__(self):
        self.screen = ""
        self.paste_chord_shift = False
        self.combos: list[list[str]] = []

    def start(self) -> None:
        pass

    def stop(self) -> None:
        pass

    def type_text(self, text: str) -> None:
        self.screen += text

    def delete_chars(self, count: int) -> None:
        self.screen = self.screen[: len(self.screen) - count]

    def press_combo(self, names: list) -> None:
        self.combos.append(list(names))


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


def _make_daemon(
    stt_client, injector, *, chunk: bytes = b"\x00" * 320, app: str = "test-editor"
) -> Daemon:
    daemon = Daemon(
        config=_flow_config(),
        injector=injector,
        ipc_server=mock.Mock(),
        tts_client=mock.Mock(),
    )
    audio_capture = mock.Mock()
    audio_capture.read_chunk = mock.Mock(return_value=chunk)
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
        return_value=FocusInfo(app=app, role="text", x=10, y=10),
    )
    return daemon
