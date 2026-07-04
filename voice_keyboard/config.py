import copy
import os
import sys
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
        # Bias recognition toward hotwords you accepted via
        # `voice-keyboard learned` (REST providers only; assembled per
        # session, never stored). Secret fields are always excluded.
        "hotword_bias": False,
    },
    "tts": {
        "provider": "xai",
        "model": DEFAULT_TTS_MODELS["xai"],
        "voice_id": DEFAULT_TTS_VOICES["xai"],
        "language": "en",
        # Speculative synthesis of the primary selection while you are
        # still highlighting, so `voice-keyboard tts` starts instantly.
        # "auto" = only against a LOCAL openai-compatible endpoint (free);
        # "always" opts in cloud TTS (spends tokens on selections never
        # played, and sends selection text before you ask); "off" = never.
        "prefetch": "off",
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
        "hold_threshold_ms": 280,
    },
    "flow": {
        # Grammar + register pipeline (all providers). Off = the daemon
        # behaves exactly as before Flow existed.
        "enabled": True,
        # Molten live injection while speaking (streaming providers).
        "live": True,
        # Spoken commands and punctuation ("scratch that", "period", ...).
        "grammar": True,
        # A molten word commits after surviving this long...
        "stability_ms": 1500,
        # ...and this many consecutive transcript updates.
        "stability_updates": 2,
        # Upper bound on the revisable tail (repairs can never be longer).
        "max_molten_chars": 160,
        # Widen the stability requirement when the provider revises deeply.
        "adaptive": True,
        # Pseudo-streaming for REST providers: auto = only local endpoints
        # (re-transcribing is free there), always, or off.
        "live_rest": "auto",
        "live_rest_interval_ms": 2500,
        # Auto-stop after this much silence (0 = off).
        "auto_stop_ms": 0,
        # Spoken cardinals -> digits: auto = terminal register only.
        "numbers": "auto",
        # Opt-in local dictation ledger (history/recall).
        "history": False,
        # Merge accepted `voice-keyboard learned` overrides into the
        # grammar vocabulary. Dormant until entries are accepted.
        "personal_dictionary": True,
        # Molten diffs: a "furion, ..." rewrite is HELD as pending instead
        # of landing — say "keep it" (or `voice-keyboard keep`) to apply,
        # "scratch that" (or `discard`) to drop. Off = rewrites land
        # immediately, exactly as before.
        "rewrite_pending": False,
        # Wake word for in-stream instructions ("furion, make that formal").
        "wake_word": "furion",
        # "spoken phrase" = "Replacement" (multi-word keys fine).
        "vocabulary": {},
        # Remap command phrases: scratch_that / new_line / new_paragraph /
        # literal, e.g. scratch_that = ["nuke it"].
        "commands": {},
        # Remap spoken punctuation: "period" = "." ("" removes a phrase).
        "punctuation": {},
    },
    "registers": {
        "default": "prose",
        # Probe the focused app (AT-SPI / Quartz / Win32) at recording start.
        "probe": True,
        # App -> register overrides, merged over the built-in terminal list.
        "map": {},
    },
    "llm": {
        # Voice-transform channel; any OpenAI-compatible chat endpoint.
        "provider": "xai",
        "base_url": "",
        "api_key": "",
        "model": "grok-4-fast",
    },
    "ambient": {
        # EXPERIMENTAL containment layer for long-open sessions: when on,
        # only utterances that START with the address word are typed
        # ("furion write ..."); everything else never reaches the engine
        # and evaporates. Does NOT start background capture — sessions
        # still begin explicitly, and the hotkey stays the hard mute.
        "enabled": False,
        # Defaults to flow.wake_word when empty.
        "address_word": "",
    },
    "ask": {
        # Talk to any app: "furion, ask why does this fail" answers about
        # the PRIMARY SELECTION through [llm], spoken via TTS ("say") or
        # typed at the caret ("type", newline-suppressed). This switch
        # gates only the voice trigger; `voice-keyboard ask "…"` is
        # explicit and always available.
        "enabled": False,
        "verbs": ["ask", "explain", "answer"],
        "mode": "say",
    },
    "recall": {
        # Total recall: search everything you ever dictated (the opt-in
        # [flow] history ledger). Keyword search works with no setup;
        # point base_url at an OpenAI-compatible /embeddings endpoint
        # (e.g. a local Ollama: http://localhost:11434/v1) for semantic
        # search. Voice trigger gated here; `voice-keyboard find "…"`
        # is explicit and always available.
        "enabled": False,
        "verbs": ["recall", "remember"],
        "mode": "say",
        "base_url": "",
        "model": "",
        "api_key": "",
    },
    "remote_mic": {
        # EXPERIMENTAL multiplayer keyboard: the daemon serves a one-page
        # LAN mic (self-signed HTTPS; your phone joins with a token and
        # streams audio into normal dictation sessions). Restart to
        # toggle — it owns a listening socket.
        "enabled": False,
        "port": 9177,
        # Auto-generated on first start when empty; shown in the logs.
        "token": "",
    },
    "intent": {
        # Voice→command channel: "furion, run …" compiles ONE command line,
        # types it at the caret, and never presses Enter — the refusal is
        # enforced inside the keystroke injector, not by the model. This
        # switch gates only the VOICE trigger; `voice-keyboard intent "…"`
        # is explicit and always available. Uses the [llm] endpoint.
        "enabled": False,
        # The instruction's first word that routes to the intent channel.
        "verbs": ["run", "command", "execute"],
    },
}

