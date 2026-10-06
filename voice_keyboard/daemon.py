import asyncio
import json
import logging
import signal
import sys
import threading
import time
from typing import Optional

from voice_keyboard import clipboard, dictionary, history, latency, recall
from voice_keyboard.ambient import AmbientGate
from voice_keyboard.assistant import Brain, create_brain
from voice_keyboard.audio_capture import AudioCapture
from voice_keyboard.config import _config_dir, load_config, validate_config
from voice_keyboard.flow import FlowConfig, FlowEngine, Grammar, InjectionWorker
from voice_keyboard.flow import nav
from voice_keyboard.flow.engine import FinalResult, NavAction, risky_backspace
from voice_keyboard.flow.registers import (
    Register,
    register_for_app,
    resolve_register,
)
from voice_keyboard.flow.vad import OnsetDetector, SilenceGate, chunk_rms, vu_bar
from voice_keyboard.flow.worker import common_prefix_len
from voice_keyboard.focusprobe import FocusInfo, probe_focus
from voice_keyboard.hotkey import HotkeyListener, create_hotkey_listener, pretty_binding
from voice_keyboard.injector import TextInjector, create_injector
from voice_keyboard.ipc import IPCServer, recv_all
from voice_keyboard.llm import create_llm_client, llm_ready
from voice_keyboard.prefetch import SelectionWatcher, prefetch_enabled
from voice_keyboard.remotemic import RemoteAudioSource, RemoteMicServer
from voice_keyboard.stt import create_stt_client

# Re-exported for backwards compatibility: these lived here before they
# moved to voice_keyboard.transcript.
from voice_keyboard.transcript import (  # noqa: F401
    _dedupe_repeated_transcript_text,
    _join_transcript_text,
    _merge_transcript_text,
    _reconcile_utterance,
    _transcript_words,
    _word_prefix_overlap,
    _word_sequence_contains,
    _word_sequence_endswith,
    _word_sequence_startswith,
)
from voice_keyboard.tts import TTSClient, create_tts_client

logger = logging.getLogger(__name__)

