import json

import aiohttp
import pytest

from harness import run_rig
from hyperfurion_relay import demo


class TestDemoStatus:
    def test_reports_live_with_caps_and_counts(self) -> None:
        async def scenario(rig):
            async with aiohttp.ClientSession() as session:
                async with session.get(f"{rig.http_base}/v1/demo/status") as resp:
                    assert resp.status == 200
                    assert resp.headers["Access-Control-Allow-Origin"] == "*"
                    body = await resp.json()
            assert body["live"] is True
            assert body["caps"]["dictation_seconds"] == demo.MAX_DICTATION_SECONDS
            assert body["served_today"] == {"dictations": 0, "tts": 0, "asks": 0}

        run_rig(scenario)

    def test_spent_budget_reports_not_live(self) -> None:
        async def scenario(rig):
            rig.store.demo_record("1.2.3.4", "asks", 999.0)  # blow the budget
            async with aiohttp.ClientSession() as session:
                async with session.get(f"{rig.http_base}/v1/demo/status") as resp:
                    body = await resp.json()
            assert body["live"] is False
            assert "budget" in body["reason"]

        run_rig(scenario)


class TestDemoSTT:
    def test_keyless_dictation_streams_and_is_metered(self) -> None:
        async def scenario(rig):
            async with aiohttp.ClientSession() as session:
                async with session.ws_connect(
                    f"{rig.ws_base}/v1/demo/stt?sample_rate=16000"
                ) as ws:
                    ready = json.loads((await ws.receive()).data)
                    assert ready["type"] == "transcript.created"
                    await ws.send_bytes(b"\x00" * 32000)  # 1.0 s
                    await ws.send_str(json.dumps({"type": "audio.done"}))
                    partial = json.loads((await ws.receive()).data)
                    assert partial["type"] == "transcript.partial"
                    done = json.loads((await ws.receive()).data)
                    assert done == {"type": "transcript.done", "text": "received 32000 bytes"}

            day = rig.store.demo_counts("")
            assert day["dictations"] == 1
            assert day["spent_usd"] == pytest.approx(1.0 * demo.STT_USD_PER_SECOND)

        run_rig(scenario)

    def test_dictation_cap_forces_finalize(self) -> None:
        async def scenario(rig):
            cap_bytes = int(demo.MAX_DICTATION_SECONDS * 16000 * 2)
            async with aiohttp.ClientSession() as session:
                async with session.ws_connect(
                    f"{rig.ws_base}/v1/demo/stt?sample_rate=16000"
                ) as ws:
                    ready = json.loads((await ws.receive()).data)
                    assert ready["type"] == "transcript.created"
                    await ws.send_bytes(b"\x00" * (cap_bytes + 32000))  # past the cap
                    events = {}
                    while len(events) < 3:
                        msg = await ws.receive()
                        if msg.type != aiohttp.WSMsgType.TEXT:
                            break
                        event = json.loads(msg.data)
                        events[event["type"]] = event
                    assert "demo.limit" in events
                    assert "transcript.done" in events

            # Metered at the cap, not at what was sent.
            day = rig.store.demo_counts("")
            assert day["spent_usd"] == pytest.approx(
                demo.MAX_DICTATION_SECONDS * demo.STT_USD_PER_SECOND
            )

        run_rig(scenario)

    def test_budget_exhaustion_refuses(self) -> None:
        async def scenario(rig):
            rig.store.demo_record("9.9.9.9", "dictations", 999.0)
            async with aiohttp.ClientSession() as session:
                async with session.ws_connect(f"{rig.ws_base}/v1/demo/stt") as ws:
                    event = json.loads((await ws.receive()).data)
                    assert event["type"] == "error"
                    assert "budget" in event["message"]
                assert ws.close_code == 4429

        run_rig(scenario)

    def test_per_ip_daily_cap(self) -> None:
        async def scenario(rig):
            for _ in range(demo.IP_DAILY_CAPS["dictations"]):
                rig.store.demo_record("127.0.0.1", "dictations", 0.0001)
            async with aiohttp.ClientSession() as session:
                async with session.ws_connect(f"{rig.ws_base}/v1/demo/stt") as ws:
                    event = json.loads((await ws.receive()).data)
                    assert "daily demo limit" in event["message"]

        run_rig(scenario)


