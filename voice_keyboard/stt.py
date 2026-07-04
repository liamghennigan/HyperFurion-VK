import asyncio
import io
import json
import logging
import threading
import time
import wave
from collections import deque
from typing import AsyncIterator, Optional
from urllib.parse import urlencode

import requests
from requests.adapters import HTTPAdapter
import websockets
from websockets.exceptions import WebSocketException
from urllib3.util.retry import Retry

logger = logging.getLogger(__name__)

XAI_STT_WS_URL = "wss://api.x.ai/v1/stt"
HYPERFURION_DEFAULT_BASE_URL = "https://api.hyperfurion.com"
OPENAI_STT_URL = "https://api.openai.com/v1/audio/transcriptions"
GROQ_STT_URL = "https://api.groq.com/openai/v1/audio/transcriptions"
DEEPGRAM_STT_URL = "https://api.deepgram.com/v1/listen"
ASSEMBLYAI_UPLOAD_URL = "https://api.assemblyai.com/v2/upload"
ASSEMBLYAI_TRANSCRIPT_URL = "https://api.assemblyai.com/v2/transcript"

SUPPORTED_STT_PROVIDERS = {"xai", "hyperfurion", "openai", "groq", "deepgram", "assemblyai"}
MAX_CONNECT_RETRIES = 2
CONNECT_BACKOFF_BASE = 0.5

DEFAULT_STT_MODELS = {
    "xai": "",
    "hyperfurion": "",
    "openai": "gpt-4o-transcribe",
    "groq": "whisper-large-v3-turbo",
    "deepgram": "nova-3",
    "assemblyai": "",
}


def _build_session() -> requests.Session:
    session = requests.Session()
    retry_strategy = Retry(
        total=3,
        backoff_factor=0.5,
        status_forcelist=[429, 500, 502, 503, 504],
        allowed_methods=["HEAD", "GET", "POST"],
    )
    adapter = HTTPAdapter(max_retries=retry_strategy)
    session.mount("https://", adapter)
    return session


def _provider_api_key(config: dict, provider: str) -> str:
    providers = config.get("providers", {})
    key = str(providers.get(provider, {}).get("api_key", "")).strip()
    if provider == "xai" and not key:
        key = str(config.get("xai", {}).get("api_key", "")).strip()
    return key


def hyperfurion_ws_url(config: dict) -> str:
    """WebSocket STT endpoint for the hosted HyperFurion relay.

    The relay speaks the same wire protocol as xAI STT, so the streaming
    client is reused as-is with a different endpoint and the user's
    subscription key instead of a raw provider key.
    """
    providers = config.get("providers", {})
    base = str(providers.get("hyperfurion", {}).get("base_url", "")).strip()
    base = (base or HYPERFURION_DEFAULT_BASE_URL).rstrip("/")
    if base.startswith("https://"):
        base = "wss://" + base[len("https://"):]
    elif base.startswith("http://"):
        base = "ws://" + base[len("http://"):]
    return f"{base}/v1/stt"


def create_stt_client(config: dict):
    stt_cfg = config.get("stt", {})
    provider = str(stt_cfg.get("provider", "xai")).lower()
    api_key = _provider_api_key(config, provider)
    language = str(stt_cfg.get("language", "en"))
    model = str(stt_cfg.get("model", "") or DEFAULT_STT_MODELS.get(provider, ""))

    if provider in {"xai", "hyperfurion"}:
        return STTClient(
            api_key=api_key,
            language=language,
            interim_results=bool(stt_cfg.get("interim_results", True)),
            ws_url=XAI_STT_WS_URL if provider == "xai" else hyperfurion_ws_url(config),
        )

    base_url = ""
    if provider == "openai":
        # An OpenAI-compatible base_url (e.g. a local Whisper server such as
        # Speaches or LocalAI) makes dictation fully offline.
        base_url = str(
            config.get("providers", {}).get("openai", {}).get("base_url", "")
        ).strip()

    client = BufferedRESTSTTClient(
        provider=provider,
        api_key=api_key,
        model=model,
        language=language,
        base_url=base_url,
    )

    if _live_rest_enabled(config, provider=provider, base_url=base_url):
        interval_ms = config.get("flow", {}).get("live_rest_interval_ms", 2500)
        try:
            interval_ms = max(800, int(interval_ms))
        except (TypeError, ValueError):
            interval_ms = 2500
        interim = BufferedRESTSTTClient(
            provider=provider,
            api_key=api_key,
            model=model,
            language=language,
            base_url=base_url,
            # Interim probes must never stall the stop path; keep them short.
            timeout=min(10.0, interval_ms / 1000.0 * 3),
        )
        return ChunkedRESTAdapter(client, interim, interval_ms=interval_ms)
    return client


