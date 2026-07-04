"""Regression guard for the GNOME 50 overlay fix.

GNOME 50 removed `affectsInputRegion` from Main.layoutManager.addChrome's
params. Params.parse throws on the unknown key, so the overlay pill never
draws on shell 50.x. The fix removed the param (click-through is preserved
because the actors are non-reactive by default). This test fails if the
token is ever reintroduced.
"""

from pathlib import Path

_EXT = (
    Path(__file__).resolve().parent.parent
    / "gnome-shell"
    / "voice-keyboard-overlay@liam-hennigan"
    / "extension.js"
)

_BANNED = "affectsInputRegion"


def test_extension_exists() -> None:
    assert _EXT.is_file(), f"overlay extension.js missing at {_EXT}"


def test_affects_input_region_never_reappears() -> None:
    text = _EXT.read_text(encoding="utf-8")
    assert _BANNED not in text, (
        f"{_BANNED!r} is back in extension.js — GNOME 50's Params.parse "
        "throws on it and the overlay pill stops drawing. Remove it; "
        "click-through comes from the actors being non-reactive."
    )


def test_kai_orb_is_wired() -> None:
    # The always-on clickable Kai orb: a reactive button that summons Kai
    # by speaking the daemon's Unix-socket IPC directly.
    text = _EXT.read_text(encoding="utf-8")
    for token in (
        "SetButton",
        "_showButton",
        "_summon",
        "St.Button",
        "UnixSocketAddress",
        "'converse'",
    ):
        assert token in text, f"{token!r} missing from extension.js (Kai orb)"
