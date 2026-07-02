"""The relay application: xAI-shaped endpoints with auth and metering.

Endpoints
    GET  /healthz          liveness + tier catalog
    WS   /v1/stt           streaming STT, proxied to xAI (same protocol)
    POST /v1/tts           TTS, proxied to xAI (same request/response)
    GET  /v1/usage         quota status for the presented key
    POST /stripe/webhook   Stripe events (signature-verified)
    GET  /welcome          one-time key pickup after Stripe checkout

Auth is `Authorization: Bearer hfk_...` everywhere — identical in shape
to xAI's own auth, which is what lets the daemon reuse its xAI clients.
"""

import asyncio
import datetime
import html
import json
import logging
import os
from urllib.parse import urlencode

import aiohttp
from aiohttp import WSMsgType, web

from . import stripe_webhook
from .db import PERIOD_SECONDS, Store
from .tiers import TIERS, tier_named

logger = logging.getLogger(__name__)

DEFAULT_UPSTREAM_STT_URL = "wss://api.x.ai/v1/stt"
DEFAULT_UPSTREAM_TTS_URL = "https://api.x.ai/v1/tts"

# Only these reach upstream; everything else a client sends is dropped.
STT_QUERY_PARAMS = ("sample_rate", "encoding", "interim_results", "language")
TTS_PAYLOAD_KEYS = ("text", "voice_id", "language", "model")

WS_CLOSE_AUTH = 4401
WS_CLOSE_QUOTA = 4429
WS_CLOSE_UPSTREAM = 1011


class QuotaExceeded(Exception):
    pass


def _config_from_env() -> dict:
    return {
        "master_key": os.environ.get("XAI_API_KEY", ""),
        "db_path": os.environ.get("RELAY_DB", "relay.db"),
        "upstream_stt_url": os.environ.get("UPSTREAM_STT_URL", DEFAULT_UPSTREAM_STT_URL),
        "upstream_tts_url": os.environ.get("UPSTREAM_TTS_URL", DEFAULT_UPSTREAM_TTS_URL),
        "stripe_webhook_secret": os.environ.get("STRIPE_WEBHOOK_SECRET", ""),
    }


def _bearer_key(request: web.Request) -> str:
    auth = request.headers.get("Authorization", "")
    if auth.startswith("Bearer "):
        return auth[len("Bearer "):].strip()
    return ""


def _authenticate(request: web.Request) -> tuple[dict | None, str]:
    """Resolve the request's bearer key. Returns (user, error_message)."""
    key = _bearer_key(request)
    if not key:
        return None, "missing Authorization bearer key"
    user = request.app["store"].lookup_key(key)
    if user is None:
        return None, "invalid HyperFurion key"
    if user["status"] != "active":
        return None, "subscription is not active"
    return user, ""


def _period_end_date(user: dict) -> str:
    end = datetime.datetime.fromtimestamp(
        user["period_start"] + PERIOD_SECONDS, tz=datetime.timezone.utc
    )
    return end.date().isoformat()


def _json_error(status: int, message: str) -> web.Response:
    return web.json_response({"error": message}, status=status)


# -- STT: WebSocket proxy --------------------------------------------------


async def _ws_refuse(ws: web.WebSocketResponse, message: str, code: int) -> web.WebSocketResponse:
    # Refusals ride inside the WebSocket as xAI-style error events so the
    # daemon's ready-wait surfaces the message to the user verbatim.
    await ws.send_str(json.dumps({"type": "error", "message": message}))
    await ws.close(code=code)
    return ws


