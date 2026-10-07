"""`voice-keyboard commands` lists what you taught it too: words accepted
with `voice-keyboard learned` (they type in dictation and in `try`) and
macros you named ("VK, trailer"), so "everything you can say" is true."""

import pytest

from voice_keyboard import dictionary
from voice_keyboard.cheatsheet import render
from voice_keyboard.config import DEFAULT_CONFIG


@pytest.fixture(autouse=True)
def state(tmp_path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path))
    return tmp_path


def _config(**flow):
    config = {key: dict(value) if isinstance(value, dict) else value for key, value in DEFAULT_CONFIG.items()}
    config["flow"] = {**DEFAULT_CONFIG["flow"], **flow}
    return config


def test_learned_words_are_listed():
    dictionary.add_word("hyper furion", "HyperFurion")
    text = render(_config())
    assert "hyper furion" in text and "HyperFurion" in text
    assert "voice-keyboard learned" in text


def test_learned_words_follow_personal_dictionary():
    dictionary.add_word("hyper furion", "HyperFurion")
    text = render(_config(personal_dictionary=False))
    assert "HyperFurion" not in text


def test_explicit_vocabulary_wins_and_is_listed_once():
    dictionary.add_word("hyper furion", "Hyperfurion")
    text = render(_config(vocabulary={"hyper furion": "HyperFurion"}))
    assert "HyperFurion" in text
    assert "Hyperfurion" not in text
    assert text.count("hyper furion") == 1


def test_named_macros_are_listed():
    data = dictionary.load_dictionary()
    data["macros"]["trailer"] = "Sent from my keyboard"
    dictionary.save_dictionary(data)
    text = render(_config())
    assert "vk, trailer" in text
    assert "macro" in text
