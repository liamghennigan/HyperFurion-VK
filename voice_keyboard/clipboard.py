"""Clipboard access for the Unicode paste fallback and safety dumps.

Linux (Wayland wl-clipboard / X11 xclip) is the load-bearing path: the
uinput injector routes non-ASCII text through the clipboard plus a paste
chord. macOS/Windows setters exist for the safety paths (dumping a
transcript to the clipboard when typing had to be frozen).

get_text() returns None when no tool worked (distinct from an empty
clipboard) so callers can tell "cannot save/restore" from "was empty".
"""

import logging
import os
import shutil
import subprocess
import sys

logger = logging.getLogger(__name__)

_TIMEOUT = 2.0


def _run(command: list[str], *, input_text: str | None = None):
    return subprocess.run(
        command,
        input=input_text,
        capture_output=True,
        text=True,
        timeout=_TIMEOUT,
        check=False,
    )


def _is_wayland() -> bool:
    return bool(os.environ.get("WAYLAND_DISPLAY"))


def available() -> bool:
    """True when a clipboard tool for the current session exists."""
    if sys.platform == "darwin":
        return shutil.which("pbcopy") is not None
    if sys.platform == "win32":
        return True  # PowerShell ships with Windows
    if _is_wayland() and shutil.which("wl-copy"):
        return True
    return shutil.which("xclip") is not None


def get_text() -> str | None:
    """Current clipboard text; "" for an empty clipboard, None on failure."""
    candidates: list[list[str]] = []
    if sys.platform == "darwin":
        candidates = [["pbpaste"]]
    elif sys.platform == "win32":
        candidates = [["powershell", "-NoProfile", "-Command", "Get-Clipboard -Raw"]]
    else:
        if _is_wayland() and shutil.which("wl-paste"):
            candidates.append(["wl-paste", "--no-newline"])
        if shutil.which("xclip"):
            candidates.append(["xclip", "-selection", "clipboard", "-o"])

    for command in candidates:
        try:
            result = _run(command)
        except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
            continue
        if result.returncode == 0:
            return result.stdout
        # wl-paste/xclip exit non-zero on an *empty* clipboard.
        stderr = (result.stderr or "").lower()
        if "empty" in stderr or "no selection" in stderr or "nothing" in stderr:
            return ""
    return None


def set_text(text: str) -> bool:
    """Put `text` on the clipboard; True on success."""
    candidates: list[list[str]] = []
    if sys.platform == "darwin":
        candidates = [["pbcopy"]]
    elif sys.platform == "win32":
        candidates = [
            ["powershell", "-NoProfile", "-Command", "Set-Clipboard -Value ([Console]::In.ReadToEnd())"]
        ]
    else:
        if _is_wayland() and shutil.which("wl-copy"):
            candidates.append(["wl-copy"])
        if shutil.which("xclip"):
            candidates.append(["xclip", "-selection", "clipboard"])

    for command in candidates:
        try:
            result = _run(command, input_text=text)
        except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
            continue
        if result.returncode == 0:
            return True
    return False
