"""Where Kai's questions and answers go, and so whether Kai is on.

[assistant] enabled is "auto" (the default), true or false. Under "auto",
Kai is on only when nothing it does leaves this computer:

- **hears you** — the [stt] that transcribes the spoken question is a
  local speech server (`stt.provider = "openai"` with a local
  `providers.openai.base_url`). xAI, Groq, Deepgram and AssemblyAI are
  online, and so is the HyperFurion relay, even one on localhost: it
  forwards to xAI.
- **thinks** — the [llm] that answers (and drafts terminal commands) is a
  local server, and no xAI voice agent is in use (`agent_id` with a key,
  `brain` = "auto" or "realtime"). A model tag ending in "-cloud" or
  ":cloud" runs on Ollama's cloud, so it is online even at a local address.
- **speaks** — the [tts] that says the answer is a local speech server.

"Local" is config._is_local_endpoint with [assistant] local_hosts. With no
[llm] and no voice agent Kai has nothing to answer with, so it is not on
under "auto".

Not part of the verdict:

- [recall] embeddings: under "auto" Kai searches your dictation history by
  keyword unless [recall] is local too; under true it uses [recall] and the
  service is listed. With memory_enabled = false there is no lookup at all.
- API keys: the verdict is about where data would go, not whether it works.
- web_enabled (HyperFurion VK fetches nothing itself), privacy_mode,
  can_act, earcon, button, name and [wake] (openWakeWord runs locally).

Pure: nothing here touches the network or the disk.
"""

from __future__ import annotations

import copy
import shutil
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

HEARS = "hears you"
THINKS = "thinks"
SPEAKS = "speaks"
LOOKS_UP = "looks up your history"

_KNOWN_HOSTS = {"api.x.ai": "xAI", "api.openai.com": "OpenAI", "api.groq.com": "Groq"}
_SPEECH_COMPANIES = {
    "xai": "xAI",
    "groq": "Groq",
    "deepgram": "Deepgram",
    "assemblyai": "AssemblyAI",
    "elevenlabs": "ElevenLabs",
}
RELAY = "xAI (through the HyperFurion relay)"
OLLAMA_CLOUD = "Ollama's cloud"
# [tts] provider = "none": nothing speaks; Kai shows its answers on screen.
NO_VOICE = "nothing: its answers show on screen"


@dataclass(frozen=True)
class Hop:
    """One part of a Kai turn and where it goes."""

    role: str  # HEARS | THINKS | SPEAKS | LOOKS_UP
    service: str  # "xAI", "qwen3.5-2b at 127.0.0.1:8080", ...
    local: bool
    section: str  # where it is set: "[stt]", "[llm]", ...
    company: str = ""  # who receives it, for "your questions go to xAI"
    # Local and on this very computer (loopback, localhost), not merely on
    # your own network (a LAN address, a .local name, [assistant] local_hosts).
    here: bool = False

    def phrase(self) -> str:
        """`<service> <does what> ([section])`, for a sentence."""
        if self.section == "[assistant] agent_id":
            return f"{self.service} to answer ({self.section})"
        doing = {
            HEARS: "for speech-to-text",
            THINKS: "as its language model",
            SPEAKS: "for its voice",
            LOOKS_UP: "to search your dictation history",
        }[self.role]
        return f"{self.service} {doing} ({self.section})"


@dataclass(frozen=True)
class KaiState:
    on: bool
    setting: str  # "auto" | "on" | "off"
    local: bool  # something can answer, and every hop is local
    hops: tuple = field(default_factory=tuple)
    can_answer: bool = False  # an [llm] client or a voice agent exists
    uses_embeddings: bool = False  # Kai's history search may call [recall]
    name: str = "Kai"
    unreadable: bool = False
    # enabled = true was copied from an older config.toml.example (where it
    # was the default), so it counts as "auto" (config.FROM_EXAMPLE).
    from_example: bool = False

    def online_hops(self) -> tuple:
        return tuple(hop for hop in self.hops if not hop.local)

    @property
    def place(self) -> str:
        """Where its local parts run: "this computer", or "this computer or
        your own network" when one of them is on another machine."""
        if all(hop.here for hop in self.hops if hop.local):
            return "this computer"
        return "this computer or your own network"

    def online(self) -> str:
        """Who would receive Kai's questions: "xAI", "xAI and ElevenLabs"."""
        names: list[str] = []
        for hop in self.online_hops():
            name = hop.company or hop.service
            if name not in names:
                names.append(name)
        return _and(names)

    def why(self) -> str:
        """One plain line: on or off, and why."""
        if self.unreadable:
            return "off: the [assistant] settings could not be read"
        if self.setting == "off":
            return "off ([assistant] enabled = false)"
        if not self.can_answer:
            if self.setting == "on":
                return "on (you turned it on), but no language model is set up for it ([llm])"
            return "off: no language model is set up for it ([llm])"
        if self.local:
            return f"on: everything {self.name} uses runs on {self.place}"
        uses = _and([hop.phrase() for hop in self.online_hops()])
        if self.setting == "on":
            return f"on (you turned it on): it uses {uses}"
        return f"off: it would use {uses}"


