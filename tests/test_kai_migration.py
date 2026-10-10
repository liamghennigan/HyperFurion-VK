"""An `[assistant] enabled = true` copied from an older config.toml.example.

From 2.1 to 2.4 the example shipped `enabled = true` as the default, and
install.sh, the macOS installer and the AUR package's instructions copy the
example. That `true` is not a choice, so under the strict reading ("if
there's no local option, then it needs to be opt-in") it reads as "auto".
A `true` someone chose stays on (A8).
"""

import codecs
from pathlib import Path

import pytest

from voice_keyboard.assistant.locality import kai_state
from voice_keyboard.config import (
    FROM_EXAMPLE,
    KAI_ENABLED_MARKER,
    enabled_true_is_a_choice,
    load_config,
    set_kai_enabled,
)

REPO = Path(__file__).resolve().parent.parent

# The [assistant] table of config.toml.example as shipped from 2.1.0 to
# 2.4.0 (the comment above `enabled` is byte-identical in every release;
# only an unrelated later comment is reworded here, to call Kai "it").
# Embedded on purpose: the current example has changed.
LEGACY_TABLE = """\
[assistant]
# Kai — the voice assistant. When enabled, a SECOND hotkey (below) takes a
# spoken query and routes it by WHERE YOU ARE:
#   • focused on a terminal  -> it turns your words into a command, types it
#     at the prompt, and NEVER presses Enter (that stays yours);
#   • anywhere else          -> it answers / searches the web, spoken back.
# Voice in, voice or a drafted command out — you never type to it (typed
# interaction is a future Seneschal-computer thing). On by default: it's
# push-to-talk, so nothing is captured until you press the hotkey or click
# the on-screen orb — the hotkey stays the hard mute.
enabled = true
# What the mind calls itself — the local brain's persona and the on-screen
# copy. "Kai", from KairOS. (The spoken voice agent introduces itself with
# whatever name you gave it in the xAI Voice Agent Builder console.)
name = "Kai"
# xAI Voice Agent Builder id for the realtime voice brain; key falls back
# to providers.xai. Leave empty to use the local [llm] brain only.
agent_id = ""
api_key = ""
# Which brain answers a SPOKEN turn: realtime | local | auto. The xAI voice
# agent is voice-to-voice — it answers your captured audio with speech; the
# terminal route always uses the local [llm] to compile a command. auto =
# the voice agent for spoken answers when configured, local otherwise.
# (`voice-keyboard summon` toggles a turn from the CLI, same as the hotkey.)
brain = "auto"
# The summon binding (a second global binding; dictation keeps Ctrl+Alt+V).
# Default is a BARE modifier — hold Right Ctrl to talk — because a modifier
# alone never reaches the focused app. Held symbol chords (e.g.
# "control+alt+.") work too, but terminals encode them as escape codes
# (CSI-u), so holding one at a shell prompt sprays junk like `6;7u6;7u`.
# While a bare-key gesture is active, pressing any OTHER key aborts it, so
# real Right-Ctrl shortcuts still work. Restart to change — it owns a
# listener.
hotkey = "rightctrl"
# How the summon key behaves, like [hotkey].mode:
#   auto   = HOLD to talk / release to send (walkie-talkie), or a quick TAP
#            to toggle; a tap while Kai is answering cuts it off (barge-in).
#   hold   = hold to talk only.  toggle = tap to start / tap to stop.
mode = "auto"
# A short offline tone when Kai starts listening / captures your question —
# eyes-free confirmation. No network, no audio file. Set false for silence.
earcon = true
# Draw an always-on clickable Kai orb on screen (the GNOME overlay extension
# renders it, bottom-right); click it to summon. Set false to hide it.
button = true
# On Wayland the daemon often can't see the focused app (GPU terminals like
# Ghostty/kitty expose no AT-SPI, and GNOME denies window introspection).
# When focus is UNKNOWN, still attempt the terminal command route — the
# classifier answers questions and only types actual commands (never Enter).
# Set false to always answer by voice when focus can't be resolved.
terminal_fallback = true
# local = never send file contents to the brain; cloud = send excerpts of
# files you explicitly name. Your selection and memory are always allowed.
privacy_mode = "local"
memory_enabled = true
web_enabled = true
max_memory_results = 5
# (Reserved) the terminal route ALWAYS drafts a command (typed, no Enter);
# this would additionally let the brain draft one when you are NOT in a
# terminal. Off by default; unused by the current voice flow.
can_act = false
# Confine any file context the brain may read to this root (default: home).
home_root = ""
"""

