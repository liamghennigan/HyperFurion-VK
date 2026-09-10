"""Preflight checks for a first-run Linux install.

`voice-keyboard doctor` does not need the daemon. Each check prints
OK / WARN / FAIL plus a one-line fix. Exit status is 1 if any check fails.
"""

from __future__ import annotations

import inspect
import os
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Iterable, Literal

from voice_keyboard.config import _config_dir, load_config, validate_config
from voice_keyboard.ipc import IPCClient

Status = Literal["OK", "WARN", "FAIL"]


@dataclass(frozen=True)
class Check:
    name: str
    status: Status
    detail: str
    fix: str = ""


def _linux() -> bool:
    return sys.platform.startswith("linux")


def _run(argv: list[str], timeout: float = 4.0) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        argv,
        capture_output=True,
        text=True,
        timeout=timeout,
        check=False,
    )


def check_cli_on_path() -> Check:
    which = shutil.which("voice-keyboard")
    local_bin = Path.home() / ".local" / "bin"
    local_cli = local_bin / "voice-keyboard"
    path_dirs = os.environ.get("PATH", "").split(os.pathsep)
    local_on_path = str(local_bin) in path_dirs
    if which:
        return Check("cli", "OK", f"on PATH at {which}")
    if local_cli.is_file() and not local_on_path:
        return Check(
            "cli",
            "FAIL",
            f"{local_cli} exists but ~/.local/bin is not on PATH",
            'export PATH="$HOME/.local/bin:$PATH"  # then open a new shell',
        )
    return Check(
        "cli",
        "FAIL",
        "voice-keyboard is not on PATH",
        "re-run install.sh, then put ~/.local/bin on PATH",
    )


def check_config(config: dict | None = None) -> Check:
    path = _config_dir() / "config.toml"
    if not path.is_file():
        return Check(
            "config",
            "FAIL",
            f"missing {path}",
            "run install.sh or copy config.toml.example to that path",
        )
    try:
        cfg = config if config is not None else load_config()
        validate_config(cfg)
    except RuntimeError as exc:
        message = str(exc)
        fix = f"edit {path}"
        if "api_key is not configured" in message:
            fix = (
                f"set a real API key in {path}, or point "
                "providers.openai.base_url at a local server"
            )
        return Check("config", "FAIL", message, fix)
    except (OSError, ValueError) as exc:
        return Check("config", "FAIL", f"cannot read {path}: {exc}", f"fix or recreate {path}")
    stt = str(cfg.get("stt", {}).get("provider", "?"))
    tts = str(cfg.get("tts", {}).get("provider", "?"))
    return Check("config", "OK", f"{path} (stt={stt}, tts={tts})")


def check_systemd() -> Check:
    if not _linux():
        return Check("systemd", "WARN", "systemd user unit is Linux-only")
    try:
        enabled = _run(["systemctl", "--user", "is-enabled", "voice-keyboard-daemon.service"])
        active = _run(["systemctl", "--user", "is-active", "voice-keyboard-daemon.service"])
    except (FileNotFoundError, subprocess.TimeoutExpired, OSError) as exc:
        return Check(
            "systemd",
            "FAIL",
            f"systemctl unavailable: {exc}",
            "install systemd user services, then re-run install.sh",
        )
    enabled_ok = enabled.returncode == 0 and enabled.stdout.strip() == "enabled"
    active_ok = active.returncode == 0 and active.stdout.strip() == "active"
    if enabled_ok and active_ok:
        return Check("systemd", "OK", "voice-keyboard-daemon.service enabled and active")
    state = f"enabled={enabled.stdout.strip() or 'no'}, active={active.stdout.strip() or 'no'}"
    return Check(
        "systemd",
        "FAIL",
        state,
        "systemctl --user enable --now voice-keyboard-daemon",
    )


def check_daemon(config: dict | None = None) -> Check:
    cfg = config if config is not None else load_config()
    socket_path = str(cfg.get("daemon", {}).get("socket_path", ""))
    try:
        response = IPCClient(socket_path).send_command("status", timeout=3.0)
    except Exception as exc:
        return Check(
            "daemon",
            "FAIL",
            f"not reachable ({exc})",
            "systemctl --user start voice-keyboard-daemon  # then journalctl --user -u voice-keyboard-daemon -n 50",
        )
    if response.get("status") == "ok" or "recording" in response:
        state = "recording" if response.get("recording") else "idle"
        return Check("daemon", "OK", f"{state} via {socket_path}")
    return Check(
        "daemon",
        "FAIL",
        response.get("message", "unexpected status reply"),
        "journalctl --user -u voice-keyboard-daemon -n 50",
    )


