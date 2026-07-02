"""Public demo endpoints for the landing page — real xAI, hard limits.

The landing page has no key (it's public JS), so these endpoints are
unauthenticated by design and defended in depth instead:

- a global daily budget in USD (default $1) shared across all demo
  traffic — when it's spent, everything refuses and the page falls back
  to the browser's engines with an honest label;
- per-IP daily counters on every endpoint;
- hard size caps per request (20 s of dictation, 220 TTS chars,
  280-char questions with a bounded completion).

Worst case with all knobs at defaults: budget × 30 ≈ $30/month, no
matter what a scraper does.

    WS   /v1/demo/stt     streaming Grok STT, xAI wire protocol
    POST /v1/demo/tts     Grok `eve` synthesis
    POST /v1/demo/ask     docs-grounded Q&A (Grok chat)
    GET  /v1/demo/status  what's live + today's served counts

All demo responses carry `Access-Control-Allow-Origin: *` — they are
public, keyless, and rate-limited, so CORS is not the defense here.
"""

import asyncio
import json
import logging
from urllib.parse import urlencode

import aiohttp
from aiohttp import WSMsgType, web

from .db import Store

logger = logging.getLogger(__name__)

# Upstream prices (see relay/README.md); the flat ask cost is a
# deliberately conservative over-estimate of a small chat completion.
STT_USD_PER_SECOND = 0.20 / 3600
TTS_USD_PER_CHAR = 4.20 / 1_000_000
ASK_USD_FLAT = 0.002

MAX_DICTATION_SECONDS = 20.0
MAX_SESSION_WALL_SECONDS = 60.0
MAX_TTS_CHARS = 220
MAX_ASK_CHARS = 280
ASK_MAX_TOKENS = 220

IP_DAILY_CAPS = {"dictations": 8, "tts": 12, "asks": 15}

CORS_HEADERS = {
    "Access-Control-Allow-Origin": "*",
    "Access-Control-Allow-Methods": "GET, POST, OPTIONS",
    "Access-Control-Allow-Headers": "Content-Type",
}

# What the `ask` command knows. Facts only — mirrors README.md.
DOCS_CONTEXT = """You answer questions about HyperFurion VK, a Linux voice
keyboard, inside a terminal on its landing page. Facts:
- Install: curl -fsSL https://raw.githubusercontent.com/liamghennigan/HyperFurion-VK/main/install.sh | bash
- Press Ctrl+Alt+V, speak, and it types into whatever app has focus.
  Tap toggles recording; hold records until release (hotkey.mode = auto,
  or force toggle/hold/disabled; hold_threshold_ms defaults to 280).
- Esc cancels. `voice-keyboard tts` (bind e.g. Ctrl+Alt+T) reads the
  selected text aloud.
- Config: ~/.config/voice-keyboard/config.toml. Restart after changes:
  systemctl --user restart voice-keyboard-daemon
- STT providers: xai (default), hyperfurion (hosted subscription),
  openai, groq, deepgram, assemblyai. TTS: xai (default, voice eve),
  hyperfurion, openai, elevenlabs. Model IDs are plain config values.
- hyperfurion provider = $5/mo hosted tier: one hfk_ key, no provider
  accounts. Free path: bring your own provider key.
- Requirements: Linux, Python 3.11+, systemd user services, uinput
  (user in `input` group — log out/in after install). GNOME Shell
  Wayland gets an overlay; other desktops get notifications.
- Audio goes to the configured cloud provider for transcription; it is
  held in memory, never written to disk. Injection is ASCII + newline +
  tab via uinput. MIT licensed.
Answer in at most three short lines, plain text, terminal voice. If the
answer isn't in these facts, say so and point to the README."""


def _client_ip(request: web.Request) -> str:
    forwarded = request.headers.get("X-Forwarded-For", "")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.remote or "unknown"


def _budget_left(store: Store, budget_usd: float) -> float:
    return budget_usd - store.demo_counts("")["spent_usd"]


def _refusal(store: Store, cfg: dict, ip: str, kind: str) -> str:
    """Why this demo request can't run right now ('' = it can)."""
    if not cfg["master_key"]:
        return "hosted demo is not configured"
    if _budget_left(store, cfg["demo_daily_budget_usd"]) <= 0:
        return "hosted demo budget is spent for today — back tomorrow"
    if store.demo_counts(ip)[kind] >= IP_DAILY_CAPS[kind]:
        return "daily demo limit reached for your address — back tomorrow"
    return ""


