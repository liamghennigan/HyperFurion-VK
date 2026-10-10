"""Telling people when Kai turns on or off, or starts using another service.

The daemon keeps a record of the last state it told you about,
`kai-announced.json` in the state folder:

    {"setting": "auto", "on": false, "online": {"[stt]": "xAI", ...}}

At start and whenever Kai's state changes it compares the record with the
state it runs under. When Kai turned on or off, or (while on) its online
services changed, it shows one notification, and writes the record only
when the notification was shown: a notice that couldn't be shown (no
notification server, an SSH session) is tried again at the next start, and
until then Right Ctrl or `voice-keyboard summon` explain it in the overlay.

Setup, `voice-keyboard kai on|off` and the Windows tray write the record
for the state they just showed, so the daemon doesn't say it twice.
Turning Kai off yourself is never announced: whatever turned it off already
said so.
"""

from __future__ import annotations

import json
import logging
import os
import sys
import tempfile
from pathlib import Path
from typing import Optional

from voice_keyboard.assistant.locality import (
    KaiState,
    _and,
    summon_hint,
    turn_off_hint,
    turn_on_hint,
    uses_voice_agent,
)

logger = logging.getLogger(__name__)

RECORD_NAME = "kai-announced.json"
# How long the notice stays (Linux); it is its own notification, never
# replaced by the next dictation's.
NOTICE_MS = 15000
# A Windows balloon shows at most this many characters.
WINDOWS_BALLOON_MAX = 255


def record_path() -> Path:
    from voice_keyboard import paths

    return paths.state_dir() / RECORD_NAME


def record_for(state: KaiState) -> dict:
    return {
        "setting": state.setting,
        "on": state.on,
        "online": {hop.section: hop.service for hop in state.online_hops()},
    }


def read_record() -> Optional[dict]:
    try:
        data = json.loads(record_path().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def write_record(state: KaiState) -> bool:
    """Remember `state` as told. Best effort: False when it couldn't be
    written (the notice is then shown again next time)."""
    if state.unreadable:
        return False
    path = record_path()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=str(path.parent), prefix=".kai-", suffix=".json")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                json.dump(record_for(state), handle)
            os.replace(tmp, path)
        except BaseException:
            try:
                os.unlink(tmp)
            except OSError:
                pass
            raise
    except OSError as exc:
        logger.debug("Couldn't write %s: %s", path, exc)
        return False
    return True


def _told(record: Optional[dict], state: KaiState) -> bool:
    """The record already describes `state`: on with the same online
    services, or off under the same setting."""
    if not isinstance(record, dict) or bool(record.get("on")) != state.on:
        return False
    if state.on:
        return record.get("online") == record_for(state)["online"]
    return record.get("setting") == state.setting


def _uses(state: KaiState) -> str:
    return _and([hop.phrase() for hop in state.online_hops()])


def notice(
    state: KaiState, record: Optional[dict], config: dict, platform: Optional[str] = None
) -> Optional[tuple]:
    """What to tell, given what was last told: (title, body); ("", "") when
    the record only needs updating (nothing to say); None when nothing
    changed."""
    platform = platform or sys.platform
    if state.unreadable or _told(record, state):
        return None
    name = state.name
    if not state.on:
        if state.setting == "off":
            return ("", "")  # you turned it off; that already said so
        if not state.can_answer:
            return (
                f"{name} is off",
                f"{name}, the voice assistant, has no language model to answer with ([llm]).",
            )
        here = "this PC" if platform == "win32" else "this computer"
        lead = (
            f"{name}, the voice assistant, turns on by itself only when everything it"
            f" uses runs on {here} or your own network."
        )
        how = f"To turn it on: {turn_on_hint(platform)}"
        extra = []
        if (config.get("wake", {}) or {}).get("enabled", False) is True:
            extra.append(f"Your wake word doesn't listen until you turn {name} on.")
        if uses_voice_agent(config):
            extra.append(
                f"You set up an xAI voice agent ([assistant] agent_id): it answers once"
                f" you turn {name} on."
            )
        body = " ".join([lead, f"It would use {_uses(state)}.", *extra, how])
        if platform == "win32" and len(body) > WINDOWS_BALLOON_MAX:
            body = " ".join(
                [lead, f"It would send your questions to {state.online()}.", how]
            )
        return (f"{name} is off", body)
    if not state.can_answer:
        return (f"{name} is on", f"{name} is {state.why()}.")
    if state.local:
        return (
            f"{name} is on",
            f"{name}, the voice assistant, is on: everything it uses runs on"
            f" {state.place}. To ask it: {summon_hint(config, platform)}.",
        )
    was_on = isinstance(record, dict) and bool(record.get("on"))
    off = f"To turn it off: {turn_off_hint(platform)}"
    if was_on:
        title = f"{name} now uses {state.online()}"
        body = f"{name} is on (you turned it on), and now uses {_uses(state)}. {off}"
    else:
        title = f"{name} is on"
        body = f"{name} is on (you turned it on): it uses {_uses(state)}. {off}"
    if platform == "win32" and len(body) > WINDOWS_BALLOON_MAX:
        body = f"{name} is on (you turned it on): it sends your questions to {state.online()}. {off}"
    return (title, body)


def show(title: str, body: str) -> bool:
    from voice_keyboard import client

    return bool(client._notify(title, body, timeout_ms=NOTICE_MS, replace=False))


def announce(state: KaiState, config: dict) -> bool:
    """Tell what changed since the last notice, if anything (blocking: run
    it off the event loop). True when the record now matches `state`."""
    told = notice(state, read_record(), config)
    if told is None:
        return True
    title, body = told
    if title:
        if not show(title, body):
            logger.info("Couldn't show the notice '%s'; trying again at the next start", title)
            return False
    return write_record(state)
