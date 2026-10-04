"""Read the highlighted text in the focused app (Windows).

Windows has no PRIMARY selection, so this does what a person would: press
Ctrl+C, read the clipboard, then put the clipboard back exactly as it was
(every format, and the copy is kept out of Win+V history). Terminals are
skipped — Ctrl+C there can interrupt a running command — and callers fall
back to the clipboard.
"""

import logging
import time
from typing import Optional

logger = logging.getLogger(__name__)

COPY_TIMEOUT_S = 0.5
POLL_S = 0.02
# Apps often post several formats one after another; let them finish
# before reading so the snapshot restore doesn't race the copy.
SETTLE_S = 0.04


def _focused_is_terminal() -> bool:
    from voice_keyboard.flow.registers import TERMINAL, register_for_app
    from voice_keyboard.focusprobe import probe_focus

    focus = probe_focus()
    if focus is None:
        return False
    return register_for_app(focus.app, focus.role) is TERMINAL


def copy_selection(
    *,
    injector=None,
    clip=None,
    is_terminal=_focused_is_terminal,
    timeout: float = COPY_TIMEOUT_S,
) -> Optional[str]:
    """The focused app's selected text, or None when nothing was copied
    (no selection, a terminal, or an app that ignores Ctrl+C)."""
    if clip is None:
        from voice_keyboard.windows import clipboard as clip
    try:
        if is_terminal():
            return None
    except Exception:
        logger.debug("focus probe failed before copy", exc_info=True)
        return None

    if injector is None:
        from voice_keyboard.windows.injector import WinTextInjector

        injector = WinTextInjector()
        injector.start()

    before = clip.sequence_number()
    saved = clip.snapshot()
    injector.press_combo(["ctrl", "c"])
    deadline = time.monotonic() + timeout
    changed = False
    while time.monotonic() < deadline:
        if clip.sequence_number() != before:
            changed = True
            break
        time.sleep(POLL_S)
    if not changed:
        return None
    time.sleep(SETTLE_S)
    text = clip.get_text()
    if saved is not None:
        clip.restore(saved)
    return text or None