def check_uinput() -> Check:
    if not _linux():
        return Check("uinput", "WARN", "uinput is Linux-only")
    path = Path("/dev/uinput")
    if not path.exists():
        return Check(
            "uinput",
            "FAIL",
            "/dev/uinput is missing",
            "sudo modprobe uinput",
        )
    if not os.access(path, os.W_OK):
        return Check(
            "uinput",
            "FAIL",
            "/dev/uinput exists but is not writable",
            "join the input group, log out and back in, then retry",
        )
    return Check("uinput", "OK", f"{path} is writable")


def _input_group_gid() -> int | None:
    try:
        import grp

        return grp.getgrnam("input").gr_gid
    except (ImportError, KeyError, OSError):
        return None


def _input_group_members() -> set[str]:
    try:
        import grp

        return set(grp.getgrnam("input").gr_mem)
    except (ImportError, KeyError, OSError):
        return set()


def check_input_group() -> Check:
    if not _linux():
        return Check("input-group", "WARN", "input group is Linux-only")
    user = os.environ.get("USER") or os.environ.get("LOGNAME") or ""
    try:
        import pwd

        user = user or pwd.getpwuid(os.getuid()).pw_name
    except (ImportError, KeyError, OSError):
        pass
    gid = _input_group_gid()
    if gid is None:
        return Check(
            "input-group",
            "FAIL",
            "no input group on this system",
            "install.sh creates the udev rule that expects an input group",
        )
    configured = user in _input_group_members()
    try:
        import pwd

        configured = configured or pwd.getpwuid(os.getuid()).pw_gid == gid
    except (ImportError, KeyError, OSError):
        pass
    effective = gid in os.getgroups()
    if configured and effective:
        return Check("input-group", "OK", f"{user} is in input (effective)")
    if configured and not effective:
        return Check(
            "input-group",
            "FAIL",
            f"{user} is in input, but this session does not have it yet",
            "log out and back in so the input group applies",
        )
    return Check(
        "input-group",
        "FAIL",
        f"{user} is not in the input group",
        f"sudo usermod -aG input {user}  # then log out and back in",
    )


def check_input_devices() -> Check:
    if not _linux():
        return Check("hotkey-devices", "WARN", "evdev hotkeys are Linux-only")
    root = Path("/dev/input")
    events = sorted(root.glob("event*")) if root.is_dir() else []
    readable = [path for path in events if os.access(path, os.R_OK)]
    if readable:
        return Check("hotkey-devices", "OK", f"{len(readable)} readable /dev/input/event*")
    if events:
        return Check(
            "hotkey-devices",
            "FAIL",
            f"{len(events)} event devices, none readable",
            "log out and back in after joining the input group",
        )
    return Check(
        "hotkey-devices",
        "FAIL",
        "no /dev/input/event* devices",
        "check that a keyboard is attached and udev is running",
    )


def check_microphone() -> Check:
    try:
        import pyaudio
    except ImportError:
        return Check(
            "microphone",
            "FAIL",
            "PyAudio is not installed in this environment",
            "re-run install.sh so the venv gets pyaudio",
        )
    pa = pyaudio.PyAudio()
    try:
        names: list[str] = []
        for index in range(pa.get_device_count()):
            info = pa.get_device_info_by_index(index)
            if int(info.get("maxInputChannels", 0) or 0) > 0:
                names.append(str(info.get("name") or f"device {index}"))
    except Exception as exc:
        return Check(
            "microphone",
            "FAIL",
            f"PortAudio probe failed: {exc}",
            "install portaudio and check audio.device_name in config",
        )
    finally:
        try:
            pa.terminate()
        except Exception:
            pass
    if not names:
        return Check(
            "microphone",
            "FAIL",
            "no PortAudio input devices",
            "plug in a mic or set [audio] device_name to a real device",
        )
    shown = ", ".join(names[:3])
    extra = f" (+{len(names) - 3} more)" if len(names) > 3 else ""
    return Check("microphone", "OK", f"{len(names)} input(s): {shown}{extra}")


def check_clipboard() -> Check:
    if sys.platform == "darwin":
        if shutil.which("pbcopy") and shutil.which("pbpaste"):
            return Check("clipboard", "OK", "pbcopy/pbpaste")
        return Check("clipboard", "WARN", "pbcopy/pbpaste missing")
    if sys.platform == "win32":
        return Check("clipboard", "OK", "Windows clipboard API")
    if shutil.which("wl-copy") or shutil.which("xclip"):
        tools = [name for name in ("wl-copy", "wl-paste", "xclip") if shutil.which(name)]
        return Check("clipboard", "OK", ", ".join(tools))
    return Check(
        "clipboard",
        "FAIL",
        "no clipboard tool (needed for non-ASCII paste injection)",
        "sudo apt install wl-clipboard xclip  # or the dnf/pacman equivalent",
    )


