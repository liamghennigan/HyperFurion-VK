"""A fake xAI upstream speaking just enough of the real wire protocol.

STT: WebSocket that sends `transcript.created` on connect, counts binary
audio frames, emits one `transcript.partial` on the first audio, and
answers `audio.done` with a `transcript.done` naming the byte count —
the same event dialect the daemon consumes, so tests can assert
byte-exact proxying end to end.

TTS: returns a fixed MP3-ish body and records the payload it was given.
Chat: returns a fixed completion and records the messages.
"""

import json

from aiohttp import WSMsgType, web


def make_fake_xai(master_key: str) -> web.Application:
    app = web.Application()
    state: dict = {"stt": [], "tts": [], "chat": []}
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
        sent_partial = False
        async for msg in ws:
            if msg.type == WSMsgType.BINARY:
                record["audio_bytes"] += len(msg.data)
                if not sent_partial:
                    sent_partial = True
                    await ws.send_str(
                        json.dumps(
                            {"type": "transcript.partial", "text": "receiving", "is_final": False}
                        )
                    )
            elif msg.type == WSMsgType.TEXT:
                event = json.loads(msg.data)
                if event.get("type") == "audio.done":
                    await ws.send_str(
                        json.dumps(
                            {
                                "type": "transcript.done",
                                "text": f"received {record['audio_bytes']} bytes",
                            }
                        )
                    )
                    break
        await ws.close()
        return ws

    async def tts(request: web.Request) -> web.Response:
        payload = await request.json()
        record = {"auth": request.headers.get("Authorization", ""), "payload": payload}
        state["tts"].append(record)
        if record["auth"] != f"Bearer {master_key}":
            return web.json_response({"error": "bad upstream key"}, status=401)
        text = str(payload.get("text", ""))
        # Sentinels: exercise upstream Content-Type headers that carry a
        # charset parameter (which web.Response(content_type=...) rejects).
        if "charsetfail" in text:
            return web.Response(
                status=400, body=b'{"error":"bad"}',
                headers={"Content-Type": "application/json; charset=utf-8"},
            )
        if "charsetok" in text:
            return web.Response(
                body=b"FAKE-MP3-BYTES",
                headers={"Content-Type": "audio/mpeg; charset=binary"},
            )
        return web.Response(body=b"FAKE-MP3-BYTES", content_type="audio/mpeg")

    async def chat(request: web.Request) -> web.Response:
        record = {
            "auth": request.headers.get("Authorization", ""),
            "payload": await request.json(),
        }
        state["chat"].append(record)
        if record["auth"] != f"Bearer {master_key}":
            return web.json_response({"error": "bad upstream key"}, status=401)
        question = record["payload"]["messages"][-1]["content"]
        return web.json_response(
            {"choices": [{"message": {"content": f"fake answer to: {question}"}}]}
        )

    app.router.add_get("/v1/stt", stt)
    app.router.add_post("/v1/tts", tts)
    app.router.add_post("/v1/chat/completions", chat)
    return app
