"""`voice-keyboard setup`, speech step: what a new user is offered first,
and that the local-server choice produces a config a local server answers.

The hosted subscription isn't on sale, so it is listed after the key and
local-server options and labelled for existing subscribers. A local
OpenAI-compatible server only serves the model ids it has installed, so the
local choice writes model names instead of leaving the OpenAI defaults.
"""

import tomllib

import pytest

from voice_keyboard.setup_wizard import Wizard


class Script:
    """Answers the wizard's prompts in order; fails on an unexpected one."""

    def __init__(self, *answers):
        self.answers = list(answers)

    def __call__(self, prompt):
        if not self.answers:
            raise AssertionError(f"unexpected prompt: {prompt!r}")
        return self.answers.pop(0)


@pytest.fixture(autouse=True)
def _config_home(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg"))
    monkeypatch.delenv("HFVK_LLAMA_URL", raising=False)
    monkeypatch.delenv("LLAMA_ARG_PORT", raising=False)


def _run(tmp_path, *answers):
    out: list = []
    wizard = Wizard(
        tmp_path / "config.toml",
        ask=Script(*answers),
        secret=Script(),
        out=out.append,
        detect=lambda extra=(): None,  # no llama.cpp on this machine
        login=lambda: True,
    )
    return wizard, wizard.run(), out


# After the speech step: language model (keep), hotkey, mode, language,
# Kai, history, navigation, self-corrections, then "save?".
_REST = ("", "", "", "", "", "", "", "", "")


def _speech_menu(out: list) -> list:
    start = out.index("How should your speech be transcribed?")
    return [line.strip() for line in out[start + 1:start + 5]]


def test_hosted_is_last_and_for_existing_subscribers(tmp_path):
    _, code, out = _run(tmp_path, "", *_REST[:-1])  # skip speech: no changes
    assert code == 0
    menu = _speech_menu(out)
    assert menu[0].startswith("1) My own API key")
    assert menu[1].startswith("2) A local OpenAI-compatible speech server")
    assert menu[2].startswith("3) HyperFurion hosted service")
    assert "existing subscribers only" in menu[2]
    assert "isn't on sale" in menu[2]
    assert menu[3].startswith("4) Skip for now")
    # The way out when nothing is set up names the key or server first.
    assert any("add a key or your own server" in line for line in out)
    assert not any("fully offline" in line for line in out)


def test_local_speaches_server_gets_model_ids(tmp_path):
    from voice_keyboard.config import is_usable, load_config
    from voice_keyboard.stt import create_stt_client
    from voice_keyboard.tts import create_tts_client

    _, code, out = _run(
        tmp_path,
        "2",   # a local speech server
        "",    # base URL: http://127.0.0.1:8000/v1
        "",    # transcription model: Systran/faster-whisper-large-v3
        "",    # it also speaks
        "",    # text-to-speech model: Kokoro
        "",    # voice: af_heart
        *_REST,
    )
    assert code == 0
    config = tomllib.loads((tmp_path / "config.toml").read_text())
    assert config["providers"]["openai"]["base_url"] == "http://127.0.0.1:8000/v1"
    assert config["stt"] == {"provider": "openai", "model": "Systran/faster-whisper-large-v3"}
    assert config["tts"] == {
        "provider": "openai",
        "model": "speaches-ai/Kokoro-82M-v1.0-ONNX",
        "voice_id": "af_heart",
    }
    assert is_usable(tmp_path / "config.toml")  # no key needed for a local server
    assert "Setup complete." in out

    effective = load_config(tmp_path / "config.toml")
    stt = create_stt_client(effective)
    stt = getattr(stt, "_inner", stt)
    assert stt._model == "Systran/faster-whisper-large-v3"  # not gpt-4o-transcribe
    tts = create_tts_client(effective)
    assert tts._model == "speaches-ai/Kokoro-82M-v1.0-ONNX"  # not gpt-4o-mini-tts
    assert tts._voice_id == "af_heart"  # not coral


def test_local_server_that_does_not_speak(tmp_path):
    """whisper.cpp only transcribes: [tts] must not point at it (read-aloud
    and Kai's voice would call a server that can't speak, and Kai would
    count a voice it doesn't have as local). Nothing speaks until you pick
    something; never a cloud voice by surprise."""
    from voice_keyboard.assistant.locality import kai_state
    from voice_keyboard.config import is_usable, load_config
    from voice_keyboard.tts import create_tts_client

    _, code, out = _run(
        tmp_path,
        "2", "http://127.0.0.1:8080/v1",  # whisper.cpp
        "whisper-1",
        "n",   # it does not speak
        "",    # what should speak: nothing for now
        *_REST,
    )
    assert code == 0
    config = tomllib.loads((tmp_path / "config.toml").read_text())
    assert config["stt"] == {"provider": "openai", "model": "whisper-1"}
    assert config["tts"] == {"provider": "none"}
    assert any("What should speak?" in line for line in out)
    assert is_usable(tmp_path / "config.toml")  # dictation still works offline
    effective = load_config(tmp_path / "config.toml")
    with pytest.raises(RuntimeError, match="Nothing is set up to speak"):
        create_tts_client(effective).synthesize("hello")
    speaks = [hop for hop in kai_state(effective).hops if hop.section == "[tts]"]
    assert speaks and "127.0.0.1" not in speaks[0].service


def test_local_server_that_does_not_speak_with_a_cloud_voice(tmp_path):
    wizard = Wizard(
        tmp_path / "config.toml",
        ask=Script(
            "2", "http://127.0.0.1:8080/v1", "whisper-1",
            "n",   # it does not speak
            "3",   # ElevenLabs speaks
            *_REST,
        ),
        secret=Script("el-key"),
        out=[].append,
        detect=lambda extra=(): None,
        login=lambda: True,
    )
    assert wizard.run() == 0
    config = tomllib.loads((tmp_path / "config.toml").read_text())
    assert config["tts"] == {"provider": "elevenlabs"}
    assert config["providers"]["elevenlabs"]["api_key"] == "el-key"
    assert config["providers"]["openai"]["base_url"] == "http://127.0.0.1:8080/v1"


def test_a_cloud_voice_drops_the_old_speech_servers_model_and_voice(tmp_path):
    # Re-run after a speech server that also spoke (Speaches): switching to
    # whisper.cpp and an xAI voice must not send Kokoro's model and voice
    # names to xAI, which doesn't know them.
    (tmp_path / "config.toml").write_text(
        '[providers.openai]\nbase_url = "http://127.0.0.1:8000/v1"\n\n'
        '[stt]\nprovider = "openai"\n\n'
        '[tts]\nprovider = "openai"\nmodel = "speaches-ai/Kokoro-82M-v1.0-ONNX"\nvoice_id = "af_heart"\n',
        encoding="utf-8",
    )
    wizard = Wizard(
        tmp_path / "config.toml",
        ask=Script(
            "2", "http://127.0.0.1:8080/v1", "whisper-1",
            "n",   # it does not speak
            "2",   # xAI speaks
            *_REST,
        ),
        secret=Script("xai-key"),
        out=[].append,
        detect=lambda extra=(): None,
        login=lambda: True,
    )
    assert wizard.run() == 0
    config = tomllib.loads((tmp_path / "config.toml").read_text())
    assert config["tts"]["provider"] == "xai"
    assert config["tts"].get("model", "") == ""
    assert config["tts"].get("voice_id", "") == ""
