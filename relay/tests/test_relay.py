import json
import re
import time

import aiohttp
import pytest

from harness import MASTER_KEY, WEBHOOK_SECRET, run_rig, wait_until
from hyperfurion_relay import stripe_webhook
from hyperfurion_relay.db import PERIOD_SECONDS, Store
from hyperfurion_relay.tiers import TIERS


def _auth(key: str) -> dict:
    return {"Authorization": f"Bearer {key}"}


class TestHealth:
    def test_healthz_lists_tiers(self) -> None:
        async def scenario(rig):
            async with aiohttp.ClientSession() as session:
                async with session.get(f"{rig.http_base}/healthz") as resp:
                    assert resp.status == 200
                    body = await resp.json()
            assert body["service"] == "hyperfurion-relay"
            assert body["tiers"]["basic"]["usd_per_month"] == 5
            assert body["tiers"]["pro"]["stt_hours"] == 60

        run_rig(scenario)


class TestSTTProxy:
    def test_invalid_key_gets_error_event_and_close(self) -> None:
        async def scenario(rig):
            async with aiohttp.ClientSession() as session:
                async with session.ws_connect(
                    f"{rig.ws_base}/v1/stt?sample_rate=16000", headers=_auth("hfk_wrong")
                ) as ws:
                    event = json.loads((await ws.receive()).data)
                    assert event["type"] == "error"
                    assert "invalid HyperFurion key" in event["message"]
                    closing = await ws.receive()
                    assert closing.type == aiohttp.WSMsgType.CLOSE
                assert ws.close_code == 4401
            assert rig.fake["stt"] == []  # never reached upstream

        run_rig(scenario)

    def test_audio_is_proxied_and_metered(self) -> None:
        async def scenario(rig):
            _, key = rig.store.create_user("basic")
            async with aiohttp.ClientSession() as session:
                async with session.ws_connect(
                    f"{rig.ws_base}/v1/stt?sample_rate=16000&encoding=pcm&language=en",
                    headers=_auth(key),
                ) as ws:
                    ready = json.loads((await ws.receive()).data)
                    assert ready["type"] == "transcript.created"
                    await ws.send_bytes(b"\x00" * 64000)  # 2.0 s @ 16 kHz 16-bit
                    await ws.send_bytes(b"\x00" * 32000)  # 1.0 s
                    await ws.send_str(json.dumps({"type": "audio.done"}))
                    partial = json.loads((await ws.receive()).data)
                    assert partial["type"] == "transcript.partial"
                    done_event = json.loads((await ws.receive()).data)
                    assert done_event == {
                        "type": "transcript.done",
                        "text": "received 96000 bytes",
                    }

            await wait_until(
                lambda: rig.store.lookup_key(key)["stt_seconds_used"] > 0
            )
            assert rig.store.lookup_key(key)["stt_seconds_used"] == pytest.approx(3.0)
            upstream = rig.fake["stt"][0]
            assert upstream["auth"] == f"Bearer {MASTER_KEY}"
            assert upstream["query"]["sample_rate"] == "16000"
            assert upstream["query"]["language"] == "en"

        run_rig(scenario)

    def test_exhausted_quota_is_refused_before_upstream(self) -> None:
        async def scenario(rig):
            user_id, key = rig.store.create_user("basic")
            rig.store.add_usage(user_id, stt_seconds=TIERS["basic"].stt_seconds)
            async with aiohttp.ClientSession() as session:
                async with session.ws_connect(
                    f"{rig.ws_base}/v1/stt?sample_rate=16000", headers=_auth(key)
                ) as ws:
                    event = json.loads((await ws.receive()).data)
                    assert event["type"] == "error"
                    assert "quota exceeded" in event["message"]
                    assert "resets" in event["message"]
                    await ws.receive()
                assert ws.close_code == 4429
            assert rig.fake["stt"] == []

        run_rig(scenario)

    def test_mid_session_cap_cuts_the_stream(self) -> None:
        async def scenario(rig):
            user_id, key = rig.store.create_user("basic")
            # One second of budget left; then try to stream three.
            rig.store.add_usage(user_id, stt_seconds=TIERS["basic"].stt_seconds - 1.0)
            async with aiohttp.ClientSession() as session:
                async with session.ws_connect(
                    f"{rig.ws_base}/v1/stt?sample_rate=16000", headers=_auth(key)
                ) as ws:
                    ready = json.loads((await ws.receive()).data)
                    assert ready["type"] == "transcript.created"
                    await ws.send_bytes(b"\x00" * 96000)  # 3.0 s
                    event = json.loads((await ws.receive()).data)
                    assert event["type"] == "error"
                    assert "mid-session" in event["message"]
                assert ws.close_code == 4429

            # Metering never exceeds the tier cap.
            await wait_until(
                lambda: rig.store.lookup_key(key)["stt_seconds_used"]
                >= TIERS["basic"].stt_seconds
            )
            assert rig.store.lookup_key(key)["stt_seconds_used"] == pytest.approx(
                TIERS["basic"].stt_seconds
            )

        run_rig(scenario)


