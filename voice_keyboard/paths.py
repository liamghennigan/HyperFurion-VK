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


def _copy_file(src: Path, dest: Path) -> None:
    """Copy through a temporary name, so a failure never leaves half a file
    under the real one. Plain reads and writes (shutil.copy2 needs
    CopyFile2, which some Windows layers such as Wine lack)."""
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_name(f".{dest.name}.migrating")
    try:
        with open(src, "rb") as fin, open(tmp, "wb") as fout:
            shutil.copyfileobj(fin, fout)
        os.replace(tmp, dest)
    except BaseException:
        try:
            tmp.unlink()
        except OSError:
            pass
        raise


def _copy_tree(src: Path, dest: Path) -> None:
    """A folder, built under a temporary name and renamed into place."""
    tmp = dest.with_name(f".{dest.name}.migrating")
    shutil.rmtree(tmp, ignore_errors=True)
    try:
        for root, _dirs, files in os.walk(src):
            folder = tmp / Path(root).relative_to(src)
            folder.mkdir(parents=True, exist_ok=True)
            for name in files:
                with open(Path(root) / name, "rb") as fin, open(folder / name, "wb") as fout:
                    shutil.copyfileobj(fin, fout)
        os.replace(tmp, dest)
    except BaseException:
        shutil.rmtree(tmp, ignore_errors=True)
        raise


def migrate_windows_beta() -> tuple[list[str], list[str]]:
    """Windows: bring over what an early beta kept under ~/.config and
    ~/.local/state. Copies, never deletes, and never overwrites real
    settings — only the beta installer's unfilled starter config, which is
    kept as config.toml.beta-starter. Done once: until every copy has
    succeeded (a failure is retried at the next start). The installer and
    the app both call it. Returns (what moved, what failed)."""
    if sys.platform != "win32":
        return [], []
    marker = state_dir() / ".beta-migrated"
    if marker.exists():
        return [], []
    moved: list[str] = []
    failed: list[str] = []
    home = Path.home()
    legacy_config = home / ".config" / APP_DIR_NAME / "config.toml"
    appdata = os.environ.get("APPDATA", "")
    if appdata and not os.environ.get("XDG_CONFIG_HOME") and legacy_config.is_file():
        from voice_keyboard.config import is_usable, lacks_credentials

        target = Path(appdata) / APP_DIR_NAME / "config.toml"
        try:
            if not target.exists():
                _copy_file(legacy_config, target)
                moved.append(r"Moved your beta settings into %APPDATA%\voice-keyboard")
            elif lacks_credentials(target) and is_usable(legacy_config):
                # The beta installer wrote a starter config here that its
                # daemon never read; the real settings are the legacy ones.
                backup = target.with_name("config.toml.beta-starter")
                if not backup.exists():
                    _copy_file(target, backup)
                _copy_file(legacy_config, target)
                moved.append(
                    r"Moved your beta settings into %APPDATA%\voice-keyboard"
                    " (its unused starter file is kept as config.toml.beta-starter)"
                )
        except OSError as exc:
            failed.append(f"Could not move the beta's settings: {exc}")
    legacy_state = home / ".local" / "state" / APP_DIR_NAME
    local = os.environ.get("LOCALAPPDATA", "")
    if local and not os.environ.get("XDG_STATE_HOME") and legacy_state.is_dir():
        target_dir = Path(local) / APP_DIR_NAME
        copied = 0
        for item in sorted(legacy_state.iterdir()):
            dest = target_dir / item.name
            if dest.exists() or item.name.endswith(".migrating"):
                continue
            try:
                if item.is_dir():
                    target_dir.mkdir(parents=True, exist_ok=True)
                    _copy_tree(item, dest)
                else:
                    _copy_file(item, dest)
                copied += 1
            except OSError as exc:
                failed.append(f"Could not move {item.name} from the beta: {exc}")
        if copied:
            moved.append(
                r"Moved your beta history and dictionary into %LOCALAPPDATA%\voice-keyboard"
            )
    if not failed:
        try:
            marker.parent.mkdir(parents=True, exist_ok=True)
            marker.write_text("", encoding="utf-8")
        except OSError:
            pass
    return moved, failed
