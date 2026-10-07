"""HyperFurion VK for Windows — the tray app that hosts the daemon.

`pythonw -m voice_keyboard.windows` is what the Start menu entry and the
login item run: no console window, one instance per user, logs in
%LOCALAPPDATA%\\voice-keyboard\\logs. It owns the overlay, the Kai orb and
the notification-area icon (voice_keyboard.windows.shell) and runs the
daemon on the main thread.

If the daemon can't start yet — no config, no API key — the app does not
just exit (a windowless process dying is invisible): the tray icon turns
amber with a setup menu, and the daemon starts by itself as soon as the
config file is saved in a valid state.

`voice-keyboard-daemon` on Windows runs the same app with --console, so
the log also streams to the terminal.
"""

import argparse
import asyncio
import ctypes
import logging
import logging.handlers
import os
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Optional

from voice_keyboard import paths

logger = logging.getLogger(__name__)

APP_NAME = "HyperFurion VK"
APP_ID = "HyperFurion.VoiceKeyboard"
MUTEX_NAME = "Local\\HyperFurionVK.Daemon"
RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
RUN_VALUE = "HyperFurion VK"
ERROR_ALREADY_EXISTS = 183
CREATE_NEW_CONSOLE = 0x00000010
MB_ICONERROR = 0x10
MB_ICONINFORMATION = 0x40

DOCS_URL = "https://github.com/liamghennigan/HyperFurion-VK#windows"

STARTER_CONFIG = """\
# HyperFurion VK settings. Save this file and the app picks it up.
# Every option, with comments: https://github.com/liamghennigan/HyperFurion-VK/blob/main/config.toml.example
#
# Put your speech provider key below. For your own speech server instead,
# set [stt] and [tts] provider = "openai", base_url below, and the model
# names your server knows (`voice-keyboard setup` asks for each of these).
# Existing hosted-service subscribers can run `voice-keyboard login
# you@example.com` instead; the subscription isn't on sale.

[stt]
provider = "xai"        # xai | openai | groq | deepgram | assemblyai | hyperfurion

[tts]
provider = "xai"        # xai | openai | elevenlabs | hyperfurion | none

[providers.xai]
api_key = "xai-your-api-key-here"

# [providers.openai]
# api_key = "sk-..."
# base_url = "http://127.0.0.1:8000/v1"   # a local Whisper/Kokoro server: your audio goes only to it

[hotkey]
key = "control+alt+v"   # tap to toggle dictation, hold to talk

[assistant]
# Turn Kai on or off from the tray icon's menu.
# Kai, the voice assistant: "auto" = on only when everything it uses runs
# on this computer; true = on, even with online services; false = off.
# `voice-keyboard kai` says which, and why.
enabled = "auto"
"""


def initial_config(
    stt_provider: str,
    tts_provider: str,
    keys: dict,
    *,
    base_url: str = "",
) -> str:
    """The first config.toml for a fresh install: the starter template with
    the chosen providers, their keys, and (for a local OpenAI-compatible
    server) its base_url filled in. Used by the Windows installer."""
    from voice_keyboard.client import _set_toml_value

    text = STARTER_CONFIG
    text = _set_toml_value(text, "stt", "provider", stt_provider)
    text = _set_toml_value(text, "tts", "provider", tts_provider)
    for provider, key in keys.items():
        if key:
            text = _set_toml_value(text, f"providers.{provider}", "api_key", key)
    if base_url:
        text = _set_toml_value(text, "providers.openai", "base_url", base_url)
    return text


def write_config_from_env() -> int:
    """Installer entry point: write config.toml from HFVK_* environment
    variables (keys never appear on a command line). Refuses to overwrite
    an existing config. Prints the path written."""
    path = _config_path()
    if path.exists():
        print(f"kept existing {path}")
        return 0
    stt = os.environ.get("HFVK_STT", "").strip().lower()
    tts = os.environ.get("HFVK_TTS", "").strip().lower() or stt
    if not stt:
        print("HFVK_STT is required", file=sys.stderr)
        return 2
    keys = {stt: os.environ.get("HFVK_STT_KEY", "").strip()}
    if tts != stt:
        keys[tts] = os.environ.get("HFVK_TTS_KEY", "").strip()
    text = initial_config(stt, tts, keys, base_url=os.environ.get("HFVK_BASE_URL", "").strip())
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    print(f"wrote {path}")
    return 0