# 2.0.0's example: Kai was off by default, so a `true` here was a choice.
V200_TABLE = """\
[assistant]
# Kai — the voice assistant. When enabled, a SECOND hotkey (below) takes a
# spoken query and routes it by WHERE YOU ARE:
#   • focused on a terminal  -> it turns your words into a command, types it
#     at the prompt, and NEVER presses Enter (that stays yours);
#   • anywhere else          -> it answers / searches the web, spoken back.
# Voice in, voice or a drafted command out — you never type to it (typed
# interaction is a future Seneschal-computer thing). Off by default.
enabled = true
name = "Kai"
agent_id = ""
api_key = ""
brain = "auto"
hotkey = "rightctrl"
mode = "auto"
earcon = true
button = true
"""

SPEECH = '[stt]\nprovider = "xai"\n\n[providers.xai]\napi_key = "xai-test-key"\n\n'
LOCAL = (
    '[stt]\nprovider = "openai"\n\n[tts]\nprovider = "openai"\n\n'
    '[providers.openai]\nbase_url = "http://127.0.0.1:8000/v1"\n\n'
    '[llm]\nprovider = "openai"\nbase_url = "http://127.0.0.1:8080/v1"\n'
    'model = "qwen3.5-2b"\napi_key = "local"\n\n'
)


def _load(tmp_path: Path, text: str, *, encoding: str = "utf-8", bom: bytes = b"") -> dict:
    path = tmp_path / "config.toml"
    path.write_bytes(bom + text.encode(encoding))
    return load_config(path)


def _trimmed(table: str) -> str:
    """The example with every comment stripped, as many people keep it."""
    return "\n".join(ln for ln in table.split("\n") if not ln.lstrip().startswith("#"))


# ── the example's default reads as "auto" ───────────────────────────────


def test_the_example_default_reads_as_auto(tmp_path: Path) -> None:
    cfg = _load(tmp_path, SPEECH + LEGACY_TABLE)
    assert cfg["assistant"]["enabled"] == "auto"
    assert cfg["assistant"][FROM_EXAMPLE] is True
    state = kai_state(cfg)
    assert state.setting == "auto" and state.from_example
    assert not state.on  # xAI everywhere: off until you turn it on


def test_the_example_default_with_everything_local_stays_on(tmp_path: Path) -> None:
    cfg = _load(tmp_path, LOCAL + LEGACY_TABLE)
    state = kai_state(cfg)
    assert state.setting == "auto" and state.on and state.local


def test_a_comment_trimmed_example_reads_as_auto(tmp_path: Path) -> None:
    cfg = _load(tmp_path, SPEECH + _trimmed(LEGACY_TABLE))
    assert cfg["assistant"]["enabled"] == "auto"
    assert cfg["assistant"][FROM_EXAMPLE] is True


@pytest.mark.parametrize("newline", ["\r\n", "\r"])
def test_the_example_default_with_other_line_endings(tmp_path: Path, newline: str) -> None:
    cfg = _load(tmp_path, (SPEECH + LEGACY_TABLE).replace("\n", newline))
    assert cfg["assistant"]["enabled"] == "auto"


def test_the_example_default_with_a_byte_order_mark(tmp_path: Path) -> None:
    cfg = _load(tmp_path, SPEECH + LEGACY_TABLE, bom=codecs.BOM_UTF8)
    assert cfg["assistant"]["enabled"] == "auto"


def test_the_example_default_saved_as_ansi(tmp_path: Path) -> None:
    # Notepad's "ANSI": the em dashes and bullets become cp1252 bytes (and
    # the bullet, which cp1252 lacks, a question mark).
    text = (SPEECH + LEGACY_TABLE).replace("•", "*")
    cfg = _load(tmp_path, text, encoding="cp1252")
    assert cfg["assistant"]["enabled"] == "auto"


