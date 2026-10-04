"""Where config, state, and logs live on each platform.

Linux/macOS follow XDG (~/.config, ~/.local/state). Windows follows its own
conventions: config in %APPDATA% (roams with the profile, where users and
the installer expect it), state and logs in %LOCALAPPDATA% (machine-local:
the dictation ledger and memory never sync anywhere). An explicit
XDG_CONFIG_HOME / XDG_STATE_HOME still wins everywhere.
"""

import os
import sys
from pathlib import Path

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
