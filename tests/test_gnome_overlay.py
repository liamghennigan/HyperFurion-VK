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


def test_supports_current_and_lts_gnome() -> None:
    # Ubuntu 24.04 LTS ships GNOME 46: a metadata list of ["50"] alone made
    # the shell refuse to load the overlay anywhere but the newest release.
    import json

    meta = json.loads((_EXT.parent / "metadata.json").read_text(encoding="utf-8"))
    assert {"46", "47", "48", "49", "50"} <= set(meta["shell-version"])


def test_box_layouts_go_through_the_version_shim() -> None:
    # St.BoxLayout's `orientation` only exists on GNOME 48+; 45-47 throw on
    # it. Every box must be built by boxLayout(), which picks the property.
    text = _EXT.read_text(encoding="utf-8")
    assert text.count("new St.BoxLayout(") == 2  # the two branches inside boxLayout()
    assert "SHELL_MAJOR >= 48" in text
