from voice_keyboard.flow.grammar import Grammar, Item
from voice_keyboard.flow.numbers import convert_numbers, parse_number_run
from voice_keyboard.flow.registers import (
    PROSE,
    TERMINAL,
    VERBATIM,
    initial_state,
    register_for_app,
    render_items,
)


def render(tokens: list[str], grammar: Grammar, register=PROSE, **parse_kwargs) -> str:
    result = grammar.parse(tokens, **parse_kwargs)
    text, _ = render_items(result.items, initial_state(register), register)
    return text


class TestNumbers:
    def test_cardinals(self) -> None:
        assert parse_number_run(["twenty", "three"]) == "23"
        assert parse_number_run(["one", "hundred", "and", "five"]) == "105"
        assert parse_number_run(["twelve", "thousand", "three", "hundred"]) == "12300"
        assert parse_number_run(["zero"]) == "0"

    def test_invalid_cardinals(self) -> None:
        assert parse_number_run(["twenty", "ten"]) is None
        assert parse_number_run(["ten", "five"]) is None
        assert parse_number_run(["hundred"]) is None

    def test_decimals_and_digit_sequences(self) -> None:
        assert parse_number_run(["three", "point", "one", "four"]) == "3.14"
        assert parse_number_run(["one", "two", "seven"]) == "127"

    def test_convert_preserves_prose_singletons(self) -> None:
        assert convert_numbers(["one", "cat"], min_value=10) == ["one", "cat"]
        assert convert_numbers(["twenty", "three", "cats"], min_value=10) == ["23", "cats"]

    def test_attached_punctuation_survives(self) -> None:
        assert convert_numbers(["twenty", "three."], min_value=0) == ["23."]


class TestGrammar:
    def test_spoken_punctuation_and_breaks(self) -> None:
        g = Grammar()
        text = render("hello world period new line goodbye period".split(), g)
        assert text == "Hello world.\nGoodbye."

    def test_literal_escapes_a_command_word(self) -> None:
        g = Grammar()
        text = render("say literal period now".split(), g, register=TERMINAL)
        assert text == "say period now"

    def test_vocabulary_longest_match(self) -> None:
        g = Grammar(vocabulary={"hyper furion": "HyperFurion"})
        text = render("i love hyper furion a lot".split(), g)
        assert "HyperFurion" in text

    def test_vocabulary_keeps_attached_punctuation(self) -> None:
        g = Grammar(vocabulary={"hyper furion": "HyperFurion"})
        text = render("i love hyper furion, a lot".split(), g)
        assert "HyperFurion," in text

    def test_scratch_emits_action_item(self) -> None:
        g = Grammar()
        result = g.parse("hello scratch that".split(), flush=True)
        assert [item.kind for item in result.items] == ["word", "scratch"]

    def test_incomplete_phrase_is_held_back(self) -> None:
        g = Grammar()
        result = g.parse("hello scratch".split())
        assert result.pending_from == 1
        assert [item.kind for item in result.items] == ["word"]

    def test_flush_resolves_holdback_as_words(self) -> None:
        g = Grammar()
        result = g.parse("hello scratch".split(), flush=True)
        assert [item.text for item in result.items] == ["hello", "scratch"]

    def test_wake_word_pending_until_flush(self) -> None:
        g = Grammar()
        tokens = "fix this vk make it formal".split()
        live = g.parse(tokens)
        assert live.pending_from == 2

        flushed = g.parse(tokens, flush=True)
        assert flushed.items[-1].kind == "instruction"
        assert flushed.items[-1].text == "make it formal"

    def test_frozen_fence_stops_cross_boundary_phrases(self) -> None:
        g = Grammar()
        tokens = "scratch that".split()
        # With the fence after "scratch", the two tokens can never fuse
        # into the command — "scratch" was already committed as a word.
        result = g.parse(tokens, flush=True, frozen=1)
        assert [item.kind for item in result.items] == ["word", "word"]

    def test_command_remapping(self) -> None:
        g = Grammar(commands={"scratch_that": ["nuke it"]})
        result = g.parse("hello nuke it".split(), flush=True)
        assert result.items[-1].kind == "scratch"
        # The default phrase no longer applies.
        result = g.parse("hello scratch that".split(), flush=True)
        assert all(item.kind == "word" for item in result.items)

    def test_punctuation_remapping_and_removal(self) -> None:
        g = Grammar(punctuation={"period": "!", "comma": ""})
        text = render("hello period".split(), g)
        assert text == "Hello!"
        text = render("well comma yes".split(), g)
        assert text == "Well comma yes"

    def test_disabled_grammar_passes_words_through(self) -> None:
        g = Grammar(enabled=False)
        text = render("hello period scratch that".split(), g, register=VERBATIM)
        assert text == "hello period scratch that"

    def test_terminal_numbers(self) -> None:
        g = Grammar(numbers="auto", numbers_on=True, numbers_min=0)
        text = render("delete twenty three files".split(), g, register=TERMINAL)
        assert text == "delete 23 files"

    def test_number_run_at_tail_is_held(self) -> None:
        g = Grammar(numbers="always", numbers_on=True, numbers_min=0)
        result = g.parse("count twenty three".split())
        assert result.pending_from == 1


class TestRegisters:
    def test_register_for_app(self) -> None:
        assert register_for_app("kitty", "").name == "terminal"
        assert register_for_app("WindowsTerminal.exe", "").name == "terminal"
        assert register_for_app("firefox", "").name == "prose"
        assert register_for_app("", "terminal").name == "terminal"
        assert register_for_app("firefox", "", config_map={"firefox": "verbatim"}).name == "verbatim"
        assert register_for_app("", "", default="terminal").name == "terminal"

    def test_prose_capitalizes_after_sentence_enders(self) -> None:
        g = Grammar()
        text = render("first period second one question mark third".split(), g)
        assert text == "First. Second one? Third"

    def test_terminal_never_capitalizes(self) -> None:
        g = Grammar()
        text = render("git status period".split(), g, register=TERMINAL)
        assert text == "git status."

    def test_paired_glyph_spacing(self) -> None:
        g = Grammar()
        text = render("open paren hello close paren".split(), g, register=TERMINAL)
        assert text == "(hello)"

    def test_render_prefix_stability(self) -> None:
        """render(prefix) must always be a prefix of render(full) — the
        property the molten repair engine depends on."""
        g = Grammar(vocabulary={"hyper furion": "HyperFurion"})
        tokens = (
            "hello world period new line hyper furion typed twenty three"
            " words comma then more period"
        ).split()
        for register in (PROSE, TERMINAL, VERBATIM):
            full = render(tokens, g, register=register, flush=True)
            for cut in range(len(tokens)):
                items = g.parse(tokens[:cut], flush=False).items
                partial, _ = render_items(items, initial_state(register), register)
                assert full.startswith(partial), (
                    f"register={register.name} cut={cut}: {partial!r} "
                    f"is not a prefix of {full!r}"
                )
