import pytest

from voice_keyboard.config import DEFAULT_CONFIG, _validate_flow_config
from voice_keyboard.flow.grammar import Grammar
from voice_keyboard.flow.registers import PROSE, initial_state, render_items


def _say(text: str, **kwargs) -> str:
    grammar = Grammar(**kwargs)
    out, _ = render_items(grammar.parse(text.split(), flush=True).items, initial_state(PROSE), PROSE)
    return out


@pytest.mark.parametrize(
    "language, spoken, typed",
    [
        ("es", "abre interrogación vienes mañana cierra interrogación nueva línea sí coma claro punto",
         "¿Vienes mañana?\nSí, claro."),
        ("fr", "bonjour virgule ça va point d'interrogation à la ligne oui point", "Bonjour, ça va?\nOui."),
        ("de", "hallo komma wie geht's fragezeichen neuer absatz gut punkt", "Hallo, wie geht's?\n\nGut."),
    ],
)
def test_a_language_pack_adds_its_phrases(language: str, spoken: str, typed: str) -> None:
    assert _say(spoken, language=language) == typed


def test_scratch_in_another_language_is_a_scratch() -> None:
    items = Grammar(language="fr").parse("bonjour efface ça".split(), flush=True).items
    assert [item.kind for item in items] == ["word", "scratch"]


def test_english_still_works_under_a_pack_and_packs_stay_off_by_default() -> None:
    assert _say("hola comma adiós period", language="es") == "Hola, adiós."
    assert _say("hola coma adiós punto") == "Hola coma adiós punto"


def test_user_commands_still_override_a_pack() -> None:
    assert _say("uno nueva línea dos", language="es", commands={"new_line": ["siguiente"]}) == "Uno nueva línea dos"


def test_config_rejects_an_unknown_language() -> None:
    config = {**DEFAULT_CONFIG, "flow": {**DEFAULT_CONFIG["flow"], "language": "xx"}}
    with pytest.raises(RuntimeError, match="flow.language"):
        _validate_flow_config(config)
