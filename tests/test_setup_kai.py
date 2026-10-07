"""`voice-keyboard setup`'s Kai question (DESIGN §4.1, A10; CRITIQUE M1, M2).

Kai, the voice assistant, turns on by itself only when everything it uses
runs on this computer. The question says where Kai's questions would go,
and its default is Kai's current state, so pressing Enter never turns Kai
on or off. A blanket `true` over local services becomes "auto", so a later
switch to an online service asks again.
"""

import json
import sys
import tomllib

import pytest

from voice_keyboard.assistant import announce
from voice_keyboard.assistant.locality import kai_state
from voice_keyboard.config import FROM_EXAMPLE, KAI_ENABLED_MARKER, load_config
from voice_keyboard.setup_wizard import Wizard

LOCAL = """\
[providers.openai]
base_url = "http://127.0.0.1:8000/v1"

[stt]
provider = "openai"

[tts]
provider = "openai"

[llm]
provider = "openai"
base_url = "http://127.0.0.1:8080/v1"
model = "qwen3.5-2b"
api_key = "local"
"""

ONLINE = '[providers.xai]\napi_key = "xai-k"\n'

LEGACY_ASSISTANT = """
[assistant]
# Kai — the voice assistant. When enabled, a SECOND hotkey (below) takes a
# spoken query and routes it by WHERE YOU ARE:
# interaction is a future Seneschal-computer thing). On by default: it's
# push-to-talk, so nothing is captured until you press the hotkey or click
# the on-screen orb — the hotkey stays the hard mute.
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


class Script:
    def __init__(self, *answers):
        self.answers = list(answers)
        self.prompts = []

    def __call__(self, prompt):
        self.prompts.append(prompt)
        if not self.answers:
            raise AssertionError(f"unexpected prompt: {prompt!r}")
        return self.answers.pop(0)


@pytest.fixture(autouse=True)
def _homes(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg"))
    monkeypatch.delenv("HFVK_LLAMA_URL", raising=False)
    monkeypatch.delenv("LLAMA_ARG_PORT", raising=False)
    monkeypatch.setattr(sys, "platform", "linux")


def _run(tmp_path, text: str, kai: str, *, save: bool = True, speech: tuple = ("4",)):
    """Keep speech and model, Enter through hotkey, mode and language, answer
    `kai`, Enter through the other extras (and save, when asked)."""
    path = tmp_path / "config.toml"
    if text:
        path.write_text(text, encoding="utf-8")
    answers = [*speech, "1", "", "", "", kai, "", "", ""] + ([""] if save else [])
    ask = Script(*answers)
    out: list = []
    wizard = Wizard(path, ask=ask, secret=Script(), out=out.append,
                    detect=lambda extra=(): None, login=lambda: True)
    assert wizard.run() == 0
    assert not ask.answers, f"unused answers: {ask.answers}"
    return path, out, ask.prompts


def _kai_prompt(prompts: list) -> str:
    return next(p for p in prompts if "Kai" in p and "?" in p)


# ── local: on, and Enter keeps it on ────────────────────────────────────


def test_local_kai_is_on_and_enter_keeps_it(tmp_path) -> None:
    path, out, prompts = _run(tmp_path, LOCAL, "", save=False)
    joined = "\n".join(out)
    assert "Everything Kai uses runs on this computer:" in joined
    assert "your speech server at 127.0.0.1:8000" in joined
    assert "qwen3.5-2b at 127.0.0.1:8080" in joined
    assert "It remembers your questions and its answers on this computer." in joined
    assert "Hold Right Ctrl and ask Kai something" in joined
    assert _kai_prompt(prompts).endswith("Keep Kai on? (Y/n): ")
    assert "No changes." in out
    assert "assistant" not in tomllib.loads(path.read_text())
    assert kai_state(load_config(path)).on


def test_local_kai_turned_off(tmp_path) -> None:
    path, out, _ = _run(tmp_path, LOCAL, "n")
    data = tomllib.loads(path.read_text())
    assert data["assistant"]["enabled"] is False
    assert "  - Kai: off" in out
    lines = path.read_text().split("\n")
    assert lines[lines.index("enabled = false") - 1] == KAI_ENABLED_MARKER


def test_local_kai_turned_off_stays_off_on_enter(tmp_path) -> None:
    text = LOCAL + "\n[assistant]\nenabled = false\n"
    path, _out, prompts = _run(tmp_path, text, "", save=False)
    assert _kai_prompt(prompts).endswith("Turn on Kai? (y/N): ")
    assert tomllib.loads(path.read_text())["assistant"]["enabled"] is False


def test_local_kai_turned_back_on_writes_auto(tmp_path) -> None:
    text = LOCAL + "\n[assistant]\nenabled = false\n"
    path, out, _ = _run(tmp_path, text, "y")
    assert tomllib.loads(path.read_text())["assistant"]["enabled"] == "auto"
    assert "  - Kai: on (runs on this computer)" in out


def test_a_local_true_becomes_auto(tmp_path) -> None:
    text = LOCAL + "\n[assistant]\nenabled = true\n"
    path, out, prompts = _run(tmp_path, text, "")
    assert _kai_prompt(prompts).endswith("Keep Kai on? (Y/n): ")
    assert tomllib.loads(path.read_text())["assistant"]["enabled"] == "auto"
    assert "  - Kai: on (runs on this computer; asks again if you switch to an online service)" in out


# ── online: off unless you say so, and it says where questions go ───────


def test_online_kai_stays_off_on_enter(tmp_path) -> None:
    path, out, prompts = _run(tmp_path, ONLINE, "", save=False)
    joined = "\n".join(out)
    assert "Kai would use online services, so it stays off unless you turn it on:" in joined
    assert "hears you:  xAI: your recorded question" in joined
    assert "thinks:     xAI (grok-4.3): your question, related notes from its" in joined
    assert "text you have highlighted" in joined  # Linux
    assert "speaks:     xAI: its answer" in joined
    start = out.index("-- Kai, the voice assistant --")
    end = out.index("To keep Kai on this computer, use a local speech server and a local model.")
    assert all(len(line) <= 78 for line in out[start:end + 1])
    assert "To keep Kai on this computer, use a local speech server and a local model." in joined
    assert _kai_prompt(prompts).endswith("Turn on Kai and send these to xAI? (y/N): ")
    assert "assistant" not in tomllib.loads(path.read_text())


def test_online_kai_turned_on_writes_true(tmp_path) -> None:
    path, out, _ = _run(tmp_path, ONLINE, "y")
    assert tomllib.loads(path.read_text())["assistant"]["enabled"] is True
    assert "  - Kai: on (uses xAI)" in out
    assert kai_state(load_config(path)).on


def test_yes_on_an_old_example_copy_is_a_real_true(tmp_path) -> None:
    text = ONLINE + LEGACY_ASSISTANT
    assert load_config_from(tmp_path, text)["assistant"][FROM_EXAMPLE] is True
    path, _out, prompts = _run(tmp_path, text, "y")
    assert "Turn on Kai and send these to xAI?" in _kai_prompt(prompts)
    config = load_config(path)
    assert config["assistant"]["enabled"] is True
    assert FROM_EXAMPLE not in config["assistant"]
    assert "On by default" not in path.read_text()


def load_config_from(tmp_path, text: str) -> dict:
    probe = tmp_path / "probe.toml"
    probe.write_text(text, encoding="utf-8")
    return load_config(probe)


def test_online_kai_you_turned_on_stays_on_on_enter(tmp_path) -> None:
    text = ONLINE + "\n[assistant]\nenabled = true\n"
    path, out, prompts = _run(tmp_path, text, "", save=False)
    assert "Kai is on (you turned it on), and uses online services:" in "\n".join(out)
    assert _kai_prompt(prompts).endswith("Keep Kai on and keep sending these to xAI? (Y/n): ")
    assert tomllib.loads(path.read_text())["assistant"]["enabled"] is True


def test_online_kai_you_turned_on_can_be_turned_off(tmp_path) -> None:
    text = ONLINE + "\n[assistant]\nenabled = true\n"
    path, out, _ = _run(tmp_path, text, "n")
    assert tomllib.loads(path.read_text())["assistant"]["enabled"] is False


def test_the_hosted_choice_counts_as_online(tmp_path) -> None:
    _path, out, prompts = _run(tmp_path, "", "", speech=("3",))
    joined = "\n".join(out)
    assert "hears you:  xAI (through the HyperFurion relay): your recorded" in joined
    assert "Turn on Kai and send these to xAI?" in _kai_prompt(prompts)


def test_kai_never_says_right_ctrl_on_macos(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(sys, "platform", "darwin")
    _path, out, _ = _run(tmp_path, LOCAL, "", save=False)
    joined = "\n".join(out)
    assert "Right Ctrl" not in joined
    assert "summon` and ask Kai something" in joined


def test_no_language_model_no_question(tmp_path) -> None:
    out: list = []
    wizard = Wizard(tmp_path / "config.toml", ask=Script(), secret=Script(), out=out.append,
                    detect=lambda extra=(): None)
    current = load_config_from(tmp_path, ONLINE)
    current["llm"].update(provider="custom", base_url="", model="")
    wizard.step_kai(current)
    assert any("has no language model to answer with ([llm])" in line for line in out)
    assert wizard.edits == []


# ── it says so once ─────────────────────────────────────────────────────


def test_setup_records_what_it_showed(tmp_path) -> None:
    path, _out, _ = _run(tmp_path, ONLINE, "y")
    record = json.loads(announce.record_path().read_text(encoding="utf-8"))
    assert record["on"] is True and record["setting"] == "on"
    assert announce.notice(kai_state(load_config(path)), record, load_config(path)) is None


def test_setup_records_even_without_changes(tmp_path) -> None:
    _run(tmp_path, ONLINE, "", save=False)
    record = json.loads(announce.record_path().read_text(encoding="utf-8"))
    assert record == {"setting": "auto", "on": False,
                      "online": {"[stt]": "xAI", "[llm]": "xAI (grok-4.3)", "[tts]": "xAI"}}


def test_a_hosted_sign_in_that_fails_says_where_kai_stands(tmp_path) -> None:
    ask = Script("3", "1", "", "", "", "", "", "", "", "")
    out: list = []
    wizard = Wizard(tmp_path / "config.toml", ask=ask, secret=Script(), out=out.append,
                    detect=lambda extra=(): None, login=lambda: False)
    assert wizard.run() == 0
    assert any(line.startswith("    Kai is off") for line in out)


def test_turning_on_names_the_history_search_it_would_use(tmp_path) -> None:
    # An online [recall] isn't used while Kai is off, but `true` uses it:
    # the question names it before Kai is turned on.
    text = ONLINE + (
        '\n[recall]\nbase_url = "https://api.openai.com/v1"\n'
        'model = "text-embedding-3-small"\napi_key = "sk-test"\n'
    )
    path, out, prompts = _run(tmp_path, text, "", save=False)
    joined = " ".join(" ".join(out).split())
    assert "looks up your history: OpenAI: your question and up to 200 lines" in joined
    assert _kai_prompt(prompts).endswith("Turn on Kai and send these to xAI and OpenAI? (y/N): ")
