from voice_keyboard.flow.engine import FlowConfig, FlowEngine, risky_backspace
from voice_keyboard.flow.grammar import Grammar
from voice_keyboard.flow.registers import PROSE, TERMINAL


def make_engine(register=PROSE, grammar=None, **cfg_kwargs) -> FlowEngine:
    config = FlowConfig(**cfg_kwargs)
    return FlowEngine(config, grammar or Grammar(), register)


class TestCommitPolicy:
    def test_nothing_commits_before_stability(self) -> None:
        engine = make_engine(stability_ms=1000, stability_updates=2)
        engine.on_transcript("hello world", is_final=False, now=0.0)
        assert engine.desired_text() == "Hello world"
        # Preview is molten: a full revision may still rewrite everything.
        engine.on_transcript("yellow whirled", is_final=False, now=0.1)
        assert engine.desired_text() == "Yellow whirled"

    def test_stable_words_commit_and_freeze(self) -> None:
        engine = make_engine(stability_ms=100, stability_updates=1, adaptive=False)
        engine.on_transcript("hello world", is_final=False, now=0.0)
        engine.on_transcript("hello world again", is_final=False, now=0.2)
        engine.on_tick(now=0.5)
        # "hello" and "world" survived an update and the horizon: committed.
        engine.on_transcript("goodbye planet again", is_final=False, now=0.6)
        desired = engine.desired_text()
        assert desired.startswith("Hello world"), desired

    def test_is_final_commits_covered_words(self) -> None:
        engine = make_engine()
        engine.on_transcript("hello world", is_final=True, now=0.0)
        engine.on_transcript("hello world", is_final=False, now=0.1)
        # Revision below the committed floor is ignored.
        engine.on_transcript("yellow whirled", is_final=False, now=0.2)
        assert engine.desired_text() == "Hello world"

    def test_desired_never_rewrites_committed_prefix(self) -> None:
        engine = make_engine(stability_ms=50, stability_updates=1, adaptive=False)
        previous_committed = ""
        now = 0.0
        transcript = ""
        for word in "the quick brown fox jumps over the lazy dog".split():
            transcript = f"{transcript} {word}".strip()
            engine.on_transcript(transcript, is_final=False, now=now)
            engine.on_tick(now=now + 0.2)
            desired = engine.desired_text()
            assert desired.startswith(previous_committed)
            previous_committed = engine._committed_render
            now += 0.3

    def test_finalize_equals_classic_render(self) -> None:
        """Live interim sequences and a single finalize converge to the
        same text when the final extends (rather than retro-revises) the
        committed words — the normal streaming case."""
        updates = [
            "just",
            "just to be",
            "just to be clear it is",
            "just to be clear it is not always",
        ]
        final = "just to be clear it is not always doubling up"

        live = make_engine(stability_ms=100, stability_updates=1, adaptive=False)
        now = 0.0
        for update in updates:
            live.on_transcript(update, is_final=False, now=now)
            live.on_tick(now=now + 0.15)
            now += 0.2
        live_result = live.finalize(final, now=now)

        classic = make_engine()
        classic_result = classic.finalize(final, now=0.0)
        assert live_result.text == classic_result.text

    def test_finalize_that_revises_committed_words_keeps_the_committed_form(self) -> None:
        """Committed text is frozen: a finalize that retro-punctuates an
        already-committed word keeps the committed spelling, and only the
        molten suffix adopts the provider's final form."""
        live = make_engine(stability_ms=100, stability_updates=1, adaptive=False)
        live.on_transcript("just to be clear it is", is_final=False, now=0.0)
        live.on_transcript("just to be clear it is", is_final=False, now=0.2)
        live.on_tick(now=1.0)  # commits all six words
        committed = live._committed_render
        assert committed.startswith("Just to be clear")
        result = live.finalize(
            "Just to be clear, it is not always doubling up.", now=1.2
        )
        assert result.text.startswith(committed)
        assert result.text.endswith("not always doubling up.")

    def test_max_molten_chars_forces_commits(self) -> None:
        engine = make_engine(stability_ms=60_000, stability_updates=99, max_molten_chars=20)
        engine.on_transcript(
            "a very long sentence that keeps going and going", is_final=False, now=0.0
        )
        # The tail is capped: older words must have committed.
        assert len(engine.desired_text()) - len(engine._committed_render) <= 20


class TestScratch:
    def test_scratch_that_removes_last_segment(self) -> None:
        engine = make_engine()
        engine.on_transcript("this is a test", is_final=True, now=0.0)
        result = engine.finalize("this is a test scratch that hello period", now=1.0)
        assert result.text == "Hello."
        assert result.scratches == 1

    def test_scratch_with_no_prior_segment_clears_everything(self) -> None:
        engine = make_engine()
        result = engine.finalize("wrong thing scratch that", now=0.0)
        assert result.text == ""


class TestWakeWord:
    def test_instruction_extracted_at_finalize(self) -> None:
        engine = make_engine()
        engine.on_transcript("send the invoice", is_final=True, now=0.0)
        result = engine.finalize("send the invoice vk make that formal", now=1.0)
        assert result.text == "Send the invoice"
        assert result.instruction == "make that formal"

    def test_instruction_words_are_never_typed_live(self) -> None:
        engine = make_engine(stability_ms=50, stability_updates=1, adaptive=False)
        engine.on_transcript("hello vk delete everything", is_final=False, now=0.0)
        engine.on_tick(now=5.0)  # far past any horizon or holdback expiry
        assert "delete" not in engine.desired_text()
        assert "vk" not in engine.desired_text()
        assert "⌁" in engine.caption()


class TestNonAsciiSafety:
    def test_non_ascii_commits_eagerly(self) -> None:
        engine = make_engine(stability_ms=60_000, stability_updates=99)
        engine.on_transcript("café time", is_final=False, now=0.0)
        engine.on_transcript("café time", is_final=False, now=0.1)
        # Way below the stability horizon, yet café must already be frozen.
        assert engine._committed_render.startswith("Café")

    def test_risky_backspace_detection(self) -> None:
        assert not risky_backspace("café")           # single codepoint, 1 BS
        assert risky_backspace("emoji 🎉")            # astral plane
        assert risky_backspace("é")             # combining accent


class TestHoldbackExpiry:
    def test_trailing_half_phrase_eventually_types(self) -> None:
        engine = make_engine(stability_ms=200, stability_updates=1, adaptive=False)
        engine.on_transcript("open", is_final=False, now=0.0)
        assert engine.desired_text() == ""  # held: could become "open quote"
        engine.on_tick(now=1.0)  # 2x stability passed: released as a word
        assert engine.desired_text() == "Open"


class TestTerminalRegister:
    def test_terminal_output(self) -> None:
        grammar = Grammar(numbers="auto", numbers_on=True, numbers_min=0)
        engine = FlowEngine(FlowConfig(), grammar, TERMINAL)
        result = engine.finalize("head dash n twenty lines period", now=0.0)
        assert result.text == "head - n 20 lines."