def _live_rest_enabled(config: dict, *, provider: str, base_url: str) -> bool:
    """Pseudo-streaming for buffered providers: "always" opts any REST
    provider in; "auto" enables it only against a local endpoint, where
    re-transcribing the growing buffer is free."""
    flow_cfg = config.get("flow", {})
    if not flow_cfg.get("enabled", True) or not flow_cfg.get("live", True):
        return False
    mode = str(flow_cfg.get("live_rest", "auto")).strip().lower()
    if mode == "always":
        return True
    if mode != "auto":
        return False
    if provider != "openai" or not base_url:
        return False
    from voice_keyboard.config import _is_local_endpoint  # lazy: avoid cycle

    return _is_local_endpoint(base_url)


class STTClient:
    """Streaming STT client for the xAI wire protocol (xAI or a HyperFurion relay)."""

    completion_timeout = 5.0
    supports_streaming = True

    def __init__(
        self,
        api_key: str,
        language: str = "en",
        interim_results: bool = True,
        connect_timeout: float = 5.0,
        ws_url: str = XAI_STT_WS_URL,
    ):
        self._api_key = api_key
        self._language = language
        self._interim_results = interim_results
        self._connect_timeout = connect_timeout
        self._ws_url = ws_url
        self._ws: Optional[websockets.WebSocketClientProtocol] = None

    def _url_for_sample_rate(self, sample_rate: int) -> str:
        query = {
            "sample_rate": str(sample_rate),
            "encoding": "pcm",
            "interim_results": "true" if self._interim_results else "false",
        }
        if self._language:
            query["language"] = self._language
        return f"{self._ws_url}?{urlencode(query)}"

    @staticmethod
    def _rejection_detail(exc: Exception) -> str:
        """The HTTP body of a rejected handshake, where the server names the
        actual problem (e.g. an invalid API key); "" when unavailable."""
        body = getattr(getattr(exc, "response", None), "body", b"") or b""
        if isinstance(body, bytes):
            body = body.decode("utf-8", "replace")
        return " ".join(str(body).split())[:300]

    async def connect(self, sample_rate: int) -> None:
        headers = {"Authorization": f"Bearer {self._api_key}"}
        url = self._url_for_sample_rate(sample_rate)
        last_error = ""
        for attempt in range(1, MAX_CONNECT_RETRIES + 1):
            try:
                self._ws = await asyncio.wait_for(
                    websockets.connect(url, additional_headers=headers),
                    timeout=self._connect_timeout,
                )
                await self._wait_for_ready()
                logger.info("Connected to xAI STT WebSocket")
                return
            except (
                WebSocketException,
                asyncio.TimeoutError,
                OSError,
                RuntimeError,
                json.JSONDecodeError,
            ) as exc:
                detail = self._rejection_detail(exc)
                last_error = f"{exc}: {detail}" if detail else str(exc)
                await self.close()
                logger.warning(
                    "STT connect attempt %d/%d failed: %s",
                    attempt,
                    MAX_CONNECT_RETRIES,
                    last_error,
                )
                if attempt < MAX_CONNECT_RETRIES:
                    await asyncio.sleep(CONNECT_BACKOFF_BASE * attempt)
        raise RuntimeError(f"Could not connect to xAI STT: {last_error}")

    async def _wait_for_ready(self) -> None:
        if self._ws is None:
            raise RuntimeError("Not connected")
        raw_event = await asyncio.wait_for(self._ws.recv(), timeout=self._connect_timeout)
        event = json.loads(raw_event)
        event_type = event.get("type", "")
        if event_type == "transcript.created":
            return
        if event_type == "error":
            raise RuntimeError(event.get("message", "xAI STT connection failed"))
        raise RuntimeError(f"Unexpected STT ready event: {event_type}")

    async def send_audio(self, data: bytes) -> None:
        if self._ws is None:
            raise RuntimeError("Not connected")
        await self._ws.send(data)

    async def send_audio_done(self) -> None:
        if self._ws is None:
            raise RuntimeError("Not connected")
        await self._ws.send(json.dumps({"type": "audio.done"}))

    async def send_config(self, sample_rate: int) -> None:
        raise RuntimeError(
            "xAI STT configuration is sent in the WebSocket URL; "
            "call connect(sample_rate) instead"
        )

    async def receive_events(self) -> AsyncIterator[dict]:
        if self._ws is None:
            raise RuntimeError("Not connected")
        async for message in self._ws:
            try:
                event = json.loads(message)
                yield event
            except json.JSONDecodeError:
                logger.warning("Invalid JSON from STT: %s", message)

    async def close(self) -> None:
        if self._ws is not None:
            try:
                await asyncio.wait_for(self._ws.close(), timeout=5.0)
            except (asyncio.TimeoutError, WebSocketException, OSError):
                pass
            self._ws = None
            logger.info("STT WebSocket closed")


