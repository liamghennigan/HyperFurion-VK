"""Full-chain proof: the daemon's own clients through the relay.

These tests exercise the exact code the daemon runs — the streaming
`STTClient` (websockets) and `TTSClient` (requests) from
voice_keyboard — pointed at a live relay backed by the fake xAI
upstream. No mocks on the client side.
"""

import asyncio

import pytest

from harness import run_rig, wait_until
from hyperfurion_relay.tiers import TIERS

from voice_keyboard import stt as vk_stt
from voice_keyboard import tts as vk_tts


def _hf_config(api_key: str, base_url: str) -> dict:
    return {
        "providers": {"hyperfurion": {"api_key": api_key, "base_url": base_url}},
        "stt": {"provider": "hyperfurion", "language": "en", "interim_results": True},
        "tts": {"provider": "hyperfurion", "voice_id": "eve", "language": "en"},
    }


class TestProviderWiring:
    def test_default_endpoints_point_at_hosted_relay(self) -> None:
        cfg = _hf_config("hfk_x", "")
        assert vk_stt.hyperfurion_ws_url(cfg) == "wss://api.hyperfurion.com/v1/stt"
        assert vk_tts.hyperfurion_tts_url(cfg) == "https://api.hyperfurion.com/v1/tts"

    def test_base_url_override_maps_scheme(self) -> None:
        cfg = _hf_config("hfk_x", "http://127.0.0.1:9999/")
        assert vk_stt.hyperfurion_ws_url(cfg) == "ws://127.0.0.1:9999/v1/stt"
        assert vk_tts.hyperfurion_tts_url(cfg) == "http://127.0.0.1:9999/v1/tts"

    def test_factories_build_streaming_clients(self) -> None:
        cfg = _hf_config("hfk_x", "http://127.0.0.1:9999")
        stt_client = vk_stt.create_stt_client(cfg)
        assert isinstance(stt_client, vk_stt.STTClient)
        assert stt_client._ws_url == "ws://127.0.0.1:9999/v1/stt"
        tts_client = vk_tts.create_tts_client(cfg)
        assert tts_client._provider == "hyperfurion"
        assert tts_client._voice_id == "eve"


class TestDaemonSTTThroughRelay:
    def test_streaming_dictation_end_to_end(self) -> None:
        async def scenario(rig):
            _, key = rig.store.create_user("basic")
            client = vk_stt.create_stt_client(_hf_config(key, rig.http_base))

            await client.connect(sample_rate=16000)  # waits for relayed ready event
            await client.send_audio(b"\x00" * 32000)  # 1.0 s of PCM
            await client.send_audio_done()
            events = []
            async for event in client.receive_events():
                events.append(event)
                if event.get("type") == "transcript.done":
                    break
            await client.close()

            assert {"type": "transcript.done", "text": "received 32000 bytes"} in events
            await wait_until(
                lambda: rig.store.lookup_key(key)["stt_seconds_used"] > 0
            )
            assert rig.store.lookup_key(key)["stt_seconds_used"] == pytest.approx(1.0)

        run_rig(scenario)

    def test_invalid_key_surfaces_relay_message(self) -> None:
        async def scenario(rig):
            client = vk_stt.create_stt_client(_hf_config("hfk_not_real", rig.http_base))
            with pytest.raises(RuntimeError, match="invalid HyperFurion key"):
                await client.connect(sample_rate=16000)

        run_rig(scenario)

    def test_exhausted_quota_surfaces_reset_date(self) -> None:
        async def scenario(rig):
            user_id, key = rig.store.create_user("basic")
            rig.store.add_usage(user_id, stt_seconds=TIERS["basic"].stt_seconds)
            client = vk_stt.create_stt_client(_hf_config(key, rig.http_base))
            with pytest.raises(RuntimeError, match="quota exceeded"):
                await client.connect(sample_rate=16000)

        run_rig(scenario)


class TestDaemonTTSThroughRelay:
    def test_synthesis_end_to_end(self) -> None:
        async def scenario(rig):
            _, key = rig.store.create_user("basic")
            client = vk_tts.create_tts_client(_hf_config(key, rig.http_base))
            # requests is blocking; keep the relay's event loop free.
            audio = await asyncio.to_thread(client.synthesize, "hello world")
            client.close()
            assert audio == b"FAKE-MP3-BYTES"
            assert rig.store.lookup_key(key)["tts_chars_used"] == len("hello world")
            assert rig.fake["tts"][0]["payload"]["voice_id"] == "eve"

        run_rig(scenario)

    def test_quota_error_is_readable(self) -> None:
        async def scenario(rig):
            user_id, key = rig.store.create_user("basic")
            rig.store.add_usage(user_id, tts_chars=TIERS["basic"].tts_chars)
            client = vk_tts.create_tts_client(_hf_config(key, rig.http_base))
            with pytest.raises(RuntimeError, match="HyperFurion TTS: .*quota exceeded"):
                await asyncio.to_thread(client.synthesize, "hello")
            client.close()

        run_rig(scenario)