class TestTTSProxy:
    def test_requires_key(self) -> None:
        async def scenario(rig):
            async with aiohttp.ClientSession() as session:
                async with session.post(
                    f"{rig.http_base}/v1/tts", json={"text": "hi"}
                ) as resp:
                    assert resp.status == 401

        run_rig(scenario)

    def test_synthesis_is_proxied_and_metered(self) -> None:
        async def scenario(rig):
            _, key = rig.store.create_user("basic")
            payload = {"text": "hello world", "voice_id": "eve", "language": "en"}
            async with aiohttp.ClientSession() as session:
                async with session.post(
                    f"{rig.http_base}/v1/tts", json=payload, headers=_auth(key)
                ) as resp:
                    assert resp.status == 200
                    assert resp.headers["Content-Type"].startswith("audio/mpeg")
                    assert await resp.read() == b"FAKE-MP3-BYTES"
            assert rig.store.lookup_key(key)["tts_chars_used"] == len("hello world")
            upstream = rig.fake["tts"][0]
            assert upstream["auth"] == f"Bearer {MASTER_KEY}"
            assert upstream["payload"] == payload

        run_rig(scenario)

    def test_quota_exceeded_is_429_and_never_forwarded(self) -> None:
        async def scenario(rig):
            user_id, key = rig.store.create_user("basic")
            rig.store.add_usage(user_id, tts_chars=TIERS["basic"].tts_chars)
            async with aiohttp.ClientSession() as session:
                async with session.post(
                    f"{rig.http_base}/v1/tts", json={"text": "hi"}, headers=_auth(key)
                ) as resp:
                    assert resp.status == 429
                    body = await resp.json()
                    assert "quota exceeded" in body["error"]
            assert rig.fake["tts"] == []

        run_rig(scenario)

    def test_upstream_charset_error_does_not_crash_handler(self) -> None:
        # xAI errors carry 'application/json; charset=utf-8'; the handler must
        # pass the status through, not raise ValueError on content_type.
        async def scenario(rig):
            _, key = rig.store.create_user("basic")
            async with aiohttp.ClientSession() as session:
                async with session.post(
                    f"{rig.http_base}/v1/tts",
                    json={"text": "charsetfail please"},
                    headers=_auth(key),
                ) as resp:
                    assert resp.status == 400  # upstream status, not a 500
                    assert resp.headers["Content-Type"].startswith("application/json")

        run_rig(scenario)

    def test_upstream_charset_success_is_delivered(self) -> None:
        async def scenario(rig):
            _, key = rig.store.create_user("basic")
            async with aiohttp.ClientSession() as session:
                async with session.post(
                    f"{rig.http_base}/v1/tts",
                    json={"text": "charsetok"},
                    headers=_auth(key),
                ) as resp:
                    assert resp.status == 200
                    assert resp.headers["Content-Type"] == "audio/mpeg"
                    assert await resp.read() == b"FAKE-MP3-BYTES"

        run_rig(scenario)

    def test_second_concurrent_stt_session_is_refused(self) -> None:
        async def scenario(rig):
            _, key = rig.store.create_user("basic")
            async with aiohttp.ClientSession() as session:
                async with session.ws_connect(
                    f"{rig.ws_base}/v1/stt?sample_rate=16000", headers=_auth(key)
                ) as first:
                    assert json.loads((await first.receive()).data)["type"] == "transcript.created"
                    # a second socket for the same key while the first is open
                    async with session.ws_connect(
                        f"{rig.ws_base}/v1/stt?sample_rate=16000", headers=_auth(key)
                    ) as second:
                        event = json.loads((await second.receive()).data)
                        assert event["type"] == "error"
                        assert "already active" in event["message"]
                        assert (await second.receive()).type == aiohttp.WSMsgType.CLOSE

        run_rig(scenario)

    def test_revoked_key_is_403(self) -> None:
        async def scenario(rig):
            user_id, key = rig.store.create_user("basic")
            rig.store.set_status(user_id, "revoked")
            async with aiohttp.ClientSession() as session:
                async with session.post(
                    f"{rig.http_base}/v1/tts", json={"text": "hi"}, headers=_auth(key)
                ) as resp:
                    assert resp.status == 403

        run_rig(scenario)


class TestUsageEndpoint:
    def test_reports_quota_state(self) -> None:
        async def scenario(rig):
            user_id, key = rig.store.create_user("pro")
            rig.store.add_usage(user_id, stt_seconds=120.0, tts_chars=500)
            async with aiohttp.ClientSession() as session:
                async with session.get(
                    f"{rig.http_base}/v1/usage", headers=_auth(key)
                ) as resp:
                    assert resp.status == 200
                    body = await resp.json()
            assert body["tier"] == "pro"
            assert body["stt_seconds_used"] == 120.0
            assert body["stt_seconds_limit"] == TIERS["pro"].stt_seconds
            assert body["tts_chars_used"] == 500
            assert re.match(r"\d{4}-\d{2}-\d{2}", body["period_resets"])

        run_rig(scenario)