def hotkey_labels(config: dict) -> dict:
    """The tray's hotkey names for a config (WinShell keyword arguments).
    Never raises: it runs before the tray exists, on a config that may not
    have passed validation yet."""
    from voice_keyboard.hotkey import pretty_binding

    def value(section: str, key: str, default: str) -> str:
        table = config.get(section) if isinstance(config, dict) else None
        found = table.get(key, default) if isinstance(table, dict) else default
        return found.strip() if isinstance(found, str) else default

    read_key = value("tts", "hotkey", "")
    # Empty when Kai has no key (the user's, or a default that stepped aside
    # for one of their own bindings): only the orb summons Kai then.
    kai_key = value("assistant", "hotkey", "rightctrl")
    return {
        "dictation_hotkey": pretty_binding(value("hotkey", "key", "") or "control+alt+v"),
        "assistant_hotkey": pretty_binding(kai_key) if kai_key else "",
        "read_hotkey": pretty_binding(read_key) if read_key else "",
    }


def welcome_text(config: dict, state=None) -> str:
    """The first-run balloon: how to dictate, and how to ask Kai while it
    is on, or that it is off and how to turn it on. At most 255 characters
    (a Windows balloon's limit)."""
    from voice_keyboard.assistant.locality import kai_state

    state = state if state is not None else kai_state(config)
    labels = hotkey_labels(config)
    dictate = f"Press {labels['dictation_hotkey']} to dictate into any app."
    name = state.name
    if state.on:
        kai = labels["assistant_hotkey"]
        ask = f"Hold {kai} to ask {name}." if kai else f"Click the orb to ask {name}."
        return f"{dictate} {ask} Right-click the tray icon for settings."
    if state.setting == "off" or state.unreadable:
        return f"{dictate} Right-click the tray icon for settings."
    if not state.can_answer:
        why = "it has no language model to answer with ([llm])"
    else:
        why = f"it would send your questions to {state.online()}"
    text = (
        f"{dictate} {name}, the voice assistant, is off: {why}. To turn it on,"
        f" right-click the tray icon → Turn on {name}…"
    )
    if len(text) > 255:
        text = f"{dictate} {name} is off. To turn it on, right-click the tray icon → Turn on {name}…"
    return text


def kai_consent_text(name: str, hops, config: dict) -> str:
    """The Yes/No question before Kai is turned on with online services:
    which service receives what."""
    from voice_keyboard.assistant.locality import _and, hop_payload

    lines = [f"{name}, the voice assistant, would use online services:", ""]
    companies: list = []
    for hop in hops:
        role = hop.role[:1].upper() + hop.role[1:]
        lines.append(f"• {role}: {hop.service} receives {hop_payload(hop, config, 'win32')}")
        company = hop.company or hop.service
        if company not in companies:
            companies.append(company)
    lines += [
        "",
        f"To keep {name} on this PC instead, use a local speech server and a local model.",
        "",
        f"Turn on {name} and send these to {_and(companies)}?",
    ]
    return "\n".join(lines)


def _version() -> str:
    try:
        from importlib.metadata import version

        return version("voice-keyboard")
    except Exception:
        return ""


def _config_path() -> Path:
    return paths.config_dir() / "config.toml"


# --------------------------------------------------------- process setup

def _set_dpi_awareness() -> None:  # pragma: no cover - requires Windows
    """Per-monitor DPI awareness, before any window exists: crisp text and
    real pixel coordinates (the caret anchor) on scaled displays."""
    try:
        user32 = ctypes.WinDLL("user32")  # type: ignore[attr-defined]
        user32.SetProcessDpiAwarenessContext.argtypes = [ctypes.c_void_p]
        if user32.SetProcessDpiAwarenessContext(ctypes.c_void_p(-4)):  # PER_MONITOR_AWARE_V2
            return
    except (OSError, AttributeError):
        pass
    try:
        ctypes.WinDLL("shcore").SetProcessDpiAwareness(2)  # type: ignore[attr-defined]
        return
    except (OSError, AttributeError):
        pass
    try:
        ctypes.WinDLL("user32").SetProcessDPIAware()  # type: ignore[attr-defined]
    except (OSError, AttributeError):
        pass


def _set_app_id() -> None:  # pragma: no cover - requires Windows
    try:
        shell32 = ctypes.WinDLL("shell32")  # type: ignore[attr-defined]
        shell32.SetCurrentProcessExplicitAppUserModelID.argtypes = [ctypes.c_wchar_p]
        shell32.SetCurrentProcessExplicitAppUserModelID(APP_ID)
    except (OSError, AttributeError):
        pass


