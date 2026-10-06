import codecs
import copy
import logging
import sys
from pathlib import Path
from typing import Optional

import tomllib

from voice_keyboard import paths
from voice_keyboard.stt import DEFAULT_STT_MODELS, SUPPORTED_STT_PROVIDERS
from voice_keyboard.tts import DEFAULT_TTS_MODELS, DEFAULT_TTS_VOICES, SUPPORTED_TTS_PROVIDERS

logger = logging.getLogger(__name__)

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
        # A global hotkey that reads the highlighted text aloud (press again
        # to stop). Off by default on Linux, where a desktop shortcut runs
        # `voice-keyboard tts`; Windows defaults to control+alt+r.
        "hotkey": "",
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
        # Spoken commands and punctuation in another language too: "es",
        # "fr" or "de" add "punto"/"virgule"/"neue Zeile"… to the English set.
        "language": "en",
        # Opt-in local dictation ledger (history/recall).
        "history": False,
        # Also append per-dictation latency numbers (no text) to
        # latency.jsonl in the state dir. `voice-keyboard stats` works
        # either way, from the daemon's in-memory ring.
        "latency_log": False,
        # Merge accepted `voice-keyboard learned` overrides into the
        # grammar vocabulary. Dormant until entries are accepted.
        "personal_dictionary": True,
        # "spell that n g i n x" replaces the last word with the spelled
        # one ("spell ..." types it); each becomes a `learned` candidate.
        "spelling": True,
        # "liam at example dot com" -> liam@example.com, "docs dot python
        # dot org" -> docs.python.org (only runs that end in a known
        # top-level domain; "meet at the office" stays prose).
        "addresses": True,
        # "snake case user id" -> user_id, "camel case get user name" ->
        # getUserName; pascal, kebab, constant, title, dot, all caps, no
        # space. "code" = in code and terminal registers only (where "no
        # space" is never prose); "everywhere"; "off".
        "formatters": "code",
        # Hesitation sounds dropped from what is typed. [] keeps them all.
        "fillers": ["um", "umm", "uh", "uhh", "uhm", "erm"],
        # Self-corrections ("Tuesday, no wait, Wednesday"): "llm" asks
        # [llm] to delete the false start when a dictation has a correction
        # cue; the answer may only delete words, or it is ignored. "off".
        "corrections": "off",
        # A recording that starts within 30 s of the last one, in the same
        # app and the same prose register, continues its text: a space
        # before the first word, a capital only after a sentence end.
        "rejoin": True,
        # Punctuation where you paused: streaming recognizers end a sentence
        # at every pause. auto = rules, plus an [llm] review of the unclear
        # pauses when [llm] is usable; llm / rules / off (keep the
        # recognizer's periods).
        "pause_review": "auto",
        # Molten diffs: a "vk, ..." rewrite is HELD as pending instead
        # of landing — say "keep it" (or `voice-keyboard keep`) to apply,
        # "scratch that" (or `discard`) to drop. Off = rewrites land
        # immediately, exactly as before.
        "rewrite_pending": False,
        # Wake word for in-stream instructions ("vk, make that formal").
        "wake_word": "vk",
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
        "model": "grok-4.3",
    },
    "ambient": {
        # EXPERIMENTAL containment layer for long-open sessions: when on,
        # only utterances that START with the address word are typed
        # ("vk write ..."); everything else never reaches the engine
        # and evaporates. Does NOT start background capture — sessions
        # still begin explicitly, and the hotkey stays the hard mute.
        "enabled": False,
        # Defaults to flow.wake_word when empty.
        "address_word": "",
    },
    "assistant": {
        # The conversational MIND: "vk, …" (or the assistant hotkey)
        # holds a conversation with memory, instead of typing. Brain is
        # the xAI realtime voice agent when configured, else the local
        # [llm]. Off by default; the daemon is a keyboard until you turn
        # the mind on. On by default: it's push-to-talk, so nothing is
        # captured until you press the hotkey (or the on-screen button) —
        # the hotkey stays the hard mute.
        "enabled": True,
        # What the mind calls itself — local brain persona + on-screen
        # copy. "Kai", from KairOS. (The spoken voice agent's own name is
        # set in the xAI Voice Agent Builder console.)
        "name": "Kai",
        # xAI Voice Agent Builder id; key falls back to providers.xai.
        "agent_id": "",
        "api_key": "",
        # realtime | local | auto (realtime when configured, else local).
        "brain": "auto",
        # Spoken conversation trigger (a second global binding; dictation
        # keeps Ctrl+Alt+V). Default is a BARE modifier — hold Right Ctrl to
        # talk — because a modifier alone never reaches the focused app:
        # held symbol chords (e.g. control+alt+.) make terminals spray
        # escape codes (CSI-u) into the shell. Chords still work if set.
        "hotkey": "rightctrl",
        # How the summon key behaves, like [hotkey].mode: auto = HOLD to
        # talk / release to send, or a quick TAP to toggle hands-free.
        "mode": "auto",
        # A short offline tone when Kai starts listening / captures your
        # question — eyes-free confirmation. No network, no asset.
        "earcon": True,
        # An always-on clickable Kai orb on screen (the GNOME overlay
        # extension draws it); click to summon. Set false to hide it.
        "button": True,
        # On Wayland the daemon often can't see the focused app (GPU
        # terminals expose no AT-SPI, GNOME denies introspection). When
        # focus is UNKNOWN, still attempt the terminal command route — the
        # classifier answers questions and only types actual commands (no
        # Enter). Off = always answer when focus can't be resolved.
        "terminal_fallback": True,
        # local = never send file contents; cloud = send excerpts of files
        # you explicitly name. Selection + memory are always allowed.
        "privacy_mode": "local",
        "memory_enabled": True,
        "web_enabled": True,
        "max_memory_results": 5,
        # The brain gets HANDS: when true it may propose one command line,
        # which the daemon TYPES at the caret and never runs (Enter is
        # refused in the injector). Off by default.
        "can_act": False,
        # Confine any file context the brain sees to this root.
        "home_root": "",
    },
    "ask": {
        # Talk to any app: "vk, ask why does this fail" answers about
        # the PRIMARY SELECTION through [llm], spoken via TTS ("say") or
        # typed at the caret ("type", newline-suppressed). This switch
        # gates only the voice trigger; `voice-keyboard ask "…"` is
        # explicit and always available.
        "enabled": False,
        "verbs": ["ask", "explain", "answer"],
        "mode": "say",
    },
    # Text you type by name: "VK, my email" types it — alone, or at the
    # end of a dictation ("send the invoice to VK, my email"). Spoken name
    # -> text, typed exactly (newlines included).
    "snippets": {},
    # Polish per app: after a prose dictation in a mapped app, [llm]
    # rewrites it in that style, the way "VK, make that …" would. Off
    # until an app is mapped; never in terminals, code or secret fields.
    "polish": {"map": {}},
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
        # Voice→command channel: "vk, run …" compiles ONE command line,
        # types it at the caret, and never presses Enter — the refusal is
        # enforced inside the keystroke injector, not by the model. This
        # switch gates only the VOICE trigger; `voice-keyboard intent "…"`
        # is explicit and always available. Uses the [llm] endpoint.
        "enabled": False,
        # The instruction's first word that routes to the intent channel.
        "verbs": ["run", "command", "execute"],
    },
    "nav": {
        # Hands-free navigation: "go left three words", "select previous
        # word", "go to end of line", "press tab". A command fires only as
        # a whole utterance of its own (pause before and after); said
        # mid-sentence it is typed as words. Never presses Enter.
        "enabled": False,
        # Chord overrides per kind of app, e.g. under [nav.keys.terminal]:
        # "move:word:left" = "ctrl+left"; "" disables a command.
        "keys": {"editor": {}, "terminal": {}},
    },
    "wake": {
        # Summon Kai hands-free by saying her name. A tiny LOCAL openWakeWord
        # model scores a rolling mic buffer — no transcription, nothing
        # leaves the box — and only when it fires does normal capture begin.
        # OFF by default: this is the ONE path that keeps the mic warm, so
        # the hotkey stays the hard mute unless you arm this. Needs the
        # optional dep: pip install 'hyperfurion-vk[wake]'.
        "enabled": False,
        "engine": "openwakeword",
        "word": "kai",
        # Path to a trained "Kai" openWakeWord model. Empty = fall back to
        # openWakeWord's bundled words (for testing); train one with
        # scripts/train_kai_wakeword.py.
        "model_path": "",
        "threshold": 0.5,
        # Ignore repeat fires within this many seconds.
        "cooldown_s": 2.0,
        "mic_device": "",
    },
}

