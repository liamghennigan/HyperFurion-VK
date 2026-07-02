"""Test rig: fake xAI upstream + relay, both on real localhost ports.

Real ports matter because the e2e tests drive the daemon's own
`websockets`/`requests` clients at the relay — no aiohttp test client
shortcuts on the caller side.
"""

import asyncio
import time

from aiohttp.test_utils import TestServer

from fake_xai import make_fake_xai
from hyperfurion_relay.app import make_app

MASTER_KEY = "xai-master-test-key"
WEBHOOK_SECRET = "whsec_test_secret"


class Rig:
    def __init__(self, relay_server: TestServer, relay_app, fake_app) -> None:
        self.store = relay_app["store"]
        self.fake = fake_app["state"]
        self.http_base = f"http://127.0.0.1:{relay_server.port}"
        self.ws_base = f"ws://127.0.0.1:{relay_server.port}"


async def _with_rig(scenario) -> None:
    fake_app = make_fake_xai(MASTER_KEY)
    fake_server = TestServer(fake_app)
    await fake_server.start_server()
    relay_app = make_app(
        {
            "master_key": MASTER_KEY,
            "db_path": ":memory:",
            "upstream_stt_url": f"ws://127.0.0.1:{fake_server.port}/v1/stt",
            "upstream_tts_url": f"http://127.0.0.1:{fake_server.port}/v1/tts",
            "upstream_chat_url": f"http://127.0.0.1:{fake_server.port}/v1/chat/completions",
            "stripe_webhook_secret": WEBHOOK_SECRET,
            "demo_daily_budget_usd": 1.0,
            "demo_chat_model": "grok-test",
        }
    )
    relay_server = TestServer(relay_app)
    await relay_server.start_server()
    try:
        await scenario(Rig(relay_server, relay_app, fake_app))
    finally:
        await relay_server.close()
        await fake_server.close()


def run_rig(scenario) -> None:
    """Drive an async scenario the same way the existing suite does: asyncio.run."""
    asyncio.run(_with_rig(scenario))


async def wait_until(predicate, timeout: float = 2.0) -> None:
    """Poll for a condition that lands just after a socket closes (usage flush)."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return
        await asyncio.sleep(0.02)
    raise AssertionError("condition not met within timeout")
