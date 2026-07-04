import asyncio
import json
import logging
import signal
import threading
import time
from typing import Optional

from voice_keyboard import clipboard, dictionary, history, recall
from voice_keyboard.ambient import AmbientGate
from voice_keyboard.audio_capture import AudioCapture
from voice_keyboard.config import _config_dir, load_config, validate_config
from voice_keyboard.flow import FlowConfig, FlowEngine, Grammar, InjectionWorker
from voice_keyboard.flow.engine import risky_backspace
from voice_keyboard.flow.registers import (
    Register,
    register_for_app,
    resolve_register,
)
from voice_keyboard.flow.vad import SilenceGate, chunk_rms, vu_bar
from voice_keyboard.flow.worker import common_prefix_len
from voice_keyboard.focusprobe import FocusInfo, probe_focus
from voice_keyboard.hotkey import HotkeyListener, create_hotkey_listener
from voice_keyboard.injector import TextInjector, create_injector
from voice_keyboard.ipc import IPCServer, recv_all
from voice_keyboard.llm import create_llm_client
from voice_keyboard.prefetch import SelectionWatcher, prefetch_enabled
from voice_keyboard.remotemic import RemoteAudioSource, RemoteMicServer
from voice_keyboard.stt import create_stt_client

# Re-exported for backwards compatibility: these lived here before they
# moved to voice_keyboard.transcript.
from voice_keyboard.transcript import (  # noqa: F401
    _dedupe_repeated_transcript_text,
    _join_transcript_text,
    _merge_transcript_text,
    _transcript_words,
    _word_prefix_overlap,
    _word_sequence_contains,
    _word_sequence_endswith,
    _word_sequence_startswith,
)
from voice_keyboard.tts import TTSClient, create_tts_client

logger = logging.getLogger(__name__)

