import asyncio
import json
import threading
from urllib.parse import parse_qs, urlsplit
from unittest import mock

import pytest

from voice_keyboard import stt


def _mock_json_response(payload: dict) -> mock.Mock:
    resp = mock.Mock()
    resp.json.return_value = payload
    resp.raise_for_status = mock.Mock()
    return resp


class TestSTTClient:
    def test_create_stt_client_returns_buffered_provider(self) -> None:
        cfg = {
            "providers": {"openai": {"api_key": "openai-key"}},
            "stt": {"provider": "openai", "model": "gpt-4o-transcribe", "language": "en"},
        }

        client = stt.create_stt_client(cfg)

        assert isinstance(client, stt.BufferedRESTSTTClient)
        assert client._provider == "openai"

    def test_connect_sends_config_in_url_and_waits_for_ready(self) -> None:
        client = stt.STTClient(api_key="k", language="es", interim_results=False)

        ws = mock.Mock()
        ws.recv = mock.AsyncMock(return_value=json.dumps({"type": "transcript.created"}))

        with mock.patch("voice_keyboard.stt.websockets.connect", new=mock.AsyncMock(return_value=ws)) as connect:
            asyncio.run(client.connect(sample_rate=48000))

        url = connect.call_args.args[0]
        query = parse_qs(urlsplit(url).query)
        assert query == {
            "sample_rate": ["48000"],
            "encoding": ["pcm"],
            "interim_results": ["false"],
            "language": ["es"],
        }
        assert connect.call_args.kwargs["additional_headers"]["Authorization"] == "Bearer k"
        ws.recv.assert_awaited_once()
        assert client._ws is ws

    def test_send_config_explains_new_protocol(self) -> None:
        client = stt.STTClient(api_key="k")
        with pytest.raises(RuntimeError, match="WebSocket URL"):
            asyncio.run(client.send_config(sample_rate=48000))

    def test_send_audio_requires_connection(self) -> None:
        client = stt.STTClient(api_key="k")
        with pytest.raises(RuntimeError, match="Not connected"):
            asyncio.run(client.send_audio(b"x"))

    def test_send_audio_done_sends_json_marker(self) -> None:
        client = stt.STTClient(api_key="k")
        client._ws = mock.Mock()
        client._ws.send = mock.AsyncMock()
        asyncio.run(client.send_audio_done())
        sent = json.loads(client._ws.send.call_args.args[0])
        assert sent == {"type": "audio.done"}

    def test_connect_retries_on_transient_errors(self) -> None:
        """One WebSocketException failure followed by success should succeed."""
        client = stt.STTClient(api_key="k", connect_timeout=1.0)

        good_ws = mock.Mock()
        good_ws.recv = mock.AsyncMock(return_value=json.dumps({"type": "transcript.created"}))
        attempts = {"n": 0}

        async def fake_connect(*args, **kwargs):
            attempts["n"] += 1
            if attempts["n"] < stt.MAX_CONNECT_RETRIES:
                raise stt.websockets.exceptions.WebSocketException("boom")
            return good_ws

        with mock.patch(
            "voice_keyboard.stt.websockets.connect",
            side_effect=fake_connect,
        ), mock.patch(
            "voice_keyboard.stt.asyncio.sleep",
            new=mock.AsyncMock(),
        ):
            asyncio.run(client.connect(sample_rate=16000))

        assert client._ws is good_ws
        assert attempts["n"] == stt.MAX_CONNECT_RETRIES

    def test_connect_exhausts_retries_and_raises(self) -> None:
        client = stt.STTClient(api_key="k", connect_timeout=1.0)

        async def always_fail(*args, **kwargs):
            raise stt.websockets.exceptions.WebSocketException("nope")

        with mock.patch(
            "voice_keyboard.stt.websockets.connect",
            side_effect=always_fail,
        ), mock.patch(
            "voice_keyboard.stt.asyncio.sleep",
            new=mock.AsyncMock(),
        ):
            with pytest.raises(RuntimeError, match="Could not connect to xAI STT"):
                asyncio.run(client.connect(sample_rate=16000))

    def test_receive_events_skips_invalid_json(self) -> None:
        client = stt.STTClient(api_key="k")

        class _FakeWS:
            def __init__(self, messages):
                self._messages = messages

            def __aiter__(self):
                self._iter = iter(self._messages)
                return self

            async def __anext__(self):
                try:
                    return next(self._iter)
                except StopIteration:
                    raise StopAsyncIteration

        client._ws = _FakeWS([b"not json", b'{"type": "transcript.done", "text": "hi"}'])

        async def collect():
            return [e async for e in client.receive_events()]

        events = asyncio.run(collect())
        assert events == [{"type": "transcript.done", "text": "hi"}]


