"""[tts] provider = "none": nothing speaks (a local speech server that only
transcribes, such as whisper.cpp). Setup offers it when the local server
doesn't speak, instead of pointing [tts] at a server that can't."""

import pytest

from voice_keyboard.assistant.locality import NO_VOICE, kai_state
from voice_keyboard.config import _default_config_with_paths, validate_config
from voice_keyboard.prefetch import prefetch_enabled
from voice_keyboard.tts import create_tts_client


def _whisper_cpp(**tts) -> dict:
    cfg = _default_config_with_paths()
    cfg["providers"]["openai"]["base_url"] = "http://127.0.0.1:8080/v1"
    cfg["stt"]["provider"] = "openai"
    cfg["tts"].update(provider="none", **tts)
    cfg["llm"].update(provider="openai", base_url="http://127.0.0.1:8081/v1", model="qwen")
    return cfg


def test_needs_no_key() -> None:
    validate_config(_whisper_cpp())


def test_reading_aloud_says_why() -> None:
    with pytest.raises(RuntimeError, match=r'Nothing is set up to speak \(\[tts\] provider = "none"\)'):
        create_tts_client(_whisper_cpp()).synthesize("hello")


@pytest.mark.parametrize("mode", ["auto", "always"])
def test_never_prefetches(mode) -> None:
    assert prefetch_enabled(_whisper_cpp(prefetch=mode)) is False


def test_kai_stays_local_and_shows_its_answers() -> None:
    state = kai_state(_whisper_cpp())
    assert state.on and state.local
    speaks = next(hop for hop in state.hops if hop.section == "[tts]")
    assert speaks.service == NO_VOICE and speaks.local