async def stt_websocket(request: web.Request) -> web.WebSocketResponse:
    ws = web.WebSocketResponse(heartbeat=20.0)
    await ws.prepare(request)

    cfg = request.app["cfg"]
    store: Store = request.app["store"]
    if not cfg["master_key"]:
        return await _ws_refuse(ws, "relay is not configured (no upstream key)", WS_CLOSE_UPSTREAM)

    user, error = _authenticate(request)
    if user is None:
        return await _ws_refuse(ws, error, WS_CLOSE_AUTH)

    tier = tier_named(user["tier"])
    budget_seconds = tier.stt_seconds - float(user["stt_seconds_used"])
    if budget_seconds <= 0:
        return await _ws_refuse(
            ws,
            f"HyperFurion STT quota exceeded ({tier.stt_seconds // 3600} h/month"
            f" on the {tier.name} tier) — resets {_period_end_date(user)}",
            WS_CLOSE_QUOTA,
        )

    try:
        sample_rate = int(request.query.get("sample_rate", "16000"))
    except ValueError:
        sample_rate = 16000
    sample_rate = min(max(sample_rate, 8000), 48000)
    bytes_per_second = sample_rate * 2  # 16-bit mono PCM

    upstream_query = {k: request.query[k] for k in STT_QUERY_PARAMS if k in request.query}
    upstream_url = f"{cfg['upstream_stt_url']}?{urlencode(upstream_query)}"
    headers = {"Authorization": f"Bearer {cfg['master_key']}"}

    session: aiohttp.ClientSession = request.app["http"]
    audio_bytes = 0
    try:
        async with session.ws_connect(upstream_url, headers=headers) as upstream:

            async def client_to_upstream() -> None:
                nonlocal audio_bytes
                async for msg in ws:
                    if msg.type == WSMsgType.BINARY:
                        audio_bytes += len(msg.data)
                        if audio_bytes / bytes_per_second > budget_seconds:
                            raise QuotaExceeded
                        await upstream.send_bytes(msg.data)
                    elif msg.type == WSMsgType.TEXT:
                        await upstream.send_str(msg.data)
                    elif msg.type == WSMsgType.ERROR:
                        break

            async def upstream_to_client() -> None:
                async for msg in upstream:
                    if msg.type == WSMsgType.TEXT:
                        await ws.send_str(msg.data)
                    elif msg.type == WSMsgType.BINARY:
                        await ws.send_bytes(msg.data)
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
                exc = task.exception()
                if isinstance(exc, QuotaExceeded):
                    await _ws_refuse(
                        ws,
                        f"HyperFurion STT quota exhausted mid-session — resets"
                        f" {_period_end_date(user)}",
                        WS_CLOSE_QUOTA,
                    )
                elif exc is not None:
                    raise exc
    except aiohttp.ClientError as exc:
        logger.warning("upstream STT connection failed: %s", exc)
        await _ws_refuse(ws, f"upstream STT connection failed: {exc}", WS_CLOSE_UPSTREAM)
    finally:
        seconds = min(audio_bytes / bytes_per_second, max(budget_seconds, 0.0))
        if seconds > 0:
            store.add_usage(int(user["id"]), stt_seconds=seconds)
    if not ws.closed:
        await ws.close()
    return ws


# -- TTS: buffered proxy ---------------------------------------------------


async def tts(request: web.Request) -> web.Response:
    cfg = request.app["cfg"]
    store: Store = request.app["store"]
    if not cfg["master_key"]:
        return _json_error(503, "relay is not configured (no upstream key)")

    user, error = _authenticate(request)
    if user is None:
        status = 401 if "missing" in error or "invalid" in error else 403
        return _json_error(status, error)

    try:
        payload = await request.json()
    except (json.JSONDecodeError, UnicodeDecodeError):
        return _json_error(400, "request body must be JSON")
    text = str(payload.get("text", ""))
    if not text:
        return _json_error(400, "text is required")

    tier = tier_named(user["tier"])
    chars = len(text)
    if user["tts_chars_used"] + chars > tier.tts_chars:
        return _json_error(
            429,
            f"HyperFurion TTS quota exceeded ({tier.tts_chars:,} chars/month on"
            f" the {tier.name} tier) — resets {_period_end_date(user)}",
        )

    forward = {k: payload[k] for k in TTS_PAYLOAD_KEYS if k in payload}
    headers = {"Authorization": f"Bearer {cfg['master_key']}"}
    session: aiohttp.ClientSession = request.app["http"]
    try:
        async with session.post(cfg["upstream_tts_url"], json=forward, headers=headers) as resp:
            body = await resp.read()
            content_type = resp.headers.get("Content-Type", "application/octet-stream")
            if resp.status >= 400:
                return web.Response(status=resp.status, body=body, content_type=content_type)
    except aiohttp.ClientError as exc:
        logger.warning("upstream TTS request failed: %s", exc)
        return _json_error(502, f"upstream TTS request failed: {exc}")

    # Metered only on upstream success — a failed synthesis costs nothing.
    store.add_usage(int(user["id"]), tts_chars=chars)
    return web.Response(body=body, content_type=content_type)


# -- account & operations --------------------------------------------------


