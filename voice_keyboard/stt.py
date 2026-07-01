import asyncio
import json
import logging
from typing import AsyncIterator, Optional
from urllib.parse import urlencode

import websockets
from websockets.exceptions import WebSocketException

logger = logging.getLogger(__name__)

XAI_STT_WS_URL = "wss://api.x.ai/v1/stt"
MAX_CONNECT_RETRIES = 2
CONNECT_BACKOFF_BASE = 0.5


class STTClient:
    def __init__(
        self,
        api_key: str,
        language: str = "en",
        interim_results: bool = True,
        connect_timeout: float = 5.0,
    ):
        self._api_key = api_key
        self._language = language
        self._interim_results = interim_results
        self._connect_timeout = connect_timeout
        self._ws: Optional[websockets.WebSocketClientProtocol] = None

    def _url_for_sample_rate(self, sample_rate: int) -> str:
        query = {
            "sample_rate": str(sample_rate),
            "encoding": "pcm",
            "interim_results": "true" if self._interim_results else "false",
        }
        if self._language:
            query["language"] = self._language
        return f"{XAI_STT_WS_URL}?{urlencode(query)}"

    async def connect(self, sample_rate: int) -> None:
        headers = {"Authorization": f"Bearer {self._api_key}"}
        url = self._url_for_sample_rate(sample_rate)
        last_error: Optional[Exception] = None
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
                last_error = exc
                await self.close()
                logger.warning(
                    "STT connect attempt %d/%d failed: %s",
                    attempt,
                    MAX_CONNECT_RETRIES,
                    exc,
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