async def demo_status(request: web.Request) -> web.Response:
    cfg = request.app["cfg"]
    store: Store = request.app["store"]
    live = bool(cfg["master_key"])
    budget_ok = live and _budget_left(store, cfg["demo_daily_budget_usd"]) > 0
    day = store.demo_counts("")
    return web.json_response(
        {
            "live": budget_ok,
            "reason": "" if budget_ok else (
                "demo budget spent for today" if live else "demo not configured"
            ),
            "caps": {
                "dictation_seconds": MAX_DICTATION_SECONDS,
                "tts_chars": MAX_TTS_CHARS,
                "ask_chars": MAX_ASK_CHARS,
            },
            "served_today": {
                "dictations": day["dictations"],
                "tts": day["tts"],
                "asks": day["asks"],
            },
        },
        headers=CORS_HEADERS,
    )


async def demo_stt(request: web.Request) -> web.WebSocketResponse:
    ws = web.WebSocketResponse(heartbeat=20.0)
    await ws.prepare(request)
    cfg = request.app["cfg"]
    store: Store = request.app["store"]
    ip = _client_ip(request)

    refusal = _refusal(store, cfg, ip, "dictations")
    if refusal:
        await ws.send_str(json.dumps({"type": "error", "message": refusal}))
        await ws.close(code=4429)
        return ws

    try:
        sample_rate = int(request.query.get("sample_rate", "16000"))
    except ValueError:
        sample_rate = 16000
    sample_rate = min(max(sample_rate, 8000), 48000)
    bytes_per_second = sample_rate * 2
    max_bytes = int(MAX_DICTATION_SECONDS * bytes_per_second)

    query = {
        "sample_rate": str(sample_rate),
        "encoding": "pcm",
        "interim_results": request.query.get("interim_results", "true"),
        "language": request.query.get("language", "en"),
    }
    upstream_url = f"{cfg['upstream_stt_url']}?{urlencode(query)}"
    headers = {"Authorization": f"Bearer {cfg['master_key']}"}
    session: aiohttp.ClientSession = request.app["http"]

    audio_bytes = 0
    try:
        async with asyncio.timeout(MAX_SESSION_WALL_SECONDS):
            async with session.ws_connect(upstream_url, headers=headers) as upstream:

                async def client_to_upstream() -> None:
                    nonlocal audio_bytes
                    capped = False
                    async for msg in ws:
                        if msg.type == WSMsgType.BINARY:
                            if capped:
                                continue  # drop audio past the cap; finalize is in flight
                            audio_bytes += len(msg.data)
                            await upstream.send_bytes(msg.data)
                            if audio_bytes >= max_bytes:
                                capped = True
                                audio_bytes = min(audio_bytes, max_bytes)
                                await ws.send_str(json.dumps({
                                    "type": "demo.limit",
                                    "message": f"{int(MAX_DICTATION_SECONDS)} s demo cap — finalizing",
                                }))
                                await upstream.send_str(json.dumps({"type": "audio.done"}))
                        elif msg.type == WSMsgType.TEXT:
                            await upstream.send_str(msg.data)
                        elif msg.type == WSMsgType.ERROR:
                            break

                async def upstream_to_client() -> None:
                    async for msg in upstream:
                        if msg.type == WSMsgType.TEXT:
                            await ws.send_str(msg.data)
                        elif msg.type == WSMsgType.ERROR:
                            break

                tasks = [
                    asyncio.create_task(client_to_upstream()),
                    asyncio.create_task(upstream_to_client()),
                ]
                done, pending = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
                for task in pending:
                    task.cancel()
                await asyncio.gather(*pending, return_exceptions=True)
                for task in done:
                    if task.exception() is not None:
                        raise task.exception()
    except TimeoutError:
        if not ws.closed:
            await ws.send_str(json.dumps({"type": "error", "message": "demo session timed out"}))
    except aiohttp.ClientError as exc:
        logger.warning("demo STT upstream failed: %s", exc)
        if not ws.closed:
            await ws.send_str(
                json.dumps({"type": "error", "message": f"upstream STT connection failed: {exc}"})
            )
    finally:
        if audio_bytes > 0:
            seconds = min(audio_bytes / bytes_per_second, MAX_DICTATION_SECONDS)
            store.demo_record(ip, "dictations", seconds * STT_USD_PER_SECOND)
    if not ws.closed:
        await ws.close()
    return ws


