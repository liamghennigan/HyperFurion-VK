"""The xAI realtime voice agent — the premium conversational brain.

Speaks the xAI realtime protocol over a websocket to a Voice Agent
Builder agent. IMPORTANT: these agents are voice-to-voice — they answer
AUDIO input with spoken audio + a transcript, and return
`unimplemented` for typed text. So the daemon feeds this the captured
microphone audio from a converse-hotkey turn; typed conversations use the
local brain instead.
"""

from __future__ import annotations

import base64
import json
from dataclasses import dataclass, field
from typing import Optional

# 20 ms of 16 kHz mono s16le per realtime audio append.
_AUDIO_CHUNK_BYTES = 3200

_REALTIME_URL = "wss://api.x.ai/v1/realtime?agent_id={agent_id}"


@dataclass
class RealtimeResult:
    transcript: str
    audio: bytes = b""
    raw_events: list[dict] = field(default_factory=list)


class XAIRealtimeClient:
    supports_audio = True

    def __init__(self, *, api_key: str, agent_id: str, connect_timeout: float = 8.0):
        self.api_key = api_key
        self.agent_id = agent_id
        self._connect_timeout = connect_timeout

    async def ask_text(self, text: str) -> RealtimeResult:
        if not self.api_key:
            raise RuntimeError("XAI_API_KEY is required for the realtime voice agent")
        if not self.agent_id:
            raise RuntimeError("assistant.agent_id is required for the realtime voice agent")
        import asyncio

        import websockets
        from websockets.exceptions import WebSocketException

        headers = {"Authorization": f"Bearer {self.api_key}"}
        url = _REALTIME_URL.format(agent_id=self.agent_id)
        transcript_parts: list[str] = []
        audio_parts: list[bytes] = []
        try:
            async with websockets.connect(
                url, additional_headers=headers, open_timeout=self._connect_timeout
            ) as ws:
                await ws.send(
                    json.dumps(
                        {
                            "type": "conversation.item.create",
                            "item": {
                                "type": "message",
                                "role": "user",
                                "content": [{"type": "input_text", "text": text}],
                            },
                        }
                    )
                )
                await ws.send(json.dumps({"type": "response.create"}))
                async for raw in ws:
                    event = json.loads(raw)
                    etype = event.get("type")
                    if etype in {
                        "response.output_audio_transcript.delta",
                        "response.output_text.delta",
                    }:
                        transcript_parts.append(str(event.get("delta", "")))
                    elif etype == "response.output_audio.delta":
                        audio_parts.append(base64.b64decode(str(event.get("delta", ""))))
                    elif etype in {"response.done", "response.completed"}:
                        break
                    elif etype == "error":
                        raise RuntimeError(str(event.get("message", event)))
        except (WebSocketException, asyncio.TimeoutError, OSError) as exc:
            raise RuntimeError(f"realtime voice agent failed: {exc}") from exc

        return RealtimeResult(
            transcript="".join(transcript_parts).strip(),
            audio=b"".join(audio_parts),
        )

    async def ask_audio(self, pcm: bytes, *, sample_rate: int = 16000) -> RealtimeResult:
        """The voice-native path: stream captured mic PCM to the agent and
        collect its spoken answer (audio) plus a transcript. This is what
        the Voice Agent Builder agent actually implements."""
        if not self.api_key:
            raise RuntimeError("XAI_API_KEY is required for the realtime voice agent")
        if not self.agent_id:
            raise RuntimeError("assistant.agent_id is required for the realtime voice agent")
        if not pcm:
            raise RuntimeError("no audio captured")
        import asyncio

        import websockets
        from websockets.exceptions import WebSocketException

        headers = {"Authorization": f"Bearer {self.api_key}"}
        url = _REALTIME_URL.format(agent_id=self.agent_id)
        transcript_parts: list[str] = []
        audio_parts: list[bytes] = []
        try:
            async with websockets.connect(
                url, additional_headers=headers, open_timeout=self._connect_timeout,
                max_size=None,
            ) as ws:
                for offset in range(0, len(pcm), _AUDIO_CHUNK_BYTES):
                    chunk = pcm[offset:offset + _AUDIO_CHUNK_BYTES]
                    await ws.send(
                        json.dumps(
                            {
                                "type": "input_audio_buffer.append",
                                "audio": base64.b64encode(chunk).decode("ascii"),
                            }
                        )
                    )
                await ws.send(json.dumps({"type": "input_audio_buffer.commit"}))
                await ws.send(json.dumps({"type": "response.create"}))
                async for raw in ws:
                    event = json.loads(raw)
                    etype = event.get("type", "")
                    if "transcript" in etype and etype.endswith(".delta"):
                        transcript_parts.append(str(event.get("delta", "")))
                    elif etype == "response.output_audio.delta":
                        audio_parts.append(base64.b64decode(str(event.get("delta", ""))))
                    elif etype == "error":
                        raise RuntimeError(str(event.get("message", event)))
                    elif etype in {"response.done", "response.completed"}:
                        # The full text also rides the terminal event; only
                        # use it when we saw no streaming deltas, so the
                        # transcript is never doubled.
                        if not transcript_parts:
                            for item in event.get("response", {}).get("output", []):
                                for content in item.get("content", []):
                                    if content.get("transcript"):
                                        transcript_parts.append(str(content["transcript"]))
                                    elif content.get("text"):
                                        transcript_parts.append(str(content["text"]))
                        break
        except (WebSocketException, asyncio.TimeoutError, OSError) as exc:
            raise RuntimeError(f"realtime voice agent failed: {exc}") from exc

        transcript = "".join(transcript_parts).strip()
        audio = b"".join(audio_parts)
        if not transcript and not audio:
            raise RuntimeError("realtime voice agent returned no output")
        return RealtimeResult(transcript=transcript, audio=audio)


def create_realtime_client(config: dict) -> Optional["XAIRealtimeClient"]:
    assistant_cfg = config.get("assistant", {})
    agent_id = str(assistant_cfg.get("agent_id", "")).strip()
    api_key = str(assistant_cfg.get("api_key", "")).strip()
    if not api_key:
        # Fall back to the provider key the rest of the daemon uses.
        providers = config.get("providers", {})
        api_key = str(providers.get("xai", {}).get("api_key", "")).strip()
        if not api_key:
            api_key = str(config.get("xai", {}).get("api_key", "")).strip()
    if not agent_id or not api_key:
        return None
    return XAIRealtimeClient(api_key=api_key, agent_id=agent_id)
