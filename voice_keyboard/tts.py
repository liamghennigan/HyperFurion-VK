import logging
import tempfile
from pathlib import Path
from typing import Optional

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

logger = logging.getLogger(__name__)

XAI_TTS_URL = "https://api.x.ai/v1/tts"


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


class TTSClient:
    def __init__(
        self,
        api_key: str,
        voice_id: str = "eve",
        language: str = "en",
        timeout: float = 30.0,
    ):
        self._api_key = api_key
        self._voice_id = voice_id
        self._language = language
        self._timeout = timeout
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
        logger.info("TTS synthesized %d bytes of audio", len(resp.content))
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
