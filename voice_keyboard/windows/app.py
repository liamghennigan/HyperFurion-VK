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
# Easiest: the hosted service — run `voice-keyboard login you@example.com`
# (or right-click the tray icon > Sign in). Or use your own provider key:

[stt]
provider = "xai"        # xai | openai | groq | deepgram | assemblyai | hyperfurion

[tts]
provider = "xai"        # xai | openai | elevenlabs | hyperfurion

[providers.xai]
api_key = "xai-your-api-key-here"

# [providers.openai]
# api_key = "sk-..."
# base_url = "http://127.0.0.1:8000/v1"   # a local Whisper/Kokoro server: fully offline

[hotkey]
key = "control+alt+v"   # tap to toggle dictation, hold to talk
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
    """The tray's hotkey names for a config (WinShell keyword arguments)."""
    from voice_keyboard.hotkey import pretty_binding

    read_key = str(config.get("tts", {}).get("hotkey", "")).strip()
    return {
        "dictation_hotkey": pretty_binding(config.get("hotkey", {}).get("key", "control+alt+v")),
        "assistant_hotkey": pretty_binding(
            config.get("assistant", {}).get("hotkey", "rightctrl") or "rightctrl"
        ),
        "read_hotkey": pretty_binding(read_key) if read_key else "",
    }


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
        daemon = self._daemon
        if daemon is None:
            return {}
        return {
            "recording": bool(getattr(daemon, "recording", False)),
            "conversing": bool(getattr(daemon, "conversing", False)),
            "assistant": bool(getattr(daemon, "assistant_enabled", True)),
        }

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
            return None, "No settings yet — sign in, or add your provider API key"
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
                    f"{reason}. Right-click the tray icon to sign in or open settings.",
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
                self._daemon = Daemon(config=config)
                asyncio.run(self._daemon.run())
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
        marker = paths.state_dir() / "welcomed"
        if marker.exists() or self._shell is None:
            return
        from voice_keyboard.hotkey import pretty_binding

        key = pretty_binding(config.get("hotkey", {}).get("key", "control+alt+v"))
        kai = pretty_binding(config.get("assistant", {}).get("hotkey", "rightctrl") or "rightctrl")
        self._shell.notify(
            f"{APP_NAME} is running",
            f"Press {key} to dictate into any app. Hold {kai} to ask Kai."
            " Right-click the tray icon for settings.",
        )
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
        moved = paths.migrate_windows_beta()  # before logging creates the state dir
    except Exception:
        moved = []
    log_path = _setup_logging(console=args.console)
    logger.info("%s %s starting (log: %s)", APP_NAME, _version(), log_path)
    for line in moved:
        logger.info("%s", line)

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