class SingleInstance:  # pragma: no cover - requires Windows
    """A per-session named mutex: one daemon per user, the second launch
    explains instead of fighting over the IPC port."""

    def __init__(self, name: str = MUTEX_NAME):
        self._name = name
        self._handle = None

    def acquire(self, wait_s: float = 0.0) -> bool:
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)  # type: ignore[attr-defined]
        kernel32.CreateMutexW.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_wchar_p]
        kernel32.CreateMutexW.restype = ctypes.c_void_p
        kernel32.WaitForSingleObject.argtypes = [ctypes.c_void_p, ctypes.c_uint32]
        kernel32.WaitForSingleObject.restype = ctypes.c_uint32
        handle = kernel32.CreateMutexW(None, True, self._name)
        if not handle:
            return False
        if ctypes.get_last_error() == ERROR_ALREADY_EXISTS:
            result = kernel32.WaitForSingleObject(handle, int(wait_s * 1000))
            if result not in (0x00000000, 0x00000080):  # WAIT_OBJECT_0 / WAIT_ABANDONED
                kernel32.CloseHandle(ctypes.c_void_p(handle))
                return False
        self._handle = handle
        return True

    def release(self) -> None:
        if self._handle is None:
            return
        kernel32 = ctypes.WinDLL("kernel32")  # type: ignore[attr-defined]
        kernel32.ReleaseMutex(ctypes.c_void_p(self._handle))
        kernel32.CloseHandle(ctypes.c_void_p(self._handle))
        self._handle = None


def _setup_logging(console: bool) -> Path:
    log_dir = paths.log_dir()
    log_dir.mkdir(parents=True, exist_ok=True)
    log_path = log_dir / "daemon.log"
    root = logging.getLogger()
    root.setLevel(logging.INFO)
    formatter = logging.Formatter("%(asctime)s [%(levelname)s] %(name)s: %(message)s")
    file_handler = logging.handlers.RotatingFileHandler(
        log_path, maxBytes=1_000_000, backupCount=3, encoding="utf-8"
    )
    file_handler.setFormatter(formatter)
    root.addHandler(file_handler)
    if console and sys.stderr is not None:
        stream = logging.StreamHandler(sys.stderr)
        stream.setFormatter(formatter)
        root.addHandler(stream)

    # pythonw has no stderr: route uncaught errors from any thread to the log.
    def _log_uncaught(exc_type, exc, tb) -> None:
        logging.getLogger("voice_keyboard").critical(
            "Uncaught exception", exc_info=(exc_type, exc, tb)
        )

    def _log_thread(hook_args) -> None:
        logging.getLogger("voice_keyboard").error(
            "Uncaught exception in thread %s",
            getattr(hook_args.thread, "name", "?"),
            exc_info=(hook_args.exc_type, hook_args.exc_value, hook_args.exc_traceback),
        )

    sys.excepthook = _log_uncaught
    threading.excepthook = _log_thread
    return log_path


# ------------------------------------------------------------ autostart

def _gui_python() -> str:
    """pythonw.exe beside the running interpreter (no console window)."""
    exe = Path(sys.executable)
    if exe.name.lower() == "pythonw.exe":
        return str(exe)
    candidate = exe.with_name("pythonw.exe")
    return str(candidate if candidate.exists() else exe)


def _console_python() -> str:
    exe = Path(sys.executable)
    if exe.name.lower() == "pythonw.exe":
        candidate = exe.with_name("python.exe")
        if candidate.exists():
            return str(candidate)
    return str(exe)


def autostart_command() -> str:
    return f'"{_gui_python()}" -m voice_keyboard.windows'


def get_autostart() -> bool:  # pragma: no cover - requires Windows
    import winreg

    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY) as key:
            winreg.QueryValueEx(key, RUN_VALUE)
            return True
    except OSError:
        return False


def set_autostart(enabled: bool) -> None:  # pragma: no cover - requires Windows
    import winreg

    with winreg.CreateKey(winreg.HKEY_CURRENT_USER, RUN_KEY) as key:
        if enabled:
            winreg.SetValueEx(key, RUN_VALUE, 0, winreg.REG_SZ, autostart_command())
        else:
            try:
                winreg.DeleteValue(key, RUN_VALUE)
            except FileNotFoundError:
                pass


# ------------------------------------------------------------------ app

