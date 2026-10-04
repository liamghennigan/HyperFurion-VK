"""Clipboard access for the Unicode paste fallback and safety dumps.

Linux (Wayland wl-clipboard / X11 xclip) is the load-bearing path: the
uinput injector routes non-ASCII text through the clipboard plus a paste
chord. macOS/Windows setters exist for the safety paths (dumping a
transcript to the clipboard when typing had to be frozen); Windows talks
to the clipboard natively (voice_keyboard.windows.clipboard).

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
        return True  # native Win32 clipboard
    if _is_wayland() and shutil.which("wl-copy"):
        return True
    return shutil.which("xclip") is not None


def get_text() -> str | None:
    """Current clipboard text; "" for an empty clipboard, None on failure."""
    if sys.platform == "win32":
        from voice_keyboard.windows import clipboard as win_clipboard

        try:
            return win_clipboard.get_text()
        except Exception:
            logger.debug("Windows clipboard read failed", exc_info=True)
            return None
    candidates: list[list[str]] = []
    if sys.platform == "darwin":
        candidates = [["pbpaste"]]
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


def get_primary_text() -> str | None:
    """Current PRIMARY selection text (Linux only — the text currently
    highlighted); "" when empty, None off-Linux or on failure."""
    if sys.platform in {"darwin", "win32"}:
        return None
    candidates: list[list[str]] = []
    if _is_wayland() and shutil.which("wl-paste"):
        candidates.append(["wl-paste", "--primary", "--no-newline"])
    if shutil.which("xclip"):
        candidates.append(["xclip", "-selection", "primary", "-o"])
    for command in candidates:
        try:
            result = _run(command)
        except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
            continue
        if result.returncode == 0:
            return result.stdout
        stderr = (result.stderr or "").lower()
        if "empty" in stderr or "no selection" in stderr or "nothing" in stderr:
            return ""
    return None


def set_text(text: str) -> bool:
    """Put `text` on the clipboard; True on success."""
    if sys.platform == "win32":
        from voice_keyboard.windows import clipboard as win_clipboard

        try:
            return win_clipboard.set_text(text)
        except Exception:
            logger.debug("Windows clipboard write failed", exc_info=True)
            return False
    candidates: list[list[str]] = []
    if sys.platform == "darwin":
        candidates = [["pbcopy"]]
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


def selection_text(*, clipboard_fallback: bool = True) -> str:
    """The text the user has highlighted right now, best effort; "" when
    none. Linux reads the PRIMARY selection; Windows copies the selection
    (and restores the clipboard after). macOS has neither, so the
    clipboard stands in — as it does on Windows when the copy yields
    nothing (a terminal, or an app that ignores Ctrl+C) — unless the
    caller opts out with clipboard_fallback=False."""
    if sys.platform == "win32":
        try:
            from voice_keyboard.windows.selection import copy_selection

            text = copy_selection()
        except Exception:
            logger.debug("Windows selection copy failed", exc_info=True)
            text = None
        if text:
            return text
        return (get_text() or "") if clipboard_fallback else ""
    if sys.platform == "darwin":
        return (get_text() or "") if clipboard_fallback else ""
    return get_primary_text() or ""
