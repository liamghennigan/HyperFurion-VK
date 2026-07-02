"""A fake xAI upstream speaking just enough of the real wire protocol.

STT: WebSocket that sends `transcript.created` on connect, counts binary
audio frames, and answers `audio.done` with a final transcript naming
the byte count — so tests can assert byte-exact proxying.

TTS: returns a fixed MP3-ish body and records the payload it was given.
"""

import json

from aiohttp import WSMsgType, web


def make_fake_xai(master_key: str) -> web.Application:
    app = web.Application()
    state: dict = {"stt": [], "tts": []}
    app["state"] = state

    async def stt(request: web.Request) -> web.WebSocketResponse:
        ws = web.WebSocketResponse()
        await ws.prepare(request)
        record = {
            "auth": request.headers.get("Authorization", ""),
            "query": dict(request.query),
            "audio_bytes": 0,
        }
        state["stt"].append(record)
        if record["auth"] != f"Bearer {master_key}":
            await ws.send_str(json.dumps({"type": "error", "message": "bad upstream key"}))
            await ws.close()
            return ws
        await ws.send_str(json.dumps({"type": "transcript.created"}))
        async for msg in ws:
            if msg.type == WSMsgType.BINARY:
                record["audio_bytes"] += len(msg.data)
            elif msg.type == WSMsgType.TEXT:
                event = json.loads(msg.data)
                if event.get("type") == "audio.done":
                    await ws.send_str(
                        json.dumps(
                            {
                                "type": "transcript.text",
                                "text": f"received {record['audio_bytes']} bytes",
                            }
                        )
                    )
                    await ws.send_str(json.dumps({"type": "transcript.done"}))
                    break
        await ws.close()
        return ws

    async def tts(request: web.Request) -> web.Response:
        record = {
            "auth": request.headers.get("Authorization", ""),
            "payload": await request.json(),
        }
        state["tts"].append(record)
        if record["auth"] != f"Bearer {master_key}":
            return web.json_response({"error": "bad upstream key"}, status=401)
        return web.Response(body=b"FAKE-MP3-BYTES", content_type="audio/mpeg")

    app.router.add_get("/v1/stt", stt)
    app.router.add_post("/v1/tts", tts)
    return app