class BufferedRESTSTTClient:
    """Record PCM chunks locally, then submit a WAV file to an STT REST API."""

    supports_streaming = False

    def __init__(
        self,
        *,
        provider: str,
        api_key: str,
        model: str = "",
        language: str = "en",
        timeout: float = 60.0,
        poll_interval: float = 1.0,
        max_poll_time: float = 120.0,
        base_url: str = "",
    ):
        self._provider = provider
        self._api_key = api_key
        self._base_url = base_url.rstrip("/")
        self._model = model or DEFAULT_STT_MODELS.get(provider, "")
        self._language = language
        self._timeout = timeout
        self._poll_interval = poll_interval
        self._max_poll_time = max_poll_time
        self._sample_rate = 16000
        self._chunks: list[bytes] = []
        self._session: Optional[requests.Session] = None
        self._events: deque[dict] = deque()
        self._event_lock = threading.Lock()
        self._worker_thread: Optional[threading.Thread] = None
        self._connected = False
        self._closed = False

    @property
    def completion_timeout(self) -> float:
        if self._provider == "assemblyai":
            return (self._timeout * 2) + self._max_poll_time + 5.0
        return self._timeout + 5.0

    @property
    def session(self) -> requests.Session:
        if self._session is None:
            self._session = _build_session()
        return self._session

    async def connect(self, sample_rate: int) -> None:
        self._sample_rate = sample_rate
        self._chunks = []
        self._events.clear()
        self._worker_thread = None
        self._connected = True
        self._closed = False
        logger.info("Buffered STT client ready: %s", self._provider)

    async def send_audio(self, data: bytes) -> None:
        self._chunks.append(bytes(data))

    async def send_audio_done(self) -> None:
        if not self._connected:
            raise RuntimeError("Not connected")
        if self._worker_thread is not None:
            return
        wav_data = self._wav_bytes()
        self._worker_thread = threading.Thread(
            target=self._transcribe_in_worker,
            args=(wav_data,),
            name=f"voice-keyboard-{self._provider}-stt",
            daemon=True,
        )
        self._worker_thread.start()

    def _transcribe_in_worker(self, wav_data: bytes) -> None:
        try:
            text = self._transcribe_wav(wav_data)
        except Exception as exc:
            logger.exception("%s STT transcription failed", self._provider)
            self._emit_worker_event(
                {
                    "type": "error",
                    "message": f"{self._provider} STT transcription failed: {exc}",
                }
            )
            return
        self._emit_worker_event({"type": "transcript.done", "text": text})

    def _emit_worker_event(self, event: dict) -> None:
        if self._closed or not self._connected:
            return
        with self._event_lock:
            self._events.append(event)

    async def receive_events(self) -> AsyncIterator[dict]:
        if not self._connected:
            raise RuntimeError("Not connected")
        while True:
            event = await self._next_event()
            yield event
            if event.get("type") in {"transcript.done", "error"}:
                break

    async def _next_event(self) -> dict:
        while True:
            with self._event_lock:
                if self._events:
                    return self._events.popleft()
            if not self._connected:
                raise RuntimeError("Not connected")
            await asyncio.sleep(0.01)

    async def close(self) -> None:
        self._closed = True
        self._connected = False
        if self._session is not None:
            self._session.close()
            self._session = None
        self._chunks = []
        with self._event_lock:
            self._events.clear()

    def _wav_bytes(self) -> bytes:
        raw_audio = b"".join(self._chunks)
        output = io.BytesIO()
        with wave.open(output, "wb") as wav:
            wav.setnchannels(1)
            wav.setsampwidth(2)
            wav.setframerate(self._sample_rate)
            wav.writeframes(raw_audio)
        return output.getvalue()

    def _transcribe_wav(self, wav_data: bytes) -> str:
        if self._provider == "openai":
            url = f"{self._base_url}/audio/transcriptions" if self._base_url else OPENAI_STT_URL
            return self._transcribe_openai_compatible(url, wav_data)
        if self._provider == "groq":
            return self._transcribe_openai_compatible(GROQ_STT_URL, wav_data)
        if self._provider == "deepgram":
            return self._transcribe_deepgram(wav_data)
        if self._provider == "assemblyai":
            return self._transcribe_assemblyai(wav_data)
        raise RuntimeError(f"unsupported STT provider: {self._provider}")

    def _transcribe_openai_compatible(self, url: str, wav_data: bytes) -> str:
        headers = {"Authorization": f"Bearer {self._api_key}"}
        data = {
            "model": self._model,
            "response_format": "json",
        }
        if self._language:
            data["language"] = self._language
        files = {
            "file": ("speech.wav", wav_data, "audio/wav"),
        }
        response = self.session.post(
            url,
            headers=headers,
            data=data,
            files=files,
            timeout=self._timeout,
        )
        response.raise_for_status()
        payload = response.json()
        return str(payload.get("text", "")).strip()

    def _transcribe_deepgram(self, wav_data: bytes) -> str:
        headers = {
            "Authorization": f"Token {self._api_key}",
            "Content-Type": "audio/wav",
        }
        params = {
            "model": self._model,
            "smart_format": "true",
        }
        if self._language:
            params["language"] = self._language
        response = self.session.post(
            DEEPGRAM_STT_URL,
            headers=headers,
            params=params,
            data=wav_data,
            timeout=self._timeout,
        )
        response.raise_for_status()
        payload = response.json()
        try:
            return str(payload["results"]["channels"][0]["alternatives"][0]["transcript"]).strip()
        except (KeyError, IndexError, TypeError) as exc:
            raise RuntimeError("Deepgram response did not contain a transcript") from exc

    def _transcribe_assemblyai(self, wav_data: bytes) -> str:
        headers = {"Authorization": self._api_key}
        upload_response = self.session.post(
            ASSEMBLYAI_UPLOAD_URL,
            headers=headers,
            data=wav_data,
            timeout=self._timeout,
        )
        upload_response.raise_for_status()
        upload_url = upload_response.json().get("upload_url")
        if not upload_url:
            raise RuntimeError("AssemblyAI upload response did not include upload_url")

        payload = {
            "audio_url": upload_url,
            "punctuate": True,
            "format_text": True,
        }
        if self._language:
            payload["language_code"] = "en_us" if self._language == "en" else self._language
        if self._model:
            payload["speech_model"] = self._model

        submit_response = self.session.post(
            ASSEMBLYAI_TRANSCRIPT_URL,
            headers={**headers, "Content-Type": "application/json"},
            json=payload,
            timeout=self._timeout,
        )
        submit_response.raise_for_status()
        transcript_id = submit_response.json().get("id")
        if not transcript_id:
            raise RuntimeError("AssemblyAI transcript response did not include id")

        deadline = time.monotonic() + self._max_poll_time
        while time.monotonic() < deadline:
            poll_response = self.session.get(
                f"{ASSEMBLYAI_TRANSCRIPT_URL}/{transcript_id}",
                headers=headers,
                timeout=self._timeout,
            )
            poll_response.raise_for_status()
            poll_payload = poll_response.json()
            status = poll_payload.get("status")
            if status == "completed":
                return str(poll_payload.get("text", "")).strip()
            if status == "error":
                raise RuntimeError(poll_payload.get("error", "AssemblyAI transcription failed"))
            time.sleep(self._poll_interval)

        raise RuntimeError("Timed out waiting for AssemblyAI transcription")


