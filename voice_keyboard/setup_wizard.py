"""`voice-keyboard setup` — the first-run walkthrough of the settings.

Both installers run it (install.sh on Linux, the PowerShell installer on
Windows), and it can be re-run any time. It edits config.toml in place,
so comments and every setting it doesn't ask about are kept.

The first thing it does is look for a llama.cpp server (`llama-server`)
already running on this machine. If one answers, it offers to make that
model the default for [llm] — "vk, ..." rewrites, `transform`, Kai's
terminal routing and pause punctuation — so a local model is one Enter
away. Only loopback ports are probed, with short timeouts.
"""

import getpass
import os
import sys
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Optional

import requests

# llama-server listens on 8080 by default; 8081/8000 are the usual
# second choices and 8012 is llama.vscode's. LLAMA_ARG_PORT is
# llama-server's own port variable, so a user who sets it is found too.
LLAMA_CPP_PORTS = (8080, 8081, 8000, 8012)
PROBE_TIMEOUT_S = 0.6

STT_PROVIDERS = ("xai", "openai", "groq", "deepgram", "assemblyai")
TTS_PROVIDERS = ("xai", "openai", "elevenlabs")
HOTKEY_MODES = ("auto", "toggle", "hold")


@dataclass
class LlamaServer:
    """A running llama.cpp server: its OpenAI-compatible base URL and the
    models it serves (one, or several in router mode)."""

    base_url: str
    models: list = field(default_factory=list)
    needs_key: bool = False


def _json(resp) -> dict:
    try:
        data = resp.json()
    except ValueError:
        return {}
    return data if isinstance(data, dict) else {}


def probe_llama_cpp(origin: str, api_key: str = "", timeout: float = PROBE_TIMEOUT_S) -> Optional[LlamaServer]:
    """The llama.cpp server at `origin` (scheme://host:port), or None.

    llama.cpp's /v1/models marks its models `"owned_by": "llamacpp"`, which
    tells it apart from other OpenAI-compatible servers. A server started
    with --api-key answers 401 there but keeps /health open; that is
    reported with needs_key so the caller can ask for the key."""
    origin = origin.rstrip("/")
    if origin.endswith("/v1"):
        origin = origin[:-3]
    headers = {"Authorization": f"Bearer {api_key}"} if api_key else {}
    try:
        resp = requests.get(f"{origin}/v1/models", headers=headers, timeout=timeout)
    except requests.RequestException:
        return None
    if resp.status_code == 401 and not api_key:
        try:
            health = requests.get(f"{origin}/health", timeout=timeout)
        except requests.RequestException:
            return None
        if health.status_code == 200 and _json(health).get("status") == "ok":
            return LlamaServer(f"{origin}/v1", needs_key=True)
        return None
    if resp.status_code != 200:
        return None
    data = _json(resp).get("data")
    if not isinstance(data, list):
        return None
    models = [
        str(item["id"])
        for item in data
        if isinstance(item, dict) and item.get("owned_by") == "llamacpp" and item.get("id")
    ]
    if not models:
        return None
    return LlamaServer(f"{origin}/v1", models)


def _candidate_origins(extra: tuple = ()) -> list:
    ports = list(LLAMA_CPP_PORTS)
    env_port = os.environ.get("LLAMA_ARG_PORT", "").strip()
    if env_port.isdigit() and int(env_port) not in ports:
        ports.insert(0, int(env_port))
    origins = [str(url) for url in extra if url]
    origins += [f"http://127.0.0.1:{port}" for port in ports]
    seen: set = set()
    unique = []
    for origin in origins:
        key = origin.rstrip("/").removesuffix("/v1")
        if key not in seen:
            seen.add(key)
            unique.append(origin)
    return unique


def detect_llama_cpp(extra: tuple = (), timeout: float = PROBE_TIMEOUT_S) -> Optional[LlamaServer]:
    """The first llama.cpp server answering on this machine, or None.
    `extra` URLs (e.g. HFVK_LLAMA_URL, or a local [llm].base_url) are tried
    before the default ports."""
    for origin in _candidate_origins(extra):
        server = probe_llama_cpp(origin, timeout=timeout)
        if server is not None:
            return server
    return None