def _and(items: list) -> str:
    items = [str(i) for i in items]
    if not items:
        return ""
    return items[0] if len(items) == 1 else ", ".join(items[:-1]) + " and " + items[-1]


def kai_setting(config) -> str:
    """"auto", "on" or "off". Missing means "auto"; anything that isn't
    true, false or "auto" reads as off (validation rejects it anyway)."""
    table = config.get("assistant", {}) if isinstance(config, dict) else {}
    if not isinstance(table, dict):
        return "off"
    value = table.get("enabled", "auto")
    if value is True:
        return "on"
    if value is False:
        return "off"
    if isinstance(value, str) and value.strip().lower() == "auto":
        return "auto"
    return "off"


def _view(config: dict) -> dict:
    """The sections Kai's verdict reads, defaults filled in for anything
    missing or not a table (doctor passes partial configs)."""
    from voice_keyboard.config import DEFAULT_CONFIG

    view = {}
    for name in ("assistant", "llm", "recall", "stt", "tts", "providers", "xai", "wake"):
        value = config.get(name) if isinstance(config, dict) else None
        if isinstance(value, dict):
            merged = copy.deepcopy(DEFAULT_CONFIG.get(name, {}))
            merged.update(value)
            view[name] = merged
        else:
            view[name] = copy.deepcopy(DEFAULT_CONFIG.get(name, {}))
    providers = view["providers"]
    for name, entry in list(providers.items()):
        if not isinstance(entry, dict):
            providers[name] = {}
    return view


def _host_port(url: str) -> str:
    from urllib.parse import urlsplit

    try:
        parts = urlsplit(url)
        host = parts.hostname or url
        port = parts.port
    except ValueError:
        return url
    if ":" in host:
        host = f"[{host}]"
    return f"{host}:{port}" if port else host


def _host(url: str) -> str:
    from urllib.parse import urlsplit

    try:
        return urlsplit(url).hostname or url
    except ValueError:
        return url


def _on_this_computer(url: str) -> bool:
    """A loopback address or a localhost name: this very computer."""
    from voice_keyboard.config import _endpoint_host, _parse_ip

    host = _endpoint_host(url)
    if host is None:
        return False
    if host == "localhost" or host.endswith(".localhost"):
        return True
    ip = _parse_ip(host)
    if ip is None:
        return False
    if ip.version == 6 and ip.ipv4_mapped is not None:
        ip = ip.ipv4_mapped
    return ip.is_loopback or ip.is_unspecified


def _cloud_model(model: str) -> bool:
    model = model.strip().lower()
    return model.endswith("-cloud") or model.endswith(":cloud")


def _speech_hop(view: dict, role: str, section: str, extra) -> Hop:
    from voice_keyboard.config import _is_local_endpoint

    provider = str(view[section.strip("[]")].get("provider", "xai")).strip().lower() or "xai"
    if provider == "none" and section == "[tts]":
        return Hop(role, NO_VOICE, True, section, here=True)
    if provider == "hyperfurion":
        return Hop(role, RELAY, False, section, "xAI")
    if provider == "openai":
        base = str(view["providers"].get("openai", {}).get("base_url", "") or "").strip()
        if not base:
            return Hop(role, "OpenAI", False, section, "OpenAI")
        if _is_local_endpoint(base, extra=extra):
            return Hop(
                role, f"your speech server at {_host_port(base)}", True, section,
                here=_on_this_computer(base),
            )
        host = _host(base)
        company = _KNOWN_HOSTS.get(host, host)
        return Hop(role, company, False, section, company)
    company = _SPEECH_COMPANIES.get(provider, provider)
    return Hop(role, company, False, section, company)


def _model_hop(role: str, section: str, base: str, model: str, extra, *, label: str = "") -> Hop:
    from voice_keyboard.config import _is_local_endpoint

    if _cloud_model(model):
        return Hop(role, OLLAMA_CLOUD, False, section, OLLAMA_CLOUD)
    if _is_local_endpoint(base, extra=extra):
        return Hop(
            role, f"{model} at {_host_port(base)}", True, section, here=_on_this_computer(base)
        )
    host = _host(base)
    company = _KNOWN_HOSTS.get(host, host)
    service = f"{company} ({model})" if label == "llm" and model else company
    return Hop(role, service, False, section, company)


def speech_hop(config: dict, section: str = "stt") -> Hop:
    """Where [stt] ("stt") or [tts] ("tts") sends audio or text. The
    HyperFurion relay is online even on localhost: it forwards to xAI."""
    from voice_keyboard.config import local_hosts

    view = _view(config)
    role = HEARS if section == "stt" else SPEAKS
    return _speech_hop(view, role, f"[{section}]", local_hosts(view))