class ChunkedRESTAdapter:
    """Pseudo-streaming on top of a buffered REST client.

    While recording, the accumulated audio is re-transcribed on an
    interval (by a second client with its own session and a short
    timeout) and surfaced as transcript.partial events — so the flow
    engine sees a streaming provider and molten dictation works against
    plain REST endpoints. The wrapped client still produces the single
    authoritative transcript.done at stop.

    "auto" mode only enables this against local endpoints: each interim
    probe re-sends the whole buffer, which is free on localhost Whisper
    but would re-bill on metered cloud REST providers ("always" opts in).
    """

    supports_streaming = True

    def __init__(
        self,
        inner: BufferedRESTSTTClient,
        interim_client: BufferedRESTSTTClient,
        *,
        interval_ms: int = 2500,
    ):
        self._inner = inner
        self._interim = interim_client
        self._interval = max(0.8, interval_ms / 1000.0)
        self._events: asyncio.Queue = asyncio.Queue()
        self._interim_task: Optional[asyncio.Task] = None
        self._pump_task: Optional[asyncio.Task] = None
        self._stopping = asyncio.Event()
        self._audio_since_probe = False
        self._last_partial = ""

    @property
    def completion_timeout(self) -> float:
        return self._inner.completion_timeout

    async def connect(self, sample_rate: int) -> None:
        await self._inner.connect(sample_rate)
        await self._interim.connect(sample_rate)
        self._stopping.clear()
        self._audio_since_probe = False
        self._last_partial = ""
        self._interim_task = asyncio.create_task(
            self._interim_loop(), name="stt-interim-probe"
        )

    async def send_audio(self, data: bytes) -> None:
        await self._inner.send_audio(data)
        self._audio_since_probe = True

    async def send_audio_done(self) -> None:
        self._stopping.set()
        await self._inner.send_audio_done()
        if self._pump_task is None:
            self._pump_task = asyncio.create_task(
                self._pump_final(), name="stt-final-pump"
            )

    async def receive_events(self) -> AsyncIterator[dict]:
        while True:
            event = await self._events.get()
            yield event
            if event.get("type") in {"transcript.done", "error"}:
                break

    async def close(self) -> None:
        self._stopping.set()
        for task in (self._interim_task, self._pump_task):
            if task is not None:
                task.cancel()
                try:
                    await task
                except (asyncio.CancelledError, Exception):
                    pass
        self._interim_task = None
        self._pump_task = None
        await self._interim.close()
        await self._inner.close()

    async def _pump_final(self) -> None:
        try:
            async for event in self._inner.receive_events():
                await self._events.put(event)
        except Exception as exc:
            await self._events.put(
                {"type": "error", "message": str(exc) or exc.__class__.__name__}
            )

    async def _interim_loop(self) -> None:
        while not self._stopping.is_set():
            try:
                await asyncio.wait_for(self._stopping.wait(), timeout=self._interval)
                return  # stopping
            except (TimeoutError, asyncio.TimeoutError):
                pass
            if not self._audio_since_probe or self._stopping.is_set():
                continue
            self._audio_since_probe = False
            wav_data = self._inner._wav_bytes()
            try:
                text = await asyncio.to_thread(
                    self._interim._transcribe_wav, wav_data
                )
            except Exception as exc:
                # Interim probes are best-effort; the final POST decides.
                logger.debug("Interim REST transcription failed: %s", exc)
                continue
            if self._stopping.is_set():
                return
            if text and text != self._last_partial:
                self._last_partial = text
                await self._events.put(
                    {"type": "transcript.partial", "text": text, "is_final": False}
                )
