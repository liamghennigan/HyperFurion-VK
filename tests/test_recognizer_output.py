"""What a real recognizer writes: capitals, its own sentence stops,
hyphenated numbers ("Twenty-five"), quotes around a phrase, the wake word
spelled "Vk" or "V.K.". The page's examples assume lowercase words with no
punctuation; these keep them true for the text a recognizer actually
sends. The page's engine is held to the same results by
tests/flow_corpus.json (scripts/flow_corpus.py)."""

import logging

import pytest

from voice_keyboard.flow.engine import FlowConfig, FlowEngine
from voice_keyboard.flow.grammar import Grammar
from voice_keyboard.flow.numbers import parse_day, split_compound
from voice_keyboard.flow.registers import (
    JAVASCRIPT,
    PROSE,
    PYTHON,
    SHELL,
    TERMINAL,
    initial_state,
    render_items,
)


def dictate(segments: list[str], register=PROSE, *, nav: bool = False, pause_review: str = "off") -> FlowEngine:
    code = bool(register.compiler) or register.terminal
    grammar = Grammar(numbers_on=register.numbers_on, numbers_min=register.numbers_min, nav=nav, code=code)
    engine = FlowEngine(FlowConfig(pause_review=pause_review), grammar, register)
    merged = ""
    for now, segment in enumerate(segments, 1):
        merged = (merged + " " + segment).strip()
        engine.on_transcript(merged, is_final=True, now=float(now))
        while engine.pending_action() is not None:
            engine.complete_action(float(now), pressed=True)
    return engine


def typed(segments: list[str], register=PROSE, **kwargs) -> str:
    engine = dictate(segments, register, **kwargs)
    result = engine.finalize(" ".join(segments), now=99.0)
    while result.action is not None:
        result = engine.complete_action(99.0, pressed=True)
    return result.typed_before + result.text


def rendered(text: str, register=PROSE) -> str:
    code = bool(register.compiler) or register.terminal
    grammar = Grammar(numbers_on=register.numbers_on, numbers_min=register.numbers_min, code=code)
    items = grammar.parse(text.split(), flush=True).items
    return render_items(items, initial_state(register), register)[0]


class TestHyphenatedNumbers:
    def test_split_compound(self) -> None:
        assert split_compound("Twenty-five") == ["Twenty", "five"]
        assert split_compound("twenty-five,") == ["twenty", "five,"]
        assert split_compound("three-thirty") == ["three", "thirty"]
        assert split_compound("four-oh-two.") == ["four", "oh", "two."]
        for word in ("twenty-first", "nine-ish", "one-on-one", "well-known", "twenty", "-five", "twenty--five"):
            assert split_compound(word) is None, word

    def test_a_hyphenated_ordinal_is_a_day(self) -> None:
        assert parse_day(["twenty-first"]) == 21
        assert parse_day(["thirty-first"]) == 31
        assert parse_day(["first-ever"]) is None

    def test_the_page_example_as_a_recognizer_writes_it(self) -> None:
        assert typed(["Twenty-five percent by October sixth."]) == "25% by October 6."
        assert typed(["twenty five percent by october sixth"]) == "25% by October 6"

    def test_folds_read_hyphens_as_spoken_words(self) -> None:
        assert typed(["Meet at three-thirty PM in room four-oh-two."]) == "Meet at 3:30 PM in room 402."
        assert typed(["Call five-five-five-one-two-three-four."]) == "Call 555-1234."
        assert typed(["Born June twenty-first, nineteen eighty-four."]) == "Born June 21, 1984."
        assert typed(["It costs twenty-five dollars and fifty-five cents."]) == "It costs $25.55."

    def test_a_number_that_does_not_fold_stays_as_written(self) -> None:
        assert typed(["Twenty-five people came, fifty-fifty."]) == "Twenty-five people came, fifty-fifty."
        assert typed(["I'm twenty-five.", "A one-two punch."]) == "I'm twenty-five. A one-two punch."

    def test_a_terminal_gets_digits(self) -> None:
        assert typed(["Twenty-three failed tests, rerun the flaky ones."], TERMINAL) == (
            "23 failed tests, rerun the flaky ones"
        )

    def test_oh_is_a_word_in_a_terminal(self) -> None:
        assert typed(["room four-oh-two"], TERMINAL) == "room four-oh-two"

    def test_a_phone_number_ends_on_the_recognizers_stop(self) -> None:
        assert typed(["Call five five five one two three four."]) == "Call 555-1234."
        assert typed(["Call five five five one two three four, then hang up."]) == "Call 555-1234, then hang up."

    def test_a_hyphenated_word_committed_as_written_stays_so(self, caplog: pytest.LogCaptureFixture) -> None:
        # "euros" kept "three-thirty" from becoming a time; at the stop the
        # word after it is past the fence, and it must not fold then
        engine = FlowEngine(FlowConfig(), Grammar(), PROSE)
        with caplog.at_level(logging.WARNING, logger="voice_keyboard.flow.engine"):
            engine.on_transcript("one not at", is_final=True, now=1.0)
            engine.on_transcript("one not at three-thirty euros type", is_final=False, now=2.0)
            result = engine.finalize("one not at three-thirty euros type", now=3.0)
        assert result.text == "One not at three-thirty euros type"
        assert not caplog.records