class WindowsApp:
    def __init__(self, *, console: bool = False):
        self._console = console
        self._daemon = None
        self._quit = threading.Event()
        self._restart = False
        self._shell = None

    # Callbacks for the shell (UI thread; must not block) ---------------

    def _status(self) -> dict:
        from voice_keyboard.assistant.locality import KaiState

        daemon = self._daemon
        if daemon is None:
            return {}
        status = {
            "recording": bool(getattr(daemon, "recording", False)),
            "conversing": bool(getattr(daemon, "conversing", False)),
            # Off unless the daemon says Kai is on.
            "assistant": bool(getattr(daemon, "assistant_enabled", False)),
        }
        state = getattr(daemon, "kai_state", None)
        if isinstance(state, KaiState):
            status.update(
                assistant_setting=state.setting,
                assistant_local=state.local,
                assistant_why=state.why(),
                assistant_hops=[
                    {"role": hop.role, "service": hop.service, "local": hop.local,
                     "section": hop.section}
                    for hop in state.hops
                ],
            )
        return status

    def _set_kai(self, on: bool) -> None:
        """The tray's Turn on Kai… / Turn off Kai: never on the UI thread
        (it may ask a question and writes the settings file)."""
        threading.Thread(
            target=self._apply_kai, args=(on,), name="vk-kai", daemon=True
        ).start()

    def _apply_kai(self, on: bool, *, confirm=None, alert=None) -> bool:
        """Turn Kai on or off: write [assistant] enabled, then apply it to
        the running daemon. Off applies at once, even if the file can't be
        written. On asks first (No by default) when Kai would use online
        services, now or after a restart. True when the file now says so."""
        from voice_keyboard.assistant import announce
        from voice_keyboard.assistant.locality import KaiState, kai_state
        from voice_keyboard.config import load_config, validate_config

        shell = self._shell
        if confirm is None:
            confirm = shell.confirm if shell is not None else (lambda title, body: False)
        if alert is None:
            alert = shell.alert if shell is not None else (lambda title, body: None)
        path = _config_path()
        daemon = self._daemon
        try:
            config = load_config(path)
            if on:
                # A file the daemon refuses can't turn Kai on: say so now.
                validate_config(config)
        except Exception as exc:
            config = None
            if on:
                alert("Kai stays off", f"The settings file has an error: {exc}\n\nFix it first.")
                return False

        def with_enabled(value) -> KaiState:
            if config is None:
                return KaiState(on=False, setting="off", local=False)
            return kai_state({**config, "assistant": {**config.get("assistant", {}), "enabled": value}})

        if not on:
            off = with_enabled(False)
            announce.write_record(off)
            written = self._write_kai(path, False, alert, name=off.name)
            if daemon is not None:
                daemon.kai_off()  # off now, whatever the file write did
            return written

        file_auto, file_on = with_enabled("auto"), with_enabled(True)
        name = file_on.name
        running = getattr(daemon, "kai_state", None) if daemon is not None else None
        if not isinstance(running, KaiState):
            running = None
        if not file_on.can_answer:
            alert(f"{name} stays off", f"{name} has no language model to answer with: set [llm] in the settings file first.")
            return False
        if file_auto.local and (running is None or running.local):
            value, state = "auto", file_auto
        else:
            # Every online service it would use: the running daemon's
            # (speech settings change only at a restart) and the file's.
            hops = list(file_on.online_hops())
            seen = {(hop.role, hop.service) for hop in hops}
            for hop in running.online_hops() if running is not None else ():
                if (hop.role, hop.service) not in seen:
                    hops.append(hop)
                    seen.add((hop.role, hop.service))
            if not confirm(f"Turn on {name}?", kai_consent_text(name, hops, config)):
                return False
            value, state = True, file_on
        announce.write_record(state)
        if not self._write_kai(path, value, alert, name=name):
            return False
        if daemon is not None:
            daemon.reload_config()
            if not getattr(daemon, "assistant_enabled", False):
                now = getattr(daemon, "kai_state", None)
                why = now.why() if isinstance(now, KaiState) else "off"
                alert(
                    f"{name} is still off",
                    f"The running {APP_NAME} still has {name} {why}.\n\nSpeech settings"
                    " change at a restart: right-click the tray icon → Restart.",
                )
        return True

    def _write_kai(self, path: Path, value, alert, *, name: str = "Kai") -> bool:
        from voice_keyboard.config import read_config_text, set_kai_enabled, write_config_text

        shown = {True: "true", False: "false", "auto": '"auto"'}[value]
        try:
            text = read_config_text(path) if path.exists() else STARTER_CONFIG
            write_config_text(path, set_kai_enabled(text, value))
        except ValueError as exc:
            alert(f"{name}'s setting wasn't changed", f"{exc}\n\nIn {path}, set [assistant] enabled = {shown}.")
            return False
        except OSError as exc:
            after = f"\n\n{name} is off until {APP_NAME} restarts." if value is False else ""
            alert(
                "Can't write the settings file",
                f"Can't write {path} ({exc}): set [assistant] enabled = {shown} where your"
                f" config comes from.{after}",
            )
            return False
        return True

    def _action(self, name: str):
        def run() -> None:
            daemon = self._daemon
            if daemon is not None:
                daemon.schedule_action(name)

        return run

    def _open_settings(self) -> None:
        from voice_keyboard.windows.shell import open_in_editor

        path = _config_path()
        if not path.exists():
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(STARTER_CONFIG, encoding="utf-8")
        open_in_editor(str(path))

    def _open_logs(self) -> None:
        from voice_keyboard.windows.shell import open_folder

        open_folder(str(paths.log_dir()))

    def _open_help(self) -> None:
        import webbrowser

        webbrowser.open(DOCS_URL)

    def _sign_in(self) -> None:
        """`voice-keyboard login` in its own console window, kept open at
        the end so the result can be read."""
        script = (
            "import sys\n"
            "sys.argv = ['voice-keyboard', 'login']\n"
            "from voice_keyboard.client import main\n"
            "try:\n    main()\nexcept SystemExit:\n    pass\n"
            "input('\\nPress Enter to close this window...')\n"
        )
        subprocess.Popen(
            [_console_python(), "-c", script],
            creationflags=CREATE_NEW_CONSOLE,
            close_fds=True,
        )

    def _request_restart(self) -> None:
        self._restart = True
        daemon = self._daemon
        if daemon is not None:
            daemon.request_stop()

    def _request_quit(self) -> None:
        self._quit.set()
        daemon = self._daemon
        if daemon is not None:
            daemon.request_stop()

    def _set_autostart(self, enabled: bool) -> None:
        set_autostart(enabled)

    # Lifecycle ----------------------------------------------------------

    def _make_shell(self, config: dict):
        from voice_keyboard.windows.shell import ShellCallbacks, WinShell

        callbacks = ShellCallbacks(
            toggle_dictation=self._action("toggle"),
            summon=self._action("converse_toggle"),
            read_clipboard=self._action("read_clipboard"),
            open_settings=self._open_settings,
            open_logs=self._open_logs,
            sign_in=self._sign_in,
            restart=self._request_restart,
            quit=self._request_quit,
            get_autostart=get_autostart,
            set_autostart=self._set_autostart,
            open_help=self._open_help,
            status=self._status,
            set_kai=self._set_kai,
        )
        return WinShell(
            callbacks,
            app_name=APP_NAME,
            version=_version(),
            orb_state_path=str(paths.state_dir() / "windows-shell.json"),
            **hotkey_labels(config),
        )

    def _load(self) -> tuple[Optional[dict], str]:
        """(config, "") when the daemon can start, else (config|None, why)."""
        from voice_keyboard.config import load_config, validate_config

        if not _config_path().exists():
            return None, "No settings yet — add a speech key or your own server (subscribers: sign in)"
        try:
            config = load_config()
        except Exception as exc:
            return None, f"Settings file has an error: {exc}"
        try:
            validate_config(config)
        except Exception as exc:
            message = str(exc)
            if "api_key is not configured" in message:
                message = "Add your speech provider API key to get started"
            return config, message
        return config, ""

    def _wait_for_settings(self, reason: str, first: bool) -> None:
        """Setup mode: amber icon + menu; return once the config file
        changes (or Quit)."""
        logger.warning("Waiting for settings: %s", reason)
        if self._shell is not None:
            self._shell.set_setup_mode(reason)
            if first:
                self._shell.notify(
                    f"{APP_NAME} needs setup",
                    f"{reason}. Right-click the tray icon to open settings.",
                )
        path = _config_path()

        def stamp():
            try:
                stat = path.stat()
                return (stat.st_mtime_ns, stat.st_size)
            except OSError:
                return None

        before = stamp()
        while not self._quit.is_set():
            if self._quit.wait(1.0):
                return
            if stamp() != before:
                time.sleep(0.3)  # let the editor finish writing
                return

    def _run_daemon(self, config: dict) -> Optional[str]:
        """Run the daemon until it stops. Returns an error message if it
        failed to start, None after a clean stop."""
        from voice_keyboard.daemon import Daemon

        for attempt in range(6):
            try:
                daemon = Daemon(config=config)
                self._daemon = daemon
                if self._quit.is_set() or self._restart:
                    # Quit/Restart clicked while it was being built.
                    daemon.request_stop()
                asyncio.run(daemon.run())
                return None
            except RuntimeError as exc:
                if "already listening" in str(exc) and attempt < 5 and not self._quit.is_set():
                    time.sleep(1.0)  # a previous instance still releasing the port
                    continue
                logger.exception("Daemon failed")
                return str(exc)
            except Exception as exc:
                logger.exception("Daemon failed")
                return str(exc) or exc.__class__.__name__
            finally:
                self._daemon = None
        return "could not start"

    def _welcome(self, config: dict) -> None:
        from voice_keyboard.assistant import announce
        from voice_keyboard.assistant.locality import kai_state

        marker = paths.state_dir() / "welcomed"
        if marker.exists() or self._shell is None:
            return
        state = kai_state(config)
        self._shell.notify(f"{APP_NAME} is running", welcome_text(config, state))
        # It said where Kai stands: the daemon needn't say it again.
        announce.write_record(state)
        try:
            marker.parent.mkdir(parents=True, exist_ok=True)
            marker.write_text("1", encoding="utf-8")
        except OSError:
            pass

    def run(self) -> int:
        from voice_keyboard import client

        config, reason = self._load()
        self._shell = self._make_shell(config or {})
        if self._shell.start():
            client.register_local_shell(self._shell)
        else:
            logger.warning("Windows shell unavailable; running without overlay/tray")
            self._shell = None
        first_wait = True
        try:
            while not self._quit.is_set():
                config, reason = self._load()
                if reason:
                    self._wait_for_settings(reason, first_wait)
                    first_wait = False
                    continue
                if self._shell is not None:
                    self._shell.set_setup_mode("")
                    labels = hotkey_labels(config)
                    self._shell.set_labels(
                        dictation=labels["dictation_hotkey"],
                        assistant=labels["assistant_hotkey"],
                        read=labels["read_hotkey"],
                    )
                self._welcome(config)
                self._restart = False
                error = self._run_daemon(config)
                if self._quit.is_set():
                    break
                if self._restart:
                    logger.info("Restarting the daemon")
                    continue
                if error:
                    self._wait_for_settings(f"Couldn't start: {error}"[:120], True)
                    continue
                break  # stopped cleanly (IPC `quit`, Ctrl+C)
        except KeyboardInterrupt:
            pass
        finally:
            client.register_local_shell(None)
            if self._shell is not None:
                self._shell.stop()
        return 0