FLOW_TICK_S = 0.25
FOCUS_WATCHDOG_S = 1.5
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
        self._stt_error: Optional[str] = None
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._hotkey_listener: Optional[HotkeyListener] = None
        self._hotkey_lock: Optional[asyncio.Lock] = None

        # Flow — molten dictation session state.
        self._flow_engine: Optional[FlowEngine] = None
        self._flow_worker: Optional[InjectionWorker] = None
        self._flow_ticker: Optional[asyncio.Task] = None
        self._focus_watchdog: Optional[asyncio.Task] = None
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
        self._last_caption = ""
        self._last_typed = ""
        self._last_error = ""
        # Molten diffs: a rewrite held for approval ([flow] rewrite_pending).
        self._pending_rewrite: Optional[dict] = None
        self._last_scratches = 0
        # Speculative TTS: (text, audio) for the last stable selection.
        self._tts_cache: Optional[tuple[str, bytes]] = None
        self._prefetch_watcher: Optional[SelectionWatcher] = None
        # The multiplayer keyboard: a phone-fed session replaces PyAudio.
        self._remote_mic: Optional[RemoteMicServer] = None
        self._audio_source_override: Optional[RemoteAudioSource] = None
        self._started_at = time.monotonic()
        self._config_mtime = self._current_config_mtime()

    async def run(self) -> None:
        self._loop = asyncio.get_running_loop()
        self._hotkey_lock = asyncio.Lock()
        self._injector.start()
        self._ipc_server.start()
        self._start_hotkey_listener()
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
        logger.info("Daemon started, socket: %s", self._socket_path)

        ipc_thread = threading.Thread(target=self._ipc_loop, daemon=True)
        ipc_thread.start()

        stop_event = asyncio.Event()

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

    async def _shutdown(self) -> None:
        logger.info("Shutting down daemon")
        if self._remote_mic is not None:
            self._remote_mic.stop()
            self._remote_mic = None
        if self._prefetch_watcher is not None:
            self._prefetch_watcher.stop()
            self._prefetch_watcher = None
        if self._hotkey_listener:
            self._hotkey_listener.stop()
            self._hotkey_listener = None
        if self._recording:
            await self._stop_recording()
        self._injector.stop()
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
                self._last_error = str(exc)
                await self._show_hotkey_overlay("error", detail=str(exc), timeout_ms=3000)

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

    async def _hotkey_start_recording(self, *, hold_to_talk: bool = False) -> None:
        if self._recording:
            return
        mode = self._config.get("hotkey", {}).get("mode", "auto")
        if hold_to_talk or mode == "hold":
            listening_detail = "Release Ctrl+Alt+V to stop"
        elif mode == "auto":
            listening_detail = "Tap Ctrl+Alt+V again to stop; hold to talk"
        else:
            listening_detail = "Press Ctrl+Alt+V again to stop"
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

                required_token = getattr(self._ipc_server, "required_token", None)
                if required_token and msg.get("token") != required_token:
                    response = {"status": "error", "message": "invalid IPC token"}

                elif command == "start":
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
                            "message": "timed out connecting to speech-to-text provider",
                        }
                    else:
                        response = {"status": "ok", "message": "recording started"}

                elif command == "stop":
                    stop_timeout = self._stop_recording_ipc_timeout()
                    future = asyncio.run_coroutine_threadsafe(
                        self._stop_recording(), self._loop
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

                elif command == "status":
                    response = self._status_response()

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
        for section in ("flow", "registers", "llm", "intent", "ambient", "ask", "recall"):
            self._config[section] = fresh.get(section, {})
        logger.info("Reloaded flow/registers/llm/intent/ambient/ask/recall config")

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
            wake_word=str(flow_cfg.get("wake_word", "furion")),
            numbers=str(flow_cfg.get("numbers", "auto")).lower(),
            numbers_on=register.numbers_on,
            numbers_min=register.numbers_min,
        )

    def _flow_config_obj(self) -> FlowConfig:
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
        )

    async def _setup_flow_session(self, probe_task: Optional[asyncio.Task]) -> None:
        self._focus_lost = False
        self._session_secret = False
        self._auto_stop_started = False
        self._levels = []
        self._last_caption = ""

        flow_cfg = self._config.get("flow", {})
        self._ambient_gate = None
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
                flow_cfg.get("wake_word", "furion")
            ).strip()
            if address:
                self._ambient_gate = AmbientGate(address)
                logger.info("Ambient containment active: address word %r", address)

        self._flow_engine = FlowEngine(
            self._flow_config_obj(), self._build_grammar(register), register
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
        self._flow_worker = InjectionWorker(self._injector) if live else None
        logger.info(
            "Flow session: register=%s app=%r live=%s",
            register.name,
            focus.app if focus else "",
            bool(self._flow_worker),
        )

    async def _teardown_flow_session(self) -> None:
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
                engine.on_tick(time.monotonic())
                worker = self._flow_worker
                if worker is not None:
                    worker.set_target(engine.desired_text())
                self._push_live_caption(engine)
        except asyncio.CancelledError:
            pass

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
        self._stt_error = None
        self._recording = True

        self._receive_task = asyncio.create_task(self._receive_events())
        self._send_task = asyncio.create_task(self._stream_audio())
        if self._flow_worker is not None:
            self._flow_worker.start()
            self._flow_ticker = asyncio.create_task(self._flow_ticker_loop())
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

        merged = _dedupe_repeated_transcript_text(
            _merge_transcript_text(self._final_text, self._interim_text)
        )

        engine = self._flow_engine
        if engine is None:
            # Flow disabled: the original type-at-stop behavior, unchanged.
            final = merged
            if final:
                await asyncio.to_thread(self._injector.type_text, final)
                logger.info("Injected %d characters", len(final))
            else:
                logger.info("No transcript received")
            self._remember_typed(final)
            return final

        result = engine.finalize(merged, now=time.monotonic())
        final = result.text
        self._last_scratches = result.scratches
        worker = self._flow_worker
        try:
            if worker is not None:
                final = await self._finish_live(worker, final, result.instruction)
            else:
                final = await self._finish_classic(final, result.instruction)
        finally:
            await self._teardown_flow_session()

        if final:
            logger.info("Inserted %d characters", len(final))
        else:
            logger.info("No transcript received")
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
        return final

    async def _transform_previous_or_report(self, instruction: str) -> str:
        """A standalone "furion, ..." utterance, routed by precedence:
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

        if await self._focus_changed_since_session():
            clipboard.set_text(command)
            raise RuntimeError("focus changed — the command is on the clipboard")

        injector = self._injector
        has_flag = hasattr(injector, "suppress_enter")
        if has_flag:
            injector.suppress_enter = True
        try:
            await asyncio.to_thread(injector.type_text, command)
        finally:
            if has_flag:
                injector.suppress_enter = False
        self._remember_typed(command, register="intent")
        await self._show_hotkey_overlay(
            "inserted", detail="⌁ typed — Enter is yours", timeout_ms=2200
        )
        return command

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
                context = (clipboard.get_primary_text() or "").strip()[:4000]
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
                await self._stt_client.send_audio(chunk)
                self._observe_audio(chunk, chunk_ms)
            except Exception:
                if self._recording:
                    logger.exception("Error streaming audio")
                break

    def _observe_audio(self, chunk: bytes, chunk_ms: float) -> None:
        level = chunk_rms(chunk)
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
            asyncio.get_running_loop().create_task(
                self._handle_hotkey_action("stop")
            )

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
                        self._feed_flow(is_final=True)
                    else:
                        self._feed_flow(is_final=False)
                elif event_type == "transcript.done":
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
        engine = self._flow_engine
        if engine is None:
            return
        merged = _merge_transcript_text(self._final_text, self._interim_text)
        if self._ambient_gate is not None:
            # Containment: room speech never reaches the engine at all.
            merged = self._ambient_gate.filter(merged, is_final=is_final)
        engine.on_transcript(merged, is_final=is_final, now=time.monotonic())
        worker = self._flow_worker
        if worker is not None:
            worker.set_target(engine.desired_text())

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

    async def _run_tts(self, text: str) -> None:
        cache = self._tts_cache
        if cache is not None and cache[0] == text and hasattr(
            self._tts_client, "play_audio"
        ):
            logger.info("TTS prefetch hit (%d chars) — instant playback", len(text))
            await asyncio.to_thread(self._tts_client.play_audio, cache[1])
            return
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
