"""Where config, state, and logs live on each platform.

Linux/macOS follow XDG (~/.config, ~/.local/state). Windows follows its own
conventions: config in %APPDATA% (roams with the profile, where users and
the installer expect it), state and logs in %LOCALAPPDATA% (machine-local:
the dictation ledger and memory never sync anywhere). An explicit
XDG_CONFIG_HOME / XDG_STATE_HOME still wins everywhere.
"""

import logging
import os
import shutil
import sys
from pathlib import Path

logger = logging.getLogger(__name__)

APP_DIR_NAME = "voice-keyboard"


def config_dir() -> Path:
    xdg = os.environ.get("XDG_CONFIG_HOME", "")
    if xdg:
        return Path(xdg) / APP_DIR_NAME
    legacy = Path.home() / ".config" / APP_DIR_NAME
    if sys.platform == "win32":
        appdata = os.environ.get("APPDATA", "")
        if appdata:
            preferred = Path(appdata) / APP_DIR_NAME
            # Early betas read ~/.config on Windows; keep honoring a config
            # left there until one exists in the proper place.
            if not (preferred / "config.toml").exists() and (legacy / "config.toml").exists():
                return legacy
            return preferred
    return legacy


def state_dir() -> Path:
    xdg = os.environ.get("XDG_STATE_HOME", "")
    if xdg:
        return Path(xdg) / APP_DIR_NAME
    if sys.platform == "win32":
        local = os.environ.get("LOCALAPPDATA", "")
        if local:
            return Path(local) / APP_DIR_NAME
    return Path.home() / ".local" / "state" / APP_DIR_NAME


def log_dir() -> Path:
    return state_dir() / "logs"


def migrate_windows_beta() -> list[str]:
    """Windows: bring over what an early beta kept under ~/.config and
    ~/.local/state. Copies (never overwrites, never deletes) and runs once;
    the installer and the app both call it. Returns what it did."""
    if sys.platform != "win32":
        return []
    marker = state_dir() / ".beta-migrated"
    if marker.exists():
        return []
    done: list[str] = []
    home = Path.home()
    legacy_config = home / ".config" / APP_DIR_NAME / "config.toml"
    appdata = os.environ.get("APPDATA", "")
    if appdata and not os.environ.get("XDG_CONFIG_HOME") and legacy_config.is_file():
        target = Path(appdata) / APP_DIR_NAME / "config.toml"
        if not target.exists():
            try:
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(legacy_config, target)
                done.append(r"Moved your beta settings into %APPDATA%\voice-keyboard")
            except OSError:
                logger.warning("Could not move the beta's settings", exc_info=True)
    legacy_state = home / ".local" / "state" / APP_DIR_NAME
    local = os.environ.get("LOCALAPPDATA", "")
    if local and not os.environ.get("XDG_STATE_HOME") and legacy_state.is_dir():
        target_dir = Path(local) / APP_DIR_NAME
        copied = 0
        for item in legacy_state.iterdir():
            dest = target_dir / item.name
            if dest.exists():
                continue
            try:
                target_dir.mkdir(parents=True, exist_ok=True)
                if item.is_dir():
                    shutil.copytree(item, dest)
                else:
                    shutil.copy2(item, dest)
                copied += 1
            except OSError:
                logger.warning("Could not move %s from the beta", item.name, exc_info=True)
        if copied:
            done.append(r"Moved your beta history and dictionary into %LOCALAPPDATA%\voice-keyboard")
    try:
        marker.parent.mkdir(parents=True, exist_ok=True)
        marker.write_text("", encoding="utf-8")
    except OSError:
        pass
    return done