class TestDemoTTS:
    def test_synthesis_forces_eve_and_truncates(self) -> None:
        async def scenario(rig):
            long_text = "x" * 1000
            async with aiohttp.ClientSession() as session:
                async with session.post(
                    f"{rig.http_base}/v1/demo/tts",
                    json={"text": long_text, "voice_id": "not-eve"},
                ) as resp:
                    assert resp.status == 200
                    assert resp.headers["Access-Control-Allow-Origin"] == "*"
                    assert await resp.read() == b"FAKE-MP3-BYTES"
            upstream = rig.fake["tts"][0]["payload"]
            assert upstream["voice_id"] == "eve"  # client's voice choice ignored
            assert len(upstream["text"]) == demo.MAX_TTS_CHARS
            assert rig.store.demo_counts("")["tts"] == 1

        run_rig(scenario)

    def test_preflight_is_open(self) -> None:
        async def scenario(rig):
            async with aiohttp.ClientSession() as session:
                async with session.options(f"{rig.http_base}/v1/demo/tts") as resp:
                    assert resp.status == 204
                    assert resp.headers["Access-Control-Allow-Origin"] == "*"

        run_rig(scenario)


class TestDemoAsk:
    def test_question_is_grounded_and_answered(self) -> None:
        async def scenario(rig):
            async with aiohttp.ClientSession() as session:
                async with session.post(
                    f"{rig.http_base}/v1/demo/ask",
                    json={"question": "how do I change the hotkey?"},
                ) as resp:
                    assert resp.status == 200
                    body = await resp.json()
            assert body["answer"] == "fake answer to: how do I change the hotkey?"
            upstream = rig.fake["chat"][0]["payload"]
            assert upstream["model"] == "grok-test"
            assert upstream["max_tokens"] == demo.ASK_MAX_TOKENS
            assert "HyperFurion VK" in upstream["messages"][0]["content"]
            assert rig.store.demo_counts("")["asks"] == 1
            assert rig.store.demo_counts("")["spent_usd"] == pytest.approx(demo.ASK_USD_FLAT)

        run_rig(scenario)

    def test_empty_question_is_400(self) -> None:
        async def scenario(rig):
            async with aiohttp.ClientSession() as session:
                async with session.post(
                    f"{rig.http_base}/v1/demo/ask", json={"question": "  "}
                ) as resp:
                    assert resp.status == 400

        run_rig(scenario)

    def test_ip_cap_refuses_with_429(self) -> None:
        async def scenario(rig):
            for _ in range(demo.IP_DAILY_CAPS["asks"]):
                rig.store.demo_record("127.0.0.1", "asks", 0.0001)
            async with aiohttp.ClientSession() as session:
                async with session.post(
                    f"{rig.http_base}/v1/demo/ask", json={"question": "hi"}
                ) as resp:
                    assert resp.status == 429
            assert rig.fake["chat"] == []

        run_rig(scenario)


class TestDemoAntiAbuse:
    def test_spoofed_xff_does_not_bypass_ip_cap_by_default(self) -> None:
        # X-Forwarded-For is not trusted unless the operator opts in, so a
        # varying XFF cannot evade the per-IP cap (all requests share the
        # peer address).
        async def scenario(rig):
            for _ in range(demo.IP_DAILY_CAPS["asks"]):
                rig.store.demo_record("127.0.0.1", "asks", 0.0001)
            async with aiohttp.ClientSession() as session:
                async with session.post(
                    f"{rig.http_base}/v1/demo/ask",
                    json={"question": "hi"},
                    headers={"X-Forwarded-For": "9.9.9.9"},
                ) as resp:
                    assert resp.status == 429  # cap still applies to the peer ip
            assert rig.fake["chat"] == []

        run_rig(scenario)

    def test_empty_forwarded_token_cannot_double_count_global(self) -> None:
        # Even if a resolved ip were "", demo_record/try_charge must not apply
        # the charge twice to the reserved global-aggregate row.
        async def scenario(rig):
            before = rig.store.demo_counts("")["spent_usd"]
            rig.store.demo_record("", "asks", 0.01)
            after = rig.store.demo_counts("")["spent_usd"]
            assert after - before == pytest.approx(0.01)  # once, not twice

        run_rig(scenario)

    def test_reserve_is_atomic_and_refunds_failed_tts(self) -> None:
        # A failed upstream synthesis leaves spend unchanged (reserved then
        # refunded), while the request count stands as a rate-limit slot.
        async def scenario(rig):
            # point the relay's TTS upstream at a dead port so it errors
            rig.relay_cfg["upstream_tts_url"] = "http://127.0.0.1:1/v1/tts"
            spent_before = rig.store.demo_counts("")["spent_usd"]
            async with aiohttp.ClientSession() as session:
                async with session.post(
                    f"{rig.http_base}/v1/demo/tts", json={"text": "hello"}
                ) as resp:
                    assert resp.status == 502
            counts = rig.store.demo_counts("")
            assert counts["spent_usd"] == pytest.approx(spent_before)  # fully refunded
            assert counts["tts"] == 1  # the attempt still counted

        run_rig(scenario)
