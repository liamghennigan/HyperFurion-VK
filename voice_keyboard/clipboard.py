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


def get_text(*, unless_sensitive: bool = False) -> str | None:
    """Current clipboard text; "" for an empty clipboard, None on failure —
    and None when `unless_sensitive` and a password manager marked it."""
    if sys.platform == "win32":
        from voice_keyboard.windows import clipboard as win_clipboard

        try:
            return win_clipboard.get_text(unless_sensitive=unless_sensitive)
        except Exception:
            logger.debug("Windows clipboard read failed", exc_info=True)
            return None
    if unless_sensitive and is_sensitive():
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


# What password managers put next to a secret they copy: nspasteboard.org's
# concealed type (macOS; 1Password adds its own), KDE's hint (KeePassXC
# on Linux).
_MAC_SECRET_TYPES = {"org.nspasteboard.ConcealedType", "com.agilebits.onepassword"}
_LINUX_SECRET_TYPES = {"x-kde-passwordManagerHint"}
_MAC_TYPES_SCRIPT = (
    'ObjC.import("AppKit"); var t = $.NSPasteboard.generalPasteboard.types;'
    ' var out = []; for (var i = 0; i < t.count; i++) out.push(t.objectAtIndex(i).js);'
    ' out.join("\\n")'
)


def _clipboard_types() -> set[str]:
    """The clipboard's data types (macOS/Linux); empty when unknown."""
    if sys.platform == "darwin":
        candidates = [["osascript", "-l", "JavaScript", "-e", _MAC_TYPES_SCRIPT]]
    else:
        candidates = []
        if _is_wayland() and shutil.which("wl-paste"):
            candidates.append(["wl-paste", "--list-types"])
        if shutil.which("xclip"):
            candidates.append(["xclip", "-selection", "clipboard", "-t", "TARGETS", "-o"])
    for command in candidates:
        try:
            result = _run(command)
        except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
            continue
        if result.returncode == 0:
            return {line.strip() for line in result.stdout.splitlines() if line.strip()}
    return set()


def is_sensitive() -> bool:
    """True when the clipboard holds something a password manager marked
    private — never read it aloud or send it anywhere."""
    if sys.platform == "win32":
        from voice_keyboard.windows import clipboard as win_clipboard

        try:
            return win_clipboard.is_sensitive()
        except Exception:
            logger.debug("Windows clipboard sensitivity check failed", exc_info=True)
            return False
    secret = _MAC_SECRET_TYPES if sys.platform == "darwin" else _LINUX_SECRET_TYPES
    return bool(_clipboard_types() & secret)


def selection_text(
    *,
    clipboard_fallback: bool = True,
    registers: dict | None = None,
    notes: list | None = None,
) -> str:
    """The text the user has highlighted right now, best effort; "" when
    none. Linux reads the PRIMARY selection; Windows copies the selection
    (and restores the clipboard after; `registers` is the [registers]
    config, so terminals you mapped are never sent a copy key; why nothing
    could be copied goes into `notes`). macOS has neither, so the clipboard
    stands in — as it does on Windows when the copy yields nothing —
    unless the caller opts out with clipboard_fallback=False. Clipboard
    content a password manager marked private never stands in."""
    if sys.platform == "win32":
        try:
            from voice_keyboard.windows.selection import copy_selection

            text = copy_selection(registers=registers, notes=notes)
        except Exception:
            logger.debug("Windows selection copy failed", exc_info=True)
            text = None
        if text:
            return text
    elif sys.platform != "darwin":
        return get_primary_text() or ""
    if not clipboard_fallback:
        return ""
    return get_text(unless_sensitive=True) or ""
