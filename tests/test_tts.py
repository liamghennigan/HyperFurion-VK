from unittest import mock

import pytest

from voice_keyboard import tts


def _mock_response(content: bytes = b"MP3DATA") -> mock.Mock:
    resp = mock.Mock()
    resp.content = content
    resp.raise_for_status = mock.Mock()
    return resp


class TestTTSClient:
    def test_create_tts_client_uses_provider_defaults(self) -> None:
        cfg = {
            "providers": {"openai": {"api_key": "openai-key"}},
            "tts": {"provider": "openai", "voice_id": "eve", "language": "en"},
        }

        client = tts.create_tts_client(cfg)

        assert client._provider == "openai"
        assert client._api_key == "openai-key"
        assert client._model == "gpt-4o-mini-tts"
        assert client._voice_id == "coral"

    def test_synthesize_sends_expected_payload_and_returns_audio(self) -> None:
        client = tts.TTSClient(api_key="key", voice_id="bob", language="fr")
        session = mock.Mock()
        session.post = mock.Mock(return_value=_mock_response(b"MP3DATA"))
        client._session = session

        data = client.synthesize("bonjour")

        assert data == b"MP3DATA"
        args, kwargs = session.post.call_args
        assert args[0] == tts.XAI_TTS_URL
        assert kwargs["json"] == {
            "text": "bonjour",
            "voice_id": "bob",
            "language": "fr",
        }
        assert kwargs["headers"]["Authorization"] == "Bearer key"
        assert kwargs["headers"]["Content-Type"] == "application/json"

    def test_openai_tts_payload(self) -> None:
        client = tts.TTSClient(
            api_key="openai-key",
            provider="openai",
            model="gpt-4o-mini-tts",
            voice_id="coral",
        )
        session = mock.Mock()
        session.post = mock.Mock(return_value=_mock_response(b"MP3DATA"))
        client._session = session

        assert client.synthesize("hello") == b"MP3DATA"
        args, kwargs = session.post.call_args
        assert args[0] == tts.OPENAI_TTS_URL
        assert kwargs["headers"]["Authorization"] == "Bearer openai-key"
        assert kwargs["json"] == {
            "model": "gpt-4o-mini-tts",
            "input": "hello",
            "voice": "coral",
            "response_format": "mp3",
        }

    def test_elevenlabs_tts_payload(self) -> None:
        client = tts.TTSClient(
            api_key="eleven-key",
            provider="elevenlabs",
            model="eleven_multilingual_v2",
            voice_id="voice123",
        )
        session = mock.Mock()
        session.post = mock.Mock(return_value=_mock_response(b"MP3DATA"))
        client._session = session

        assert client.synthesize("hello") == b"MP3DATA"
        args, kwargs = session.post.call_args
        assert args[0] == "https://api.elevenlabs.io/v1/text-to-speech/voice123"
        assert kwargs["headers"]["xi-api-key"] == "eleven-key"
        assert kwargs["params"]["output_format"] == "mp3_44100_128"
        assert kwargs["json"] == {
            "text": "hello",
            "model_id": "eleven_multilingual_v2",
        }

    def test_session_is_reused_across_calls(self) -> None:
        client = tts.TTSClient(api_key="k")
        session = mock.Mock()
        session.post = mock.Mock(return_value=_mock_response(b"x"))
        client._session = session

        client.synthesize("a")
        client.synthesize("b")

        assert session.post.call_count == 2

    def test_close_releases_session(self) -> None:
        client = tts.TTSClient(api_key="k")
        session = mock.Mock()
        client._session = session

        client.close()

        session.close.assert_called_once()
        assert client._session is None

    def test_close_is_noop_when_no_session(self) -> None:
        client = tts.TTSClient(api_key="k")
        client.close()  # must not raise
        assert client._session is None


class TestPlayPygameMixerGuard:
    def test_quit_not_called_when_init_fails(self) -> None:
        pytest.importorskip("pygame")
        client = tts.TTSClient(api_key="k")

        with mock.patch("pygame.mixer.init", side_effect=RuntimeError("no mixer")), \
             mock.patch("pygame.mixer.quit") as m_quit:
            with pytest.raises(RuntimeError, match="no mixer"):
                client._play_pygame("/tmp/does-not-exist.mp3")
        m_quit.assert_not_called()


class TestHyperFurionTTSProvider:
    def _client(self) -> "tts.TTSClient":
        cfg = {
            "providers": {
                "hyperfurion": {"api_key": "hfk_abc", "base_url": "http://relay.local"}
            },
            "tts": {"provider": "hyperfurion", "voice_id": "eve", "language": "en"},
        }
        return tts.create_tts_client(cfg)

    def test_synthesize_posts_xai_payload_to_relay(self) -> None:
        client = self._client()
        resp = _mock_response(b"MP3DATA")
        resp.status_code = 200
        session = mock.Mock()
        session.post = mock.Mock(return_value=resp)
        client._session = session

        data = client.synthesize("hello")

        assert data == b"MP3DATA"
        args, kwargs = session.post.call_args
        assert args[0] == "http://relay.local/v1/tts"
        assert kwargs["json"] == {"text": "hello", "voice_id": "eve", "language": "en"}
        assert kwargs["headers"]["Authorization"] == "Bearer hfk_abc"

    def test_relay_error_detail_is_surfaced(self) -> None:
        client = self._client()
        resp = mock.Mock()
        resp.status_code = 429
        resp.json.return_value = {"error": "HyperFurion TTS quota exceeded — resets 2026-08-01"}
        session = mock.Mock()
        session.post = mock.Mock(return_value=resp)
        client._session = session

        with pytest.raises(RuntimeError, match="quota exceeded"):
            client.synthesize("hello")
