import logging
import tempfile
from pathlib import Path
from typing import Optional

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

logger = logging.getLogger(__name__)

XAI_TTS_URL = "https://api.x.ai/v1/tts"
OPENAI_TTS_URL = "https://api.openai.com/v1/audio/speech"
ELEVENLABS_TTS_URL_TEMPLATE = "https://api.elevenlabs.io/v1/text-to-speech/{voice_id}"
HYPERFURION_DEFAULT_BASE_URL = "https://api.hyperfurion.com"

SUPPORTED_TTS_PROVIDERS = {"xai", "hyperfurion", "openai", "elevenlabs"}

DEFAULT_TTS_MODELS = {
    "xai": "",
    "hyperfurion": "",
    "openai": "gpt-4o-mini-tts",
    "elevenlabs": "eleven_multilingual_v2",
}

DEFAULT_TTS_VOICES = {
    "xai": "eve",
    "hyperfurion": "eve",
    "openai": "coral",
    "elevenlabs": "JBFqnCBsd6RMkjVDRZzb",
}


def _build_session() -> requests.Session:
    session = requests.Session()
    retry_strategy = Retry(
        total=3,
        backoff_factor=0.5,
        status_forcelist=[429, 500, 502, 503, 504],
        allowed_methods=["HEAD", "GET", "POST"],
    )
    adapter = HTTPAdapter(max_retries=retry_strategy)
    session.mount("https://", adapter)
    return session


def _provider_api_key(config: dict, provider: str) -> str:
    providers = config.get("providers", {})
    key = str(providers.get(provider, {}).get("api_key", "")).strip()
    if provider == "xai" and not key:
        key = str(config.get("xai", {}).get("api_key", "")).strip()
    return key


def hyperfurion_tts_url(config: dict) -> str:
    providers = config.get("providers", {})
    base = str(providers.get("hyperfurion", {}).get("base_url", "")).strip()
    base = (base or HYPERFURION_DEFAULT_BASE_URL).rstrip("/")
    return f"{base}/v1/tts"


def create_tts_client(config: dict):
    tts_cfg = config.get("tts", {})
    provider = str(tts_cfg.get("provider", "xai")).lower()
    voice_id = str(tts_cfg.get("voice_id", ""))
    if not voice_id or (provider != "xai" and voice_id == DEFAULT_TTS_VOICES["xai"]):
        voice_id = DEFAULT_TTS_VOICES.get(provider, DEFAULT_TTS_VOICES["xai"])
    openai_base = ""
    if provider == "openai":
        # An OpenAI-compatible base_url (e.g. a local Kokoro/Piper server)
        # keeps speech fully offline.
        openai_base = str(
            config.get("providers", {}).get("openai", {}).get("base_url", "")
        ).strip()
    return TTSClient(
        api_key=_provider_api_key(config, provider),
        provider=provider,
        voice_id=voice_id,
        model=str(tts_cfg.get("model", "") or DEFAULT_TTS_MODELS.get(provider, "")),
        language=str(tts_cfg.get("language", "en")),
        hyperfurion_url=hyperfurion_tts_url(config) if provider == "hyperfurion" else "",
        openai_base_url=openai_base,
    )