FLOW_TICK_S = 0.25
# Pause reviews ([flow] pause_review): one call's limit, and how long the
# stop path waits for the last ones before the rules decide.
PAUSE_REVIEW_CALL_S = 5.0
PAUSE_REVIEW_FINAL_S = 2.5
FOCUS_WATCHDOG_S = 1.5
# A hands-free Kai question (a tap, or the wake word) ends after this much
# trailing silence — you just stop talking, no second press.
CONVERSE_AUTO_STOP_MS = 1500
# Hard cap on one Kai turn (route + answer + speech). A wedged LLM endpoint
# or a hung websocket must surface as a visible error, never a stuck overlay.
CONVERSE_TURN_TIMEOUT_S = 90.0
CAPTION_MAX_CHARS = 46
# A held rewrite that is neither kept nor discarded evaporates.
PENDING_REWRITE_TTL_S = 120.0


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
        self._injector = injector if injector is not None else create_injector()
        self._tts_client = tts_client if tts_client is not None else create_tts_client(self._config)
        self._audio_capture: Optional[AudioCapture] = None
        self._stt_client = None
        self._recording = False
        self._send_task: Optional[asyncio.Task] = None
        self._receive_task: Optional[asyncio.Task] = None
        self._final_text: str = ""
        self._interim_text: str = ""
        # The provider sends chunks and utterances (xAI's speech_final
        # protocol): finals are appended as they come, never merged.
        self._chunked_stt = False
        self._utterance_start = 0  # where the current utterance begins in _final_text
        self._stt_error: Optional[str] = None
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._hotkey_listener: Optional[HotkeyListener] = None
        self._assistant_hotkey_listener: Optional[HotkeyListener] = None
        # [tts] hotkey: read the highlighted text aloud (press again to stop).
        self._read_hotkey_listener: Optional[HotkeyListener] = None
        self._read_task: Optional[asyncio.Task] = None
        # Set to stop the current read wherever it is: fetching, decoding,
        # or playing (a thread can't be cancelled, so playback checks it).
        self._read_cancel: Optional[threading.Event] = None
        # The same for Kai's spoken answer (barge-in).
        self._converse_cancel_flag: Optional[threading.Event] = None
        self._stop_event: Optional[asyncio.Event] = None
        # A stop requested before run() has its loop (a Quit clicked while
        # the daemon is still starting) must not be lost.
        self._stop_requested = False
        self._hotkey_lock: Optional[asyncio.Lock] = None
        # The conversational mind (None until [assistant] enabled).
        self._brain: Optional[Brain] = create_brain(self._config)
        # This recording session routes to the brain, not the keyboard.
        self._converse_capture = False
        self._converse_pcm: list[bytes] = []
        # A Kai turn (route + answer + speak) runs here, OFF the hotkey lock,
        # so the daemon never freezes and a second press can cut it off.
        self._converse_task: Optional[asyncio.Task] = None
        # Hands-free summon (a tap, or the wake word) ends on silence; a hold
        # ends on release.
        self._converse_hands_free = False
        self._last_answer = ""

        # Flow — molten dictation session state.
        self._flow_engine: Optional[FlowEngine] = None
        self._flow_worker: Optional[InjectionWorker] = None
        self._flow_ticker: Optional[asyncio.Task] = None
        self._focus_watchdog: Optional[asyncio.Task] = None
        # The [llm] client reviewing punctuation at pauses (None = rules only).
        self._pause_reviewer = None
        self._pause_tasks: set = set()
        self._session_focus: Optional[FocusInfo] = None
        self._session_register: Register = resolve_register(
            self._config.get("registers", {}).get("default", "prose")
        )
        self._focus_lost = False
        self._session_secret = False
        self._ambient_gate: Optional[AmbientGate] = None
        self._silence_gate: Optional[SilenceGate] = None
        self._auto_stop_started = False
        self._levels: list[float] = []
        # Was any non-zero sample captured this session? A blocked mic
        # (Windows privacy settings) delivers pure digital silence.
        self._chunks_seen = 0
        self._heard_signal = False
        # The session's audio came from the phone (remote mic), not this
        # PC's microphone: its silence says nothing about mic privacy.
        self._session_remote_audio = False
        self._last_caption = ""
        self._last_typed = ""
        self._last_error = ""
        # Molten diffs: a rewrite held for approval ([flow] rewrite_pending).
        self._pending_rewrite: Optional[dict] = None
        self._last_scratches = 0
        # Hands-free navigation ([nav]): this session's chord table, and
        # the task pressing a command's keys mid-dictation.
        self._nav_keys: dict = {}
        self._nav_task: Optional[asyncio.Task] = None
        # Per-utterance latency for `voice-keyboard stats`.
        self._timer: Optional[latency.UtteranceTimer] = None
        self._onset: Optional[OnsetDetector] = None
        self._latency = latency.LatencyLog(
            persist=bool(self._config.get("flow", {}).get("latency_log", False))
        )
        # Speculative TTS: (text, audio) for the last stable selection.
        self._tts_cache: Optional[tuple[str, bytes]] = None
        self._prefetch_watcher: Optional[SelectionWatcher] = None
        # The multiplayer keyboard: a phone-fed session replaces PyAudio.
        self._remote_mic: Optional[RemoteMicServer] = None
        self._audio_source_override: Optional[RemoteAudioSource] = None
        # Wake word "Kai" — opt-in, always-on LOCAL detector (None unless
        # [wake] enabled).
        self._wake_listener = None
        self._started_at = time.monotonic()
        self._config_mtime = self._current_config_mtime()

    async def run(self) -> None:
        from voice_keyboard import client as _client

        _client.mark_daemon_process()
        if self._stop_requested:
            return
        self._stop_event = asyncio.Event()
        self._loop = asyncio.get_running_loop()
        if self._stop_requested:  # raced with request_stop() above
            self._stop_event.set()
        self._hotkey_lock = asyncio.Lock()
        self._injector.start()
        try:
            self._ipc_server.start()
        except BaseException:
            self._injector.stop()
            raise
        try:
            self._start_services()
        except BaseException:
            # A half-started daemon must release its port and listeners, or
            # an in-process restart can't bind again.
            await self._shutdown()
            raise
        logger.info(
            "Daemon started, socket: %s", getattr(self._ipc_server, "endpoint", self._socket_path)
        )

        ipc_thread = threading.Thread(target=self._ipc_loop, daemon=True)
        ipc_thread.start()

        stop_event = self._stop_event

        def _signal_handler():
            logger.info("Received shutdown signal")
            stop_event.set()

        for sig in (signal.SIGINT, signal.SIGTERM):
            try:
                self._loop.add_signal_handler(sig, _signal_handler)
            except NotImplementedError:
                # Windows event loops can't add signal handlers; Ctrl+C still
                # raises KeyboardInterrupt in the main thread.
                signal.signal(sig, lambda *_: _signal_handler())

        await stop_event.wait()
        await self._shutdown()

    def _start_services(self) -> None:
        """Everything after the injector and the IPC server: hotkeys, the
        overlay's orb, prefetch, the remote mic, the wake word."""
        self._start_hotkey_listener()
        self._start_assistant_hotkey_listener()
        self._start_read_hotkey_listener()
        # The Windows hook installs on its own thread; say so if Windows
        # refused it, or the hotkeys would just silently do nothing.
        self._loop.call_later(1.5, self._check_hotkey_hooks)
        self._push_button_visibility()
        # Re-push shortly after start in case the shell extension was not yet
        # listening on the bus (daemon-before-extension ordering).
        self._loop.call_later(1.6, self._push_button_visibility)
        if prefetch_enabled(self._config):
            # A separate TTS client: the watcher thread never shares a
            # requests session with the playback path.
            self._prefetch_watcher = SelectionWatcher(
                tts_client=create_tts_client(self._config),
                store=self._store_tts_prefetch,
                is_busy=lambda: self._recording,
            )
            self._prefetch_watcher.start()
        mic_cfg = self._config.get("remote_mic", {})
        if bool(mic_cfg.get("enabled", False)):
            self._remote_mic = RemoteMicServer(
                port=int(mic_cfg.get("port", 9177)),
                token=str(mic_cfg.get("token", "")).strip(),
                on_start=self._schedule_remote_start,
                on_stop=self._schedule_remote_stop,
            )
            self._remote_mic.start()
        self._start_wake_listener()

    def _check_hotkey_hooks(self) -> None:
        for listener in (
            self._hotkey_listener, self._assistant_hotkey_listener, self._read_hotkey_listener
        ):
            error = getattr(listener, "hook_error", None)
            if not isinstance(error, int) or isinstance(error, bool):
                continue
            message = f"Windows refused the keyboard hook (error {error}): hotkeys won't work"
            logger.error("%s", message)
            self._last_error = message
            asyncio.ensure_future(
                self._show_hotkey_overlay("error", detail=message, timeout_ms=6000)
            )
            return

    @property
    def recording(self) -> bool:
        return self._recording

    @property
    def assistant_enabled(self) -> bool:
        return self._brain is not None

    @property
    def conversing(self) -> bool:
        """Kai is capturing a question or running a turn."""
        return self._converse_capture or bool(
            self._converse_task and not self._converse_task.done()
        )

    def request_stop(self) -> None:
        """Ask the daemon to shut down cleanly — also before or while it
        starts. Thread-safe (the Windows tray's Quit and the IPC `quit`
        command use it)."""
        self._stop_requested = True
        loop, event = self._loop, self._stop_event
        if loop is None or event is None or loop.is_closed():
            return  # run() sees _stop_requested
        loop.call_soon_threadsafe(event.set)

    def schedule_action(self, action: str) -> None:
        """Run a hotkey action (toggle, converse_toggle, read_selection,
        read_clipboard, ...) from any thread — the tray menu's entry point."""
        self._schedule_hotkey_action(action)

    async def _shutdown(self) -> None:
        """Stop everything. The injector and the IPC server are released
        whatever else fails — an in-process restart must be able to bind
        again, and a dead daemon must not keep answering commands."""
        logger.info("Shutting down daemon")
        try:
            for name in (
                "_wake_listener", "_remote_mic", "_prefetch_watcher", "_hotkey_listener",
                "_assistant_hotkey_listener", "_read_hotkey_listener",
            ):
                part = getattr(self, name)
                if part is not None:
                    setattr(self, name, None)
                    try:
                        part.stop()
                    except Exception:
                        logger.exception("Stopping %s failed", name.strip("_"))
            if await self._stop_reading():
                # Its pill was shown without a timeout: take it down too.
                try:
                    from voice_keyboard.client import _stop_overlay

                    await asyncio.to_thread(_stop_overlay)
                except Exception:
                    logger.debug("Could not hide the overlay", exc_info=True)
            if self._recording:
                await self._stop_recording()
        except Exception:
            logger.exception("Error while shutting down; releasing resources anyway")
        finally:
            try:
                self._injector.stop()
            except Exception:
                logger.exception("Stopping the injector failed")
            self._ipc_server.stop()

    def _start_hotkey_listener(self) -> None:
        try:
            self._hotkey_listener = create_hotkey_listener(
                self._config.get("hotkey", {}),
                on_toggle=lambda: self._schedule_hotkey_action("toggle"),
                on_hold_start=lambda: self._schedule_hotkey_action("start"),
                on_hold_stop=lambda: self._schedule_hotkey_action("stop"),
            )
            self._hotkey_listener.start()
        except Exception:
            logger.exception("Failed to start hotkey listener")
            self._hotkey_listener = None

    def _start_assistant_hotkey_listener(self) -> None:
        """A SECOND global hotkey to summon Kai — the mind, not the keyboard.

        Always bound when a binding exists (even if the mind is off, so a
        press gives a helpful hint rather than silence), and it behaves like
        dictation: [assistant].mode = auto means HOLD to talk / release to
        send, or a quick TAP to toggle."""
        assistant_cfg = self._config.get("assistant", {})
        binding = str(assistant_cfg.get("hotkey", "")).strip()
        if not binding:
            return
        mode = str(assistant_cfg.get("mode", "auto")).strip().lower() or "auto"
        hold_ms = int(self._config.get("hotkey", {}).get("hold_threshold_ms", 280))
        # A bare-modifier binding (default: rightctrl) is the terminal-safe
        # summon — a modifier alone never reaches the focused app, so holding
        # it can't spray escape codes into a shell the way a held symbol
        # chord (Ctrl+Alt+.) does. A bare TAP is ignored when idle (too easy
        # to brush); it still ends a capture or barges in on a running turn.
        bare = "+" not in binding
        tap_action = "converse_tap" if bare else "converse_toggle"
        try:
            self._assistant_hotkey_listener = create_hotkey_listener(
                {
                    "enabled": True,
                    "key": binding,
                    "mode": mode,
                    "hold_threshold_ms": hold_ms,
                    "allow_bare": True,
                },
                on_toggle=lambda: self._schedule_hotkey_action(tap_action),
                on_hold_start=lambda: self._schedule_hotkey_action("converse_start"),
                on_hold_stop=lambda: self._schedule_hotkey_action("converse_stop"),
                on_hold_cancel=lambda: self._schedule_hotkey_action("converse_cancel"),
            )
            self._assistant_hotkey_listener.start()
            logger.info("Assistant hotkey listening: %s (%s)", binding, mode)
        except Exception:
            logger.exception("Failed to start assistant hotkey listener")
            self._assistant_hotkey_listener = None

    def _start_read_hotkey_listener(self) -> None:
        """[tts] hotkey: one press reads the highlighted text aloud, the
        next press stops it. Fires on press (toggle), so it feels instant."""
        binding = str(self._config.get("tts", {}).get("hotkey", "")).strip()
        if not binding:
            return
        try:
            self._read_hotkey_listener = create_hotkey_listener(
                {"enabled": True, "key": binding, "mode": "toggle", "allow_bare": True},
                on_toggle=lambda: self._schedule_hotkey_action("read_selection"),
                on_hold_start=lambda: None,
                on_hold_stop=lambda: None,
            )
            self._read_hotkey_listener.start()
            logger.info("Read-aloud hotkey listening: %s", binding)
        except Exception:
            logger.exception("Failed to start read-aloud hotkey listener")
            self._read_hotkey_listener = None

    def _start_wake_listener(self) -> None:
        """Arm the opt-in local wake word. A detection fires the same summon
        toggle as the hotkey, hands-free."""
        from voice_keyboard.wake import WakeListener, wake_enabled

        if not wake_enabled(self._config):
            return
        try:
            self._wake_listener = WakeListener(
                config=self._config,
                on_wake=lambda: self._schedule_hotkey_action("converse_toggle"),
                is_busy=lambda: self._recording
                or bool(self._converse_task and not self._converse_task.done()),
            )
            self._wake_listener.start()
        except Exception:
            logger.exception("Failed to start wake listener")
            self._wake_listener = None

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
                elif action == "converse_start":
                    # A HOLD began — talk until release.
                    await self._converse_start(hands_free=False)
                elif action == "converse_stop":
                    await self._converse_stop()
                elif action == "converse_cancel":
                    await self._converse_cancel()
                elif action == "converse_toggle":
                    # A quick tap (or the button / wake word): cut Kai off if
                    # she's mid-turn, end an in-flight question, or start a
                    # new hands-free one. Never touches a live dictation
                    # session (that's the other key's job).
                    if self._converse_task and not self._converse_task.done():
                        await self._converse_cancel()
                    elif self._converse_capture:
                        await self._converse_stop()
                    elif self._recording:
                        await self._show_hotkey_overlay(
                            "processing",
                            detail="⌁ busy — dictation is live",
                            timeout_ms=1400,
                        )
                    else:
                        await self._converse_start(hands_free=True)
                elif action in {"read_selection", "read_clipboard"}:
                    await self._toggle_read_aloud(
                        from_selection=action == "read_selection"
                    )
                elif action == "converse_tap":
                    # A bare-modifier tap: barge in or end a capture, but
                    # NEVER open the mic — a stray Right-Ctrl tap is too easy
                    # to brush. Summon = hold (or the orb / wake word).
                    if self._converse_task and not self._converse_task.done():
                        await self._converse_cancel()
                    elif self._converse_capture:
                        await self._converse_stop()
            except Exception as exc:
                logger.exception("Hotkey action failed: %s", action)
                self._last_error = str(exc)
                await self._show_hotkey_overlay("error", detail=str(exc), timeout_ms=3000)

    async def _locked_start_recording(self) -> None:
        """IPC `start` under the same lock as the hotkeys. A GNOME custom
        shortcut spawning the CLI races the evdev listener on the very same
        keypress; without this, two _start_recording coroutines interleave
        and fight over one STT websocket (ConcurrencyError)."""
        if self._hotkey_lock is None:
            self._hotkey_lock = asyncio.Lock()
        async with self._hotkey_lock:
            await self._start_recording()

    async def _locked_stop_recording(self) -> str:
        if self._hotkey_lock is None:
            self._hotkey_lock = asyncio.Lock()
        async with self._hotkey_lock:
            return await self._stop_recording()

    async def _show_hotkey_overlay(
        self,
        state: str,
        *,
        detail: str = "",
        timeout_ms: int = 0,
        anchor: Optional[tuple[int, int]] = None,
    ) -> None:
        from voice_keyboard.client import _show_overlay

        await asyncio.to_thread(
            _show_overlay,
            state,
            detail=detail,
            timeout_ms=timeout_ms,
            anchor=anchor,
        )

    def _push_button_visibility(self) -> None:
        """Tell the overlay extension whether to draw the always-on Kai orb.
        The orb defaults visible, so we only need to hide it when the button
        is switched off or the mind is disabled; a missed call in the common
        case leaves it correctly shown."""
        cfg = self._config.get("assistant", {})
        visible = bool(cfg.get("button", True)) and bool(cfg.get("enabled", True))
        try:
            from voice_keyboard.client import _set_overlay_button

            _set_overlay_button(visible)
        except Exception:
            logger.debug("Could not push Kai button visibility")

    def _hotkey_label(self) -> str:
        """The dictation binding as people write it (Ctrl+Alt+V)."""
        return pretty_binding(str(self._config.get("hotkey", {}).get("key", "control+alt+v")))

    async def _hotkey_start_recording(self, *, hold_to_talk: bool = False) -> None:
        if self._recording:
            return
        mode = self._config.get("hotkey", {}).get("mode", "auto")
        label = self._hotkey_label()
        if hold_to_talk or mode == "hold":
            listening_detail = f"Release {label} to stop"
        elif mode == "auto":
            listening_detail = f"Tap {label} again to stop; hold to talk"
        else:
            listening_detail = f"Press {label} again to stop"
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
            await self._show_hotkey_overlay(
                "empty", detail=self._no_signal_hint(), timeout_ms=3200
            )

    def _no_signal_hint(self) -> str:
        """NO SIGNAL detail: name the likely cause when the mic delivered
        nothing but exact zeros for a whole session (over a second)."""
        if self._heard_signal or self._chunks_seen < 10 or self._session_remote_audio:
            return ""
        if sys.platform == "win32":
            return "Mic sent pure silence — Settings › Privacy & security › Microphone"
        return "Mic sent pure silence — is it muted?"

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

                required_token = getattr(self._ipc_server, "required_token", None)
                if required_token and msg.get("token") != required_token:
                    response = {"status": "error", "message": "invalid IPC token"}

                elif command == "start":
                    future = asyncio.run_coroutine_threadsafe(
                        self._locked_start_recording(), self._loop
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
                            "message": "timed out connecting to speech-to-text provider",
                        }
                    else:
                        response = {"status": "ok", "message": "recording started"}

                elif command == "stop":
                    stop_timeout = self._stop_recording_ipc_timeout()
                    future = asyncio.run_coroutine_threadsafe(
                        self._locked_stop_recording(), self._loop
                    )
                    try:
                        result = future.result(timeout=stop_timeout)
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

                elif command == "transform":
                    instruction = str(payload.get("instruction", "")).strip()
                    if not instruction:
                        response = {"status": "error", "message": "no instruction provided"}
                    else:
                        future = asyncio.run_coroutine_threadsafe(
                            self._transform_last(instruction), self._loop
                        )
                        try:
                            text = future.result(timeout=45)
                        except TimeoutError:
                            response = {
                                "status": "error",
                                "message": "timed out applying transform",
                            }
                        else:
                            response = {
                                "status": "ok",
                                "message": "transform applied",
                                "text": text,
                            }

                elif command == "ask":
                    question = str(payload.get("instruction", "")).strip()
                    if not question:
                        response = {"status": "error", "message": "no question provided"}
                    else:
                        future = asyncio.run_coroutine_threadsafe(
                            self._ask_last(question), self._loop
                        )
                        try:
                            text = future.result(timeout=90)
                        except TimeoutError:
                            response = {"status": "error", "message": "timed out answering"}
                        else:
                            response = {"status": "ok", "message": "answered", "text": text}

                elif command == "keep":
                    future = asyncio.run_coroutine_threadsafe(
                        self._keep_pending(), self._loop
                    )
                    try:
                        text = future.result(timeout=30)
                    except TimeoutError:
                        response = {"status": "error", "message": "timed out applying rewrite"}
                    else:
                        response = {"status": "ok", "message": "rewrite kept", "text": text}

                elif command == "discard":
                    future = asyncio.run_coroutine_threadsafe(
                        self._discard_pending(), self._loop
                    )
                    try:
                        had = future.result(timeout=10)
                    except TimeoutError:
                        response = {"status": "error", "message": "timed out"}
                    else:
                        response = (
                            {"status": "ok", "message": "rewrite discarded"}
                            if had
                            else {"status": "error", "message": "no pending rewrite"}
                        )

                elif command == "intent":
                    request = str(payload.get("instruction", "")).strip()
                    if not request:
                        response = {"status": "error", "message": "no request provided"}
                    else:
                        future = asyncio.run_coroutine_threadsafe(
                            self._intent_last(request), self._loop
                        )
                        try:
                            text = future.result(timeout=45)
                        except TimeoutError:
                            response = {
                                "status": "error",
                                "message": "timed out compiling the command",
                            }
                        else:
                            response = {
                                "status": "ok",
                                "message": "command typed — Enter is yours",
                                "text": text,
                            }

                elif command == "type":
                    text = str(payload.get("text", ""))
                    if not text:
                        response = {"status": "error", "message": "no text provided"}
                    else:
                        future = asyncio.run_coroutine_threadsafe(
                            self._type_text(text), self._loop
                        )
                        try:
                            future.result(timeout=max(15.0, len(text) * 0.01))
                        except TimeoutError:
                            response = {"status": "error", "message": "timed out typing"}
                        else:
                            response = {"status": "ok", "message": "typed"}

                elif command == "key":
                    keys = str(payload.get("keys", "")).strip()
                    names = [p.strip() for p in keys.split("+") if p.strip()]
                    if not names:
                        response = {"status": "error", "message": "no keys provided"}
                    else:
                        future = asyncio.run_coroutine_threadsafe(
                            self._press_keys(names), self._loop
                        )
                        try:
                            future.result(timeout=10)
                        except TimeoutError:
                            response = {"status": "error", "message": "timed out pressing keys"}
                        except Exception as exc:
                            response = {"status": "error", "message": str(exc)}
                        else:
                            response = {"status": "ok", "message": f"pressed {keys}"}

                elif command == "overlay":
                    # A CLI process asking the daemon to draw its overlay
                    # (Windows: the overlay lives in the daemon process).
                    from voice_keyboard.client import _show_overlay

                    anchor = None
                    if "x" in payload and "y" in payload:
                        anchor = (int(payload["x"]), int(payload["y"]))
                    _show_overlay(
                        str(payload.get("state", "processing")),
                        detail=str(payload.get("detail", "")),
                        timeout_ms=int(payload.get("timeout_ms", 0)),
                        anchor=anchor,
                    )
                    response = {"status": "ok", "message": "overlay shown"}

                elif command == "quit":
                    self.request_stop()
                    response = {"status": "ok", "message": "daemon stopping"}

                elif command == "converse":
                    # Summon Kai (or end/cancel a turn) — the same toggle the
                    # hotkey fires. Fire-and-forget: the turn is long and owns
                    # its own overlay, so don't block the caller (the orb) on
                    # it.
                    asyncio.run_coroutine_threadsafe(
                        self._handle_hotkey_action("converse_toggle"), self._loop
                    )
                    response = {"status": "ok", "message": "converse toggled"}

                elif command == "status":
                    response = self._status_response()

                elif command == "stats":
                    records = self._latency.records()
                    response = {
                        "status": "ok",
                        "summary": self._latency.summary(),
                        "count": len(records),
                        "last": records[-1] if records else None,
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

    def _status_response(self) -> dict:
        flow_cfg = self._config.get("flow", {})
        return {
            "status": "ok",
            "recording": self._recording,
            "stt_provider": str(self._config.get("stt", {}).get("provider", "")),
            "tts_provider": str(self._config.get("tts", {}).get("provider", "")),
            "register": self._session_register.name,
            "flow_enabled": bool(flow_cfg.get("enabled", True)),
            "flow_live": bool(flow_cfg.get("live", True)),
            "focused_app": self._session_focus.app if self._session_focus else "",
            "pending_rewrite": self._pending_rewrite is not None,
            "ambient": self._ambient_gate is not None,
            "assistant": self._brain is not None,
            "assistant_can_act": bool(self._config.get("assistant", {}).get("can_act", False)),
            "last_text_len": len(self._last_typed),
            "last_error": self._last_error,
            "uptime_s": int(time.monotonic() - self._started_at),
        }

    def _stt_completion_timeout(self) -> float:
        try:
            timeout = float(getattr(self._stt_client, "completion_timeout", 5.0))
        except (TypeError, ValueError):
            timeout = 5.0
        return max(5.0, timeout)

    def _stop_recording_ipc_timeout(self) -> float:
        return max(18.0, self._stt_completion_timeout() + 10.0)

    # ------------------------------------------------------------ config

    def _current_config_mtime(self) -> float:
        try:
            return (_config_dir() / "config.toml").stat().st_mtime
        except OSError:
            return 0.0

    def _maybe_reload_flow_config(self) -> None:
        """Adopt [flow]/[registers]/[llm]/[intent] edits without a restart.

        Provider, audio, hotkey, and daemon changes still need a restart —
        they own live resources. Reload happens at recording start, so a
        broken config never interrupts an active session.
        """
        mtime = self._current_config_mtime()
        if mtime == self._config_mtime:
            return
        self._config_mtime = mtime
        try:
            fresh = load_config()
            validate_config(fresh)
        except Exception as exc:
            logger.warning("Config changed but did not validate; keeping old: %s", exc)
            return
        for section in (
            "flow", "registers", "llm", "intent", "ambient", "ask", "recall", "assistant",
            "nav",
        ):
            self._config[section] = fresh.get(section, {})
        # Rebuild the brain so [assistant] edits (brain, can_act, agent_id,
        # privacy) take effect at the next turn without a restart. The
        # assistant HOTKEY still needs a restart — it owns a listener.
        self._brain = create_brain(self._config)
        self._latency.persist = bool(self._config["flow"].get("latency_log", False))
        logger.info("Reloaded flow/registers/llm/intent/ambient/ask/recall/assistant config")

    # ------------------------------------------------------- flow session

    def _build_grammar(self, register: Register) -> Grammar:
        flow_cfg = self._config.get("flow", {})
        vocabulary = dict(flow_cfg.get("vocabulary") or {})
        if flow_cfg.get("personal_dictionary", True):
            # Accepted `voice-keyboard learned` entries; explicit config wins.
            try:
                for spoken, replacement in dictionary.vocabulary_overrides().items():
                    vocabulary.setdefault(spoken, replacement)
            except Exception:
                logger.exception("Could not load the personal dictionary")
        return Grammar(
            enabled=bool(flow_cfg.get("grammar", True)) and register.grammar_enabled,
            commands=flow_cfg.get("commands") or {},
            punctuation=flow_cfg.get("punctuation") or {},
            vocabulary=vocabulary,
            wake_word=str(flow_cfg.get("wake_word", "vk")),
            numbers=str(flow_cfg.get("numbers", "auto")).lower(),
            numbers_on=register.numbers_on,
            numbers_min=register.numbers_min,
            spelling=bool(flow_cfg.get("spelling", True)),
            nav=self._nav_enabled(),
        )

    def _nav_enabled(self) -> bool:
        """[nav] is on and this platform's injector can press chords
        (macOS's cannot yet: the commands stay words there)."""
        if not bool(self._config.get("nav", {}).get("enabled", False)):
            return False
        return callable(getattr(self._injector, "press_combo", None))

    def _flow_config_obj(self, pause_review: str = "off") -> FlowConfig:
        flow_cfg = self._config.get("flow", {})
        defaults = FlowConfig()

        def _int(key: str, fallback: int) -> int:
            try:
                return max(1, int(flow_cfg.get(key, fallback)))
            except (TypeError, ValueError):
                return fallback

        return FlowConfig(
            live=bool(flow_cfg.get("live", True)),
            stability_ms=_int("stability_ms", defaults.stability_ms),
            stability_updates=_int("stability_updates", defaults.stability_updates),
            max_molten_chars=_int("max_molten_chars", defaults.max_molten_chars),
            adaptive=bool(flow_cfg.get("adaptive", True)),
            pause_review=pause_review,
        )

    def _pause_review_mode(self) -> str:
        """How this session settles punctuation at pauses: "llm" (rules,
        then the [llm] reviewer), "rules", or "off". Sets the reviewer."""
        self._pause_reviewer = None
        mode = str(self._config.get("flow", {}).get("pause_review", "auto")).strip().lower()
        if mode == "off":
            return "off"
        if mode in ("auto", "llm") and not self._session_secret:
            if llm_ready(self._config):
                self._pause_reviewer = create_llm_client(self._config)
            elif mode == "llm":
                logger.warning("flow.pause_review = llm, but [llm] has no usable endpoint/key")
        return "llm" if self._pause_reviewer is not None else "rules"

    async def _setup_flow_session(self, probe_task: Optional[asyncio.Task]) -> None:
        self._focus_lost = False
        self._session_secret = False
        self._auto_stop_started = False
        self._levels = []
        self._last_caption = ""

        flow_cfg = self._config.get("flow", {})
        self._ambient_gate = None
        if self._converse_capture:
            # Kai captures a spoken query but never types it live. It still
            # needs to know WHERE you are — a terminal query becomes a
            # drafted command, anything else becomes a spoken answer — so
            # resolve focus + register, but build no engine/worker.
            focus = None
            if probe_task is not None:
                try:
                    focus = await asyncio.wait_for(probe_task, timeout=1.5)
                except Exception:
                    focus = None
            self._session_focus = focus
            registers_cfg = self._config.get("registers", {})
            self._session_register = register_for_app(
                focus.app if focus else "",
                focus.role if focus else "",
                config_map=registers_cfg.get("map", {}) or {},
                default=str(registers_cfg.get("default", "prose")),
            )
            self._flow_engine = None
            self._flow_worker = None
            # A hands-free question ends itself on silence; a hold ends on
            # release, so no endpointer there.
            self._silence_gate = (
                SilenceGate(auto_stop_ms=CONVERSE_AUTO_STOP_MS)
                if self._converse_hands_free
                else None
            )
            return
        if not flow_cfg.get("enabled", True):
            self._flow_engine = None
            self._flow_worker = None
            self._session_focus = None
            self._silence_gate = None
            if probe_task is not None:
                probe_task.cancel()
            return

        focus: Optional[FocusInfo] = None
        if probe_task is not None:
            try:
                focus = await asyncio.wait_for(probe_task, timeout=1.5)
            except Exception:
                focus = None
        self._session_focus = focus

        registers_cfg = self._config.get("registers", {})
        register = register_for_app(
            focus.app if focus else "",
            focus.role if focus else "",
            config_map=registers_cfg.get("map", {}) or {},
            default=str(registers_cfg.get("default", "prose")),
        )
        self._session_register = register
        kind = "terminal" if register.terminal else "editor"
        self._nav_keys = nav.keymap(
            terminal=register.terminal,
            overrides=(self._config.get("nav", {}).get("keys", {}) or {}).get(kind),
        )
        if hasattr(self._injector, "paste_chord_shift"):
            self._injector.paste_chord_shift = register.paste_chord_shift

        # A secret widget gets maximum protection: verbatim register (set
        # above via the role), no ledger entry, no vocabulary bias.
        self._session_secret = bool(getattr(focus, "secret", False)) if focus else False
        bias = ""
        if not self._session_secret and bool(
            self._config.get("stt", {}).get("hotword_bias", False)
        ):
            bias = self._hotword_bias()
        if hasattr(self._stt_client, "bias_prompt"):
            self._stt_client.bias_prompt = bias

        ambient_cfg = self._config.get("ambient", {})
        if bool(ambient_cfg.get("enabled", False)):
            address = str(ambient_cfg.get("address_word", "")).strip() or str(
                flow_cfg.get("wake_word", "vk")
            ).strip()
            if address:
                self._ambient_gate = AmbientGate(address)
                logger.info("Ambient containment active: address word %r", address)

        self._flow_engine = FlowEngine(
            self._flow_config_obj(self._pause_review_mode()),
            self._build_grammar(register),
            register,
        )

        auto_stop_ms = flow_cfg.get("auto_stop_ms", 0)
        try:
            auto_stop_ms = max(0, int(auto_stop_ms))
        except (TypeError, ValueError):
            auto_stop_ms = 0
        self._silence_gate = (
            SilenceGate(auto_stop_ms=auto_stop_ms) if auto_stop_ms > 0 else None
        )

        live = bool(flow_cfg.get("live", True)) and bool(
            getattr(self._stt_client, "supports_streaming", False)
        )
        self._flow_worker = (
            InjectionWorker(
                self._injector,
                on_first_output=lambda: self._mark_latency("first_key"),
            )
            if live
            else None
        )
        logger.info(
            "Flow session: register=%s app=%r live=%s pause_review=%s",
            register.name,
            focus.app if focus else "",
            bool(self._flow_worker),
            self._flow_engine._cfg.pause_review,
        )

    async def _teardown_flow_session(self) -> None:
        if self._nav_task is not None:
            self._nav_task.cancel()
            self._nav_task = None
        for task in list(self._pause_tasks):
            task.cancel()
        self._pause_tasks.clear()
        self._pause_reviewer = None
        for task_attr in ("_flow_ticker", "_focus_watchdog"):
            task = getattr(self, task_attr)
            if task is not None:
                task.cancel()
                try:
                    await task
                except asyncio.CancelledError:
                    pass
                setattr(self, task_attr, None)
        if self._flow_worker is not None:
            await self._flow_worker.close()
            self._flow_worker = None
        self._flow_engine = None
        self._silence_gate = None

    async def _flow_ticker_loop(self) -> None:
        try:
            while self._recording:
                await asyncio.sleep(FLOW_TICK_S)
                engine = self._flow_engine
                if engine is None or not self._recording:
                    break
                worker = self._flow_worker
                if worker is not None:
                    engine.on_tick(time.monotonic())
                self._start_pause_reviews(engine)
                if worker is not None:
                    worker.set_target(engine.desired_text())
                    self._push_live_caption(engine)
                    self._maybe_run_nav(engine, worker)
        except asyncio.CancelledError:
            pass

    def _maybe_run_nav(self, engine: FlowEngine, worker: InjectionWorker) -> None:
        """Start pressing a navigation command's keys once the engine holds
        one — unless the hold-to-talk keys are still down (the chord would
        combine with them); then it fires at the next tick, or at stop."""
        if engine.pending_action() is None:
            return
        if self._nav_task is not None and not self._nav_task.done():
            return
        if self._hotkey_combo_held():
            return
        self._nav_task = asyncio.create_task(self._run_nav_live(engine, worker))

    def _hotkey_combo_held(self) -> bool:
        held = getattr(self._hotkey_listener, "combo_held", None)
        try:
            return bool(held()) if callable(held) else False
        except Exception:
            return False

    async def _run_nav_live(self, engine: FlowEngine, worker: InjectionWorker) -> None:
        action = engine.pending_action()
        if action is None:
            return
        await worker.drain(timeout=self._drain_timeout(engine.desired_text()))
        if engine is not self._flow_engine or engine.pending_action() is not action:
            return  # the dictation ended (stop handles the rest) or moved on
        await self._press_nav(action, abandoned=worker.abandoned)
        engine.complete_action(time.monotonic())
        worker.reset()
        worker.set_target(engine.desired_text())

    async def _press_nav(self, action: NavAction, *, abandoned: bool = False) -> bool:
        """Press a navigation command's chords; False when refused (this
        kind of app has no binding, focus moved, or typing failed)."""
        if abandoned or self._focus_lost:
            logger.info("nav: not pressing %s — focus moved or typing failed", action.action)
            return False
        chords = nav.chords_for(action.action, action.count, self._nav_keys)
        if chords is None:
            logger.info("nav: %s has no binding in this app", action.action)
            await self._show_hotkey_overlay(
                "error",
                detail=f"Can't {action.action.split(':')[0]} that here",
                timeout_ms=1800,
            )
            return False
        try:
            for chord in chords:
                await asyncio.to_thread(self._injector.press_combo, chord)
        except Exception as exc:
            logger.warning("nav: pressing %s failed: %s", action.action, exc)
            return False
        logger.info("nav: %s x%d", action.action, action.count)
        return True

    async def _await_nav_task(self) -> None:
        """Let a command already pressing its keys finish first, so stop
        never presses it twice."""
        task, self._nav_task = self._nav_task, None
        if task is not None and not task.done():
            try:
                await task
            except Exception:
                logger.exception("nav: live command failed")

    async def _finish_nav_at_stop(
        self, engine: FlowEngine, result: FinalResult, worker: Optional[InjectionWorker]
    ) -> FinalResult:
        """Stop reached navigation commands not yet pressed: put each
        segment on screen, press its keys, and move on to the next."""
        while result.action is not None:
            if worker is not None:
                worker.set_target(result.text)
                await worker.drain(timeout=self._drain_timeout(result.text))
                abandoned = worker.abandoned
            else:
                abandoned = self._focus_lost
                if result.text and not abandoned:
                    await asyncio.to_thread(self._injector.type_text, result.text)
            await self._press_nav(result.action, abandoned=abandoned)
            result = engine.complete_action(time.monotonic())
            if worker is not None:
                worker.reset()
        return result

    def _start_pause_reviews(self, engine: FlowEngine, *, final: bool = False) -> None:
        reviewer = self._pause_reviewer
        if reviewer is None:
            return
        for query in engine.review_requests(time.monotonic(), final=final):
            task = asyncio.create_task(self._review_pause(engine, reviewer, query))
            self._pause_tasks.add(task)
            task.add_done_callback(self._pause_tasks.discard)

    async def _review_pause(self, engine: FlowEngine, reviewer, query) -> None:
        """Ask the reviewer about one pause; the engine takes its answer (or
        falls back to the rules), and the screen follows."""
        try:
            answer = await asyncio.wait_for(
                asyncio.to_thread(reviewer.review_pause, query.before, query.after),
                timeout=PAUSE_REVIEW_CALL_S,
            )
        except Exception as exc:
            logger.info("Pause review failed; the rules decide: %s", exc)
            answer = None
        if not isinstance(answer, str):
            answer = None
        if engine is not self._flow_engine:
            return  # that dictation is over
        engine.resolve_pause(query.index, answer, now=time.monotonic())
        worker = self._flow_worker
        if worker is not None:
            worker.set_target(engine.desired_text())

    async def _settle_pause_reviews(self, engine: FlowEngine, merged: str) -> None:
        """At stop: the final words are in, so ask about every pause still
        open, and give the answers a moment before the rules take over."""
        if self._pause_reviewer is None:
            return
        engine.on_transcript(merged, is_final=True, now=time.monotonic())
        if not engine.pauses_pending():
            return
        self._start_pause_reviews(engine, final=True)
        pending = [task for task in self._pause_tasks if not task.done()]
        if pending:
            await asyncio.wait(pending, timeout=PAUSE_REVIEW_FINAL_S)

    def _push_live_caption(self, engine: FlowEngine) -> None:
        caption = engine.caption()
        if len(caption) > CAPTION_MAX_CHARS:
            caption = "…" + caption[-CAPTION_MAX_CHARS:]
        detail = f"{vu_bar(self._levels)} {caption}".rstrip()
        if detail == self._last_caption:
            return
        self._last_caption = detail
        anchor = (
            (self._session_focus.x, self._session_focus.y)
            if self._session_focus is not None
            else None
        )
        asyncio.create_task(
            self._show_hotkey_overlay("listening", detail=detail, anchor=anchor)
        )

    async def _focus_watchdog_loop(self) -> None:
        focus = self._session_focus
        if focus is None or not focus.identity:
            return
        baseline = focus.identity
        try:
            while self._recording:
                await asyncio.sleep(FOCUS_WATCHDOG_S)
                if not self._recording:
                    break
                current = await asyncio.to_thread(probe_focus)
                if current is None or not current.identity:
                    continue
                if current.identity != baseline:
                    self._focus_lost = True
                    worker = self._flow_worker
                    if worker is not None:
                        worker.abandon()
                    logger.warning(
                        "Focus moved from %r to %r during dictation; typing frozen",
                        baseline,
                        current.identity,
                    )
                    await self._show_hotkey_overlay(
                        "error",
                        detail="Focus changed — typing frozen; transcript goes to the clipboard",
                        timeout_ms=2600,
                    )
                    break
        except asyncio.CancelledError:
            pass

    # --------------------------------------------------------- recording

    async def _start_recording(self) -> None:
        if self._recording:
            return
        self._timer = latency.UtteranceTimer()
        self._timer.mark("start")
        self._onset = OnsetDetector()
        self._maybe_reload_flow_config()
        validate_config(self._config)

        flow_cfg = self._config.get("flow", {})
        probe_task: Optional[asyncio.Task] = None
        if flow_cfg.get("enabled", True) and self._config.get("registers", {}).get(
            "probe", True
        ):
            # Kick the focus probe early so it overlaps the STT connect.
            probe_task = asyncio.create_task(asyncio.to_thread(probe_focus))

        override = self._audio_source_override
        self._session_remote_audio = override is not None
        if override is not None:
            # A remote mic session: the phone's frames replace PyAudio.
            self._audio_capture = override
        else:
            self._audio_capture = AudioCapture(
                sample_rate=self._config["audio"]["sample_rate"],
                chunk_ms=self._config["audio"]["chunk_ms"],
                device_name=self._config["audio"]["device_name"],
            )
        try:
            self._audio_capture.start()
        except Exception:
            self._audio_capture = None
            if probe_task is not None:
                probe_task.cancel()
            raise

        self._stt_client = create_stt_client(self._config)
        try:
            await self._stt_client.connect(self._audio_capture.sample_rate)
        except Exception:
            # Roll back partial state so a failed start doesn't leak the
            # audio capture handle or leave the daemon in a half-open state.
            if probe_task is not None:
                probe_task.cancel()
            await self._cleanup_after_failed_start()
            raise

        await self._setup_flow_session(probe_task)

        self._final_text = ""
        self._interim_text = ""
        self._chunked_stt = False
        self._utterance_start = 0
        self._stt_error = None
        self._chunks_seen = 0
        self._heard_signal = False
        self._recording = True

        self._receive_task = asyncio.create_task(self._receive_events())
        self._send_task = asyncio.create_task(self._stream_audio())
        if self._flow_engine is not None:
            # Live typing's time-based commits, and pause reviews (which a
            # type-at-stop session wants too).
            self._flow_ticker = asyncio.create_task(self._flow_ticker_loop())
        if self._flow_worker is not None:
            self._flow_worker.start()
            self._focus_watchdog = asyncio.create_task(self._focus_watchdog_loop())
        logger.info("Recording started")

    async def _cleanup_after_failed_start(self) -> None:
        """Roll back resources allocated by a failed _start_recording attempt.

        Called from the IPC loop when the start coroutine times out (after
        the future is cancelled) or from _start_recording itself when STT
        connect raises. Safe to call multiple times.
        """
        self._recording = False
        await self._teardown_flow_session()
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
        self._stt_error = None

    async def _stop_recording(self) -> str:
        if not self._recording:
            return ""

        self._recording = False
        self._mark_latency("stop")

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
            except Exception as exc:
                logger.exception("Error sending audio.done")
                self._stt_error = f"failed to finalize audio: {exc}"

            if self._receive_task:
                receive_timeout = self._stt_completion_timeout()
                try:
                    await asyncio.wait_for(self._receive_task, timeout=receive_timeout)
                except asyncio.TimeoutError:
                    self._stt_error = (
                        "timed out waiting for speech-to-text provider "
                        f"after {receive_timeout:g}s"
                    )
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

        if self._stt_error:
            # Error path: never delete what was already typed live. Freeze
            # the screen as-is and surface the error.
            self._last_error = self._stt_error
            if self._flow_worker is not None:
                self._flow_worker.abandon()
            await self._teardown_flow_session()
            raise RuntimeError(self._stt_error)

        merged = self._session_transcript()

        if self._converse_capture:
            # A spoken question for the mind, not text for the keyboard. Hand
            # the turn to a background task so the hotkey lock is freed at
            # once: the daemon stays responsive and a second press can cut
            # Kai off mid-answer.
            self._converse_capture = False
            pcm = b"".join(self._converse_pcm)
            self._converse_pcm = []
            await self._teardown_flow_session()
            if pcm or merged.strip():
                self._converse_task = asyncio.create_task(
                    self._converse_turn(pcm, merged.strip())
                )
            else:
                await self._show_hotkey_overlay("empty", timeout_ms=1800)
            return ""

        engine = self._flow_engine
        if engine is None:
            # Flow disabled: the original type-at-stop behavior, unchanged.
            final = merged
            if final:
                await asyncio.to_thread(self._injector.type_text, final)
                self._mark_latency("first_key")
                logger.info("Injected %d characters", len(final))
            else:
                logger.info("No transcript received")
            self._record_latency(final)
            self._remember_typed(final)
            return final

        await self._settle_pause_reviews(engine, merged)
        await self._await_nav_task()
        result = engine.finalize(merged, now=time.monotonic())
        worker = self._flow_worker
        try:
            if result.action is not None:
                result = await self._finish_nav_at_stop(engine, result, worker)
            final = result.text
            self._last_scratches = result.scratches
            self._note_spellings(result.corrections)
            if worker is not None:
                final = await self._finish_live(worker, final, result.instruction)
            else:
                final = await self._finish_classic(final, result.instruction)
            if result.typed_before:
                # Earlier segments, before navigation moved the caret.
                final = (result.typed_before + " " + final).strip()
        finally:
            await self._teardown_flow_session()

        if final:
            logger.info("Inserted %d characters", len(final))
        else:
            logger.info("No transcript received")
        self._record_latency(final)
        self._remember_typed(final)
        return final

    async def _finish_live(
        self, worker: InjectionWorker, final: str, instruction: str
    ) -> str:
        """Reconcile a live-typed session at stop: converge the screen to
        the finalized text (never re-type it wholesale — the live worker
        already typed the bulk), then apply any wake-word instruction."""
        if self._focus_lost:
            typed = worker.screen
            if final and final != typed:
                if clipboard.set_text(final):
                    logger.info("Focus changed; final transcript is on the clipboard")
            return typed

        resolved = await self._maybe_resolve_pending(final, worker)
        if resolved is not None:
            return resolved

        worker.set_target(final)
        typed = await worker.drain(timeout=self._drain_timeout(final))
        self._mark_latency("settled")
        if typed != final:
            logger.warning(
                "Live injection finished at %d/%d characters", len(typed), len(final)
            )
            final = typed

        if instruction and final and not worker.abandoned:
            try:
                final = await self._run_transform(instruction, worker=worker)
            except Exception as exc:
                logger.warning("Voice transform failed: %s", exc)
                self._last_error = str(exc)
                await self._show_hotkey_overlay("error", detail=str(exc), timeout_ms=3000)
        elif instruction and not final:
            final = await self._transform_previous_or_report(instruction)
        return final

    async def _finish_classic(self, final: str, instruction: str) -> str:
        """Type-at-stop path (buffered providers or flow.live=false), with
        grammar and registers already applied by the engine."""
        resolved = await self._maybe_resolve_pending(final, None)
        if resolved is not None:
            return resolved
        if instruction and final:
            llm_client = create_llm_client(self._config)
            if llm_client is None:
                await self._show_hotkey_overlay(
                    "error", detail="[llm] is not configured", timeout_ms=3000
                )
            else:
                await self._show_hotkey_overlay("processing", detail="Transforming…")
                try:
                    final = await asyncio.to_thread(
                        llm_client.rewrite, final, instruction
                    )
                except Exception as exc:
                    logger.warning("Voice transform failed: %s", exc)
                    self._last_error = str(exc)
                    await self._show_hotkey_overlay(
                        "error", detail=str(exc), timeout_ms=3000
                    )
        elif instruction and not final:
            return await self._transform_previous_or_report(instruction)

        if final:
            if self._focus_lost:
                if clipboard.set_text(final):
                    logger.info("Focus changed; transcript is on the clipboard")
                    return ""
            await asyncio.to_thread(self._injector.type_text, final)
            self._mark_latency("first_key")
            self._mark_latency("settled")
        return final

    async def _transform_previous_or_report(self, instruction: str) -> str:
        """A standalone "vk, ..." utterance, routed by precedence:
        an exact macro name types its saved text; an intent verb types a
        command (never Enter); an ask verb answers about the selection; a
        recall verb searches the ledger; anything else rewrites the
        previous dictation in place."""
        try:
            macro = dictionary.macro_text(instruction)
            if macro is not None:
                return await self._run_macro(macro)
            if self._intent_request(instruction):
                return await self._run_intent(instruction)
            if self._verb_request("ask", instruction):
                return await self._run_ask(self._strip_verb(instruction, {"ask", "answer"}))
            if self._verb_request("recall", instruction):
                query = self._strip_verb(instruction, {"recall", "remember"})
                return await self._run_recall(query)
            return await self._run_transform(instruction, worker=None)
        except Exception as exc:
            logger.warning("Voice transform failed: %s", exc)
            self._last_error = str(exc)
            await self._show_hotkey_overlay("error", detail=str(exc), timeout_ms=3000)
            return ""

    def _drain_timeout(self, final: str) -> float:
        # ~6ms per key edge pair on the slowest backend, plus headroom.
        return max(6.0, len(final) * 0.012 + 4.0)

    def _hotword_bias(self) -> str:
        """User-accepted hotwords as an STT vocabulary prior — the only
        biasing signal that earns its keep: curated words the user
        actually says. (Screen text is NOT harvested: dictation is new
        thought, not a continuation of what is near the caret.)"""
        try:
            accepted = dictionary.hotwords()
        except Exception:
            accepted = []
        return ", ".join(accepted[:24])

    def _note_spellings(self, corrections) -> None:
        """Each "spell that ..." becomes a `voice-keyboard learned`
        candidate — offered, never applied until accepted."""
        if not corrections or self._session_secret:
            return
        if not self._config.get("flow", {}).get("personal_dictionary", True):
            return
        for heard, meant in corrections:
            try:
                dictionary.record_spelling(heard, meant)
            except Exception:
                logger.exception("Could not record a spelled correction")

    def _mark_latency(self, name: str) -> None:
        if self._timer is not None:
            self._timer.mark(name)

    def _record_latency(self, final: str) -> None:
        """Close this dictation's timer into the latency ring (never for a
        secret field, or a session that typed nothing)."""
        timer, self._timer = self._timer, None
        if timer is None or not final or self._session_secret:
            return
        timer.mark("settled")
        self._latency.record(
            timer,
            app=self._session_focus.app if self._session_focus else "",
            register=self._session_register.name,
        )

    def _remember_typed(self, final: str, *, register: str = "") -> None:
        if not final:
            return
        if self._session_secret:
            logger.info("Secret field: not remembering what was typed")
            return
        self._last_typed = final
        self._last_error = ""
        if self._config.get("flow", {}).get("history", False):
            history.append_entry(
                final,
                app=self._session_focus.app if self._session_focus else "",
                register=register or self._session_register.name,
            )

    # -------------------------------------------------------- transforms

    async def _transform_last(self, instruction: str) -> str:
        """IPC `transform`: rewrite the last dictation in place."""
        if self._hotkey_lock is None:
            self._hotkey_lock = asyncio.Lock()
        async with self._hotkey_lock:
            if self._recording:
                raise RuntimeError("stop recording before transforming")
            text = await self._run_transform(instruction, worker=None)
            self._remember_typed(text)
            return text

    async def _run_transform(
        self, instruction: str, *, worker: Optional[InjectionWorker]
    ) -> str:
        llm_client = create_llm_client(self._config)
        if llm_client is None:
            raise RuntimeError("[llm] is not configured")

        target = worker.screen if worker is not None and worker.screen else self._last_typed
        if not target:
            raise RuntimeError("nothing to transform yet")

        await self._show_hotkey_overlay("processing", detail=f"⌁ {instruction}")
        rewritten = await asyncio.to_thread(llm_client.rewrite, target, instruction)

        if await self._focus_changed_since_session():
            clipboard.set_text(rewritten)
            raise RuntimeError("focus changed — the rewrite is on the clipboard")

        if bool(self._config.get("flow", {}).get("rewrite_pending", False)):
            # Molten diffs: hold the rewrite; nothing touches the screen
            # until it is kept. The original text stays frozen in place.
            self._pending_rewrite = {
                "text": rewritten,
                "target": target,
                "expires": time.monotonic() + PENDING_REWRITE_TTL_S,
            }
            preview = rewritten if len(rewritten) <= 90 else rewritten[:87] + "…"
            await self._show_hotkey_overlay(
                "listening",
                detail=f"⌁ pending: {preview} — say 'keep it' or 'scratch that'",
                timeout_ms=8000,
            )
            return target

        if worker is not None and worker.screen and not worker.abandoned:
            to_delete = worker.screen[common_prefix_len(worker.screen, rewritten):]
            if risky_backspace(to_delete):
                clipboard.set_text(rewritten)
                raise RuntimeError(
                    "can't repair across pasted text — the rewrite is on the clipboard"
                )
            worker.set_target(rewritten)
            return await worker.drain(timeout=self._drain_timeout(rewritten))

        if risky_backspace(target):
            clipboard.set_text(rewritten)
            raise RuntimeError(
                "can't repair across pasted text — the rewrite is on the clipboard"
            )
        await asyncio.to_thread(self._injector.delete_chars, len(target))
        await asyncio.to_thread(self._injector.type_text, rewritten)
        return rewritten

    # ----------------------------------------------------------- intents

    def _verb_request(self, section: str, instruction: str) -> bool:
        """True when a voice instruction routes to a verb channel:
        the section is enabled and the first word is one of its verbs."""
        cfg = self._config.get(section, {})
        if not cfg.get("enabled", False):
            return False
        verbs = cfg.get("verbs") or []
        first = instruction.strip().split(" ", 1)[0].strip(",.:;!?").casefold()
        return first in {str(v).strip().casefold() for v in verbs}

    @staticmethod
    def _strip_verb(instruction: str, strippable: set) -> str:
        parts = instruction.strip().split(" ", 1)
        first = parts[0].strip(",.:;!?").casefold()
        if first in strippable and len(parts) > 1:
            return parts[1].strip()
        return instruction.strip()

    def _intent_request(self, instruction: str) -> bool:
        return self._verb_request("intent", instruction)

    async def _intent_last(self, request: str) -> str:
        """IPC `intent`: compile and type a command line, never Enter."""
        if self._hotkey_lock is None:
            self._hotkey_lock = asyncio.Lock()
        async with self._hotkey_lock:
            if self._recording:
                raise RuntimeError("stop recording before typing a command")
            return await self._run_intent(request)

    async def _run_intent(self, request: str) -> str:
        """The intent channel: one spoken request becomes ONE typed command
        line at the caret. The injector's no-Enter mode guarantees nothing
        executes — pressing Enter stays a human act."""
        llm_client = create_llm_client(self._config)
        if llm_client is None:
            raise RuntimeError("[llm] is not configured")

        await self._show_hotkey_overlay("processing", detail=f"⌁ {request}")
        command = await asyncio.to_thread(llm_client.compile_command, request)
        return await self._deliver_command(command)

    async def _deliver_command(self, command: str) -> str:
        """Type a compiled command at the caret, never Enter. Shared by the
        intent channel and Kai's terminal route."""
        if await self._focus_changed_since_session():
            clipboard.set_text(command)
            raise RuntimeError("focus changed — the command is on the clipboard")
        await self._type_no_enter(command)
        self._remember_typed(command, register="intent")
        await self._show_hotkey_overlay(
            "inserted", detail="⌁ typed — Enter is yours", timeout_ms=2200
        )
        return command

    async def _type_no_enter(self, command: str) -> None:
        """Type a command at the caret with Enter refused on every injector
        path. The single chokepoint for 'draft an action, never run it' —
        used by the intent channel AND the assistant's hands."""
        injector = self._injector
        has_flag = hasattr(injector, "suppress_enter")
        if has_flag:
            injector.suppress_enter = True
        try:
            await asyncio.to_thread(injector.type_text, command)
        finally:
            if has_flag:
                injector.suppress_enter = False

    # --------------------------------------------------------- the mind

    def _assistant_name(self) -> str:
        return str(self._config.get("assistant", {}).get("name", "Kai")).strip() or "Kai"

    async def _converse_start(self, *, hands_free: bool = False) -> None:
        """Summon Kai: open the mic for a spoken question. Shared by the
        hotkey (hold or tap), the on-screen button, and the wake word.

        hands_free (a tap or the wake word) ends the question on silence; a
        hold ends it on release."""
        if self._recording or (self._converse_task and not self._converse_task.done()):
            return
        if self._brain is None:
            # Bound but the mind is off — a helpful nudge, never silence.
            await self._show_hotkey_overlay(
                "empty",
                detail="⌁ enable [assistant] to summon Kai",
                timeout_ms=2600,
            )
            return
        self._converse_capture = True
        self._converse_hands_free = hands_free
        self._converse_pcm = []
        await self._show_hotkey_overlay("starting", detail=f"⌁ {self._assistant_name()}…")
        try:
            await self._start_recording()
        except Exception as exc:
            self._converse_capture = False
            self._converse_pcm = []
            logger.exception("Kai summon failed to start")
            await self._show_hotkey_overlay("error", detail=str(exc), timeout_ms=3000)
            return
        # The mic is live NOW — sound the cue and show LISTENING only after
        # connect, so the start of the question is never clipped.
        self._earcon("listen")
        await self._show_hotkey_overlay(
            "listening", detail=f"⌁ ask {self._assistant_name()}… (release to send)"
        )

    async def _converse_stop(self) -> None:
        """End the spoken question and hand the turn off the hotkey lock."""
        if not self._converse_capture:
            return
        self._earcon("captured")
        await self._show_hotkey_overlay(
            "processing", detail=f"⌁ {self._assistant_name()}…"
        )
        # _stop_recording drains STT, then schedules self._converse_task.
        await self._stop_recording()

    async def _converse_cancel(self) -> None:
        """Barge-in: cut off Kai's answer, or discard a live question."""
        if self._converse_cancel_flag is not None:
            self._converse_cancel_flag.set()  # an answer not yet playing never will
        await self._stop_audio()
        task = self._converse_task
        self._converse_task = None
        if task and not task.done():
            task.cancel()
            try:
                await task
            except (asyncio.CancelledError, Exception):
                pass
        if self._converse_capture and self._recording:
            # A question was still being captured — tear it down WITHOUT the
            # type-at-stop path, so nothing is injected.
            self._converse_capture = False
            self._converse_pcm = []
            try:
                await self._cleanup_after_failed_start()
            except Exception:
                logger.exception("Error discarding live capture on cancel")
        await self._show_hotkey_overlay("empty", detail="⌁ cancelled", timeout_ms=1200)

    async def _converse_turn(self, pcm: bytes, transcript: str) -> None:
        """Run one Kai turn to completion, owning its own overlay. Runs as a
        task off the hotkey lock; cancellable for barge-in; hard-capped so a
        wedged brain/LLM can never leave the overlay stuck on PROCESSING."""
        try:
            await asyncio.wait_for(
                self._run_converse_audio(pcm, transcript),
                timeout=CONVERSE_TURN_TIMEOUT_S,
            )
        except asyncio.CancelledError:
            raise
        except asyncio.TimeoutError:
            logger.error("Kai turn timed out after %ss", CONVERSE_TURN_TIMEOUT_S)
            self._last_error = "Kai timed out"
            await self._show_hotkey_overlay(
                "error", detail="⌁ Kai timed out", timeout_ms=3000
            )
        except Exception as exc:
            logger.exception("Kai turn failed")
            self._last_error = str(exc)
            await self._show_hotkey_overlay("error", detail=str(exc), timeout_ms=3000)
        finally:
            self._converse_task = None

    def _earcon(self, kind: str) -> None:
        """A short offline tone when Kai starts listening / captures your
        question — eyes-free confirmation. Non-blocking; never fatal."""
        if not bool(self._config.get("assistant", {}).get("earcon", True)):
            return
        try:
            from voice_keyboard.earcon import play_earcon

            play_earcon(kind)
        except Exception as exc:
            logger.debug("earcon unavailable: %s", exc)

    async def _run_converse_audio(self, pcm: bytes, transcript: str) -> str:
        """A spoken query to Kai, routed by where you are:

        - focused on a TERMINAL → turn the query into a command, type it at
          the prompt, and never press Enter (that stays yours);
        - anywhere else → answer / search the web, spoken back (the voice
          agent when configured, else the local brain).
        """
        if self._brain is None:
            raise RuntimeError("[assistant] is not enabled")

        # On Wayland the daemon often can't see the focused app at all —
        # GPU terminals (Ghostty, kitty, …) expose no AT-SPI, and GNOME
        # denies window introspection. So when focus is UNKNOWN, fall back
        # to attempting the command route: the classifier below returns None
        # for questions, so "what is X" is still answered, but "list the
        # folders" becomes a typed command. Turn this off to force answers
        # when focus can't be resolved.
        focus_unknown = self._session_focus is None
        terminal_fallback = bool(
            self._config.get("assistant", {}).get("terminal_fallback", True)
        )
        try_command = self._session_register.name == "terminal" or (
            focus_unknown and terminal_fallback
        )
        if try_command and transcript.strip():
            # A terminal being focused makes a command POSSIBLE, not
            # certain — the user might just have a question. Classify the
            # query: a runnable request becomes a typed command (no Enter);
            # a question falls through to the spoken answer below.
            llm_client = create_llm_client(self._config)
            if llm_client is not None:
                await self._show_hotkey_overlay("processing", detail=f"⌁ {transcript[:40]}")
                try:
                    command = await asyncio.to_thread(
                        llm_client.route_terminal_request, transcript
                    )
                except Exception as exc:
                    logger.warning("Terminal routing failed (%s); answering instead", exc)
                    command = None
                if command:
                    return await self._deliver_command(command)
                # Not a command — answer it, even though we're in a terminal.

        await self._show_hotkey_overlay("processing", detail=f"⌁ {self._assistant_name()}…")
        sample_rate = int(self._config.get("audio", {}).get("sample_rate", 16000))
        result = await self._brain.respond_audio(
            pcm, sample_rate=sample_rate, transcript_hint=transcript
        )
        answer = result.text
        self._last_answer = answer
        if transcript:
            self._brain.remember_interaction(transcript, answer)
        # Show the answer BEFORE speaking it — playback takes seconds and the
        # persistent PROCESSING pill must never outlive the thinking.
        if answer:
            await self._show_hotkey_overlay(
                "inserted", detail=f"⌁ {answer[:64]}", timeout_ms=6000
            )
        else:
            await self._show_hotkey_overlay(
                "empty", detail="⌁ no answer", timeout_ms=2200
            )
        # A barge-in stops the answer wherever it is: fetching, decoding, or
        # playing.
        cancel = self._converse_cancel_flag = threading.Event()
        # The voice agent speaks its own answer as RAW PCM (s16le), not an
        # MP3 container — play it as PCM or the decoder chews static.
        if result.audio and hasattr(self._tts_client, "play_pcm"):
            await _in_daemon_thread(
                self._tts_client.play_pcm,
                result.audio,
                getattr(result, "audio_sample_rate", 24000),
                cancel=cancel,
            )
        elif answer:
            try:
                await self._run_tts(answer, cancel=cancel)
            except Exception as exc:
                logger.debug("Assistant TTS failed: %s", exc)
        logger.info("Kai (%s): heard %r", result.brain or "?", transcript[:60])
        return answer

    # ------------------------------------------------------ pending rewrite

    _KEEP_PHRASES = {"keep it", "keep that", "apply it", "apply that"}

    def _peek_pending_rewrite(self) -> Optional[dict]:
        """The live pending rewrite, dropping it silently if expired."""
        pending = self._pending_rewrite
        if pending is None:
            return None
        if time.monotonic() > float(pending.get("expires", 0)):
            logger.info("Pending rewrite expired unapplied")
            self._pending_rewrite = None
            return None
        return pending

    async def _maybe_resolve_pending(
        self, final: str, worker: Optional[InjectionWorker]
    ) -> Optional[str]:
        """Voice approval for a held rewrite: "keep it" applies it, a bare
        "scratch that" discards it. Returns None when this utterance is
        ordinary dictation."""
        if self._peek_pending_rewrite() is None:
            return None
        spoken = final.strip().strip(".,!?").casefold()
        if spoken in self._KEEP_PHRASES:
            if worker is not None:
                # The approval words were molten-typed live; erase them
                # before the held rewrite lands.
                worker.set_target("")
                await worker.drain(timeout=6.0)
            try:
                return await self._apply_pending_rewrite()
            except Exception as exc:
                logger.warning("Pending rewrite failed to apply: %s", exc)
                self._last_error = str(exc)
                await self._show_hotkey_overlay("error", detail=str(exc), timeout_ms=3000)
                return ""
        if not final and self._last_scratches > 0:
            self._pending_rewrite = None
            await self._show_hotkey_overlay("empty", detail="⌁ discarded", timeout_ms=1500)
            return ""
        return None

    async def _apply_pending_rewrite(self) -> str:
        pending = self._peek_pending_rewrite()
        self._pending_rewrite = None
        if pending is None:
            raise RuntimeError("no pending rewrite")
        target = str(pending["target"])
        rewritten = str(pending["text"])
        if await self._focus_changed_since_session():
            clipboard.set_text(rewritten)
            raise RuntimeError("focus changed — the rewrite is on the clipboard")
        if risky_backspace(target):
            clipboard.set_text(rewritten)
            raise RuntimeError(
                "can't repair across pasted text — the rewrite is on the clipboard"
            )
        await asyncio.to_thread(self._injector.delete_chars, len(target))
        await asyncio.to_thread(self._injector.type_text, rewritten)
        self._remember_typed(rewritten)
        await self._show_hotkey_overlay("inserted", detail="⌁ kept", timeout_ms=1500)
        return rewritten

    async def _keep_pending(self) -> str:
        """IPC `keep`: apply the held rewrite."""
        if self._hotkey_lock is None:
            self._hotkey_lock = asyncio.Lock()
        async with self._hotkey_lock:
            if self._recording:
                raise RuntimeError("stop recording before keeping the rewrite")
            return await self._apply_pending_rewrite()

    async def _discard_pending(self) -> bool:
        """IPC `discard`: drop the held rewrite; True when one existed."""
        had = self._peek_pending_rewrite() is not None
        self._pending_rewrite = None
        return had

    async def _run_macro(self, text: str) -> str:
        """Procedural memory: type a user-named macro verbatim. The body
        is the user's own accepted text, typed on their spoken command —
        newlines preserved (this is recall of consented text, not model
        output)."""
        if await self._focus_changed_since_session():
            clipboard.set_text(text)
            raise RuntimeError("focus changed — the macro is on the clipboard")
        await asyncio.to_thread(self._injector.type_text, text)
        self._remember_typed(text, register="macro")
        await self._show_hotkey_overlay("inserted", detail="⌁ macro typed", timeout_ms=1500)
        return text

    async def _ask_last(self, question: str) -> str:
        """IPC `ask`: answer a question about the current selection."""
        if self._hotkey_lock is None:
            self._hotkey_lock = asyncio.Lock()
        async with self._hotkey_lock:
            if self._recording:
                raise RuntimeError("stop recording before asking")
            return await self._run_ask(question)

    async def _run_ask(self, question: str) -> str:
        """Talk to any app: answer about the PRIMARY selection through
        [llm]; spoken via TTS or typed (newline-suppressed) per config."""
        if not question.strip():
            raise RuntimeError("ask needs a question")
        llm_client = create_llm_client(self._config)
        if llm_client is None:
            raise RuntimeError("[llm] is not configured")
        context = ""
        if not self._session_secret:
            try:
                context = (
                    await asyncio.to_thread(
                        clipboard.selection_text,
                        clipboard_fallback=False,
                        registers=self._config.get("registers", {}),
                    )
                ).strip()[:4000]
            except Exception:
                context = ""
        await self._show_hotkey_overlay("processing", detail=f"⌁ {question[:40]}")
        answer = await asyncio.to_thread(llm_client.answer, question, context)
        if str(self._config.get("ask", {}).get("mode", "say")).lower() == "type":
            injector = self._injector
            has_flag = hasattr(injector, "suppress_enter")
            if has_flag:
                injector.suppress_enter = True
            try:
                await asyncio.to_thread(injector.type_text, answer)
            finally:
                if has_flag:
                    injector.suppress_enter = False
            self._remember_typed(answer, register="ask")
        else:
            await self._run_tts(answer)
        return answer

    async def _run_recall(self, query: str) -> str:
        """Total recall: the best ledger match, spoken or typed."""
        if not query.strip():
            raise RuntimeError("recall needs a query")
        entries = await asyncio.to_thread(history.last_entries, 500)
        if not entries:
            raise RuntimeError("the ledger is empty — enable [flow] history")
        embedder = recall.create_embedder(self._config)
        hits = await asyncio.to_thread(
            recall.search, entries, query, embedder=embedder, limit=1
        )
        if not hits:
            raise RuntimeError("nothing recalled for that")
        text = str(hits[0].get("text", ""))
        if str(self._config.get("recall", {}).get("mode", "say")).lower() == "type":
            await asyncio.to_thread(self._injector.type_text, text)
        else:
            await self._run_tts(text)
        return text

    async def _focus_changed_since_session(self) -> bool:
        focus = self._session_focus
        if focus is None or not focus.identity:
            return False
        if not self._config.get("registers", {}).get("probe", True):
            return False
        current = await asyncio.to_thread(probe_focus)
        return bool(current and current.identity and current.identity != focus.identity)

    async def _type_text(self, text: str) -> None:
        """IPC `type`: inject text directly (used by `voice-keyboard recall`)."""
        if self._recording:
            raise RuntimeError("cannot type while recording")
        await asyncio.to_thread(self._injector.type_text, text)

    async def _press_keys(self, names: list) -> None:
        """IPC `key`: press a key chord (e.g. ctrl+t, alt+Tab, Return). Used by
        integrators — Seneschal.Computer drives the agent's keystrokes through
        this so all injection shares VK's one virtual keyboard."""
        if self._recording:
            raise RuntimeError("cannot press keys while recording")
        press = getattr(self._injector, "press_combo", None)
        if press is None:
            raise RuntimeError("key injection unsupported on this platform backend")
        await asyncio.to_thread(press, names)

    # ------------------------------------------------------------ streams

    async def _stream_audio(self) -> None:
        chunk_ms = float(self._config.get("audio", {}).get("chunk_ms", 100))
        while self._recording and self._stt_client:
            try:
                audio_capture = self._audio_capture
                if audio_capture is None:
                    break
                chunk = await asyncio.to_thread(audio_capture.read_chunk)
                if not self._recording or self._stt_client is None:
                    break
                if self._converse_capture:
                    # Keep the raw PCM: a converse turn may go to the voice
                    # agent, which answers audio, not text. STT still runs
                    # so we have a transcript for memory + local fallback.
                    self._converse_pcm.append(bytes(chunk))
                await self._stt_client.send_audio(chunk)
                self._observe_audio(chunk, chunk_ms)
            except Exception:
                if self._recording:
                    logger.exception("Error streaming audio")
                break

    def _observe_audio(self, chunk: bytes, chunk_ms: float) -> None:
        self._chunks_seen += 1
        if not self._heard_signal and chunk.strip(b"\x00"):
            self._heard_signal = True
        level = chunk_rms(chunk)
        if self._onset is not None and self._onset.feed(level):
            self._mark_latency("onset")
        self._levels.append(level)
        del self._levels[:-8]
        gate = self._silence_gate
        if (
            gate is not None
            and not self._auto_stop_started
            and gate.feed(level, chunk_ms)
        ):
            self._auto_stop_started = True
            logger.info("Auto-stop: %dms of silence", gate.auto_stop_ms)
            # A hands-free Kai question ends via its own path (which schedules
            # the turn off-lock and owns its overlay); dictation ends normally.
            action = "converse_stop" if self._converse_capture else "stop"
            asyncio.get_running_loop().create_task(
                self._handle_hotkey_action(action)
            )

    def _session_transcript(self) -> str:
        """Everything recognized so far: the finals plus the interim tail."""
        if self._chunked_stt:
            return _join_transcript_text(self._final_text, self._interim_text)
        return _dedupe_repeated_transcript_text(
            _merge_transcript_text(self._final_text, self._interim_text)
        )

    def _on_chunked_event(self, event: dict) -> None:
        """xAI's chunk/utterance protocol (grok-voice-transcribe-2.0): an
        interim is the current chunk so far; a chunk final locks that chunk
        (appended — chunks never overlap, so no merging heuristics); an
        utterance final repeats the whole utterance, which normally adds
        nothing new."""
        text = str(event.get("text") or "")
        if not event.get("is_final"):
            self._interim_text = text
            self._feed_flow(is_final=False)
            return
        if event.get("speech_final"):
            before = self._final_text[:self._utterance_start]
            spoken = self._final_text[self._utterance_start:].strip()
            self._final_text = _join_transcript_text(
                before, _reconcile_utterance(spoken, text)
            )
            self._utterance_start = len(self._final_text)
        else:
            self._final_text = _join_transcript_text(self._final_text, text.strip())
        self._interim_text = ""
        self._feed_flow(is_final=True)

    async def _receive_events(self) -> None:
        try:
            async for event in self._stt_client.receive_events():
                event_type = event.get("type", "")
                if "speech_final" in event:
                    self._chunked_stt = True
                if event_type == "transcript.partial" and self._chunked_stt:
                    self._on_chunked_event(event)
                elif event_type == "transcript.partial":
                    self._interim_text = event.get("text", "")
                    logger.debug("Interim: %r", self._interim_text)
                    if event.get("is_final"):
                        self._final_text = _merge_transcript_text(
                            self._final_text,
                            self._interim_text,
                        )
                        self._interim_text = ""
                        self._feed_flow(is_final=True)
                    else:
                        self._feed_flow(is_final=False)
                elif event_type == "transcript.done":
                    if self._chunked_stt:
                        # Empty from grok-voice-transcribe-2.0: the chunks
                        # already carried everything.
                        self._final_text = _reconcile_utterance(
                            self._final_text, str(event.get("text") or "")
                        )
                    else:
                        self._final_text = _merge_transcript_text(
                            self._final_text,
                            event.get("text", ""),
                        )
                    self._interim_text = ""
                    logger.debug("Final transcript received")
                    self._feed_flow(is_final=True)
                    break
                elif event_type == "error":
                    self._stt_error = str(
                        event.get("message") or "speech-to-text provider failed"
                    )
                    logger.error("STT error event: %s", self._stt_error)
                    break
        except Exception as exc:
            self._stt_error = str(exc) or exc.__class__.__name__
            logger.exception("Error receiving STT events")

    def _feed_flow(self, *, is_final: bool) -> None:
        if self._final_text or self._interim_text:
            self._mark_latency("first_partial")
        engine = self._flow_engine
        if engine is None:
            return
        if self._chunked_stt:
            merged = _join_transcript_text(self._final_text, self._interim_text)
        else:
            merged = _merge_transcript_text(self._final_text, self._interim_text)
        if self._ambient_gate is not None:
            # Containment: room speech never reaches the engine at all.
            merged = self._ambient_gate.filter(merged, is_final=is_final)
        engine.on_transcript(merged, is_final=is_final, now=time.monotonic())
        worker = self._flow_worker
        if worker is not None:
            worker.set_target(engine.desired_text())
            self._maybe_run_nav(engine, worker)

    # ---------------------------------------------------------- remote mic

    def _schedule_remote_start(self, source: RemoteAudioSource) -> None:
        if self._loop is None or self._loop.is_closed():
            return
        asyncio.run_coroutine_threadsafe(self._remote_start(source), self._loop)

    def _schedule_remote_stop(self) -> None:
        if self._loop is None or self._loop.is_closed():
            return
        asyncio.run_coroutine_threadsafe(self._remote_stop(), self._loop)

    async def _remote_start(self, source: RemoteAudioSource) -> None:
        if self._hotkey_lock is None:
            self._hotkey_lock = asyncio.Lock()
        async with self._hotkey_lock:
            if self._recording:
                logger.info("Remote mic start ignored: already recording")
                return
            self._audio_source_override = source
            try:
                await self._start_recording()
            except Exception as exc:
                logger.exception("Remote mic session failed to start")
                self._last_error = str(exc)
            finally:
                self._audio_source_override = None

    async def _remote_stop(self) -> None:
        await self._handle_hotkey_action("stop")

    def _store_tts_prefetch(self, text: str, audio: bytes) -> None:
        # Called from the watcher thread; a single tuple swap is atomic.
        self._tts_cache = (text, audio)

    async def _stop_audio(self) -> None:
        """stop_playback, off the event loop: it may wait for an audio
        device that is still opening."""
        try:
            await asyncio.to_thread(self._tts_client.stop_playback)
        except Exception:
            pass

    async def _stop_reading(self) -> bool:
        """Stop the read in progress, if any (True when there was one)."""
        task = self._read_task
        if task is None or task.done():
            return False
        self._read_task = None
        if self._read_cancel is not None:
            self._read_cancel.set()  # before stop_playback: no gap to start in
        task.cancel()
        await self._stop_audio()
        return True

    async def _toggle_read_aloud(self, *, from_selection: bool) -> None:
        """The read-aloud hotkey / tray item: speak the highlighted text (or
        the clipboard), or stop speaking if it already is. Playback runs as
        a task, off the hotkey lock, so dictation stays available."""
        if await self._stop_reading():
            await self._show_hotkey_overlay("empty", detail="Stopped reading", timeout_ms=1200)
            return
        if self._recording:
            await self._show_hotkey_overlay(
                "processing", detail="⌁ busy — dictation is live", timeout_ms=1400
            )
            return
        notes: list[str] = []
        if from_selection:
            # Only macOS, which has no way to read a selection, falls back to
            # the clipboard: elsewhere "nothing selected" must not quietly
            # read out whatever was copied last.
            text = await asyncio.to_thread(
                clipboard.selection_text,
                clipboard_fallback=sys.platform == "darwin",
                registers=self._config.get("registers", {}),
                notes=notes,
            )
        else:
            if await asyncio.to_thread(clipboard.is_sensitive):
                await self._show_hotkey_overlay(
                    "empty", detail="Not reading a copied password", timeout_ms=2200
                )
                return
            text = await asyncio.to_thread(clipboard.get_text) or ""
        text = text.strip()
        if not text:
            if notes:
                detail = notes[0]
            else:
                detail = "Select some text first" if from_selection else "Clipboard is empty"
            await self._show_hotkey_overlay("empty", detail=detail, timeout_ms=2600)
            return
        self._read_cancel = threading.Event()
        self._read_task = asyncio.create_task(self._read_aloud(text, self._read_cancel))

    async def _read_aloud(self, text: str, cancel: threading.Event) -> None:
        try:
            await self._show_hotkey_overlay(
                "processing", detail=f"Reading {len(text)} characters — press again to stop"
            )
            await self._run_tts(text, cancel=cancel)
            if not cancel.is_set():
                await self._show_hotkey_overlay(
                    "inserted", detail="Finished reading", timeout_ms=1500
                )
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.exception("Read-aloud failed")
            self._last_error = str(exc)
            await self._show_hotkey_overlay("error", detail=str(exc), timeout_ms=3000)
        finally:
            if self._read_task is asyncio.current_task():
                self._read_task = None

    async def _run_tts(self, text: str, cancel: Optional[threading.Event] = None) -> None:
        """Speak `text` (a prefetched clip plays at once). With `cancel`
        (read-aloud), fetching and playing are separate awaits on daemon
        threads and playback checks the event: a stop at any point is never
        lost, and Quit never waits for a download it no longer needs."""
        client = self._tts_client
        cache = self._tts_cache
        if cancel is not None and hasattr(client, "synthesize") and hasattr(client, "play_audio"):
            if cache is not None and cache[0] == text:
                logger.info("TTS prefetch hit (%d chars) — instant playback", len(text))
                audio = cache[1]
            else:
                audio = await _in_daemon_thread(client.synthesize, text)
            await _in_daemon_thread(client.play_audio, audio, cancel=cancel)
            return
        if cache is not None and cache[0] == text and hasattr(client, "play_audio"):
            logger.info("TTS prefetch hit (%d chars) — instant playback", len(text))
            await asyncio.to_thread(client.play_audio, cache[1])
            return
        await asyncio.to_thread(client.synthesize_and_play, text)


def _in_daemon_thread(func, *args, **kwargs) -> "asyncio.Future":
    """Like asyncio.to_thread, but on a daemon thread outside the default
    executor: asyncio.run() (and interpreter exit) never waits for it, so a
    cancelled read-aloud's download can't hold up Quit or Restart."""
    loop = asyncio.get_running_loop()
    future = loop.create_future()

    def deliver(setter, value) -> None:
        if not future.done():
            setter(value)

    def run() -> None:
        try:
            result = func(*args, **kwargs)
        except BaseException as exc:  # handed to the awaiting coroutine
            outcome = (future.set_exception, exc)
        else:
            outcome = (future.set_result, result)
        try:
            loop.call_soon_threadsafe(deliver, *outcome)
        except RuntimeError:
            pass  # the loop is gone: nobody is waiting any more

    threading.Thread(target=run, name="vk-tts", daemon=True).start()
    return future


def main() -> None:
    if sys.platform == "win32":
        # On Windows the daemon always runs inside the app that owns its
        # overlay, orb, and tray icon; this entry point is the console
        # flavour (logs to the terminal as well as the log file).
        from voice_keyboard.windows.app import main as windows_main

        raise SystemExit(windows_main(["--console"]))
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