def test_the_example_default_saved_as_utf16(tmp_path: Path) -> None:
    cfg = _load(tmp_path, SPEECH + LEGACY_TABLE, encoding="utf-16")
    assert cfg["assistant"]["enabled"] == "auto"


# ── a true someone chose stays on ───────────────────────────────────────


def test_true_under_the_writers_comment_is_a_choice(tmp_path: Path) -> None:
    cfg = _load(tmp_path, SPEECH + set_kai_enabled(LEGACY_TABLE, True))
    assert cfg["assistant"]["enabled"] is True
    assert FROM_EXAMPLE not in cfg["assistant"]
    assert kai_state(cfg).on


def test_true_in_the_current_example_is_a_choice(tmp_path: Path) -> None:
    text = (REPO / "config.toml.example").read_text(encoding="utf-8")
    text = text.replace('\nenabled = "auto"\n', "\nenabled = true\n", 1)
    cfg = _load(tmp_path, text)
    assert cfg["assistant"]["enabled"] is True


def test_the_current_example_reads_as_auto(tmp_path: Path) -> None:
    cfg = _load(tmp_path, (REPO / "config.toml.example").read_text(encoding="utf-8"))
    assert cfg["assistant"]["enabled"] == "auto"
    assert FROM_EXAMPLE not in cfg["assistant"]


def test_true_in_the_200_example_is_a_choice(tmp_path: Path) -> None:
    cfg = _load(tmp_path, SPEECH + V200_TABLE)
    assert cfg["assistant"]["enabled"] is True
    assert kai_state(cfg).on and kai_state(cfg).setting == "on"


@pytest.mark.parametrize("table", [
    "[assistant]\nenabled = true\n",
    '[assistant]\nenabled = true\nname = "Kai"\nbrain = "local"\n',
    "[assistant]\n# I want Kai\nenabled = true\n",
    '[assistant]\nhotkey = "control+alt+k"\nenabled = true\n',
])
def test_a_hand_written_true_is_a_choice(tmp_path: Path, table: str) -> None:
    cfg = _load(tmp_path, SPEECH + table)
    assert cfg["assistant"]["enabled"] is True


@pytest.mark.parametrize("text", [
    "assistant.enabled = true\n",
    "assistant = { enabled = true }\n",
])
def test_true_outside_an_assistant_table_is_a_choice(tmp_path: Path, text: str) -> None:
    cfg = _load(tmp_path, text)
    assert cfg["assistant"]["enabled"] is True


def test_the_example_comment_with_false_stays_false(tmp_path: Path) -> None:
    cfg = _load(tmp_path, SPEECH + LEGACY_TABLE.replace("enabled = true", "enabled = false"))
    assert cfg["assistant"]["enabled"] is False
    assert FROM_EXAMPLE not in cfg["assistant"]


def test_the_example_comment_kept_but_keys_trimmed_is_still_the_example(tmp_path: Path) -> None:
    # "On by default" in the comment above is the example's own default.
    head = LEGACY_TABLE.split("enabled = true")[0]
    cfg = _load(tmp_path, SPEECH + head + "enabled = true\n")
    assert cfg["assistant"]["enabled"] == "auto"


def test_enabled_true_is_a_choice_ignores_other_values() -> None:
    assert enabled_true_is_a_choice({"assistant": {"enabled": False}}, LEGACY_TABLE)
    assert enabled_true_is_a_choice({}, "")


def test_writers_leave_the_marker(tmp_path: Path) -> None:
    for value in (True, False, "auto"):
        text = set_kai_enabled(LEGACY_TABLE, value)
        lines = text.split("\n")
        at = next(i for i, ln in enumerate(lines) if ln.startswith("enabled ="))
        assert lines[at - 1] == KAI_ENABLED_MARKER


# ── doctor says why ─────────────────────────────────────────────────────


def test_doctor_says_the_true_came_from_the_example(tmp_path: Path) -> None:
    from voice_keyboard import doctor

    cfg = _load(tmp_path, SPEECH + LEGACY_TABLE)
    finding = doctor.check_kai(cfg, {})
    assert "copied from an older example config" in finding.detail
    assert '"auto"' in finding.detail