class TTSClient:
    def __init__(
        self,
        api_key: str,
        voice_id: str = "eve",
        language: str = "en",
        timeout: float = 30.0,
        *,
        provider: str = "xai",
        model: str = "",
        hyperfurion_url: str = "",
        openai_base_url: str = "",
    ):
        self._api_key = api_key
        self._provider = provider
        self._voice_id = voice_id or DEFAULT_TTS_VOICES.get(provider, "eve")
        self._language = language
        self._timeout = timeout
        self._model = model or DEFAULT_TTS_MODELS.get(provider, "")
        self._hyperfurion_url = hyperfurion_url or f"{HYPERFURION_DEFAULT_BASE_URL}/v1/tts"
        self._openai_url = (
            f"{openai_base_url.rstrip('/')}/audio/speech" if openai_base_url else OPENAI_TTS_URL
        )
        self._session: Optional[requests.Session] = None

    @property
    def session(self) -> requests.Session:
        if self._session is None:
            self._session = _build_session()
        return self._session

    def close(self) -> None:
        if self._session is not None:
            self._session.close()
            self._session = None

    def synthesize(self, text: str) -> bytes:
        if self._provider == "xai":
            audio = self._synthesize_xai(text)
        elif self._provider == "hyperfurion":
            audio = self._synthesize_hyperfurion(text)
        elif self._provider == "openai":
            audio = self._synthesize_openai(text)
        elif self._provider == "elevenlabs":
            audio = self._synthesize_elevenlabs(text)
        else:
            raise RuntimeError(f"unsupported TTS provider: {self._provider}")
        logger.info("TTS synthesized %d bytes of audio using %s", len(audio), self._provider)
        return audio

    def _synthesize_xai(self, text: str) -> bytes:
        headers = {
            "Authorization": f"Bearer {self._api_key}",
            "Content-Type": "application/json",
        }
        payload = {
            "text": text,
            "voice_id": self._voice_id,
            "language": self._language,
        }
        resp = self.session.post(
            XAI_TTS_URL, json=payload, headers=headers, timeout=self._timeout
        )
        resp.raise_for_status()
        return resp.content

    def _synthesize_hyperfurion(self, text: str) -> bytes:
        """Same request shape as xAI TTS, but sent to the HyperFurion relay.

        The relay returns structured JSON errors (invalid key, quota
        exceeded), so those are surfaced verbatim instead of a bare
        HTTP status.
        """
        headers = {
            "Authorization": f"Bearer {self._api_key}",
            "Content-Type": "application/json",
        }
        payload = {
            "text": text,
            "voice_id": self._voice_id,
            "language": self._language,
        }
        resp = self.session.post(
            self._hyperfurion_url, json=payload, headers=headers, timeout=self._timeout
        )
        if resp.status_code >= 400:
            detail = ""
            try:
                detail = str(resp.json().get("error", ""))
            except ValueError:
                pass
            if detail:
                raise RuntimeError(f"HyperFurion TTS: {detail}")
            resp.raise_for_status()
        return resp.content

    def _synthesize_openai(self, text: str) -> bytes:
        headers = {
            "Authorization": f"Bearer {self._api_key}",
            "Content-Type": "application/json",
        }
        payload = {
            "model": self._model,
            "input": text,
            "voice": self._voice_id,
            "response_format": "mp3",
        }
        resp = self.session.post(
            self._openai_url, json=payload, headers=headers, timeout=self._timeout
        )
        resp.raise_for_status()
        return resp.content

    def _synthesize_elevenlabs(self, text: str) -> bytes:
        headers = {
            "xi-api-key": self._api_key,
            "Content-Type": "application/json",
        }
        payload = {
            "text": text,
            "model_id": self._model,
        }
        resp = self.session.post(
            ELEVENLABS_TTS_URL_TEMPLATE.format(voice_id=self._voice_id),
            params={"output_format": "mp3_44100_128"},
            json=payload,
            headers=headers,
            timeout=self._timeout,
        )
        resp.raise_for_status()
        return resp.content

    def synthesize_and_play(self, text: str) -> None:
        audio_data = self.synthesize(text)
        with tempfile.NamedTemporaryFile(suffix=".mp3", delete=False) as f:
            f.write(audio_data)
            tmp_path = f.name

        try:
            self._play_sounddevice(tmp_path)
        except Exception:
            logger.exception("sounddevice playback failed, falling back to pygame")
            try:
                self._play_pygame(tmp_path)
            except Exception:
                logger.exception("pygame playback also failed")
                raise RuntimeError("Failed to play TTS audio with any backend")
        finally:
            Path(tmp_path).unlink(missing_ok=True)

    def _play_sounddevice(self, tmp_path: str) -> None:
        import numpy as np
        import sounddevice as sd
        import soundfile as sf

        data, samplerate = sf.read(tmp_path)
        if data.ndim == 1:
            data = data[:, np.newaxis]
        sd.play(data, samplerate)
        sd.wait()

    def _play_pygame(self, tmp_path: str) -> None:
        import pygame

        mixer_ready = False
        try:
            pygame.mixer.init()
            mixer_ready = True
            pygame.mixer.music.load(tmp_path)
            pygame.mixer.music.play()
            while pygame.mixer.music.get_busy():
                pygame.time.Clock().tick(10)
        finally:
            if mixer_ready:
                pygame.mixer.quit()