async def demo_tts(request: web.Request) -> web.Response:
    cfg = request.app["cfg"]
    store: Store = request.app["store"]
    ip = _client_ip(request)

    refusal = _refusal(store, cfg, ip, "tts")
    if refusal:
        return web.json_response({"error": refusal}, status=429, headers=CORS_HEADERS)

    try:
        payload = await request.json()
    except (json.JSONDecodeError, UnicodeDecodeError):
        return web.json_response({"error": "request body must be JSON"}, status=400, headers=CORS_HEADERS)
    text = str(payload.get("text", "")).strip()[:MAX_TTS_CHARS]
    if not text:
        return web.json_response({"error": "text is required"}, status=400, headers=CORS_HEADERS)

    forward = {"text": text, "voice_id": "eve", "language": "en"}
    headers = {"Authorization": f"Bearer {cfg['master_key']}"}
    session: aiohttp.ClientSession = request.app["http"]
    try:
        async with session.post(cfg["upstream_tts_url"], json=forward, headers=headers) as resp:
            body = await resp.read()
            if resp.status >= 400:
                logger.warning("demo TTS upstream returned %d", resp.status)
                return web.json_response(
                    {"error": "upstream TTS failed"}, status=502, headers=CORS_HEADERS
                )
            content_type = resp.headers.get("Content-Type", "audio/mpeg")
    except aiohttp.ClientError as exc:
        logger.warning("demo TTS upstream failed: %s", exc)
        return web.json_response(
            {"error": f"upstream TTS request failed: {exc}"}, status=502, headers=CORS_HEADERS
        )

    store.demo_record(ip, "tts", len(text) * TTS_USD_PER_CHAR)
    return web.Response(body=body, content_type=content_type, headers=CORS_HEADERS)


async def demo_ask(request: web.Request) -> web.Response:
    cfg = request.app["cfg"]
    store: Store = request.app["store"]
    ip = _client_ip(request)

    refusal = _refusal(store, cfg, ip, "asks")
    if refusal:
        return web.json_response({"error": refusal}, status=429, headers=CORS_HEADERS)

    try:
        payload = await request.json()
    except (json.JSONDecodeError, UnicodeDecodeError):
        return web.json_response({"error": "request body must be JSON"}, status=400, headers=CORS_HEADERS)
    question = str(payload.get("question", "")).strip()[:MAX_ASK_CHARS]
    if not question:
        return web.json_response({"error": "question is required"}, status=400, headers=CORS_HEADERS)

    body = {
        "model": cfg["demo_chat_model"],
        "messages": [
            {"role": "system", "content": DOCS_CONTEXT},
            {"role": "user", "content": question},
        ],
        "max_tokens": ASK_MAX_TOKENS,
        "temperature": 0.3,
    }
    headers = {"Authorization": f"Bearer {cfg['master_key']}"}
    session: aiohttp.ClientSession = request.app["http"]
    try:
        async with session.post(cfg["upstream_chat_url"], json=body, headers=headers) as resp:
            data = await resp.json(content_type=None)
            if resp.status >= 400:
                logger.warning("demo ask upstream returned %d", resp.status)
                return web.json_response(
                    {"error": "upstream chat failed"}, status=502, headers=CORS_HEADERS
                )
    except (aiohttp.ClientError, json.JSONDecodeError) as exc:
        logger.warning("demo ask upstream failed: %s", exc)
        return web.json_response(
            {"error": f"upstream chat request failed: {exc}"}, status=502, headers=CORS_HEADERS
        )

    try:
        answer = str(data["choices"][0]["message"]["content"]).strip()
    except (KeyError, IndexError, TypeError):
        return web.json_response(
            {"error": "upstream chat response was malformed"}, status=502, headers=CORS_HEADERS
        )

    store.demo_record(ip, "asks", ASK_USD_FLAT)
    return web.json_response({"answer": answer}, headers=CORS_HEADERS)


async def demo_preflight(_request: web.Request) -> web.Response:
    return web.Response(status=204, headers=CORS_HEADERS)


def register(app: web.Application) -> None:
    app.router.add_get("/v1/demo/status", demo_status)
    app.router.add_get("/v1/demo/stt", demo_stt)
    app.router.add_post("/v1/demo/tts", demo_tts)
    app.router.add_post("/v1/demo/ask", demo_ask)
    app.router.add_options("/v1/demo/tts", demo_preflight)
    app.router.add_options("/v1/demo/ask", demo_preflight)
