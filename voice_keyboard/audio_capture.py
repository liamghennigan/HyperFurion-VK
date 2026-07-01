import logging
import threading
from typing import Optional

import pyaudio

logger = logging.getLogger(__name__)


class AudioCapture:
    def __init__(
        self,
        sample_rate: int = 16000,
        chunk_ms: int = 100,
        device_name: str = "default",
    ):
        self._sample_rate = sample_rate
        self._chunk_size = int(sample_rate * chunk_ms / 1000)
        self._device_name = device_name
        self._pa: Optional[pyaudio.PyAudio] = None
        self._stream: Optional[pyaudio.Stream] = None
        self._running = False
        self._lock = threading.Lock()
        self._io_lock = threading.Lock()

    def _find_device_index(self) -> Optional[int]:
        if self._device_name == "default":
            try:
                return self._pa.get_default_input_device_info()["index"]
            except OSError:
                return None

        for i in range(self._pa.get_device_count()):
            info = self._pa.get_device_info_by_index(i)
            name = info["name"]
            if (
                (name == self._device_name or self._device_name in name)
                and info["maxInputChannels"] > 0
            ):
                return i
        return None

    def start(self) -> None:
        with self._lock:
            if self._running:
                return
            self._pa = pyaudio.PyAudio()
            device_index = self._find_device_index()
            if device_index is None:
                self._pa.terminate()
                self._pa = None
                raise RuntimeError(
                    f"Input device not found: {self._device_name}"
                )

            self._stream = self._pa.open(
                format=pyaudio.paInt16,
                channels=1,
                rate=self._sample_rate,
                input=True,
                input_device_index=device_index,
                frames_per_buffer=self._chunk_size,
            )
            self._running = True
            logger.info(
                "Audio capture started: device=%s, rate=%d, chunk=%d",
                self._device_name,
                self._sample_rate,
                self._chunk_size,
            )

    def read_chunk(self) -> bytes:
        with self._io_lock:
            if not self._running or self._stream is None:
                raise RuntimeError("Audio capture not started")
            return self._stream.read(self._chunk_size, exception_on_overflow=False)

    def stop(self) -> None:
        with self._lock:
            if not self._running and self._stream is None and self._pa is None:
                return
            self._running = False
            stream = self._stream
            pa = self._pa
            self._stream = None
            self._pa = None

        with self._io_lock:
            if stream is not None:
                stream.stop_stream()
                stream.close()
            if pa is not None:
                pa.terminate()
            logger.info("Audio capture stopped")

    @property
    def sample_rate(self) -> int:
        return self._sample_rate

    @property
    def chunk_size(self) -> int:
        return self._chunk_size

    @property
    def running(self) -> bool:
        return self._running
