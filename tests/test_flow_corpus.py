"""The flow parity corpus (scripts/flow_corpus.py): the daemon's engine
still produces tests/flow_corpus.json, which the landing page's engine
port (docs/js/flow.js) is checked against under `node --test docs/js/test`
in CI. Plus the regressions the corpus caught."""

import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

import flow_corpus  # noqa: E402

from voice_keyboard.flow.engine import FlowConfig, FlowEngine  # noqa: E402
from voice_keyboard.flow.grammar import Grammar  # noqa: E402
from voice_keyboard.flow.registers import PROSE, TERMINAL  # noqa: E402


def test_corpus_is_current() -> None:
    written = json.loads((ROOT / "tests" / "flow_corpus.json").read_text())
    assert written == flow_corpus.build(), "run scripts/flow_corpus.py"


def test_every_case_keeps_the_reparse_invariant(caplog: pytest.LogCaptureFixture) -> None:
    """Parsing below the committed fence must reproduce the committed
    items — or finalize commits them twice."""
    with caplog.at_level("WARNING", logger="voice_keyboard.flow.engine"):
        for name, register, segments, opts in flow_corpus.CASES:
            caplog.clear()
            flow_corpus.run_case(register, segments, opts)
            assert not caplog.records, name


class TestReparseBelowTheFence:
    def test_folded_numbers_do_not_duplicate_the_last_word_at_stop(self) -> None:
        engine = FlowEngine(FlowConfig(), Grammar(numbers_on=True, numbers_min=0), TERMINAL)
        engine.on_transcript("twenty three failed tests", is_final=True, now=1.0)
        assert engine.desired_text() == "23 failed tests"
        assert engine.finalize("twenty three failed tests", now=2.0).text == "23 failed tests"

    def test_folded_numbers_survive_a_second_segment(self) -> None:
        engine = FlowEngine(FlowConfig(), Grammar(numbers_on=True, numbers_min=0), TERMINAL)
        engine.on_transcript("ping one two seven dot zero", is_final=True, now=1.0)
        engine.on_transcript("ping one two seven dot zero dot one", is_final=True, now=2.0)
        assert engine.finalize("ping one two seven dot zero dot one", now=3.0).text == "ping 127 dot 0 dot 1"

    def test_a_number_after_the_fence_cannot_change_a_committed_one(self) -> None:
        engine = FlowEngine(FlowConfig(), Grammar(numbers_on=True, numbers_min=0), TERMINAL)
        engine.on_transcript("port eighty", is_final=True, now=1.0)
        engine.on_tick(now=5.0)  # the held run expires and commits as "80"
        assert engine.desired_text() == "port 80"
        engine.on_transcript("port eighty hundred", is_final=True, now=6.0)
        assert engine.finalize("port eighty hundred", now=7.0).text == "port 80 hundred"

    def test_literal_does_not_duplicate_the_last_word_at_stop(self) -> None:
        engine = FlowEngine(FlowConfig(), Grammar(), PROSE)
        engine.on_transcript("say literal period now", is_final=True, now=1.0)
        assert engine.finalize("say literal period now", now=2.0).text == "Say period now"

    def test_a_bare_literal_reads_back_bare(self) -> None:
        engine = FlowEngine(FlowConfig(), Grammar(), PROSE)
        engine.on_transcript("say literal", is_final=True, now=1.0)
        result = engine.finalize("say literal", now=2.0)
        assert result.text == "Say literal"
