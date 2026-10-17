"""The microphone reader keeps pulling while the consumer is busy."""

import time
from unittest import mock

import pytest

from voice_keyboard.audio_capture import AudioCapture


class _Stream:
    def __init__(self) -> None:
        self.reads = 0
        self.closed = False

    def read(self, n: int, exception_on_overflow: bool = False) -> bytes:
        self.reads += 1
        if self.reads == 1:
            return b"\x01\x02\x03\x04"
        # Later reads block briefly, the way a real device does, so stop()
        # can wait for read() to return before closing.
        time.sleep(0.05)
        if self.closed:
            raise OSError("closed")
        return b"\x00" * 4

    def stop_stream(self) -> None:
        self.closed = True

    def close(self) -> None:
        self.closed = True


class _PyAudio:
    def __init__(self) -> None:
        self.stream = _Stream()
        self.terminated = False

    def get_default_input_device_info(self) -> dict:
        return {"index": 1}

    def open(self, **kwargs) -> _Stream:
        return self.stream

    def terminate(self) -> None:
        self.terminated = True


def test_reader_pulls_before_the_consumer_asks() -> None:
    pa = _PyAudio()
    with mock.patch("voice_keyboard.audio_capture.pyaudio.PyAudio", return_value=pa):
        capture = AudioCapture()
        capture.start()
        try:
            deadline = time.time() + 1
            while pa.stream.reads < 1 and time.time() < deadline:
                time.sleep(0.01)
            assert pa.stream.reads >= 1
            assert capture.read_chunk() == b"\x01\x02\x03\x04"
        finally:
            capture.stop()
    assert pa.terminated
    assert pa.stream.closed


def test_read_before_start_raises() -> None:
    capture = AudioCapture()
    with pytest.raises(RuntimeError, match="not started"):
        capture.read_chunk()


def test_stop_is_safe_twice() -> None:
    pa = _PyAudio()
    with mock.patch("voice_keyboard.audio_capture.pyaudio.PyAudio", return_value=pa):
        capture = AudioCapture()
        capture.start()
        capture.stop()
        capture.stop()
    assert not capture.running
