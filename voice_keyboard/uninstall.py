"""Remove a user-local HyperFurion VK install.

Default: systemd unit, venv, CLI links, GNOME overlay copy.
`--purge` also deletes config and state.
`--system` also deletes the curl-installer's udev / modules-load files.
The input group is never removed automatically — it is shared.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path


OVERLAY_UUID = "voice-keyboard-overlay@liam-hennigan"
SERVICE_NAME = "voice-keyboard-daemon.service"
UDEV_RULE = Path("/etc/udev/rules.d/99-uinput.rules")
MODULES_LOAD = Path("/etc/modules-load.d/uinput.conf")
INSTALLER_UDEV_LINE = 'KERNEL=="uinput", GROUP="input", MODE="0660"'


def _home() -> Path:
    return Path.home()


def _xdg_config_home() -> Path:
    xdg = os.environ.get("XDG_CONFIG_HOME", "")
    return Path(xdg) if xdg else _home() / ".config"


def _xdg_state_home() -> Path:
    xdg = os.environ.get("XDG_STATE_HOME", "")
    return Path(xdg) if xdg else _home() / ".local" / "state"


def _xdg_data_home() -> Path:
    xdg = os.environ.get("XDG_DATA_HOME", "")
    return Path(xdg) if xdg else _home() / ".local" / "share"


def user_targets(*, purge: bool) -> list[Path]:
    data = _xdg_data_home()
    config = _xdg_config_home()
    paths = [
        config / "systemd" / "user" / SERVICE_NAME,
        _home() / ".local" / "bin" / "voice-keyboard",
        _home() / ".local" / "bin" / "voice-keyboard-daemon",
        data / "voice-keyboard-venv",
        data / "gnome-shell" / "extensions" / OVERLAY_UUID,
    ]
    if purge:
        paths.append(config / "voice-keyboard")
        paths.append(_xdg_state_home() / "voice-keyboard")
    return paths


def _run(argv: list[str]) -> None:
    try:
        subprocess.run(argv, check=False, capture_output=True, text=True, timeout=15)
    except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
        pass


def _stop_systemd() -> None:
    if not sys.platform.startswith("linux"):
        return
    _run(["systemctl", "--user", "disable", "--now", SERVICE_NAME])
    _run(["systemctl", "--user", "daemon-reload"])


def _remove_path(path: Path) -> bool:
    if not path.exists() and not path.is_symlink():
        return False
    if path.is_symlink() or path.is_file():
        path.unlink()
        return True
    shutil.rmtree(path)
    return True


def _system_files_look_like_ours(path: Path, expected: str) -> bool:
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return False
    return expected in text


def remove_system_files() -> list[str]:
    """Best-effort removal of installer udev/modules files. Needs sudo."""
    notes: list[str] = []
    for path, marker in (
        (UDEV_RULE, INSTALLER_UDEV_LINE),
        (MODULES_LOAD, "uinput"),
    ):
        if not path.exists():
            continue
        if not _system_files_look_like_ours(path, marker):
            notes.append(f"left {path} (contents do not match the installer)")
            continue
        argv = ["sudo", "rm", "-f", str(path)]
        try:
            result = subprocess.run(argv, check=False, capture_output=True, text=True, timeout=30)
        except (FileNotFoundError, subprocess.TimeoutExpired, OSError) as exc:
            notes.append(f"could not remove {path}: {exc}")
            continue
        if result.returncode == 0:
            notes.append(f"removed {path}")
        else:
            notes.append(
                f"could not remove {path}: {(result.stderr or result.stdout).strip() or 'sudo failed'}"
            )
    _run(["sudo", "udevadm", "control", "--reload-rules"])
    notes.append(
        "input group membership was left in place "
        "(sudo gpasswd -d \"$USER\" input  — only if nothing else needs it)"
    )
    return notes


def uninstall(*, purge: bool = False, system: bool = False) -> list[str]:
    log: list[str] = []
    _stop_systemd()
    log.append("stopped and disabled the user systemd unit (if present)")
    for path in user_targets(purge=purge):
        if _remove_path(path):
            log.append(f"removed {path}")
        else:
            log.append(f"already gone: {path}")
    if system:
        log.extend(remove_system_files())
    else:
        log.append("left udev/uinput/input-group system files (pass --system to remove them)")
    return log


def main(*, purge: bool = False, system: bool = False) -> int:
    print("Uninstalling HyperFurion VK (user-local" + (", purge" if purge else "") + ")...")
    for line in uninstall(purge=purge, system=system):
        print(f"  {line}")
    print("Done.")
    return 0
