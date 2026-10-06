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
        ("fr", "bonjour virgule ça va point d'interrogation à la ligne oui point", "Bonjour, ça va\u202f?\nOui."),
        ("de", "hallo komma wie geht's fragezeichen neuer absatz gut punkt", "Hallo, wie geht's?\n\nGut."),
        ("de", "er sagt anführungszeichen auf ja anführungszeichen zu", "Er sagt \u201eja\u201c"),
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


@pytest.mark.parametrize(
    "language, spoken, typed",
    [
        ("es", "estoy a punto de salir", "Estoy a punto de salir"),
        ("es", "son las tres en punto", "Son las tres en punto"),
        ("es", "desde mi punto de vista", "Desde mi punto de vista"),
        ("es", "hola punto adiós punto", "Hola. Adiós."),
        ("fr", "son point de vue", "Son point de vue"),
        ("fr", "à point", "À point"),
        ("fr", "vingt point final", "Vingt."),
        ("de", "um punkt zwei uhr", "Um punkt zwei uhr"),
        ("de", "hallo punkt", "Hallo."),
    ],
)
def test_a_bare_mark_is_speech_where_the_words_around_it_say_so(language, spoken, typed) -> None:
    assert _say(spoken, language=language) == typed


def test_english_decimals_survive_french_point() -> None:
    from voice_keyboard.flow.registers import TERMINAL

    grammar = Grammar(language="fr", numbers_on=True, numbers_min=0)
    out, _ = render_items(grammar.parse("three point five".split(), flush=True).items, initial_state(TERMINAL), TERMINAL)
    assert out == "3.5"


def test_a_bare_mark_waits_for_the_next_word_to_be_final() -> None:
    grammar = Grammar(language="es")
    held = grammar.parse(["hola", "punto"], frozen=0, settled=0)
    assert held.pending_from == 1
    decided = grammar.parse(["hola", "punto", "de"], frozen=0, settled=3)
    assert [i.kind for i in decided.items] == ["word", "word", "word"]