def check_atspi() -> Check:
    if not _linux():
        return Check("at-spi", "WARN", "AT-SPI focus probe is Linux-only")
    python = "/usr/bin/python3"
    if not Path(python).is_file():
        return Check(
            "at-spi",
            "FAIL",
            f"{python} is missing (focus probe uses the system interpreter)",
            "install python3 plus python3-gi and gir1.2-atspi-2.0",
        )
    try:
        result = _run(
            [
                python,
                "-c",
                "import gi; gi.require_version('Atspi', '2.0'); from gi.repository import Atspi",
            ]
        )
    except (FileNotFoundError, subprocess.TimeoutExpired, OSError) as exc:
        return Check("at-spi", "FAIL", str(exc), "install python3-gi gir1.2-atspi-2.0 at-spi2-core")
    if result.returncode != 0:
        err = (result.stderr or result.stdout or "import failed").strip().splitlines()
        detail = err[-1] if err else "system python cannot import Atspi"
        return Check(
            "at-spi",
            "FAIL",
            detail,
            "sudo apt install python3-gi gir1.2-atspi-2.0 at-spi2-core",
        )
    return Check("at-spi", "OK", f"{python} can import gi.repository.Atspi")


def check_overlay() -> Check:
    if not _linux():
        return Check("overlay", "WARN", "GNOME overlay is Linux-only")
    desktop = os.environ.get("XDG_CURRENT_DESKTOP", "")
    gnome = "GNOME" in desktop.upper()
    try:
        result = _run(
            [
                "gdbus",
                "call",
                "--session",
                "--dest",
                "org.voicekeyboard.Overlay",
                "--object-path",
                "/org/voicekeyboard/Overlay",
                "--method",
                "org.freedesktop.DBus.Peer.Ping",
            ],
            timeout=2.0,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
        result = None
    if result is not None and result.returncode == 0:
        return Check("overlay", "OK", "org.voicekeyboard.Overlay is on the session bus")
    if gnome:
        return Check(
            "overlay",
            "WARN",
            "GNOME session, but the overlay D-Bus name is not loaded",
            "log out and back in so GNOME loads voice-keyboard-overlay",
        )
    return Check(
        "overlay",
        "WARN",
        "overlay is GNOME Shell 50 Wayland only; this session uses notifications",
        "install notify-send (libnotify) for status toasts",
    )


def check_notify_send() -> Check:
    if not _linux():
        return Check("notify-send", "WARN", "notify-send fallback is Linux-only")
    path = shutil.which("notify-send")
    if path:
        return Check("notify-send", "OK", path)
    return Check(
        "notify-send",
        "FAIL",
        "notify-send is not on PATH",
        "sudo apt install libnotify-bin",
    )


DEFAULT_CHECKS: tuple[Callable[..., Check], ...] = (
    check_cli_on_path,
    check_config,
    check_systemd,
    check_daemon,
    check_uinput,
    check_input_group,
    check_input_devices,
    check_microphone,
    check_clipboard,
    check_atspi,
    check_overlay,
    check_notify_send,
)


def run_checks(
    config: dict | None = None,
    checks: Iterable[Callable[..., Check]] | None = None,
) -> list[Check]:
    results: list[Check] = []
    for fn in checks if checks is not None else DEFAULT_CHECKS:
        try:
            if "config" in inspect.signature(fn).parameters:
                results.append(fn(config))
            else:
                results.append(fn())
        except Exception as exc:
            results.append(Check(fn.__name__, "FAIL", f"check crashed: {exc}"))
    return results


def format_report(results: list[Check]) -> str:
    width = max((len(item.name) for item in results), default=4)
    lines = ["voice-keyboard doctor"]
    for item in results:
        suffix = item.detail
        if item.fix and item.status != "OK":
            suffix = f"{item.detail}  → {item.fix}"
        lines.append(f"  {item.status:<4}  {item.name:<{width}}  {suffix}")
    fails = sum(1 for item in results if item.status == "FAIL")
    warns = sum(1 for item in results if item.status == "WARN")
    oks = sum(1 for item in results if item.status == "OK")
    lines.append(f"{oks} ok, {warns} warning(s), {fails} failed")
    return "\n".join(lines)


def main() -> int:
    try:
        config = load_config()
    except Exception:
        config = None
    results = run_checks(config)
    print(format_report(results))
    return 1 if any(item.status == "FAIL" for item in results) else 0
