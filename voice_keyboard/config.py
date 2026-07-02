import copy
import os
from pathlib import Path

import tomllib

from voice_keyboard.stt import DEFAULT_STT_MODELS, SUPPORTED_STT_PROVIDERS
from voice_keyboard.tts import DEFAULT_TTS_MODELS, DEFAULT_TTS_VOICES, SUPPORTED_TTS_PROVIDERS

DEFAULT_CONFIG: dict = {
    "xai": {
        "api_key": "",
    },
    "providers": {
        "xai": {
            "api_key": "",
        },
        "hyperfurion": {
            "api_key": "",
            # Hosted HyperFurion relay; override for self-hosted relays.
            "base_url": "",
        },
        "openai": {
            "api_key": "",
            # Point at any OpenAI-compatible server (e.g. a local Whisper
            # or Kokoro server) for fully offline dictation and speech.
            "base_url": "",
        },
        "groq": {
            "api_key": "",
        },
        "deepgram": {
            "api_key": "",
        },
        "assemblyai": {
            "api_key": "",
        },
        "elevenlabs": {
            "api_key": "",
        },
    },
    "stt": {
        "provider": "xai",
        "model": DEFAULT_STT_MODELS["xai"],
        "language": "en",
        "interim_results": True,
    },
    "tts": {
        "provider": "xai",
        "model": DEFAULT_TTS_MODELS["xai"],
        "voice_id": DEFAULT_TTS_VOICES["xai"],
        "language": "en",
    },
    "audio": {
        "sample_rate": 16000,
        "chunk_ms": 100,
        "device_name": "default",
    },
    "daemon": {
        "socket_path": "",
    },
    "hotkey": {
        "enabled": True,
        "key": "control+alt+v",
        "mode": "auto",
    },
}

PLACEHOLDER_API_KEYS = {
    "xai-your-api-key-here",
    "hfk-your-subscription-key-here",
    "openai-your-api-key-here",
    "groq-your-api-key-here",
    "deepgram-your-api-key-here",
    "assemblyai-your-api-key-here",
    "elevenlabs-your-api-key-here",
}


def _config_dir() -> Path:
    xdg = os.environ.get("XDG_CONFIG_HOME", "")
    if xdg:
        return Path(xdg) / "voice-keyboard"
    return Path.home() / ".config" / "voice-keyboard"


def _deep_merge(base: dict, override: dict) -> dict:
    """Deep-merge override into base; returns a fresh dict with no shared refs.

    `copy.deepcopy` of `base` keeps nested dicts/DEFAULT_CONFIG pristine, and
    nested overrides are themselves recursively merged so we never mutate the
    input `override` dict either.
    """
    result = copy.deepcopy(base)
    for key, value in override.items():
        if key in result and isinstance(result[key], dict) and isinstance(value, dict):
            result[key] = _deep_merge(result[key], value)
        else:
            result[key] = copy.deepcopy(value)
    return result


def _default_config_with_paths() -> dict:
    config = copy.deepcopy(DEFAULT_CONFIG)
    config["daemon"]["socket_path"] = str(_config_dir() / "socket")
    return config


def load_config() -> dict:
    config = _default_config_with_paths()
    config_path = _config_dir() / "config.toml"
    if config_path.exists():
        with open(config_path, "rb") as f:
            user_config = tomllib.load(f)
        config = _deep_merge(config, user_config)

    legacy_xai_key = str(config.get("xai", {}).get("api_key", "")).strip()
    providers = config.setdefault("providers", {})
    xai_provider = providers.setdefault("xai", {})
    if legacy_xai_key and not str(xai_provider.get("api_key", "")).strip():
        xai_provider["api_key"] = legacy_xai_key

    # If the user left socket_path empty (or set an empty string), fall back.
    if not config.get("daemon", {}).get("socket_path"):
        config.setdefault("daemon", {})["socket_path"] = str(_config_dir() / "socket")
    return config


def _active_provider_api_key(config: dict, provider: str) -> str:
    providers = config.get("providers", {})
    api_key = str(providers.get(provider, {}).get("api_key", "")).strip()
    if provider == "xai" and not api_key:
        api_key = str(config.get("xai", {}).get("api_key", "")).strip()
    return api_key


def _validate_api_key(config: dict, provider: str) -> None:
    if provider == "openai":
        # A custom OpenAI-compatible endpoint (e.g. a local Whisper/Kokoro
        # server) commonly runs without authentication.
        base_url = str(
            config.get("providers", {}).get("openai", {}).get("base_url", "")
        ).strip()
        if base_url:
            return
    api_key = _active_provider_api_key(config, provider)
    if (
        not isinstance(api_key, str)
        or not api_key.strip()
        or api_key.strip() in PLACEHOLDER_API_KEYS
    ):
        raise RuntimeError(f"providers.{provider}.api_key is not configured")


def validate_config(config: dict) -> None:
    """Validate config and raise a clear RuntimeError on missing/invalid values."""
    stt_cfg = config.get("stt", {})
    tts_cfg = config.get("tts", {})
    stt_provider = str(stt_cfg.get("provider", "xai")).lower()
    tts_provider = str(tts_cfg.get("provider", "xai")).lower()
    if stt_provider not in SUPPORTED_STT_PROVIDERS:
        raise RuntimeError(
            f"stt.provider must be one of: {', '.join(sorted(SUPPORTED_STT_PROVIDERS))}"
        )
    if tts_provider not in SUPPORTED_TTS_PROVIDERS:
        raise RuntimeError(
            f"tts.provider must be one of: {', '.join(sorted(SUPPORTED_TTS_PROVIDERS))}"
        )
    _validate_api_key(config, stt_provider)
    _validate_api_key(config, tts_provider)

    audio_cfg = config.get("audio", {})
    sample_rate = audio_cfg.get("sample_rate", 0)
    chunk_ms = audio_cfg.get("chunk_ms", 0)
    if not isinstance(sample_rate, int) or isinstance(sample_rate, bool) or sample_rate <= 0:
        raise RuntimeError("audio.sample_rate must be a positive integer")
    if not isinstance(chunk_ms, int) or isinstance(chunk_ms, bool) or chunk_ms <= 0:
        raise RuntimeError("audio.chunk_ms must be a positive integer")

    if not config.get("daemon", {}).get("socket_path"):
        raise RuntimeError("daemon.socket_path is not configured")

    hotkey_cfg = config.get("hotkey", {})
    enabled = hotkey_cfg.get("enabled", True)
    if not isinstance(enabled, bool):
        raise RuntimeError("hotkey.enabled must be a boolean")
    key = hotkey_cfg.get("key", "")
    if not isinstance(key, str) or not key.strip():
        raise RuntimeError("hotkey.key must be a non-empty string")
    mode = hotkey_cfg.get("mode", "auto")
    if mode not in {"auto", "toggle", "hold", "disabled"}:
        raise RuntimeError("hotkey.mode must be one of: auto, toggle, hold, disabled")