class TestCorrect:
    def test_the_recognizers_stop_ends_the_command_not_the_word(self) -> None:
        said = ["The launch is on Monday period.", "We ship at noon period.", "Correct Monday to Friday."]
        assert typed(said) == "The launch is on Friday. We ship at noon."
        assert typed(said, pause_review="rules") == "The launch is on Friday. We ship at noon."

    def test_mid_sentence(self) -> None:
        said = ["The launch on Monday is confirmed.", "Correct Monday to Friday."]
        assert typed(said) == "The launch on Friday is confirmed."

    def test_it_applies_when_the_utterance_closes(self) -> None:
        engine = dictate(["The launch is on Monday.", "Correct Monday to Friday."], pause_review="rules")
        assert engine.desired_text() == "The launch is on Friday."

    def test_a_spoken_period_is_not_typed_twice(self) -> None:
        assert typed(["See you Monday.", "Correct Monday to Friday period."]) == "See you Friday."


class TestCommandsAsARecognizerWritesThem:
    def test_a_quoted_caret_command_acts(self) -> None:
        engine = dictate(["Ship it on Friday.", 'Select "Previous Word".'], nav=True)
        assert engine.pending_action() is None  # pressed by dictate()
        result = engine.finalize('Ship it on Friday. Select "Previous Word".', now=9.0)
        assert result.typed_before == "Ship it on Friday."
        assert result.text == ""

    def test_curly_quotes_too(self) -> None:
        engine = FlowEngine(FlowConfig(), Grammar(nav=True), PROSE)
        engine.on_transcript("Hello there.", is_final=True, now=1.0)
        engine.on_transcript("Hello there. Go to “end of line.”", is_final=True, now=2.0)
        action = engine.pending_action()
        assert action is not None and action.action == "move:line:end"

    def test_quoted_words_inside_a_sentence_type_as_said(self) -> None:
        assert typed(['I said "select previous word" twice.'], nav=True) == 'I said "select previous word" twice.'

    def test_an_emoji_said_alone_takes_no_stop(self) -> None:
        assert typed(["Emoji rocket."]) == "🚀"
        assert typed(["We launched today.", "Emoji rocket."], pause_review="rules") == "We launched today. 🚀"

    def test_an_emoji_ending_a_sentence_keeps_it(self) -> None:
        assert typed(["Ship it emoji rocket."]) == "Ship it 🚀."

    def test_spell_that_a_name(self) -> None:
        said = ["I'll send it to Shivon.", "Spell that S I O B H A N."]
        assert typed(said, pause_review="rules") == "I'll send it to Siobhan."
        assert typed(["i'll send it to Shivon", "spell that s i o b h a n"]) == "I'll send it to Siobhan"

    def test_spell_that_applies_when_the_utterance_closes(self) -> None:
        engine = dictate(["I'll send it to Shivon.", "Spell that S I O B H A N."], pause_review="rules")
        assert engine.desired_text() == "I'll send it to Siobhan."


class TestWakeWord:
    @pytest.mark.parametrize("heard", ["VK,", "Vk", "vk.", "V.K.,", "V-K", "Veekay,"])
    def test_spellings_of_the_wake_word(self, heard: str) -> None:
        result = dictate(["I think it works now.", f"{heard} make that formal."]).finalize(
            f"I think it works now. {heard} make that formal.", now=9.0
        )
        assert result.text == "I think it works now."
        assert result.instruction == "make that formal."

    def test_two_letters(self) -> None:
        result = dictate(["V K, run find every to do."], SHELL).finalize("V K, run find every to do.", now=9.0)
        assert result.instruction == "run find every to do."

    @pytest.mark.parametrize("said", ["Tell Vicky I said hi.", "Bk is closed.", "The tooth decay, the k value."])
    def test_names_and_sound_alikes_are_dictation(self, said: str) -> None:
        result = dictate([said]).finalize(said, now=9.0)
        assert result.instruction == ""
        assert result.text == said

    def test_another_wake_word_has_no_aliases(self) -> None:
        grammar = Grammar(wake_word="jarvis")
        assert grammar.wake_at(["vk"], 0) == 0
        assert grammar.wake_at(["jarvis"], 0) == 1


class TestCodeRegisters:
    def test_python(self) -> None:
        assert typed(["For i in range ten colon.", "Print i close paren."], PYTHON) == "for i in range(10): print(i)"

    def test_javascript(self) -> None:
        assert typed(["Const total equals count plus one."], JAVASCRIPT) == "const total = count + 1"

    def test_shell(self) -> None:
        assert typed(["List files pipe grep dash i error."], SHELL) == "list files | grep -i error"

    def test_a_terminal_keeps_what_was_said(self) -> None:
        # a spoken "period", capitals inside the sentence, the pronoun
        assert typed(["Echo hello GitHub period"], TERMINAL) == "echo hello GitHub."
        assert typed(["Grep TODO. I think so!"], TERMINAL) == "grep TODO I think so"
        assert typed(["cd .."], TERMINAL) == "cd .."

    def test_the_panes_see_the_same(self) -> None:
        said = "Twenty-three failed tests, rerun the flaky ones."
        assert rendered(said, PROSE) == "Twenty-three failed tests, rerun the flaky ones."
        assert rendered(said, TERMINAL) == "23 failed tests, rerun the flaky ones"

    def test_prose_is_untouched(self) -> None:
        assert typed(["List files. Then grep."]) == "List files. Then grep."
