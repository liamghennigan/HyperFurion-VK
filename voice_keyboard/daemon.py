import asyncio
import json
import logging
import re
import signal
import threading
from typing import Optional

from voice_keyboard.audio_capture import AudioCapture
from voice_keyboard.config import load_config, validate_config
from voice_keyboard.hotkey import HotkeyListener
from voice_keyboard.injector import TextInjector
from voice_keyboard.ipc import IPCServer, recv_all
from voice_keyboard.stt import STTClient
from voice_keyboard.tts import TTSClient

logger = logging.getLogger(__name__)
_TRANSCRIPT_WORD_RE = re.compile(r"[A-Za-z0-9']+")


def _transcript_words(text: str) -> list[tuple[str, int, int]]:
    return [
        (match.group(0).casefold(), match.start(), match.end())
        for match in _TRANSCRIPT_WORD_RE.finditer(text)
    ]


def _word_sequence_startswith(
    words: list[tuple[str, int, int]],
    prefix: list[tuple[str, int, int]],
) -> bool:
    return len(words) >= len(prefix) and [
        word for word, _, _ in words[:len(prefix)]
    ] == [word for word, _, _ in prefix]


def _word_sequence_endswith(
    words: list[tuple[str, int, int]],
    suffix: list[tuple[str, int, int]],
) -> bool:
    return len(words) >= len(suffix) and [
        word for word, _, _ in words[-len(suffix):]
    ] == [word for word, _, _ in suffix]


def _word_sequence_contains(
    words: list[tuple[str, int, int]],
    needle: list[tuple[str, int, int]],
) -> bool:
    if not needle:
        return True
    if len(needle) > len(words):
        return False
    needle_values = [word for word, _, _ in needle]
    for index in range(len(words) - len(needle) + 1):
        if [word for word, _, _ in words[index:index + len(needle)]] == needle_values:
            return True
    return False


def _word_prefix_overlap(
    current: list[tuple[str, int, int]],
    update: list[tuple[str, int, int]],
) -> int:
    max_overlap = min(len(current), len(update))
    for overlap in range(max_overlap, 0, -1):
        if [word for word, _, _ in current[-overlap:]] == [
            word for word, _, _ in update[:overlap]
        ]:
            return overlap
    return 0


