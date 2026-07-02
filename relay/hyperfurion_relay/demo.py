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
    # X-Forwarded-For is client-supplied and trivially spoofable, so it is
    # only honored when the operator has declared a trusted reverse proxy
    # (DEMO_TRUST_FORWARDED=1). Otherwise the peer address is authoritative.
    # Never returns "" (which is the reserved global-aggregate key).
    if request.app["cfg"].get("demo_trust_forwarded"):
        first = request.headers.get("X-Forwarded-For", "").split(",")[0].strip()
        if first:
            return first
    return request.remote or "unknown"


def _demo_error(status: int, message: str) -> web.Response:
    return web.json_response({"error": message}, status=status, headers=CORS_HEADERS)


def _safe_content_type(raw: str | None, default: str) -> str:
    # aiohttp's web.Response rejects a content_type carrying a charset/param;
    # keep only the media type.
    return (raw or default).split(";")[0].strip() or default


def _budget_left(store: Store, budget_usd: float) -> float:
    return budget_usd - store.demo_counts("")["spent_usd"]


def _reserve(store: Store, cfg: dict, ip: str, kind: str, usd: float) -> str:
    """Atomically admit a demo request, reserving `usd` against the budget +
    per-IP cap. Returns "" if admitted (and charged), else a user-facing
    refusal. STT reserves its max cost here and reconciles at close."""
    if not cfg["master_key"]:
        return "hosted demo is not configured"
    outcome = store.demo_try_charge(
        ip, kind, usd, IP_DAILY_CAPS[kind], cfg["demo_daily_budget_usd"]
    )
    if outcome == "budget":
        return "hosted demo budget is spent for today — back tomorrow"
    if outcome == "ip-cap":
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

    # Reserve the session's MAX possible cost up front so concurrent sessions
    # cannot each pass a stale budget check; reconcile down to actual duration
    # at close.
    max_usd = MAX_DICTATION_SECONDS * STT_USD_PER_SECOND
    refusal = _reserve(store, cfg, ip, "dictations", max_usd)
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
        # Refund the unused portion of the reserved max (the count already
        # landed at admission; only the spend is reconciled here).
        seconds = min(audio_bytes / bytes_per_second, MAX_DICTATION_SECONDS)
        store.demo_adjust_spend(ip, seconds * STT_USD_PER_SECOND - max_usd)
    if not ws.closed:
        await ws.close()
    return ws


async def demo_tts(request: web.Request) -> web.Response:
    cfg = request.app["cfg"]
    store: Store = request.app["store"]
    ip = _client_ip(request)

    if not cfg["master_key"]:
        return _demo_error(503, "hosted demo is not configured")
    try:
        payload = await request.json()
    except (json.JSONDecodeError, UnicodeDecodeError):
        return _demo_error(400, "request body must be JSON")
    text = str(payload.get("text", "")).strip()[:MAX_TTS_CHARS]
    if not text:
        return _demo_error(400, "text is required")

    usd = len(text) * TTS_USD_PER_CHAR
    refusal = _reserve(store, cfg, ip, "tts", usd)  # charges before upstream
    if refusal:
        return _demo_error(429, refusal)

    forward = {"text": text, "voice_id": "eve", "language": "en"}
    headers = {"Authorization": f"Bearer {cfg['master_key']}"}
    session: aiohttp.ClientSession = request.app["http"]
    try:
        async with session.post(cfg["upstream_tts_url"], json=forward, headers=headers) as resp:
            body = await resp.read()
            if resp.status >= 400:
                logger.warning("demo TTS upstream returned %d", resp.status)
                store.demo_adjust_spend(ip, -usd)  # refund a failed synthesis
                return _demo_error(502, "upstream TTS failed")
            content_type = _safe_content_type(resp.headers.get("Content-Type"), "audio/mpeg")
    except aiohttp.ClientError as exc:
        logger.warning("demo TTS upstream failed: %s", exc)
        store.demo_adjust_spend(ip, -usd)
        return _demo_error(502, f"upstream TTS request failed: {exc}")

    return web.Response(body=body, content_type=content_type, headers=CORS_HEADERS)


async def demo_ask(request: web.Request) -> web.Response:
    cfg = request.app["cfg"]
    store: Store = request.app["store"]
    ip = _client_ip(request)

    if not cfg["master_key"]:
        return _demo_error(503, "hosted demo is not configured")
    try:
        payload = await request.json()
    except (json.JSONDecodeError, UnicodeDecodeError):
        return _demo_error(400, "request body must be JSON")
    question = str(payload.get("question", "")).strip()[:MAX_ASK_CHARS]
    if not question:
        return _demo_error(400, "question is required")

    refusal = _reserve(store, cfg, ip, "asks", ASK_USD_FLAT)
    if refusal:
        return _demo_error(429, refusal)

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
                store.demo_adjust_spend(ip, -ASK_USD_FLAT)
                return _demo_error(502, "upstream chat failed")
    except (aiohttp.ClientError, json.JSONDecodeError) as exc:
        logger.warning("demo ask upstream failed: %s", exc)
        store.demo_adjust_spend(ip, -ASK_USD_FLAT)
        return _demo_error(502, f"upstream chat request failed: {exc}")

    try:
        answer = str(data["choices"][0]["message"]["content"]).strip()
    except (KeyError, IndexError, TypeError):
        store.demo_adjust_spend(ip, -ASK_USD_FLAT)
        return _demo_error(502, "upstream chat response was malformed")

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