class TestBufferedRESTSTTClient:
    def test_send_audio_done_transcribes_in_worker_thread(self) -> None:
        async def run() -> dict:
            started = threading.Event()
            release = threading.Event()
            client = stt.BufferedRESTSTTClient(
                provider="openai",
                api_key="openai-key",
                model="gpt-4o-transcribe",
                language="en",
            )

            def slow_transcribe(wav_data: bytes) -> str:
                assert wav_data.startswith(b"RIFF")
                started.set()
                release.wait(timeout=2)
                return "worker result"

            client._transcribe_wav = mock.Mock(side_effect=slow_transcribe)
            await client.connect(sample_rate=16000)
            await client.send_audio(b"\x00\x00" * 20)
            await asyncio.wait_for(client.send_audio_done(), timeout=0.1)
            assert started.wait(timeout=0.5)

            async def next_event() -> dict:
                async for event in client.receive_events():
                    return event
                raise AssertionError("receive_events ended without an event")

            event_task = asyncio.create_task(next_event())
            await asyncio.sleep(0.05)
            assert not event_task.done()
            release.set()
            return await asyncio.wait_for(event_task, timeout=1.0)

        assert asyncio.run(run()) == {
            "type": "transcript.done",
            "text": "worker result",
        }

    def test_openai_compatible_provider_posts_wav_and_yields_done(self) -> None:
        async def run() -> list[dict]:
            client = stt.BufferedRESTSTTClient(
                provider="openai",
                api_key="openai-key",
                model="gpt-4o-transcribe",
                language="en",
            )
            session = mock.Mock()
            session.post.return_value = _mock_json_response({"text": "hello from openai"})
            client._session = session

            await client.connect(sample_rate=16000)
            await client.send_audio(b"\x00\x00" * 20)
            await client.send_audio_done()
            events = [event async for event in client.receive_events()]

            args, kwargs = session.post.call_args
            assert args[0] == stt.OPENAI_STT_URL
            assert kwargs["headers"]["Authorization"] == "Bearer openai-key"
            assert kwargs["data"]["model"] == "gpt-4o-transcribe"
            assert kwargs["data"]["language"] == "en"
            assert kwargs["files"]["file"][0] == "speech.wav"
            assert kwargs["files"]["file"][2] == "audio/wav"
            return events

        assert asyncio.run(run()) == [
            {"type": "transcript.done", "text": "hello from openai"}
        ]

    def test_rest_provider_failure_yields_error_event(self) -> None:
        async def run() -> list[dict]:
            client = stt.BufferedRESTSTTClient(
                provider="openai",
                api_key="bad-key",
                model="gpt-4o-transcribe",
                language="en",
            )
            client._transcribe_wav = mock.Mock(side_effect=RuntimeError("bad key"))

            await client.connect(sample_rate=16000)
            await client.send_audio(b"\x00\x00" * 20)
            await client.send_audio_done()
            return [event async for event in client.receive_events()]

        events = asyncio.run(run())
        assert len(events) == 1
        assert events[0]["type"] == "error"
        assert "bad key" in events[0]["message"]

    def test_deepgram_response_is_parsed(self) -> None:
        client = stt.BufferedRESTSTTClient(
            provider="deepgram",
            api_key="deepgram-key",
            model="nova-3",
            language="en",
        )
        session = mock.Mock()
        session.post.return_value = _mock_json_response({
            "results": {
                "channels": [
                    {"alternatives": [{"transcript": "hello from deepgram"}]}
                ]
            }
        })
        client._session = session

        assert client._transcribe_deepgram(b"RIFF") == "hello from deepgram"
        args, kwargs = session.post.call_args
        assert args[0] == stt.DEEPGRAM_STT_URL
        assert kwargs["headers"]["Authorization"] == "Token deepgram-key"
        assert kwargs["params"]["model"] == "nova-3"