VALID_REGISTERS = {"prose", "terminal", "verbatim", "python", "shell"}
_FLOW_BOOL_KEYS = (
    "enabled",
    "live",
    "grammar",
    "adaptive",
    "history",
    "latency_log",
    "personal_dictionary",
    "spelling",
    "addresses",
    "rejoin",
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
    return paths.config_dir()


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
        return "tcp:127.0.0.1:0"  # a free port per daemon (see ipc.py)
    return str(_config_dir() / "socket")


DEFAULT_WINDOWS_TTS_HOTKEY = "control+alt+r"


def _default_config_with_paths() -> dict:
    config = copy.deepcopy(DEFAULT_CONFIG)
    config["daemon"]["socket_path"] = _default_socket_path()
    if sys.platform == "win32":
        # No desktop-shortcut system to bind `voice-keyboard tts` to, so the
        # daemon owns a read-aloud hotkey there.
        config["tts"]["hotkey"] = DEFAULT_WINDOWS_TTS_HOTKEY
    return config


def read_config_text(path: Path) -> str:
    """config.toml as text, however Notepad saved it: UTF-8 with or without
    a byte-order mark, "Unicode" (UTF-16), or the ANSI code page. Line
    endings come back as \n, so a rewrite (sign-in) can't double them."""
    data = path.read_bytes()
    if data.startswith(codecs.BOM_UTF8):
        text = data[len(codecs.BOM_UTF8):].decode("utf-8")
    elif data.startswith((codecs.BOM_UTF16_LE, codecs.BOM_UTF16_BE)):
        text = data.decode("utf-16")
    else:
        try:
            text = data.decode("utf-8")
        except UnicodeDecodeError:
            import locale

            # The ANSI code page even in UTF-8 mode (where
            # getpreferredencoding would say UTF-8 and mangle every accent).
            text = data.decode(locale.getencoding() or "cp1252", errors="replace")
    return text.replace("\r\n", "\n").replace("\r", "\n")


def _table(config: dict, name: str) -> dict:
    value = config.get(name, {})
    return value if isinstance(value, dict) else {}


# Bindings besides dictation's whose defaults may step aside (dictation
# itself always keeps a key).
_YIELDING_BINDINGS = (("tts", "hotkey"), ("assistant", "hotkey"))


def _yield_default_hotkeys(config: dict, user_config: dict) -> None:
    """A default binding the user never wrote (Windows' read-aloud
    Ctrl+Alt+R, Kai's Right Ctrl) steps aside when one of the user's own
    bindings uses the same chord: theirs wins, and the default goes unbound
    with a warning — rather than refusing to start over a setting they never
    wrote. Two clashing bindings the user did write stay an error."""
    from voice_keyboard.hotkey import bindings_clash

    def written(section: str, key: str) -> bool:
        table = user_config.get(section)
        return isinstance(table, dict) and key in table

    def value(section: str, key: str) -> str:
        found = _table(config, section).get(key, "")
        return found.strip() if isinstance(found, str) else ""

    everything = (("hotkey", "key"),) + _YIELDING_BINDINGS
    for section, key in _YIELDING_BINDINGS:
        default = value(section, key)
        if not default or written(section, key):
            continue
        for other_section, other_key in everything:
            if (other_section, other_key) == (section, key):
                continue
            other = value(other_section, other_key)
            if other and written(other_section, other_key) and bindings_clash(default, other):
                logger.warning(
                    "%s is your %s.%s, so the default %s.%s is unbound; set it to"
                    " another key to use it", default, other_section, other_key, section, key,
                )
                _table(config, section)[key] = ""
                break


def load_config(path: Optional[Path] = None) -> dict:
    """The effective config: defaults merged with config.toml (or `path`)."""
    config = _default_config_with_paths()
    config_path = path if path is not None else _config_dir() / "config.toml"
    if config_path.exists():
        user_config = tomllib.loads(read_config_text(config_path))
        config = _deep_merge(config, user_config)
        _yield_default_hotkeys(config, user_config)

    # Sections written as plain values are reported by validate_config;
    # nothing here may trip over them first.
    legacy_xai_key = str(_table(config, "xai").get("api_key", "")).strip()
    providers = config.get("providers")
    if isinstance(providers, dict):
        xai_provider = providers.setdefault("xai", {})
        if (
            isinstance(xai_provider, dict)
            and legacy_xai_key
            and not str(xai_provider.get("api_key", "")).strip()
        ):
            xai_provider["api_key"] = legacy_xai_key

    # If the user left socket_path empty (or set an empty string), fall back.
    daemon = config.get("daemon")
    if isinstance(daemon, dict) and not daemon.get("socket_path"):
        daemon["socket_path"] = _default_socket_path()
    return config


def _active_provider_api_key(config: dict, provider: str) -> str:
    entry = _table(config, "providers").get(provider, {})
    api_key = str(entry.get("api_key", "") if isinstance(entry, dict) else "").strip()
    if provider == "xai" and not api_key:
        api_key = str(_table(config, "xai").get("api_key", "")).strip()
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


def lacks_credentials(path: Path) -> bool:
    """True for a config file whose only problem is that no API key was ever
    filled in — e.g. the early beta installer's copy of the example."""
    try:
        validate_config(load_config(path))
    except RuntimeError as exc:
        return "api_key is not configured" in str(exc)
    except Exception:
        return False
    return False


def is_usable(path: Path) -> bool:
    try:
        validate_config(load_config(path))
    except Exception:
        return False
    return True


def validate_config(config: dict) -> None:
    """Validate config and raise a clear RuntimeError on missing/invalid values."""
    for name, default in DEFAULT_CONFIG.items():
        if isinstance(default, dict) and name in config and not isinstance(config[name], dict):
            raise RuntimeError(
                f"{name} must be a [{name}] section, not a {type(config[name]).__name__}"
            )
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
    tts_hotkey = tts_cfg.get("hotkey", "")
    if not isinstance(tts_hotkey, str):
        raise RuntimeError("tts.hotkey must be a string")
    if tts_hotkey.strip():
        from voice_keyboard.hotkey import parse_binding

        try:
            parse_binding(tts_hotkey, allow_bare=True)
        except ValueError as exc:
            raise RuntimeError(f"tts.hotkey is invalid: {exc}") from exc
        from voice_keyboard.hotkey import bindings_clash

        main_key = str(config.get("hotkey", {}).get("key", ""))
        if bindings_clash(tts_hotkey, main_key):
            raise RuntimeError("tts.hotkey must differ from the dictation hotkey.key")
        assistant_key = str(config.get("assistant", {}).get("hotkey", ""))
        if assistant_key.strip() and bindings_clash(tts_hotkey, assistant_key):
            raise RuntimeError("tts.hotkey must differ from assistant.hotkey")

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
    _validate_nav_config(config)
    snippets = config.get("snippets", {})
    if not isinstance(snippets, dict):
        raise RuntimeError("snippets must be a [snippets] table of name = \"text\"")
    for name, text in snippets.items():
        if not str(name).strip() or not isinstance(text, str) or not text:
            raise RuntimeError(f"snippets.{name!r}: a spoken name and the text to type")
    polish_map = (config.get("polish", {}) or {}).get("map", {})
    if not isinstance(polish_map, dict) or not all(
        isinstance(v, str) and v.strip() for v in polish_map.values()
    ):
        raise RuntimeError('polish.map must be a table of app = "style", e.g. slack = "casual"')
    _validate_ambient_config(config)
    _validate_verb_channel(config, "ask")
    _validate_verb_channel(config, "recall")
    _validate_remote_mic_config(config)
    _validate_assistant_config(config)
    _validate_wake_config(config)


def _validate_wake_config(config: dict) -> None:
    cfg = config.get("wake", {})
    if not isinstance(cfg.get("enabled", False), bool):
        raise RuntimeError("wake.enabled must be a boolean")
    for key in ("engine", "word", "model_path", "mic_device"):
        if not isinstance(cfg.get(key, ""), str):
            raise RuntimeError(f"wake.{key} must be a string")
    threshold = cfg.get("threshold", 0.5)
    if not isinstance(threshold, (int, float)) or isinstance(threshold, bool) or not (
        0.0 <= float(threshold) <= 1.0
    ):
        raise RuntimeError("wake.threshold must be a number in [0, 1]")
    cooldown = cfg.get("cooldown_s", 2.0)
    if not isinstance(cooldown, (int, float)) or isinstance(cooldown, bool) or cooldown < 0:
        raise RuntimeError("wake.cooldown_s must be a non-negative number")
    if cfg.get("enabled", False) and str(cfg.get("word", "")).strip() == "":
        raise RuntimeError("wake.enabled needs a wake.word")


def _validate_assistant_config(config: dict) -> None:
    cfg = config.get("assistant", {})
    for key in (
        "enabled", "memory_enabled", "web_enabled", "can_act",
        "earcon", "button", "terminal_fallback",
    ):
        if not isinstance(cfg.get(key, False), bool):
            raise RuntimeError(f"assistant.{key} must be a boolean")
    if str(cfg.get("brain", "auto")).lower() not in {"realtime", "local", "auto"}:
        raise RuntimeError("assistant.brain must be one of: realtime, local, auto")
    if str(cfg.get("mode", "auto")).lower() not in {"auto", "toggle", "hold"}:
        raise RuntimeError("assistant.mode must be one of: auto, toggle, hold")
    if str(cfg.get("privacy_mode", "local")).lower() not in {"local", "cloud"}:
        raise RuntimeError("assistant.privacy_mode must be one of: local, cloud")
    for key in ("name", "agent_id", "api_key", "hotkey", "home_root"):
        if not isinstance(cfg.get(key, ""), str):
            raise RuntimeError(f"assistant.{key} must be a string")
    max_mem = cfg.get("max_memory_results", 5)
    if not isinstance(max_mem, int) or isinstance(max_mem, bool) or max_mem < 0:
        raise RuntimeError("assistant.max_memory_results must be a non-negative integer")
    hotkey = str(cfg.get("hotkey", "")).strip()
    if hotkey:
        # The summon key is always bound (a press gives a helpful hint even
        # when the mind is off), so a typo must fail loud at load, not
        # silently at listener start. Checked against this platform's
        # keycode table (skipped where none is available).
        from voice_keyboard.hotkey import parse_binding

        try:
            parse_binding(hotkey, allow_bare=True)
        except ValueError as exc:
            raise RuntimeError(f"assistant.hotkey is invalid: {exc}") from exc
    # Bound even while the mind is off (a press explains how to turn it on).
    if hotkey:
        from voice_keyboard.hotkey import bindings_clash

        if bindings_clash(hotkey, str(config.get("hotkey", {}).get("key", ""))):
            raise RuntimeError(
                "assistant.hotkey must differ from the dictation hotkey.key"
            )


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


def _validate_nav_config(config: dict) -> None:
    from voice_keyboard.flow.nav import parse_override

    nav_cfg = config.get("nav", {})
    if not isinstance(nav_cfg.get("enabled", False), bool):
        raise RuntimeError("nav.enabled must be a boolean")
    keys = nav_cfg.get("keys", {})
    if not isinstance(keys, dict):
        raise RuntimeError("nav.keys must be a table")
    for kind, table in keys.items():
        if kind not in ("editor", "terminal"):
            raise RuntimeError(f"nav.keys.{kind}: use nav.keys.editor or nav.keys.terminal")
        if not isinstance(table, dict):
            raise RuntimeError(f"nav.keys.{kind} must be a table")
        for action, value in table.items():
            try:
                parse_override(value)
            except ValueError as exc:
                raise RuntimeError(f"nav.keys.{kind}.{action}: {exc}") from None


def _validate_flow_config(config: dict) -> None:
    flow_cfg = config.get("flow", {})
    fillers = flow_cfg.get("fillers", DEFAULT_CONFIG["flow"]["fillers"])
    if not isinstance(fillers, list) or not all(
        isinstance(f, str) and f.strip() and " " not in f.strip() for f in fillers
    ):
        raise RuntimeError("flow.fillers must be a list of single words")
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
    from voice_keyboard.flow.languages import LANGUAGES

    if str(flow_cfg.get("language", "en")).lower() not in LANGUAGES:
        raise RuntimeError("flow.language must be one of: " + ", ".join(LANGUAGES))
    if str(flow_cfg.get("numbers", "auto")).lower() not in {"auto", "always", "off"}:
        raise RuntimeError("flow.numbers must be one of: auto, always, off")
    if str(flow_cfg.get("formatters", "code")).lower() not in {"code", "everywhere", "off"}:
        raise RuntimeError("flow.formatters must be one of: code, everywhere, off")
    if str(flow_cfg.get("corrections", "off")).lower() not in {"llm", "off"}:
        raise RuntimeError("flow.corrections must be one of: llm, off")
    if str(flow_cfg.get("pause_review", "auto")).lower() not in {"auto", "llm", "rules", "off"}:
        raise RuntimeError("flow.pause_review must be one of: auto, llm, rules, off")
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
