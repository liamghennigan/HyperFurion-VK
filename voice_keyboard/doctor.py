"""`voice-keyboard doctor`: what stands between you and dictation, and the fix.

Each check is independent and best-effort: a check that can't run says so
instead of failing the rest. Nothing here sends audio or text anywhere;
the only connection made is to the local daemon's control socket.
"""

import os
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Optional

OK, WARN, FAIL = "ok", "warn", "fail"
_MARK = {OK: "✓", WARN: "!", FAIL: "✗"}


@dataclass
class Finding:
    status: str
    label: str
    detail: str = ""
    fix: str = ""


def check_config(path: Path) -> Finding:
    from voice_keyboard.config import load_config, validate_config

    if not path.exists():
        return Finding(FAIL, "config", f"no {path}", "run `voice-keyboard setup`")
    try:
        validate_config(load_config(path))
    except Exception as exc:
        return Finding(FAIL, "config", str(exc), "run `voice-keyboard setup`, or fix the file")
    return Finding(OK, "config", str(path))


def check_audio(config: dict) -> Finding:
    try:
        from voice_keyboard.client import _list_input_devices

        devices = _list_input_devices()
    except ModuleNotFoundError:
        return Finding(FAIL, "microphone", "PyAudio is not installed",
                       "reinstall: pip install --force-reinstall voice-keyboard (needs libportaudio2)")
    except Exception as exc:
        return Finding(FAIL, "microphone", f"can't list audio inputs ({exc})",
                       "install PortAudio (libportaudio2) and check your sound settings")
    if not devices:
        return Finding(FAIL, "microphone", "no audio input devices", "plug in or enable a microphone")
    wanted = str(config.get("audio", {}).get("device_name", "") or "").strip()
    if wanted and wanted.lower() != "default":
        matches = [d for d in devices if wanted.lower() in d["name"].lower()]
        if len(matches) != 1:
            what = "matches nothing" if not matches else f"matches {len(matches)} devices"
            return Finding(FAIL, "microphone", f'[audio] device_name "{wanted}" {what}',
                           "pick one from `voice-keyboard devices`")
        return Finding(OK, "microphone", matches[0]["name"])
    default = next((d for d in devices if d["default"]), None)
    if default is None:
        return Finding(WARN, "microphone", "no system default input",
                       'set [audio] device_name (see `voice-keyboard devices`)')
    return Finding(OK, "microphone", default["name"])


def check_daemon(config: dict) -> Finding:
    try:
        from voice_keyboard.ipc import IPCClient

        response = IPCClient(config["daemon"]["socket_path"]).send_command("status", timeout=2.0)
    except Exception as exc:
        return Finding(FAIL, "daemon", f"not reachable ({exc.__class__.__name__})",
                       "start it: `systemctl --user start voice-keyboard-daemon` (or run voice-keyboard-daemon)")
    if response.get("status", "ok") != "ok":
        return Finding(FAIL, "daemon", str(response.get("message", "refused")), "restart the daemon")
    stt = response.get("stt_provider", "")
    return Finding(OK, "daemon", "running" + (f", speech: {stt}" if stt else ""))


def check_typing() -> Finding:
    if sys.platform == "win32":
        return Finding(OK, "typing", "SendInput (no setup needed)")
    if sys.platform == "darwin":
        return Finding(WARN, "typing", "needs Accessibility permission",
                       "System Settings → Privacy & Security → Accessibility: allow your terminal or the app")
    if not os.path.exists("/dev/uinput"):
        return Finding(FAIL, "typing", "/dev/uinput is missing", "sudo modprobe uinput")
    if not os.access("/dev/uinput", os.W_OK):
        return Finding(FAIL, "typing", "can't write /dev/uinput",
                       "add a udev rule and join its group: see the README's install section")
    return Finding(OK, "typing", "/dev/uinput is writable")


def check_clipboard() -> Finding:
    """Non-ASCII text (é, emoji) is pasted, so a clipboard tool matters."""
    if sys.platform in ("win32", "darwin"):
        return Finding(OK, "clipboard", "built in")
    wayland = os.environ.get("XDG_SESSION_TYPE") == "wayland" or bool(os.environ.get("WAYLAND_DISPLAY"))
    tool = "wl-copy" if wayland else "xclip"
    if shutil.which(tool):
        return Finding(OK, "clipboard", f"{tool} ({'Wayland' if wayland else 'X11'})")
    package = "wl-clipboard" if wayland else "xclip"
    return Finding(WARN, "clipboard", f"no {tool}: accents and emoji can't be typed",
                   f"install {package}")


def check_focus_probe() -> Finding:
    """AT-SPI lets the keyboard see which app has focus (registers, safety)."""
    if sys.platform != "linux":
        return Finding(OK, "focus", "built in")
    python = "/usr/bin/python3"
    if not os.path.exists(python):
        return Finding(WARN, "focus", "no /usr/bin/python3 for the AT-SPI probe",
                       "install python3 and python3-gi (gir1.2-atspi-2.0)")
    try:
        probe = subprocess.run(
            [python, "-c", "import gi; gi.require_version('Atspi', '2.0'); from gi.repository import Atspi"],
            capture_output=True, timeout=5,
        )
    except Exception as exc:
        return Finding(WARN, "focus", f"probe failed to start ({exc})", "")
    if probe.returncode != 0:
        return Finding(WARN, "focus", "AT-SPI bindings missing: every app is treated as prose",
                       "install python3-gi and gir1.2-atspi-2.0")
    return Finding(OK, "focus", "AT-SPI available")


def check_speech(config: dict) -> Finding:
    stt = config.get("stt", {})
    provider = str(stt.get("provider", "xai")).lower()
    base = str(config.get("providers", {}).get(provider, {}).get("base_url", "") or "")
    local = any(host in base for host in ("localhost", "127.0.0.1", "[::1]"))
    where = "local, nothing leaves the machine" if local else "cloud"
    return Finding(OK, "speech", f"{provider} ({where})")


def run(config_path: Path, config: Optional[dict]) -> list[Finding]:
    findings = [check_config(config_path)]
    checks: list[Callable[[], Finding]] = [check_typing, check_clipboard, check_focus_probe]
    if config is not None:
        checks = [lambda: check_speech(config), lambda: check_audio(config),
                  lambda: check_daemon(config)] + checks
    for check in checks:
        try:
            findings.append(check())
        except Exception as exc:  # a broken check never hides the others
            findings.append(Finding(WARN, getattr(check, "__name__", "check"), f"could not run ({exc})"))
    return findings


def render(findings: list[Finding]) -> str:
    width = max(len(f.label) for f in findings)
    lines = []
    for f in findings:
        line = f"{_MARK[f.status]} {f.label:<{width}}  {f.detail}"
        if f.fix and f.status != OK:
            line += f"\n  {'':<{width}}  → {f.fix}"
        lines.append(line)
    failed = sum(f.status == FAIL for f in findings)
    warned = sum(f.status == WARN for f in findings)
    if failed:
        lines.append(f"\n{failed} problem(s) to fix" + (f", {warned} warning(s)." if warned else "."))
    elif warned:
        lines.append(f"\nReady, with {warned} warning(s).")
    else:
        lines.append("\nEverything checks out.")
    return "\n".join(lines) + "\n"