class TestStripe:
    def test_signature_roundtrip(self) -> None:
        payload = b'{"type":"x"}'
        now = 1_700_000_000
        header = stripe_webhook.sign_payload(payload, "sec", now)
        assert stripe_webhook.verify_signature(payload, header, "sec", clock=lambda: now)
        assert not stripe_webhook.verify_signature(payload, header, "other", clock=lambda: now)
        assert not stripe_webhook.verify_signature(b"tampered", header, "sec", clock=lambda: now)
        assert not stripe_webhook.verify_signature(
            payload, header, "sec", clock=lambda: now + 3600
        )
        assert not stripe_webhook.verify_signature(payload, "garbage", "sec")

    def test_checkout_creates_user_and_welcome_shows_key_once(self) -> None:
        async def scenario(rig):
            payload = json.dumps(
                {
                    "type": "checkout.session.completed",
                    "data": {
                        "object": {
                            "id": "cs_test_123",
                            "customer": "cus_1",
                            "subscription": "sub_1",
                            "amount_total": 1000,
                            "metadata": {"tier": "pro"},
                            "customer_details": {"email": "fan@example.com"},
                        }
                    },
                }
            ).encode()
            header = stripe_webhook.sign_payload(payload, WEBHOOK_SECRET, int(time.time()))
            async with aiohttp.ClientSession() as session:
                async with session.post(
                    f"{rig.http_base}/stripe/webhook",
                    data=payload,
                    headers={"Stripe-Signature": header},
                ) as resp:
                    assert resp.status == 200
                    assert (await resp.json())["tier"] == "pro"

                async with session.get(
                    f"{rig.http_base}/welcome?session_id=cs_test_123"
                ) as resp:
                    assert resp.status == 200
                    page = await resp.text()
                match = re.search(r"hfk_[0-9a-f]{40}", page)
                assert match, "welcome page must contain the key"
                key = match.group(0)

                # One-time pickup: gone on the second visit.
                async with session.get(
                    f"{rig.http_base}/welcome?session_id=cs_test_123"
                ) as resp:
                    assert resp.status == 410

                # And the key actually works.
                async with session.get(
                    f"{rig.http_base}/v1/usage", headers=_auth(key)
                ) as resp:
                    assert (await resp.json())["tier"] == "pro"

        run_rig(scenario)

    def test_bad_signature_is_rejected(self) -> None:
        async def scenario(rig):
            async with aiohttp.ClientSession() as session:
                async with session.post(
                    f"{rig.http_base}/stripe/webhook",
                    data=b"{}",
                    headers={"Stripe-Signature": "t=1,v1=bad"},
                ) as resp:
                    assert resp.status == 400

        run_rig(scenario)

    def test_invoice_paid_resets_usage(self) -> None:
        async def scenario(rig):
            user_id, key = rig.store.create_user("basic", stripe_subscription_id="sub_9")
            rig.store.add_usage(user_id, stt_seconds=1000.0, tts_chars=999)
            payload = json.dumps(
                {"type": "invoice.paid", "data": {"object": {"subscription": "sub_9"}}}
            ).encode()
            header = stripe_webhook.sign_payload(payload, WEBHOOK_SECRET, int(time.time()))
            async with aiohttp.ClientSession() as session:
                async with session.post(
                    f"{rig.http_base}/stripe/webhook",
                    data=payload,
                    headers={"Stripe-Signature": header},
                ) as resp:
                    assert resp.status == 200
            user = rig.store.lookup_key(key)
            assert user["stt_seconds_used"] == 0
            assert user["tts_chars_used"] == 0

        run_rig(scenario)

    def test_subscription_deleted_revokes_access(self) -> None:
        async def scenario(rig):
            _, key = rig.store.create_user("basic", stripe_subscription_id="sub_9")
            payload = json.dumps(
                {"type": "customer.subscription.deleted", "data": {"object": {"id": "sub_9"}}}
            ).encode()
            header = stripe_webhook.sign_payload(payload, WEBHOOK_SECRET, int(time.time()))
            async with aiohttp.ClientSession() as session:
                async with session.post(
                    f"{rig.http_base}/stripe/webhook",
                    data=payload,
                    headers={"Stripe-Signature": header},
                ) as resp:
                    assert resp.status == 200
                async with session.post(
                    f"{rig.http_base}/v1/tts", json={"text": "hi"}, headers=_auth(key)
                ) as resp:
                    assert resp.status == 403

        run_rig(scenario)


class TestPeriodRollover:
    def test_usage_resets_after_thirty_days(self) -> None:
        now = [1_700_000_000.0]
        store = Store(":memory:", clock=lambda: now[0])
        user_id, key = store.create_user("basic")
        store.add_usage(user_id, stt_seconds=1234.0, tts_chars=99)

        now[0] += PERIOD_SECONDS - 1  # still inside the window
        user = store.lookup_key(key)
        assert user["stt_seconds_used"] == pytest.approx(1234.0)

        now[0] += 2  # tip over the edge
        user = store.lookup_key(key)
        assert user["stt_seconds_used"] == 0
        assert user["tts_chars_used"] == 0
        assert user["period_start"] == pytest.approx(1_700_000_000.0 + PERIOD_SECONDS)
        store.close()
