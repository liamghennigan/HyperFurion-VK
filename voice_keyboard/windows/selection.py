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
  back.
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


def _focus_is_unsafe(registers: Optional[dict] = None) -> bool:
    """True unless the focused app is known and is neither a terminal nor
    a password field."""
    from voice_keyboard.flow.registers import SHELL, TERMINAL, register_for_app
    from voice_keyboard.focusprobe import probe_focus

    focus = probe_focus()
    if focus is None or not (focus.app or "").strip():
        return True
    if focus.secret or (focus.role or "").strip().lower() == "password text":
        return True
    cfg = registers or {}
    register = register_for_app(
        focus.app,
        focus.role,
        config_map=cfg.get("map") or {},
        default=str(cfg.get("default", "prose")),
    )
    return register in (TERMINAL, SHELL)


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


def _restore_after_late_copy(clip, before: int, saved, seconds: float, cancel) -> None:
    """Put the clipboard back if the app copies after all."""
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline and not cancel.is_set():
        if clip.sequence_number() != before:
            time.sleep(SETTLE_S)
            with _watch_lock:
                if cancel.is_set():
                    return
                if not clip.restore(saved):
                    logger.warning("Could not restore the clipboard after a late copy")
            return
        time.sleep(POLL_S)


def _watch_for_late_copy(clip, before: int, saved, seconds: float) -> None:
    global _watch_cancel
    cancel = threading.Event()
    with _watch_lock:
        _watch_cancel = cancel
    threading.Thread(
        target=_restore_after_late_copy,
        args=(clip, before, saved, seconds, cancel),
        name="vk-clipboard-late-copy",
        daemon=True,
    ).start()


def copy_selection(
    *,
    injector=None,
    clip=None,
    registers: Optional[dict] = None,
    should_skip: Optional[Callable[[], bool]] = None,
    timeout: float = COPY_TIMEOUT_S,
    late_copy_s: float = LATE_COPY_S,
) -> Optional[str]:
    """The focused app's selected text, or None when nothing was copied
    (no selection, an app that was skipped or ignores the copy key, or a
    clipboard that couldn't be saved)."""
    if clip is None:
        from voice_keyboard.windows import clipboard as clip
    _cancel_late_copy_watch()
    if should_skip is None:
        def should_skip() -> bool:
            return _focus_is_unsafe(registers)
    try:
        if should_skip():
            logger.info("Not copying the selection: terminal, password, or unknown window")
            return None
    except Exception:
        logger.debug("focus probe failed before copy", exc_info=True)
        return None

    # Snapshot first, then read the change counter: saving can make an app
    # render delayed formats, which counts as a change.
    saved = clip.snapshot()
    if saved is None or not saved.complete:
        logger.info("Clipboard can't be saved completely; leaving it alone")
        return None
    before = clip.sequence_number()

    if injector is None:
        from voice_keyboard.windows.injector import WinTextInjector

        injector = WinTextInjector()
        injector.start()
    injector.press_combo(COPY_KEYS)

    if not _wait_for_change(clip, before, timeout):
        if late_copy_s > 0:
            _watch_for_late_copy(clip, before, saved, late_copy_s)
        return None
    time.sleep(SETTLE_S)
    text = None if clip.is_sensitive() else clip.get_text()
    if not clip.restore(saved):
        logger.warning("Could not restore the clipboard after copying the selection")
    return text or None