def model_label(model_id: str) -> str:
    """A model id as people know it: llama.cpp reports the alias, or the
    path of the .gguf file it loaded."""
    name = model_id.replace("\\", "/").rsplit("/", 1)[-1]
    return name[:-5] if name.lower().endswith(".gguf") else name


def _set_value(text: str, table: str, key: str, value) -> str:
    from voice_keyboard.client import _set_toml_value

    return _set_toml_value(text, table, key, value)


class _Abort(Exception):
    pass


class Wizard:
    """The walkthrough. Input and output are injectable so it can be
    tested (and so the installers can hand it the terminal)."""

    def __init__(
        self,
        path: Path,
        *,
        ask: Callable[[str], str] = input,
        secret: Callable[[str], str] = getpass.getpass,
        out: Callable[[str], None] = print,
        detect: Callable[..., Optional[LlamaServer]] = detect_llama_cpp,
        probe: Callable[..., Optional[LlamaServer]] = probe_llama_cpp,
        login: Optional[Callable[[], bool]] = None,
    ):
        self.path = path
        self._ask = ask
        self._secret = secret
        self.out = out
        self._detect = detect
        self._probe = probe
        self._login = login or _hosted_login
        self.edits: list = []  # (table, key, value, label or "")
        self.hosted = False
        self.llm_from_llama = False

    # ------------------------------------------------------------ prompts

    def ask(self, prompt: str, default: str = "") -> str:
        suffix = f" [{default}]" if default else ""
        try:
            answer = self._ask(f"{prompt}{suffix}: ")
        except (EOFError, KeyboardInterrupt):
            raise _Abort() from None
        return (answer or "").strip() or default

    def confirm(self, prompt: str, default: bool = True) -> bool:
        hint = "Y/n" if default else "y/N"
        while True:
            answer = self.ask(f"{prompt} ({hint})").lower()
            if not answer:
                return default
            if answer in {"y", "yes"}:
                return True
            if answer in {"n", "no"}:
                return False
            self.out("    Please answer y or n.")

    def choose(self, prompt: str, options: list, default: int) -> int:
        """Numbered menu; returns the 1-based choice."""
        self.out(prompt)
        for number, label in enumerate(options, 1):
            self.out(f"  {number}) {label}")
        while True:
            answer = self.ask(f"Choose 1-{len(options)}", str(default))
            if answer.isdigit() and 1 <= int(answer) <= len(options):
                return int(answer)
            self.out(f"    Please enter a number from 1 to {len(options)}.")

    def pick(self, prompt: str, choices: tuple, default: str) -> str:
        while True:
            answer = self.ask(f"{prompt} ({', '.join(choices)})", default).lower()
            if answer in choices:
                return answer
            self.out(f"    '{answer}' is not one of: {', '.join(choices)}.")

    def secret(self, prompt: str) -> str:
        try:
            return (self._secret(f"{prompt} (input hidden, Enter to skip): ") or "").strip()
        except (EOFError, KeyboardInterrupt):
            raise _Abort() from None

    def set(self, table: str, key: str, value, label: str = "") -> None:
        self.edits.append((table, key, value, label))

    # ------------------------------------------------------------- steps

    def run(self) -> int:
        try:
            text = self._load_text()
            current = self._current_config()
            self.out("")
            self.out("=== HyperFurion VK setup ===")
            self.out("Press Enter to keep the value in [brackets]. Ctrl+C quits without saving.")
            self.out(f"Settings file: {self.path}")
            self.step_local_model(current)
            self.step_speech(current)
            if not self.llm_from_llama:
                self.step_language_model(current)
            self.step_hotkey(current)
            self.step_language(current)
            self.step_extras(current)
            if not self.save(text):
                return 1
        except _Abort:
            self.out("")
            self.out("Setup cancelled; nothing was changed.")
            return 1
        if self.hosted and not self._login():
            self.out("    Sign-in did not finish. Run `voice-keyboard login` later to finish it.")
        self.finish()
        return 0

    def _load_text(self) -> str:
        from voice_keyboard.config import read_config_text

        if self.path.exists():
            return read_config_text(self.path)
        from voice_keyboard.windows.app import STARTER_CONFIG

        return STARTER_CONFIG

    def _current_config(self) -> dict:
        from voice_keyboard.config import load_config

        # A file that doesn't parse yet still gets a walkthrough, from the
        # defaults; only the values asked about are rewritten.
        try:
            return load_config(self.path)
        except Exception:
            return _defaults()

    def step_local_model(self, current: dict) -> None:
        self.out("")
        self.out("-- Local model --")
        self.out("Looking for a llama.cpp server running on this computer...")
        extra = [os.environ.get("HFVK_LLAMA_URL", "")]
        llm_url = str(current.get("llm", {}).get("base_url", "") or "")
        if llm_url:
            from voice_keyboard.config import _is_local_endpoint

            if _is_local_endpoint(llm_url):
                extra.append(llm_url)
        server = self._detect(tuple(extra))
        if server is None:
            self.out("    None found (llama-server checked on ports "
                     + ", ".join(str(p) for p in LLAMA_CPP_PORTS) + ").")
            return
        api_key = ""
        if server.needs_key:
            self.out(f"    Found a server at {server.base_url} that looks like llama.cpp but needs an API key.")
            api_key = self.secret("Its API key (llama-server --api-key)")
            if not api_key:
                return
            confirmed = self._probe(server.base_url, api_key=api_key)
            if confirmed is None:
                self.out("    That key was not accepted (or it is not llama.cpp); skipping.")
                return
            server = confirmed
        model = server.models[0]
        if len(server.models) > 1:
            self.out(f"    Found llama.cpp at {server.base_url} serving {len(server.models)} models.")
            index = self.choose(
                "Which one should HyperFurion VK use?",
                [model_label(m) for m in server.models],
                1,
            )
            model = server.models[index - 1]
        else:
            self.out(f"    Found llama.cpp at {server.base_url} running {model_label(model)}.")
        self.out("    The language model rewrites text (\"vk, make that formal\"), turns Kai's")
        self.out("    requests into terminal commands, and reviews punctuation where you paused.")
        if not self.confirm(f"Use {model_label(model)} as your default model?", True):
            return
        self.set("llm", "provider", "openai")
        self.set("llm", "base_url", server.base_url, f"Language model: {model_label(model)} (llama.cpp, {server.base_url})")
        self.set("llm", "model", model)
        # Never fall back to a cloud key for a local server.
        self.set("llm", "api_key", api_key or "local")
        self.llm_from_llama = True

    def step_speech(self, current: dict) -> None:
        self.out("")
        self.out("-- Speech --")
        stt_now = str(current.get("stt", {}).get("provider", "xai"))
        configured = _has_key(current, stt_now)
        keep = f"Keep the current setup ({stt_now})" if configured else "Skip for now (set it up later)"
        choice = self.choose(
            "How should your speech be transcribed?",
            [
                "HyperFurion hosted service - sign in with your subscription email",
                "My own API key (xAI, OpenAI, Groq, Deepgram, AssemblyAI)",
                "A local OpenAI-compatible speech server (Speaches, whisper.cpp - fully offline)",
                keep,
            ],
            4,
        )
        if choice == 1:
            self.hosted = True
            self.edits.append(("", "", None, "Speech: HyperFurion hosted (you sign in after saving)"))
        elif choice == 2:
            stt = self.pick("Speech-to-text provider", STT_PROVIDERS, "xai")
            self.set("stt", "provider", stt, f"Speech-to-text: {stt}")
            key = self.secret(f"{stt} API key")
            if key:
                self.set(f"providers.{stt}", "api_key", key)
            tts = stt
            if stt not in TTS_PROVIDERS:
                self.out(f"    {stt} does not speak. Read-aloud and Kai's voice need a text-to-speech provider.")
                tts = self.pick("Text-to-speech provider", TTS_PROVIDERS, "xai")
                if tts != stt:
                    tts_key = self.secret(f"{tts} API key")
                    if tts_key:
                        self.set(f"providers.{tts}", "api_key", tts_key)
            self.set("tts", "provider", tts, f"Text-to-speech: {tts}")
        elif choice == 3:
            url = self.ask("Server base URL", "http://127.0.0.1:8000/v1")
            self.set("stt", "provider", "openai", f"Speech: local server at {url}")
            self.set("tts", "provider", "openai")
            self.set("providers.openai", "base_url", url)

    def step_language_model(self, current: dict) -> None:
        self.out("")
        self.out("-- Language model (rewrites, Kai's terminal commands, pause punctuation) --")
        llm = current.get("llm", {})
        now = str(llm.get("model", "") or "")
        now_url = str(llm.get("base_url", "") or "")
        keep = f"Keep the current model ({now}{' at ' + now_url if now_url else ''})" if now else "Keep the default"
        choice = self.choose(
            "Which model should it use?",
            [
                keep,
                "xAI Grok (cloud; uses your xAI key)",
                "A local OpenAI-compatible server (llama.cpp, Ollama, vLLM)",
            ],
            1,
        )
        if choice == 2:
            self.set("llm", "provider", "xai", "Language model: xAI grok-4.3")
            self.set("llm", "base_url", "")
            self.set("llm", "model", "grok-4.3")
            self.set("llm", "api_key", "")
        elif choice == 3:
            url = self.ask("Server base URL", "http://127.0.0.1:8080/v1").rstrip("/")
            if not url.endswith("/v1"):
                url += "/v1"
            server = self._probe(url)
            default_model = server.models[0] if server and server.models else ""
            model = self.ask("Model name", default_model)
            if not model:
                self.out("    No model given; keeping the current one.")
                return
            self.set("llm", "provider", "openai")
            self.set("llm", "base_url", url, f"Language model: {model_label(model)} at {url}")
            self.set("llm", "model", model)
            self.set("llm", "api_key", self.secret("Its API key, if it needs one") or "local")

    def step_hotkey(self, current: dict) -> None:
        from voice_keyboard.hotkey import parse_binding

        self.out("")
        self.out("-- Dictation hotkey --")
        hotkey = current.get("hotkey", {})
        now = str(hotkey.get("key", "") or "control+alt+v")
        while True:
            key = self.ask("Hotkey (e.g. control+alt+v, super+h)", now).lower()
            try:
                parse_binding(key)
            except ValueError as exc:
                self.out(f"    {exc}")
                continue
            break
        mode_now = str(hotkey.get("mode", "auto"))
        if mode_now not in HOTKEY_MODES:
            mode_now = "auto"
        self.out("    auto = tap to start/stop or hold to talk; toggle = tap only; hold = hold only")
        mode = self.pick("Hotkey mode", HOTKEY_MODES, mode_now)
        if key != now:
            self.set("hotkey", "key", key, f"Dictation hotkey: {key}")
        if mode != str(hotkey.get("mode", "auto")):
            self.set("hotkey", "mode", mode, f"Hotkey mode: {mode}")

    def step_language(self, current: dict) -> None:
        self.out("")
        self.out("-- Language --")
        now = str(current.get("stt", {}).get("language", "") or "en")
        lang = self.ask("Language you dictate in (en, de, fr, es, ...)", now).lower()
        if lang != now:
            self.set("stt", "language", lang, f"Language: {lang}")
            self.set("tts", "language", lang)

    def step_extras(self, current: dict) -> None:
        self.out("")
        self.out("-- Extras --")
        kai_now = bool(current.get("assistant", {}).get("enabled", True))
        kai = self.confirm("Turn on Kai, the voice assistant (its own hotkey: hold Right Ctrl)?", kai_now)
        if kai != kai_now:
            self.set("assistant", "enabled", kai, f"Kai: {'on' if kai else 'off'}")
        history_now = bool(current.get("flow", {}).get("history", False))
        history = self.confirm("Keep a private local history of what you dictate?", history_now)
        if history != history_now:
            self.set("flow", "history", history, f"Dictation history: {'on' if history else 'off'}")
        nav_now = bool(current.get("nav", {}).get("enabled", False))
        self.out('    Hands-free navigation: say "select previous word", "go to end of line"')
        self.out("    or \"delete the line\" on its own and the keys are pressed for you.")
        self.out("    Enter is never one of them. Mid-sentence, the words are just typed.")
        nav = self.confirm("Move the caret by voice?", nav_now)
        if nav != nav_now:
            self.set("nav", "enabled", nav, f"Hands-free navigation: {'on' if nav else 'off'}")
        fix_now = str(current.get("flow", {}).get("corrections", "off")).lower() == "llm"
        self.out('    Self-corrections: "send it Tuesday, no wait, Wednesday" becomes')
        self.out('    "Send it Wednesday". Only dictations with a correction cue go to your')
        self.out("    language model, and it may only delete words, never add or change one.")
        fix = self.confirm("Tidy self-corrections with your language model?", fix_now)
        if fix != fix_now:
            self.set("flow", "corrections", "llm" if fix else "off",
                     f"Self-corrections: {'tidied by the language model' if fix else 'kept as dictated'}")

    # --------------------------------------------------------------- save

    def save(self, text: str) -> bool:
        changes = [label for _, _, _, label in self.edits if label]
        self.out("")
        if not changes:
            self.out("No changes.")
            if not self.path.exists():
                self._write(text)
            return True
        self.out("Summary:")
        for label in changes:
            self.out(f"  - {label}")
        if not self.confirm(f"Save to {self.path}?", True):
            self.out("Not saved.")
            return False
        for table, key, value, _ in self.edits:
            if table:
                text = _set_value(text, table, key, value)
        self._write(text)
        self.out(f"Saved {self.path}")
        return True

    def _write(self, text: str) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        # Written beside the target and renamed over it: an interrupted
        # save never leaves half a config, and keys are never world-readable.
        fd, tmp = tempfile.mkstemp(dir=str(self.path.parent), prefix=".config-", suffix=".toml")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                handle.write(text)
            if os.name == "posix":
                os.chmod(tmp, 0o600)
            os.replace(tmp, self.path)
        except BaseException:
            try:
                os.unlink(tmp)
            except OSError:
                pass
            raise

    def finish(self) -> None:
        from voice_keyboard.config import is_usable

        self.out("")
        if is_usable(self.path):
            self.out("Setup complete.")
            self.out("Say \"scratch that\", \"new line\" or \"correct monday to friday\" while you dictate;")
            self.out("`voice-keyboard commands` lists everything you can say;")
            self.out("`voice-keyboard doctor` checks the microphone, typing and the daemon.")
        else:
            self.out("Saved, but HyperFurion VK can't start yet: it still needs a speech provider.")
            self.out("Run `voice-keyboard setup` again, or `voice-keyboard login` for the hosted service.")
        self.out("Change these any time with `voice-keyboard setup`, or edit the settings file.")


def _defaults() -> dict:
    from voice_keyboard.config import _default_config_with_paths

    return _default_config_with_paths()


def _has_key(config: dict, provider: str) -> bool:
    from voice_keyboard.config import _validate_api_key

    try:
        _validate_api_key(config, provider)
    except RuntimeError:
        return False
    return True


def _hosted_login() -> bool:
    from voice_keyboard.client import _run_login
    from voice_keyboard.config import load_config

    try:
        _run_login(load_config(), [])
    except SystemExit as exc:
        return not exc.code
    except (EOFError, KeyboardInterrupt):
        return False
    return True


def main() -> int:
    from voice_keyboard import paths

    if not sys.stdin or not sys.stdin.isatty():
        print(
            "voice-keyboard setup is interactive: run it in a terminal.",
            file=sys.stderr,
        )
        return 2
    return Wizard(paths.config_dir() / "config.toml").run()
