import asyncio
import json
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
