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


class TestPausePunctuation:
    """A provider's sentence end at a pause stays revisable until the words
    after the pause decide it (grok-voice-transcribe-2.0 punctuates every
    pause-delimited chunk as a sentence)."""

    def _engine(self, mode="rules", **kwargs) -> FlowEngine:
        return make_engine(pause_review=mode, **kwargs)

    def test_off_keeps_the_providers_periods(self) -> None:
        engine = self._engine("off")
        engine.on_transcript("I was thinking about the project.", is_final=True, now=0.0)
        engine.on_transcript(
            "I was thinking about the project. And how", is_final=False, now=1.0
        )
        assert engine.desired_text() == "I was thinking about the project. And how"

    def test_a_continuation_joins_and_the_period_never_froze(self) -> None:
        engine = self._engine()
        engine.on_transcript("I was thinking about the project.", is_final=True, now=0.0)
        # The pause's word is final but still molten: nothing after it yet.
        assert engine._committed_render == "I was thinking about the"
        assert engine.desired_text() == "I was thinking about the project."
        engine.on_transcript(
            "I was thinking about the project. And how", is_final=False, now=1.0
        )
        assert engine.desired_text() == "I was thinking about the project and how"
        engine.on_transcript(
            "I was thinking about the project. And how we", is_final=False, now=1.5
        )
        assert engine._committed_render.endswith("project")
        result = engine.finalize(
            "I was thinking about the project. And how we could.", now=2.0
        )
        assert result.text == "I was thinking about the project and how we could."

    def test_a_real_sentence_end_keeps_its_period(self) -> None:
        engine = self._engine()
        engine.on_transcript("That works.", is_final=True, now=0.0)
        engine.on_transcript("That works. Thanks", is_final=False, now=1.0)
        engine.on_tick(now=2.0)
        result = engine.finalize("That works. Thanks.", now=2.5)
        assert result.text == "That works. Thanks."

    def test_the_period_ending_the_dictation_stays(self) -> None:
        engine = self._engine()
        result = engine.finalize("Send it to the team.", now=0.0)
        assert result.text == "Send it to the team."

    def test_an_unclear_pause_waits_for_the_reviewer(self) -> None:
        engine = self._engine("llm")
        engine.on_transcript("I think.", is_final=True, now=0.0)
        engine.on_transcript("I think. We should", is_final=False, now=0.5)
        assert engine.review_requests(now=0.5) == []  # too few words yet
        engine.on_transcript("I think. We should wait", is_final=False, now=1.0)
        engine.on_tick(now=1.1)
        # Still the provider's period: the reviewer hasn't answered, and
        # only the word before the pause is held back.
        assert engine.desired_text() == "I think. We should wait"
        assert engine._committed_render == "I"
        queries = engine.review_requests(now=1.1)
        assert [(q.index, q.before, q.after) for q in queries] == [
            (2, "I think.", "We should wait")
        ]
        assert engine.review_requests(now=1.2) == []  # handed out once
        assert engine.resolve_pause(2, "think we", now=1.5)
        assert engine.desired_text() == "I think we should wait"
        result = engine.finalize("I think. We should wait.", now=2.0)
        assert result.text == "I think we should wait."

    def test_a_bad_or_missing_reply_falls_back_to_the_rules(self) -> None:
        engine = self._engine("llm")
        engine.on_transcript("I think.", is_final=True, now=0.0)
        engine.on_transcript("I think. We should wait", is_final=False, now=0.5)
        [query] = engine.review_requests(now=0.6)
        assert engine.resolve_pause(query.index, "I don't know", now=0.7)
        assert engine.desired_text() == "I think. We should wait"
        assert not engine.resolve_pause(query.index, "think we", now=0.8)  # settled

    def test_a_review_that_never_answers_stops_holding_the_text(self) -> None:
        engine = self._engine("llm")
        engine.on_transcript("I think.", is_final=True, now=0.0)
        engine.on_transcript("I think. We should wait", is_final=True, now=0.5)
        assert engine.review_requests(now=0.6)
        engine.on_tick(now=3.0)
        assert engine._committed_render == "I"
        engine.on_tick(now=5.0)
        assert engine._committed_render == "I think. We should wait"

    def test_the_rules_settle_clear_pauses_without_a_review(self) -> None:
        engine = self._engine("llm")
        engine.on_transcript("Ship it today.", is_final=True, now=0.0)
        engine.on_transcript("Ship it today. But not the docs", is_final=False, now=0.5)
        assert engine.review_requests(now=0.5) == []
        assert engine.desired_text() == "Ship it today, but not the docs"

    def test_review_queries_show_earlier_decisions(self) -> None:
        engine = self._engine("llm")
        engine.on_transcript("Look at the.", is_final=True, now=0.0)
        engine.on_transcript("Look at the. Numbers first.", is_final=True, now=0.5)
        engine.on_transcript(
            "Look at the. Numbers first. Then we decide", is_final=False, now=1.0
        )
        [query] = engine.review_requests(now=1.0)
        assert query.before == "Look at the Numbers first."

    def test_scratch_after_a_pause_removes_the_segment_before_it(self) -> None:
        engine = self._engine()
        engine.on_transcript("Hello there.", is_final=True, now=0.0)
        engine.on_transcript("Hello there. Send the email.", is_final=True, now=1.0)
        engine.on_transcript(
            "Hello there. Send the email. Scratch that.", is_final=True, now=2.0
        )
        result = engine.finalize("Hello there. Send the email. Scratch that.", now=3.0)
        assert result.text == "Hello there."
        assert result.scratches == 1

    def test_the_molten_valve_freezes_what_shows(self) -> None:
        engine = self._engine("llm", max_molten_chars=20)
        engine.on_transcript("I think.", is_final=True, now=0.0)
        engine.on_transcript(
            "I think. We should wait for the numbers before deciding",
            is_final=False,
            now=0.5,
        )
        # Over the molten limit: the pause is committed as it showed.
        assert engine._committed_render.startswith("I think.")
        assert not engine.resolve_pause(2, "think we", now=0.6)

    def test_a_terminal_never_reviews_pauses(self) -> None:
        engine = make_engine(register=TERMINAL, pause_review="rules")
        engine.on_transcript("git status.", is_final=True, now=0.0)
        engine.on_transcript("git status. And", is_final=False, now=0.5)
        assert engine.desired_text() == "git status. And"

    def test_a_pause_nobody_asks_about_is_released_too(self) -> None:
        engine = self._engine("llm")
        engine.on_transcript("I think.", is_final=True, now=0.0)
        engine.on_transcript("I think. We should wait", is_final=False, now=0.5)
        engine.on_tick(now=4.0)
        assert engine._committed_render == "I"
        engine.on_tick(now=6.0)  # never requested: released by the rules
        assert engine._committed_render.startswith("I think.")