VALID_REGISTERS = {"prose", "terminal", "verbatim", "python", "shell"}
_FLOW_BOOL_KEYS = (
    "enabled",
    "live",
    "grammar",
    "adaptive",
    "history",
    "personal_dictionary",
    "rewrite_pending",
)
_FLOW_INT_KEYS = (
    "stability_ms",
    "stability_updates",
    "max_molten_chars",
    "live_rest_interval_ms",
)

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


def _default_socket_path() -> str:
    if sys.platform == "win32":
        # Windows Python has no AF_UNIX; loopback TCP is the IPC transport.
        return "tcp:127.0.0.1:48765"
    return str(_config_dir() / "socket")


def _default_config_with_paths() -> dict:
    config = copy.deepcopy(DEFAULT_CONFIG)
    config["daemon"]["socket_path"] = _default_socket_path()
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
        config.setdefault("daemon", {})["socket_path"] = _default_socket_path()
    return config


def _active_provider_api_key(config: dict, provider: str) -> str:
    providers = config.get("providers", {})
    api_key = str(providers.get(provider, {}).get("api_key", "")).strip()
    if provider == "xai" and not api_key:
        api_key = str(config.get("xai", {}).get("api_key", "")).strip()
    return api_key


def _is_local_endpoint(url: str) -> bool:
    """True for loopback / link-local / private-network hosts — the only
    endpoints allowed to run keyless (a local Whisper/Kokoro server). A
    remote authenticated gateway still needs a real key, so a placeholder
    fails fast at startup instead of 401ing at runtime."""
    from urllib.parse import urlparse

    host = (urlparse(url).hostname or "").lower()
    if host in {"localhost", "127.0.0.1", "::1", "0.0.0.0"} or host.endswith(".local"):
        return True
    return (
        host.startswith("127.")
        or host.startswith("10.")
        or host.startswith("192.168.")
        or any(host.startswith(f"172.{n}.") for n in range(16, 32))
    )


def _validate_api_key(config: dict, provider: str) -> None:
    if provider == "openai":
        # Only a LOCAL OpenAI-compatible endpoint may run without a key.
        base_url = str(
            config.get("providers", {}).get("openai", {}).get("base_url", "")
        ).strip()
        if base_url and _is_local_endpoint(base_url):
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

    hotword_bias = stt_cfg.get("hotword_bias", False)
    if not isinstance(hotword_bias, bool):
        raise RuntimeError("stt.hotword_bias must be a boolean")

    if str(tts_cfg.get("prefetch", "off")).lower() not in {"off", "auto", "always"}:
        raise RuntimeError("tts.prefetch must be one of: off, auto, always")

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

    _validate_flow_config(config)
    _validate_intent_config(config)
    _validate_ambient_config(config)
    _validate_verb_channel(config, "ask")
    _validate_verb_channel(config, "recall")
    _validate_remote_mic_config(config)


def _validate_ambient_config(config: dict) -> None:
    ambient_cfg = config.get("ambient", {})
    enabled = ambient_cfg.get("enabled", False)
    if not isinstance(enabled, bool):
        raise RuntimeError("ambient.enabled must be a boolean")
    address_word = ambient_cfg.get("address_word", "")
    if not isinstance(address_word, str):
        raise RuntimeError("ambient.address_word must be a string")
    if enabled:
        fallback = str(config.get("flow", {}).get("wake_word", "")).strip()
        if not address_word.strip() and not fallback:
            raise RuntimeError(
                "ambient.enabled needs ambient.address_word or flow.wake_word"
            )


