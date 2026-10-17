import logging
import queue
import threading
from typing import Optional

import pyaudio

logger = logging.getLogger(__name__)

# Unblocks a consumer waiting in read_chunk when the reader stops.
_CAPTURE_END = object()


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
        # The reader thread pulls the device continuously. The event loop
        # only drains this queue, so a slow live transcription cannot
        # overflow the microphone buffer and drop the samples the final
        # transcription needs.
        self._queue: queue.Queue = queue.Queue()
        self._reader: Optional[threading.Thread] = None

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
                if self._device_name == "default":
                    raise RuntimeError(
                        "No microphone found — connect one or set a default"
                        " input device in your sound settings"
                    )
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
            self._queue = queue.Queue()
            self._running = True
            self._reader = threading.Thread(
                target=self._read_loop,
                name="voice-keyboard-mic",
                daemon=True,
            )
            self._reader.start()
            logger.info(
                "Audio capture started: device=%s, rate=%d, chunk=%d",
                self._device_name,
                self._sample_rate,
                self._chunk_size,
            )

    def _read_loop(self) -> None:
        try:
            while self._running:
                stream = self._stream
                if stream is None:
                    break
                try:
                    # Not under a lock. stop() waits for this call to
                    # return before it closes the stream: closing from
                    # another thread during a native read can segfault.
                    data = stream.read(self._chunk_size, exception_on_overflow=False)
                except Exception:
                    if self._running:
                        logger.exception("Audio read failed")
                    break
                if not self._running:
                    break
                self._queue.put(data)
        finally:
            self._queue.put(_CAPTURE_END)

    def read_chunk(self) -> bytes:
        if not self._running and self._reader is None:
            raise RuntimeError("Audio capture not started")
        item = self._queue.get()
        if item is _CAPTURE_END:
            raise RuntimeError("Audio capture stopped")
        return item

    def stop(self) -> None:
        with self._lock:
            if not self._running and self._stream is None and self._pa is None:
                return
            self._running = False
            reader = self._reader
            self._reader = None

        # Let the current read() return, then close. Closing a PortAudio
        # stream from another thread while read() is in the native library
        # can segfault, so the close waits until the reader has left read().
        if reader is not None:
            reader.join(timeout=2.0)
            if reader.is_alive():
                logger.warning("Microphone reader did not stop; closing the stream anyway")

        with self._lock:
            stream = self._stream
            pa = self._pa
            self._stream = None
            self._pa = None

        with self._io_lock:
            if stream is not None:
                try:
                    stream.stop_stream()
                except Exception:
                    logger.debug("stopping audio stream", exc_info=True)
                try:
                    stream.close()
                except Exception:
                    logger.debug("closing audio stream", exc_info=True)
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