def _dedupe_repeated_transcript_text(text: str, *, min_words: int = 4) -> str:
    """Collapse a transcript that is one whole phrase repeated twice."""
    words = _transcript_words(text)
    if len(words) < min_words * 2:
        return text

    word_values = [word for word, _, _ in words]
    for block_size in range(len(words) // 2, min_words - 1, -1):
        if block_size * 2 != len(words):
            continue
        if word_values[:block_size] == word_values[block_size:block_size * 2]:
            second_copy_start = words[block_size][1]
            return text[:second_copy_start].rstrip()

    return text


def _join_transcript_text(left: str, right: str) -> str:
    if not left:
        return right
    if not right:
        return left
    if left[-1].isspace() or right[0].isspace():
        return f"{left}{right}"
    return f"{left} {right}"


def _merge_transcript_text(current: str, update: str) -> str:
    """Merge STT updates that may be full transcripts or finalized segments."""
    update = update or ""
    if not update:
        return current
    if not current:
        return update
    if update == current or update.startswith(current):
        return update
    if current.endswith(update):
        return current

    current_words = _transcript_words(current)
    update_words = _transcript_words(update)
    if current_words and update_words:
        if _word_sequence_startswith(update_words, current_words):
            return update
        if _word_sequence_endswith(current_words, update_words):
            return current
        if len(current_words) >= 3 and _word_sequence_contains(update_words, current_words):
            return update
        if len(update_words) >= 3 and _word_sequence_contains(current_words, update_words):
            return current

        word_overlap = _word_prefix_overlap(current_words, update_words)
        if word_overlap:
            prefix_end = current_words[-word_overlap][1]
            return _join_transcript_text(current[:prefix_end].rstrip(), update)

    max_overlap = min(len(current), len(update))
    for overlap in range(max_overlap, 0, -1):
        if current[-overlap:] == update[:overlap]:
            return f"{current}{update[overlap:]}"

    return _join_transcript_text(current, update)


class Daemon:
    def __init__(
        self,
        config: Optional[dict] = None,
        injector: Optional[TextInjector] = None,
        ipc_server: Optional[IPCServer] = None,
        tts_client: Optional[TTSClient] = None,
    ):
        self._config = config if config is not None else load_config()
        validate_config(self._config)
        self._socket_path = self._config["daemon"]["socket_path"]
        self._ipc_server = ipc_server if ipc_server is not None else IPCServer(self._socket_path)
        self._injector = injector if injector is not None else TextInjector()
        self._tts_client = tts_client if tts_client is not None else TTSClient(
            api_key=self._config["xai"]["api_key"],
            voice_id=self._config["tts"]["voice_id"],
            language=self._config["tts"]["language"],
        )
        self._audio_capture: Optional[AudioCapture] = None
        self._stt_client: Optional[STTClient] = None
        self._recording = False
        self._send_task: Optional[asyncio.Task] = None
        self._receive_task: Optional[asyncio.Task] = None
        self._final_text: str = ""
        self._interim_text: str = ""
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._hotkey_listener: Optional[HotkeyListener] = None
        self._hotkey_lock: Optional[asyncio.Lock] = None

    async def run(self) -> None:
        self._loop = asyncio.get_running_loop()
        self._hotkey_lock = asyncio.Lock()
        self._injector.start()
        self._ipc_server.start()
        self._start_hotkey_listener()
        logger.info("Daemon started, socket: %s", self._socket_path)

        ipc_thread = threading.Thread(target=self._ipc_loop, daemon=True)
        ipc_thread.start()

        stop_event = asyncio.Event()

        def _signal_handler():
            logger.info("Received shutdown signal")
            stop_event.set()

        for sig in (signal.SIGINT, signal.SIGTERM):
            self._loop.add_signal_handler(sig, _signal_handler)

        await stop_event.wait()
        await self._shutdown()

    async def _shutdown(self) -> None:
        logger.info("Shutting down daemon")
        if self._hotkey_listener:
            self._hotkey_listener.stop()
            self._hotkey_listener = None
        if self._recording:
            await self._stop_recording()
        self._injector.stop()
        self._ipc_server.stop()

    def _start_hotkey_listener(self) -> None:
        try:
            self._hotkey_listener = HotkeyListener(
                self._config.get("hotkey", {}),
                on_toggle=lambda: self._schedule_hotkey_action("toggle"),
                on_hold_start=lambda: self._schedule_hotkey_action("start"),
                on_hold_stop=lambda: self._schedule_hotkey_action("stop"),
            )
            self._hotkey_listener.start()
        except Exception:
            logger.exception("Failed to start hotkey listener")
            self._hotkey_listener = None

    def _schedule_hotkey_action(self, action: str) -> None:
        if self._loop is None or self._loop.is_closed():
            return
        asyncio.run_coroutine_threadsafe(self._handle_hotkey_action(action), self._loop)

    async def _handle_hotkey_action(self, action: str) -> None:
        if self._hotkey_lock is None:
            self._hotkey_lock = asyncio.Lock()
        async with self._hotkey_lock:
            try:
                if action == "toggle":
                    if self._recording:
                        await self._hotkey_stop_recording()
                    else:
                        await self._hotkey_start_recording()
                elif action == "start":
                    await self._hotkey_start_recording(hold_to_talk=True)
                elif action == "stop":
                    await self._hotkey_stop_recording()
            except Exception as exc:
                logger.exception("Hotkey action failed: %s", action)
                await self._show_hotkey_overlay("error", detail=str(exc), timeout_ms=3000)

    async def _show_hotkey_overlay(
        self,
        state: str,
        *,
        detail: str = "",
        timeout_ms: int = 0,
    ) -> None:
        from voice_keyboard.client import _show_overlay

        await asyncio.to_thread(
            _show_overlay,
            state,
            detail=detail,
            timeout_ms=timeout_ms,
        )

    async def _hotkey_start_recording(self, *, hold_to_talk: bool = False) -> None:
        if self._recording:
            return
        mode = self._config.get("hotkey", {}).get("mode", "auto")
        if hold_to_talk or mode == "hold":
            listening_detail = "Release Ctrl+Space to stop"
        elif mode == "auto":
            listening_detail = "Tap Ctrl+Space again to stop; hold to talk"
        else:
            listening_detail = "Press Ctrl+Space again to stop"
        await self._show_hotkey_overlay("starting")
        await self._start_recording()
        await self._show_hotkey_overlay("listening", detail=listening_detail)

    async def _hotkey_stop_recording(self) -> None:
        if not self._recording:
            return
        await self._show_hotkey_overlay("processing")
        final = await self._stop_recording()
        if final:
            await self._show_hotkey_overlay(
                "inserted",
                detail=f"Inserted {len(final)} characters",
                timeout_ms=1800,
            )
        else:
            await self._show_hotkey_overlay("empty", timeout_ms=2200)

    def _ipc_loop(self) -> None:
        while True:
            try:
                conn = self._ipc_server.accept()
            except OSError:
                break
            try:
                data = recv_all(conn)
                if not data:
                    continue
                msg = json.loads(data.decode("utf-8"))
                command = msg.get("command", "")
                payload = msg.get("payload", {})

                if command == "start":
                    future = asyncio.run_coroutine_threadsafe(
                        self._start_recording(), self._loop
                    )
                    try:
                        future.result(timeout=12)
                    except TimeoutError:
                        future.cancel()
                        # Wait for the cancellation to propagate before
                        # cleaning up, so we don't race with a coroutine that
                        # is still mid-await inside connect().
                        try:
                            future.result(timeout=5)
                        except (TimeoutError, asyncio.CancelledError, Exception):
                            pass
                        cleanup = asyncio.run_coroutine_threadsafe(
                            self._cleanup_after_failed_start(), self._loop
                        )
                        cleanup.result(timeout=5)
                        response = {
                            "status": "error",
                            "message": "timed out connecting to xAI STT",
                        }
                    else:
                        response = {"status": "ok", "message": "recording started"}

                elif command == "stop":
                    future = asyncio.run_coroutine_threadsafe(
                        self._stop_recording(), self._loop
                    )
                    try:
                        result = future.result(timeout=18)
                    except TimeoutError:
                        response = {
                            "status": "error",
                            "message": "timed out stopping recording",
                        }
                    else:
                        response = {
                            "status": "ok",
                            "message": "recording stopped",
                            "text": result,
                        }

                elif command == "tts":
                    text = payload.get("text", "")
                    if text:
                        future = asyncio.run_coroutine_threadsafe(
                            self._run_tts(text), self._loop
                        )
                        try:
                            future.result(timeout=33)
                        except TimeoutError:
                            response = {
                                "status": "error",
                                "message": "timed out playing TTS",
                            }
                        else:
                            response = {"status": "ok", "message": "tts played"}
                    else:
                        response = {"status": "error", "message": "no text provided"}

                elif command == "status":
                    response = {
                        "status": "ok",
                        "recording": self._recording,
                    }

                else:
                    response = {"status": "error", "message": f"unknown command: {command}"}

                conn.sendall(json.dumps(response).encode("utf-8"))
            except Exception as exc:
                logger.exception("Error handling IPC command")
                try:
                    conn.sendall(
                        json.dumps({"status": "error", "message": str(exc)}).encode("utf-8")
                    )
                except OSError:
                    pass
            finally:
                try:
                    conn.close()
                except OSError:
                    pass

    async def _start_recording(self) -> None:
        if self._recording:
            return

        api_key = self._config["xai"]["api_key"]
        if not api_key:
            raise RuntimeError("xAI API key not configured")

        self._audio_capture = AudioCapture(
            sample_rate=self._config["audio"]["sample_rate"],
            chunk_ms=self._config["audio"]["chunk_ms"],
            device_name=self._config["audio"]["device_name"],
        )
        try:
            self._audio_capture.start()
        except Exception:
            self._audio_capture = None
            raise

        self._stt_client = STTClient(
            api_key=api_key,
            language=self._config["stt"]["language"],
            interim_results=self._config["stt"].get("interim_results", True),
        )
        try:
            await self._stt_client.connect(self._audio_capture.sample_rate)
        except Exception:
            # Roll back partial state so a failed start doesn't leak the
            # audio capture handle or leave the daemon in a half-open state.
            await self._cleanup_after_failed_start()
            raise

        self._final_text = ""
        self._interim_text = ""
        self._recording = True

        self._receive_task = asyncio.create_task(self._receive_events())
        self._send_task = asyncio.create_task(self._stream_audio())
        logger.info("Recording started")

    async def _cleanup_after_failed_start(self) -> None:
        """Roll back resources allocated by a failed _start_recording attempt.

        Called from the IPC loop when the start coroutine times out (after
        the future is cancelled) or from _start_recording itself when STT
        connect raises. Safe to call multiple times.
        """
        self._recording = False
        if self._audio_capture is not None:
            try:
                self._audio_capture.stop()
            except Exception:
                logger.exception("Error stopping audio capture after failed start")
            self._audio_capture = None
        if self._stt_client is not None:
            try:
                await self._stt_client.close()
            except Exception:
                pass
            self._stt_client = None
        self._final_text = ""
        self._interim_text = ""

    async def _stop_recording(self) -> str:
        if not self._recording:
            return ""

        self._recording = False

        # Let any in-flight PyAudio read complete before closing the stream.
        # Closing a PortAudio stream from another thread while read() is active
        # can segfault inside the native library.
        if self._send_task:
            try:
                await asyncio.wait_for(self._send_task, timeout=3.0)
            except asyncio.TimeoutError:
                logger.warning("Timed out waiting for audio streaming task to stop")
                self._send_task.cancel()
                try:
                    await self._send_task
                except asyncio.CancelledError:
                    pass
            except asyncio.CancelledError:
                pass
            self._send_task = None

        if self._audio_capture:
            audio_capture = self._audio_capture
            self._audio_capture = None
            await asyncio.to_thread(audio_capture.stop)

        if self._stt_client:
            try:
                await self._stt_client.send_audio_done()
            except Exception:
                logger.exception("Error sending audio.done")

            if self._receive_task:
                try:
                    await asyncio.wait_for(self._receive_task, timeout=5.0)
                except asyncio.TimeoutError:
                    self._receive_task.cancel()
                    try:
                        await self._receive_task
                    except asyncio.CancelledError:
                        pass
                except asyncio.CancelledError:
                    pass
                self._receive_task = None

            try:
                await self._stt_client.close()
            except Exception:
                pass
            self._stt_client = None

        final = _dedupe_repeated_transcript_text(
            _merge_transcript_text(self._final_text, self._interim_text)
        )
        if final:
            await asyncio.to_thread(self._injector.type_text, final)
            logger.info("Injected %d characters", len(final))
        else:
            logger.info("No transcript received")

        return final

    async def _stream_audio(self) -> None:
        while self._recording and self._stt_client:
            try:
                audio_capture = self._audio_capture
                if audio_capture is None:
                    break
                chunk = await asyncio.to_thread(audio_capture.read_chunk)
                if not self._recording or self._stt_client is None:
                    break
                await self._stt_client.send_audio(chunk)
            except Exception:
                if self._recording:
                    logger.exception("Error streaming audio")
                break

    async def _receive_events(self) -> None:
        try:
            async for event in self._stt_client.receive_events():
                event_type = event.get("type", "")
                if event_type == "transcript.partial":
                    self._interim_text = event.get("text", "")
                    logger.debug("Interim: %r", self._interim_text)
                    if event.get("is_final"):
                        self._final_text = _merge_transcript_text(
                            self._final_text,
                            self._interim_text,
                        )
                        self._interim_text = ""
                elif event_type == "transcript.done":
                    self._final_text = _merge_transcript_text(
                        self._final_text,
                        event.get("text", ""),
                    )
                    self._interim_text = ""
                    logger.debug("Final transcript received")
                    break
                elif event_type == "error":
                    logger.error("STT error event: %s", event.get("message", event))
                    break
        except Exception:
            if self._recording:
                logger.exception("Error receiving STT events")

    async def _run_tts(self, text: str) -> None:
        await asyncio.to_thread(self._tts_client.synthesize_and_play, text)


def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )
    daemon = Daemon()
    try:
        asyncio.run(daemon.run())
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