async def usage(request: web.Request) -> web.Response:
    user, error = _authenticate(request)
    if user is None:
        return _json_error(401, error)
    tier = tier_named(user["tier"])
    return web.json_response(
        {
            "tier": tier.name,
            "usd_per_month": tier.usd_per_month,
            "stt_seconds_used": round(float(user["stt_seconds_used"]), 1),
            "stt_seconds_limit": tier.stt_seconds,
            "tts_chars_used": int(user["tts_chars_used"]),
            "tts_chars_limit": tier.tts_chars,
            "period_resets": _period_end_date(user),
        }
    )


async def healthz(request: web.Request) -> web.Response:
    return web.json_response(
        {
            "service": "hyperfurion-relay",
            "tiers": {
                t.name: {
                    "usd_per_month": t.usd_per_month,
                    "stt_hours": t.stt_seconds // 3600,
                    "tts_chars": t.tts_chars,
                }
                for t in TIERS.values()
            },
        }
    )


async def stripe_events(request: web.Request) -> web.Response:
    cfg = request.app["cfg"]
    if not cfg["stripe_webhook_secret"]:
        return _json_error(503, "stripe webhook secret is not configured")
    payload = await request.read()
    signature = request.headers.get("Stripe-Signature", "")
    if not stripe_webhook.verify_signature(payload, signature, cfg["stripe_webhook_secret"]):
        return _json_error(400, "invalid signature")
    try:
        event = stripe_webhook.parse_event(payload)
    except json.JSONDecodeError:
        return _json_error(400, "invalid JSON")
    summary = stripe_webhook.handle_event(request.app["store"], event)
    return web.json_response(summary)


_WELCOME_PAGE = """<!doctype html>
<meta charset="utf-8"><title>HyperFurion VK — your key</title>
<body style="font-family: monospace; max-width: 640px; margin: 48px auto; line-height: 1.6">
<h1>Welcome to HyperFurion VK</h1>
<p>Your subscription key — shown <strong>once</strong>, save it now:</p>
<pre style="background:#f4f4f4;padding:12px;border-radius:4px">{key}</pre>
<p>Put this in <code>~/.config/voice-keyboard/config.toml</code>:</p>
<pre style="background:#f4f4f4;padding:12px;border-radius:4px">[providers.hyperfurion]
api_key = "{key}"

[stt]
provider = "hyperfurion"

[tts]
provider = "hyperfurion"</pre>
<p>Then: <code>systemctl --user restart voice-keyboard-daemon</code></p>
</body>"""

_WELCOME_GONE = """<!doctype html>
<meta charset="utf-8"><title>HyperFurion VK</title>
<body style="font-family: monospace; max-width: 640px; margin: 48px auto">
<h1>Key already collected</h1>
<p>This link works exactly once. If you lost your key, reply to your
receipt email and a replacement will be issued.</p>
</body>"""


async def welcome(request: web.Request) -> web.Response:
    session_id = request.query.get("session_id", "")
    key = request.app["store"].pop_pending_key(session_id) if session_id else None
    if key is None:
        return web.Response(text=_WELCOME_GONE, content_type="text/html", status=410)
    return web.Response(
        text=_WELCOME_PAGE.format(key=html.escape(key)), content_type="text/html"
    )


# -- wiring ------------------------------------------------------------------


def make_app(overrides: dict | None = None) -> web.Application:
    cfg = _config_from_env()
    cfg.update(overrides or {})
    app = web.Application()
    app["cfg"] = cfg
    app["store"] = Store(cfg["db_path"])

    async def _startup(app: web.Application) -> None:
        app["http"] = aiohttp.ClientSession()

    async def _cleanup(app: web.Application) -> None:
        await app["http"].close()
        app["store"].close()

    app.on_startup.append(_startup)
    app.on_cleanup.append(_cleanup)

    app.router.add_get("/", healthz)
    app.router.add_get("/healthz", healthz)
    app.router.add_get("/v1/stt", stt_websocket)
    app.router.add_post("/v1/tts", tts)
    app.router.add_get("/v1/usage", usage)
    app.router.add_post("/stripe/webhook", stripe_events)
    app.router.add_get("/welcome", welcome)
    return app


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    host = os.environ.get("RELAY_HOST", "0.0.0.0")
    port = int(os.environ.get("RELAY_PORT", "8787"))
    app = make_app()
    if not app["cfg"]["master_key"]:
        logger.warning("XAI_API_KEY is not set — STT/TTS will refuse until it is")
    web.run_app(app, host=host, port=port)


if __name__ == "__main__":
    main()