def main(argv: Optional[list] = None) -> int:
    parser = argparse.ArgumentParser(
        prog="voice-keyboard-daemon",
        description=f"{APP_NAME} for Windows: the daemon with its tray icon and overlay",
    )
    parser.add_argument("--console", action="store_true",
                        help="also log to this terminal")
    parser.add_argument("--wait", type=float, default=0.0,
                        help="seconds to wait for a previous instance to exit")
    args = parser.parse_args(argv)

    if sys.platform != "win32":
        print("voice_keyboard.windows is the Windows app; on Linux run voice-keyboard-daemon",
              file=sys.stderr)
        return 2

    _set_dpi_awareness()
    _set_app_id()
    try:
        moved, failed = paths.migrate_windows_beta()
    except Exception as exc:
        moved, failed = [], [f"Could not bring over the beta's files: {exc}"]
    log_path = _setup_logging(console=args.console)
    logger.info("%s %s starting (log: %s)", APP_NAME, _version(), log_path)
    for line in moved:
        logger.info("%s", line)
    for line in failed:
        logger.warning("%s (will retry at the next start)", line)

    instance = SingleInstance()
    if not instance.acquire(wait_s=args.wait):
        message = (
            f"{APP_NAME} is already running — look for its icon in the"
            " notification area (you may need to click ^ to see it)."
        )
        logger.info("Another instance is running; exiting")
        if args.console:
            print(message, file=sys.stderr)
        else:
            ctypes.WinDLL("user32").MessageBoxW(  # type: ignore[attr-defined]
                None, message, APP_NAME, MB_ICONINFORMATION
            )
        return 1
    try:
        return WindowsApp(console=args.console).run()
    finally:
        instance.release()
        logger.info("%s stopped", APP_NAME)
