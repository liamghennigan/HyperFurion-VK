import pytest

from voice_keyboard import trial
from voice_keyboard.config import DEFAULT_CONFIG


def _config(**flow):
    return {**DEFAULT_CONFIG, "flow": {**DEFAULT_CONFIG["flow"], **flow}}


def test_prose_and_registers() -> None:
    assert trial.run(_config(), "hello comma world period".split()) == "Hello, world."
    assert trial.run(_config(), "python: x equals snake case user id".split()) == "x = user_id"


def test_pauses_split_utterances_for_commands_said_alone() -> None:
    said = "the meeting is on monday period | correct monday to friday".split()
    assert trial.run(_config(), said) == "The meeting is on Friday."


def test_config_applies_and_instructions_are_noted() -> None:
    assert trial.run(_config(vocabulary={"hyper furion": "HyperFurion"}), ["hyper", "furion"]) == "HyperFurion"
    assert trial.run(_config(language="es"), "hola coma adiós punto final".split()) == "Hola, adiós."
    assert trial.run(_config(), "vk make that formal".split()).endswith("[instruction: make that formal]")


def test_nothing_said_is_an_error() -> None:
    with pytest.raises(ValueError):
        trial.run(_config(), ["prose:"])
