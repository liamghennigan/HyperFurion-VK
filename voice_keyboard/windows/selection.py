"""Read the highlighted text in the focused app (Windows).

Windows has no PRIMARY selection, so this does what a person would: copy,
read the clipboard, and put the clipboard back exactly as it was. The copy
key is Ctrl+Insert — Windows' other copy shortcut, which every standard
text control, browser, Office, and Windows Terminal honor, and which
(unlike Ctrl+C) never means "interrupt". It is careful not to cost you
anything:

- Terminals (by your [registers] map, then the built-in list), password
  fields, and windows it can't identify (an elevated app, a failed probe)
  get no key press at all.
- If the clipboard can't be saved completely (a very large image, an
  app-private format), it is left untouched and nothing is copied.
- An app that copies after we stop waiting still gets your clipboard put
  back — unless something else changed it, or you typed or clicked since
  (that copy may be yours).
- Text a password manager marks private is never returned.

The copied selection does show up in clipboard history (Win+V) like any
copy; only the restore is kept out of it.
"""

import logging
import threading
import time
from typing import Callable, Optional

logger = logging.getLogger(__name__)

COPY_KEYS = ["ctrl", "insert"]
COPY_TIMEOUT_S = 0.6
# How much longer to watch for a slow app's copy before giving up on it.
LATE_COPY_S = 1.5
POLL_S = 0.02
# Apps often post several formats one after another; let them finish
# before reading so the restore doesn't race the copy.
SETTLE_S = 0.04

# Why nothing was read (shown on the overlay instead of "Select some text").
NOTE_SKIPPED = "Not in a terminal or password field — copy it instead"
NOTE_UNKNOWN = "Can't tell what's focused — copy the text instead"
NOTE_UNSAVABLE = "Clipboard too big to set aside — copy the text instead"
NOTE_BUSY = "Clipboard is busy — try again"
NOTE_FOREIGN = "The clipboard changed elsewhere — try again"


def _focus_is_unsafe(registers: Optional[dict] = None) -> str:
    """Why the focused window must not be sent a copy key ("" when it's
    fine): an app that couldn't be identified, a password field, or a
    terminal (by the [registers] map, then the built-in list)."""
    from voice_keyboard.flow.registers import SHELL, TERMINAL, register_for_app
    from voice_keyboard.focusprobe import probe_focus

    focus = probe_focus()
    if focus is None or not (focus.app or "").strip():
        return NOTE_UNKNOWN
    if focus.secret or (focus.role or "").strip().lower() == "password text":
        return NOTE_SKIPPED
    cfg = registers or {}
    register = register_for_app(
        focus.app,
        focus.role,
        config_map=cfg.get("map") or {},
        default=str(cfg.get("default", "prose")),
    )
    return NOTE_SKIPPED if register in (TERMINAL, SHELL) else ""


_win = None


def _foreground_pids() -> frozenset:  # pragma: no cover - requires Windows
    """The foreground app's process and its child windows' (a UWP app's
    content runs in its own process under ApplicationFrameHost)."""
    global _win
    import ctypes
    from ctypes import wintypes

    if _win is None:
        user32 = ctypes.WinDLL("user32")  # type: ignore[attr-defined]
        enum_proc = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
        user32.GetForegroundWindow.restype = wintypes.HWND
        user32.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
        user32.GetWindowThreadProcessId.restype = wintypes.DWORD
        user32.EnumChildWindows.argtypes = [wintypes.HWND, enum_proc, wintypes.LPARAM]
        _win = (user32, enum_proc)
    user32, enum_proc = _win
    hwnd = user32.GetForegroundWindow()
    if not hwnd:
        return frozenset()
    pids: set[int] = set()

    def add(window) -> None:
        pid = wintypes.DWORD()
        user32.GetWindowThreadProcessId(window, ctypes.byref(pid))
        if pid.value:
            pids.add(int(pid.value))

    def each_child(window, _param):
        add(window)
        return True

    add(hwnd)
    user32.EnumChildWindows(hwnd, enum_proc(each_child), 0)
    return frozenset(pids)


def _wait_for_change(clip, before: int, seconds: float) -> bool:
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        if clip.sequence_number() != before:
            return True
        time.sleep(POLL_S)
    return False


