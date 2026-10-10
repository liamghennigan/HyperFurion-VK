"""Recording feedback on macOS, until there is a native overlay.

The daemon "shows the overlay" for every state change, and during a
dictation that is several times a second (the live caption and level
meter). Linux draws those in the GNOME Shell extension and Windows in its
own window. macOS has no overlay yet, and before this module each of
those calls fell through to a Notification Center banner — a banner storm
during every dictation, one osascript process per word.

The policy here:

    starting / listening   a short rising tone, once per dictation
    processing             nothing
    inserted               a short falling tone
    empty / error          the falling tone and ONE notification (the
                           detail says why: NO SIGNAL, a provider error)

Live states never become notifications. This is also the seam for the
native overlay (an NSPanel helper process, see MACOS.md): show() and
hide() are all client.py calls on macOS.
"""

import logging
import subprocess
import threading
import time
from typing import Callable, Optional

logger = logging.getLogger(__name__)

LIVE_STATES = frozenset({"starting", "listening"})
DONE_STATES = frozenset({"inserted", "empty", "error"})
NOTIFY_STATES = frozenset({"empty", "error"})
# The same banner twice within this many seconds is shown once.
DEDUPE_S = 5.0
TITLE = "HyperFurion VK"


def _applescript_string(text: str) -> str:
    return '"' + text.replace("\\", "\\\\").replace('"', '\\"') + '"'


def _post_notification(title: str, body: str) -> None:
    script = (f"display notification {_applescript_string(body)}"
              f" with title {_applescript_string(title)}")

    def run() -> None:
        try:
            subprocess.run(["osascript", "-e", script], timeout=3, check=False,
                           capture_output=True)
        except (OSError, subprocess.TimeoutExpired):
            pass

    # Off-thread: osascript takes ~0.1 s and the caller may be the
    # daemon's overlay path.
    threading.Thread(target=run, name="vk-notify", daemon=True).start()


def _play_cue(kind: str) -> None:
    try:
        from voice_keyboard.earcon import play_earcon

        play_earcon(kind)
    except Exception:
        logger.debug("cue unavailable", exc_info=True)


class MacFeedback:
    def __init__(
        self,
        notify: Callable[[str, str], None] = _post_notification,
        cue: Optional[Callable[[str], None]] = _play_cue,
        clock: Callable[[], float] = time.monotonic,
    ):
        self._notify = notify
        self._cue = cue
        self._clock = clock
        self._live = False
        self._last: tuple[str, float] = ("", -DEDUPE_S)
        self._lock = threading.Lock()

    def show(self, state: str, detail: str = "") -> None:
        cue = None
        banner = None
        with self._lock:
            if state in LIVE_STATES:
                if not self._live:
                    self._live = True
                    cue = "listen"
            elif state in DONE_STATES:
                if self._live:
                    cue = "captured"
                self._live = False
                if state in NOTIFY_STATES:
                    body = detail.strip() or ("Nothing heard" if state == "empty" else "Something went wrong")
                    now = self._clock()
                    if self._last[0] != body or now - self._last[1] >= DEDUPE_S:
                        self._last = (body, now)
                        banner = body
        if cue and self._cue is not None:
            self._cue(cue)
        if banner:
            self._notify(TITLE, banner)

    def hide(self) -> None:
        with self._lock:
            self._live = False


_feedback = MacFeedback()


def show(state: str, *, detail: str = "", timeout_ms: int = 0, anchor=None) -> None:
    """client._show_overlay on macOS. `timeout_ms` and `anchor` are for the
    native overlay to come; feedback today needs neither."""
    _feedback.show(state, detail)


def hide() -> None:
    _feedback.hide()