def uses_voice_agent(config: dict) -> bool:
    """Kai answers spoken questions through the xAI voice agent: an
    agent_id with a key, and [assistant] brain = "auto" or "realtime"
    (mirrors Brain.has_voice_agent)."""
    try:
        view = _view(config)
        brain = str(view["assistant"].get("brain", "auto")).strip().lower()
        if brain not in ("realtime", "auto"):
            return False
        from voice_keyboard.assistant.realtime import create_realtime_client

        return create_realtime_client(view) is not None
    except Exception:
        return False


def _recall_hop(view: dict, extra) -> Optional[Hop]:
    recall = view["recall"]
    base = str(recall.get("base_url", "") or "").strip()
    model = str(recall.get("model", "") or "").strip()
    if not base or not model:
        return None
    return _model_hop(LOOKS_UP, "[recall]", base, model, extra)


def _resolve(config: dict) -> KaiState:
    from voice_keyboard.config import local_hosts
    from voice_keyboard.llm import _llm_settings

    setting = kai_setting(config)
    view = _view(config)
    extra = local_hosts(view)
    name = str(view["assistant"].get("name", "Kai") or "").strip() or "Kai"
    hops = [_speech_hop(view, HEARS, "[stt]", extra)]
    base, _key, model = _llm_settings(view)
    has_llm = bool(base and model)
    if has_llm:
        hops.append(_model_hop(THINKS, "[llm]", base, model, extra, label="llm"))
    agent = uses_voice_agent(view)
    if agent:
        hops.append(Hop(THINKS, "xAI's voice agent", False, "[assistant] agent_id", "xAI"))
    hops.append(_speech_hop(view, SPEAKS, "[tts]", extra))
    uses_embeddings = False
    if view["assistant"].get("memory_enabled", True) is not False:
        recall = _recall_hop(view, extra)
        # Under "auto" an online [recall] is skipped (keyword search), so it
        # never keeps Kai off; under true it is used, and listed.
        if recall is not None and (recall.local or setting == "on"):
            hops.append(recall)
            uses_embeddings = True
    can_answer = has_llm or agent
    local = can_answer and all(hop.local for hop in hops)
    on = setting == "on" or (setting == "auto" and local)
    from voice_keyboard.config import FROM_EXAMPLE

    return KaiState(
        on=on, setting=setting, local=local, hops=tuple(hops), can_answer=can_answer,
        uses_embeddings=uses_embeddings, name=name,
        from_example=setting == "auto" and view["assistant"].get(FROM_EXAMPLE) is True,
    )


def kai_hops(config: dict) -> list:
    return list(kai_state(config).hops)


def kai_state(config: dict) -> KaiState:
    """Is Kai on, and why. Never raises: settings it can't read keep Kai off."""
    try:
        return _resolve(config)
    except Exception:
        return KaiState(on=False, setting=kai_setting(config), local=False, unreadable=True)


# ── telling people what goes where, and how to change it (A14) ─────────


def hop_payload(hop: Hop, config: dict, platform: Optional[str] = None) -> str:
    """What a hop receives, in plain words, for consent prompts."""
    platform = platform or sys.platform
    if hop.role == HEARS:
        return "your recorded question"
    if hop.role == SPEAKS:
        return "its answer"
    if hop.role == LOOKS_UP:
        return "your question and up to 200 lines of your dictation history"
    if hop.section == "[assistant] agent_id":
        return "your recorded question"
    view = _view(config)
    parts = ["your question"]
    if view["assistant"].get("memory_enabled", True) is not False:
        parts += ["related notes from its memory", "your dictation history"]
    if platform.startswith("linux"):
        parts.append("text you have highlighted anywhere on screen")
    if str(view["assistant"].get("privacy_mode", "local")).strip().lower() == "cloud":
        parts.append("excerpts of files and lists of folders you name")
    return _and(parts)


def summon_hint(config: dict, platform: Optional[str] = None) -> str:
    """How to ask Kai something, e.g. "hold Right Ctrl". On macOS a bare
    modifier can't be bound, so never Right Ctrl there."""
    platform = platform or sys.platform
    view = _view(config)
    binding = str(view["assistant"].get("hotkey", "") or "").strip()
    summon = f"run `{_cli(platform)} summon`"
    if not binding or (platform == "darwin" and "+" not in binding):
        return summon
    try:
        from voice_keyboard.hotkey import pretty_binding

        label = pretty_binding(binding)
    except Exception:
        label = binding
    return f"hold {label}" if "+" not in binding else f"hold or tap {label}"



def _cli(platform: str) -> str:
    """`voice-keyboard`, or its full path when it isn't on PATH (macOS)."""
    if platform == "win32" or shutil.which("voice-keyboard"):
        return "voice-keyboard"
    beside = Path(sys.executable).with_name("voice-keyboard")
    return str(beside) if beside.exists() else "voice-keyboard"


def turn_on_hint(platform: Optional[str] = None) -> str:
    platform = platform or sys.platform
    if platform == "win32":
        return "right-click the tray icon → Turn on Kai…"
    return f"{_cli(platform)} kai on"


def turn_off_hint(platform: Optional[str] = None) -> str:
    platform = platform or sys.platform
    if platform == "win32":
        return "right-click the tray icon → Turn off Kai"
    return f"{_cli(platform)} kai off"