def _validate_verb_channel(config: dict, section: str) -> None:
    cfg = config.get(section, {})
    enabled = cfg.get("enabled", False)
    if not isinstance(enabled, bool):
        raise RuntimeError(f"{section}.enabled must be a boolean")
    verbs = cfg.get("verbs", DEFAULT_CONFIG[section]["verbs"])
    if not isinstance(verbs, list) or not all(
        isinstance(v, str) and v.strip() for v in verbs
    ):
        raise RuntimeError(f"{section}.verbs must be a list of non-empty strings")
    mode = str(cfg.get("mode", "say")).lower()
    if mode not in {"say", "type"}:
        raise RuntimeError(f"{section}.mode must be one of: say, type")


def _validate_remote_mic_config(config: dict) -> None:
    mic_cfg = config.get("remote_mic", {})
    enabled = mic_cfg.get("enabled", False)
    if not isinstance(enabled, bool):
        raise RuntimeError("remote_mic.enabled must be a boolean")
    port = mic_cfg.get("port", 9177)
    if not isinstance(port, int) or isinstance(port, bool) or not 1 <= port <= 65535:
        raise RuntimeError("remote_mic.port must be a port number")
    if not isinstance(mic_cfg.get("token", ""), str):
        raise RuntimeError("remote_mic.token must be a string")


def _validate_intent_config(config: dict) -> None:
    intent_cfg = config.get("intent", {})
    enabled = intent_cfg.get("enabled", False)
    if not isinstance(enabled, bool):
        raise RuntimeError("intent.enabled must be a boolean")
    verbs = intent_cfg.get("verbs", DEFAULT_CONFIG["intent"]["verbs"])
    if not isinstance(verbs, list) or not all(
        isinstance(v, str) and v.strip() for v in verbs
    ):
        raise RuntimeError("intent.verbs must be a list of non-empty strings")


def _validate_flow_config(config: dict) -> None:
    flow_cfg = config.get("flow", {})
    for key in _FLOW_BOOL_KEYS:
        value = flow_cfg.get(key, DEFAULT_CONFIG["flow"][key])
        if not isinstance(value, bool):
            raise RuntimeError(f"flow.{key} must be a boolean")
    for key in _FLOW_INT_KEYS:
        value = flow_cfg.get(key, DEFAULT_CONFIG["flow"][key])
        if not isinstance(value, int) or isinstance(value, bool) or value <= 0:
            raise RuntimeError(f"flow.{key} must be a positive integer")
    auto_stop = flow_cfg.get("auto_stop_ms", 0)
    if not isinstance(auto_stop, int) or isinstance(auto_stop, bool) or auto_stop < 0:
        raise RuntimeError("flow.auto_stop_ms must be a non-negative integer (0 = off)")
    if str(flow_cfg.get("live_rest", "auto")).lower() not in {"auto", "always", "off"}:
        raise RuntimeError("flow.live_rest must be one of: auto, always, off")
    if str(flow_cfg.get("numbers", "auto")).lower() not in {"auto", "always", "off"}:
        raise RuntimeError("flow.numbers must be one of: auto, always, off")
    vocabulary = flow_cfg.get("vocabulary", {})
    if not isinstance(vocabulary, dict) or not all(
        isinstance(k, str) and isinstance(v, str) for k, v in vocabulary.items()
    ):
        raise RuntimeError("flow.vocabulary must map spoken phrases to strings")

    registers_cfg = config.get("registers", {})
    default_register = str(registers_cfg.get("default", "prose")).lower()
    if default_register not in VALID_REGISTERS:
        raise RuntimeError(
            f"registers.default must be one of: {', '.join(sorted(VALID_REGISTERS))}"
        )
    register_map = registers_cfg.get("map", {})
    if not isinstance(register_map, dict):
        raise RuntimeError("registers.map must be a table of app = register")
    for app, register in register_map.items():
        if str(register).lower() not in VALID_REGISTERS:
            raise RuntimeError(
                f"registers.map.{app} must be one of: "
                f"{', '.join(sorted(VALID_REGISTERS))}"
            )
