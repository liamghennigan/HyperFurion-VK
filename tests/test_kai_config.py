"""[assistant] enabled: true, false or "auto" (the default), and the one
writer that changes it (setup, `voice-keyboard kai on|off`, the tray)."""

import os
import sys
import tomllib
from pathlib import Path

import pytest

from voice_keyboard.assistant.locality import kai_setting
from voice_keyboard.config import (
    KAI_ENABLED_MARKER,
    _default_config_with_paths,
    load_config,
    set_kai_enabled,
    validate_config,
    write_config_text,
)

REPO = Path(__file__).resolve().parent.parent


def _config(**assistant) -> dict:
    cfg = _default_config_with_paths()
    cfg["xai"]["api_key"] = "test-api-key"
    cfg["assistant"].update(assistant)
    return cfg


# ── validation ──────────────────────────────────────────────────────────


@pytest.mark.parametrize("value", [True, False, "auto", "AUTO", " Auto "])
def test_enabled_accepts_true_false_and_auto(value) -> None:
    validate_config(_config(enabled=value))


@pytest.mark.parametrize("value", ["yes", "on", "true", 1, 0, None, ["auto"]])
def test_enabled_rejects_anything_else(value) -> None:
    with pytest.raises(RuntimeError, match='assistant.enabled must be true, false or "auto"'):
        validate_config(_config(enabled=value))


def test_defaults_validate_with_auto() -> None:
    cfg = _config()
    assert cfg["assistant"]["enabled"] == "auto"
    assert cfg["assistant"]["local_hosts"] == []
    validate_config(cfg)


@pytest.mark.parametrize("hosts", [
    [], ["gpu-box"], ["ollama", "100.64.0.0/10", "192.0.2.10", "fd00::/8", "nas.lan."],
])
def test_local_hosts_accepts_names_ips_and_networks(hosts) -> None:
    validate_config(_config(local_hosts=hosts))


@pytest.mark.parametrize("hosts", [
    "gpu-box", ["http://gpu-box:8080"], [""], ["gpu box"], [3], ["10.0.0.0/99"],
])
def test_local_hosts_rejects_the_rest(hosts) -> None:
    with pytest.raises(RuntimeError, match="assistant.local_hosts"):
        validate_config(_config(local_hosts=hosts))


def test_example_config_defaults_to_auto() -> None:
    text = (REPO / "config.toml.example").read_text(encoding="utf-8")
    table = tomllib.loads(text)["assistant"]
    assert table["enabled"] == "auto"
    assert table["local_hosts"] == []
    lines = text.split("\n")
    at = lines.index('enabled = "auto"')
    # The writers' marker sits directly above the value.
    assert lines[at - 1] == KAI_ENABLED_MARKER
    assert "On by default" not in "\n".join(lines[at - 12:at])


def test_example_wake_says_kai_must_be_on() -> None:
    text = (REPO / "config.toml.example").read_text(encoding="utf-8")
    wake = text[text.index("[wake]"):]
    assert "Kai must be on" in wake[:1200]


# ── the writer ──────────────────────────────────────────────────────────


LEGACY = """\
[stt]
provider = "xai"

[assistant]
# Kai — the voice assistant. When enabled, a SECOND hotkey (below) takes a
# spoken query and routes it by WHERE YOU ARE:
# Voice in, voice or a drafted command out — you never type to it (typed
# interaction is a future Seneschal-computer thing). On by default: it's
# push-to-talk, so nothing is captured until you press the hotkey or click
# the on-screen orb — the hotkey stays the hard mute.
enabled = true
# What the mind calls itself
name = "Kai"

[wake]
enabled = false
"""


@pytest.mark.parametrize("value, setting", [(True, "on"), (False, "off"), ("auto", "auto")])
def test_set_kai_enabled_round_trips(value, setting) -> None:
    text = set_kai_enabled(LEGACY, value)
    data = tomllib.loads(text)
    assert data["assistant"]["enabled"] == value
    assert kai_setting(data) == setting
    # Everything else is kept.
    assert data["assistant"]["name"] == "Kai"
    assert data["wake"]["enabled"] is False
    assert "# What the mind calls itself" in text


def test_set_kai_enabled_replaces_the_old_example_wording() -> None:
    text = set_kai_enabled(LEGACY, True)
    assert "On by default" not in text
    lines = text.split("\n")
    at = lines.index("enabled = true")
    assert lines[at - 1] == KAI_ENABLED_MARKER
    assert lines[at - 4] == "[assistant]"


def test_set_kai_enabled_keeps_your_own_comment() -> None:
    text = set_kai_enabled("[assistant]\n# mine, keep me\nenabled = false\n", True)
    lines = text.split("\n")
    assert lines[1] == "# mine, keep me"
    assert lines[lines.index("enabled = true") - 1] == KAI_ENABLED_MARKER


def test_set_kai_enabled_is_idempotent() -> None:
    once = set_kai_enabled(LEGACY, "auto")
    assert set_kai_enabled(once, "auto") == once
    twice = set_kai_enabled(once, False)
    assert twice.count(KAI_ENABLED_MARKER) == 1


def test_set_kai_enabled_adds_the_table() -> None:
    text = set_kai_enabled('[stt]\nprovider = "xai"\n', False)
    assert tomllib.loads(text)["assistant"]["enabled"] is False
    assert KAI_ENABLED_MARKER in text


def test_set_kai_enabled_only_touches_assistant() -> None:
    text = set_kai_enabled("[assistant]\nname = \"Kai\"\n\n[wake]\nenabled = true\n", False)
    data = tomllib.loads(text)
    assert data["assistant"]["enabled"] is False
    assert data["wake"]["enabled"] is True


@pytest.mark.parametrize("text", [
    "assistant.enabled = true\n",
    "assistant = { enabled = true }\n",
])
def test_set_kai_enabled_refuses_what_it_cannot_edit(text) -> None:
    with pytest.raises(ValueError, match="edit \\[assistant\\] enabled by hand"):
        set_kai_enabled(text, False)


def test_set_kai_enabled_rejects_other_values() -> None:
    with pytest.raises(ValueError):
        set_kai_enabled("", "yes")


def test_write_config_text(tmp_path: Path) -> None:
    path = tmp_path / "voice-keyboard" / "config.toml"
    write_config_text(path, "[assistant]\nenabled = false\n")
    assert load_config(path)["assistant"]["enabled"] is False
    if os.name == "posix":
        assert (path.stat().st_mode & 0o777) == 0o600


@pytest.mark.skipif(sys.platform == "win32", reason="symlinks need privileges on Windows")
def test_write_config_text_writes_through_a_link(tmp_path: Path) -> None:
    real = tmp_path / "dotfiles" / "config.toml"
    real.parent.mkdir()
    real.write_text("[assistant]\nenabled = true\n", encoding="utf-8")
    link = tmp_path / "config.toml"
    link.symlink_to(real)
    write_config_text(link, "[assistant]\nenabled = false\n")
    assert link.is_symlink()
    assert "enabled = false" in real.read_text(encoding="utf-8")


@pytest.mark.skipif(os.name != "posix" or os.geteuid() == 0, reason="needs a non-root POSIX user")
def test_write_config_text_raises_on_a_read_only_folder(tmp_path: Path) -> None:
    folder = tmp_path / "ro"
    folder.mkdir()
    path = folder / "config.toml"
    path.write_text("", encoding="utf-8")
    folder.chmod(0o500)
    try:
        with pytest.raises(OSError):
            write_config_text(path, "[assistant]\nenabled = false\n")
    finally:
        folder.chmod(0o700)