class TestHyperFurionSTTProvider:
    def test_create_stt_client_targets_hosted_relay(self) -> None:
        cfg = {
            "providers": {"hyperfurion": {"api_key": "hfk_abc", "base_url": ""}},
            "stt": {"provider": "hyperfurion", "language": "en", "interim_results": True},
        }

        client = stt.create_stt_client(cfg)

        assert isinstance(client, stt.STTClient)
        assert client._api_key == "hfk_abc"
        assert client._ws_url == "wss://api.hyperfurion.com/v1/stt"

    def test_base_url_override_maps_scheme_and_strips_slash(self) -> None:
        cfg = {
            "providers": {
                "hyperfurion": {"api_key": "hfk_abc", "base_url": "https://relay.example.com/"}
            },
            "stt": {"provider": "hyperfurion"},
        }
        assert stt.hyperfurion_ws_url(cfg) == "wss://relay.example.com/v1/stt"

        cfg["providers"]["hyperfurion"]["base_url"] = "http://127.0.0.1:8787"
        assert stt.hyperfurion_ws_url(cfg) == "ws://127.0.0.1:8787/v1/stt"

    def test_connect_uses_relay_url_with_standard_query(self) -> None:
        cfg = {
            "providers": {
                "hyperfurion": {"api_key": "hfk_abc", "base_url": "https://relay.example.com"}
            },
            "stt": {"provider": "hyperfurion", "language": "en", "interim_results": True},
        }
        client = stt.create_stt_client(cfg)

        ws = mock.Mock()
        ws.recv = mock.AsyncMock(return_value=json.dumps({"type": "transcript.created"}))

        with mock.patch(
            "voice_keyboard.stt.websockets.connect", new=mock.AsyncMock(return_value=ws)
        ) as connect:
            asyncio.run(client.connect(sample_rate=16000))

        url = connect.call_args.args[0]
        assert url.startswith("wss://relay.example.com/v1/stt?")
        query = parse_qs(urlsplit(url).query)
        assert query["sample_rate"] == ["16000"]
        assert connect.call_args.kwargs["additional_headers"]["Authorization"] == "Bearer hfk_abc"


class TestOpenAICompatibleBaseURL:
    def test_base_url_reroutes_transcription(self) -> None:
        cfg = {
            "providers": {"openai": {"api_key": "", "base_url": "http://localhost:8000/v1/"}},
            "stt": {"provider": "openai", "language": "en"},
        }
        client = stt.create_stt_client(cfg)
        # A local endpoint gets the pseudo-streaming adapter by default
        # (flow.live_rest = "auto"): re-transcribing locally is free.
        assert isinstance(client, stt.ChunkedRESTAdapter)
        assert client.supports_streaming
        client = client._inner
        assert isinstance(client, stt.BufferedRESTSTTClient)

        captured = {}

        def fake_post(url, **kwargs):
            captured["url"] = url
            return _mock_json_response({"text": "offline transcript"})

        client._session = mock.Mock()
        client._session.post = fake_post
        text = client._transcribe_wav(b"RIFFfake")

        assert text == "offline transcript"
        assert captured["url"] == "http://localhost:8000/v1/audio/transcriptions"

    def test_without_base_url_openai_default_holds(self) -> None:
        cfg = {
            "providers": {"openai": {"api_key": "k"}},
            "stt": {"provider": "openai"},
        }
        client = stt.create_stt_client(cfg)
        assert client._base_url == ""
