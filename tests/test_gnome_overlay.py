"""Regression guard for the overlay's addChrome params across GNOME 45-50.

GNOME 50 removed `affectsInputRegion` from Main.layoutManager.addChrome's
params, and Params.parse throws on the unknown key — passed on 50.x, the
pill never draws. GNOME 45-49 still have it and default it to true, which
on X11 makes the click-through pill swallow clicks. So the parameter may
appear only behind the SHELL_MAJOR < 50 gate, and only for the pill.
"""

import re

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


def test_affects_input_region_only_where_it_exists() -> None:
    text = _EXT.read_text(encoding="utf-8")
    code = [
        line for line in text.splitlines()
        if _BANNED in line and not line.strip().startswith("//")
    ]
    assert code == ["        return {...params, affectsInputRegion: false};"], (
        f"{_BANNED!r} must only be added by pillChromeParams: GNOME 50's "
        "Params.parse throws on it and the overlay pill stops drawing."
    )
    gate = re.search(r"function pillChromeParams\(params\) \{(.*?)\n\}", text, re.S)
    assert gate and "if (SHELL_MAJOR < 50)" in gate.group(1)
    # The pill goes through the gate; the reactive orb must stay clickable.
    assert "addChrome(actor, pillChromeParams({" in text
    assert "addChrome(orb, {" in text


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