# A late-copy watch must never restore over the next copy: starting a copy
# cancels the watch, under the same lock the watch restores under.
_watch_lock = threading.Lock()
_watch_cancel: Optional[threading.Event] = None


def _cancel_late_copy_watch() -> None:
    global _watch_cancel
    with _watch_lock:
        if _watch_cancel is not None:
            _watch_cancel.set()
            _watch_cancel = None


def _restore_after_late_copy(clip, before: int, saved, seconds: float, cancel, targets) -> None:
    """Put the clipboard back if the app copies after all — and only then.
    A change the app didn't make (a sync, a password manager), or one that
    follows any key press or mouse move (the user copying something
    themselves), is left alone."""
    idle_since = clip.last_input_tick()
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline and not cancel.is_set():
        if clip.sequence_number() != before:
            time.sleep(SETTLE_S)
            owner = clip.owner_pid()
            if owner is None or owner not in targets:
                return
            if idle_since is None or clip.last_input_tick() != idle_since:
                return
            with _watch_lock:
                if cancel.is_set():
                    return
                if not clip.restore(saved):
                    logger.warning("Could not restore the clipboard after a late copy")
            return
        time.sleep(POLL_S)


def _watch_for_late_copy(clip, before: int, saved, seconds: float, targets) -> None:
    global _watch_cancel
    cancel = threading.Event()
    with _watch_lock:
        _watch_cancel = cancel
    threading.Thread(
        target=_restore_after_late_copy,
        args=(clip, before, saved, seconds, cancel, targets),
        name="vk-clipboard-late-copy",
        daemon=True,
    ).start()


def copy_selection(
    *,
    injector=None,
    clip=None,
    registers: Optional[dict] = None,
    should_skip: Optional[Callable[[], object]] = None,
    foreground_pids: Callable[[], frozenset] = _foreground_pids,
    timeout: float = COPY_TIMEOUT_S,
    late_copy_s: float = LATE_COPY_S,
    notes: Optional[list] = None,
) -> Optional[str]:
    """The focused app's selected text, or None when nothing was copied
    (no selection, an app that was skipped or ignores the copy key, or a
    clipboard that couldn't be saved). Why, when known, is appended to
    `notes`."""

    def note(text: str) -> None:
        if notes is not None:
            notes.append(text)

    if clip is None:
        from voice_keyboard.windows import clipboard as clip
    if should_skip is None:
        def should_skip() -> object:
            return _focus_is_unsafe(registers)
    try:
        reason = should_skip()
    except Exception:
        logger.debug("focus probe failed before copy", exc_info=True)
        reason = NOTE_UNKNOWN
    if reason:
        logger.info("Not copying the selection: %s", reason)
        note(reason if isinstance(reason, str) else NOTE_SKIPPED)
        return None

    # This press will touch the clipboard: an older late-copy watch ends.
    _cancel_late_copy_watch()
    # Snapshot first, then read the change counter: saving can make an app
    # render delayed formats, which counts as a change.
    saved = clip.snapshot()
    if saved is None:
        note(NOTE_BUSY)
        return None
    if not saved.complete:
        logger.info("Clipboard can't be saved completely; leaving it alone")
        note(NOTE_UNSAVABLE)
        return None
    try:
        targets = foreground_pids()
    except Exception:
        targets = frozenset()
    before = clip.sequence_number()

    if injector is None:
        from voice_keyboard.windows.injector import WinTextInjector

        injector = WinTextInjector()
        injector.start()
    injector.press_combo(COPY_KEYS)

    if not _wait_for_change(clip, before, timeout):
        if late_copy_s > 0 and targets:
            _watch_for_late_copy(clip, before, saved, late_copy_s, targets)
        return None
    owner = clip.owner_pid()
    if owner is not None and targets and owner not in targets:
        # Someone else changed the clipboard just now: it isn't the
        # selection, and restoring would undo their change.
        logger.info("The clipboard changed, but not by the focused app; leaving it alone")
        note(NOTE_FOREIGN)
        return None
    time.sleep(SETTLE_S)
    text = clip.get_text(unless_sensitive=True)
    if not clip.restore(saved):
        logger.warning("Could not restore the clipboard after copying the selection")
    return text or None
