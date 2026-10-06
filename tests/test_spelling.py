"""Spell-to-fix: letters in their many spoken shapes, the grammar's
"spell that ..." / "spell ..." items, the engine's in-place swap, the
dictionary candidates it offers, and the daemon end to end."""

import asyncio

import pytest
from fakes import FakeStreamingSTT, RecordingInjector, _make_daemon
from waiting import wait_until

from voice_keyboard import dictionary
from voice_keyboard.flow.engine import FlowConfig, FlowEngine
from voice_keyboard.flow.grammar import Grammar
from voice_keyboard.flow.registers import PROSE, TERMINAL
from voice_keyboard.flow.spelling import MAX_SPELLED_LETTERS, letters_at


@pytest.fixture(autouse=True)
def state_dir(tmp_path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path))
    return tmp_path


def make_engine(register=PROSE, grammar=None) -> FlowEngine:
    return FlowEngine(FlowConfig(), grammar or Grammar(), register)


class TestLetters:
    @pytest.mark.parametrize(
        "tokens, expected",
        [
            (["N"], ("n", 1)),
            (["x."], ("x", 1)),
            (["november"], ("n", 1)),
            (["X-ray"], ("x", 1)),
            (["eight"], ("8", 1)),
            (["capital", "k"], ("K", 2)),
            (["Capital", "kilo"], ("K", 2)),
            (["N-G-I-N-X"], ("nginx", 1)),
            (["hello"], ("", 0)),
            (["capital"], ("", 0)),
            (["capital", "eight"], ("", 0)),
        ],
    )
    def test_letters_at(self, tokens, expected) -> None:
        assert letters_at(tokens, 0) == expected


class TestGrammar:
    def _items(self, text: str, **kwargs):
        return Grammar().parse(text.split(), **kwargs)

    def test_spell_that_replaces(self) -> None:
        result = self._items("hello wrold spell that w o r l d okay")
        respell = [item for item in result.items if item.kind == "respell"]
        assert len(respell) == 1
        assert (respell[0].text, respell[0].mode, respell[0].span) == ("world", "replace", (2, 9))
        assert result.items[-1].text == "okay"

    def test_spell_inserts_and_mixes_shapes(self) -> None:
        result = self._items("deploy to spell k eight s now", flush=True)
        respell = [item for item in result.items if item.kind == "respell"]
        assert (respell[0].text, respell[0].mode) == ("k8s", "insert")

    def test_tail_run_is_held_back(self) -> None:
        result = self._items("hello spell that n g")
        assert result.pending_from == 1
        assert [item.text for item in result.items] == ["hello"]

    def test_trailing_capital_waits_for_its_letter(self) -> None:
        result = self._items("spell that n capital")
        assert result.pending_from == 0

    def test_flush_resolves_the_run(self) -> None:
        result = self._items("hello spell that n g", flush=True)
        assert result.items[-1].kind == "respell"
        assert result.items[-1].text == "ng"

    def test_prose_spell_stays_a_word(self) -> None:
        result = self._items("cast a spell on them", flush=True)
        assert [item.kind for item in result.items] == ["word"] * 5

    def test_insert_needs_two_letters(self) -> None:
        result = self._items("spell a word", flush=True)
        assert all(item.kind == "word" for item in result.items)

    def test_run_is_capped(self) -> None:
        letters = " ".join(["a"] * (MAX_SPELLED_LETTERS + 5))
        result = self._items(f"spell {letters}", flush=True)
        assert len(result.items[0].text) == MAX_SPELLED_LETTERS

    def test_frozen_run_never_grows_past_the_fence(self) -> None:
        # Committed (flushed) as "ng" when the fence was at token 4; later
        # letters are new words, never a longer spelled run.
        tokens = "spell that n g i".split()
        result = Grammar().parse(tokens, frozen=4)
        assert result.items[0].kind == "respell"
        assert result.items[0].text == "ng"
        assert result.items[0].span == (0, 4)

    def test_disabled(self) -> None:
        result = Grammar(spelling=False).parse("spell that n g".split(), flush=True)
        assert all(item.kind == "word" for item in result.items)


class TestEngine:
    def test_replaces_last_word_keeping_punctuation_and_case(self) -> None:
        engine = make_engine()
        engine.on_transcript("I run wrold.", is_final=True, now=0.0)
        result = engine.finalize("I run wrold. spell that w o r l d", now=1.0)
        assert result.text == "I run world."
        assert result.corrections == (("wrold", "world"),)

    def test_keeps_capital_of_replaced_word(self) -> None:
        engine = make_engine()
        result = engine.finalize("Ngnix spell that n g i n x", now=0.0)
        assert result.text == "Nginx"

    def test_insert_types_the_spelled_word(self) -> None:
        engine = make_engine(TERMINAL)
        result = engine.finalize("kubectl spell k eight s", now=0.0)
        assert result.text == "kubectl k8s"
        assert result.corrections == ()

    def test_replace_waits_for_a_final(self) -> None:
        engine = make_engine()
        engine.on_transcript("hello wrold spell that w o r l d then", is_final=False, now=0.0)
        for step in range(1, 20):
            engine.on_transcript(
                "hello wrold spell that w o r l d then", is_final=False, now=step
            )
            engine.on_tick(now=step + 0.5)
        assert "wrold" in engine.desired_text().lower()
        engine.on_transcript("hello wrold spell that w o r l d then", is_final=True, now=30)
        assert engine.desired_text() == "Hello world then"

    def test_letters_may_follow_in_the_next_segment(self) -> None:
        engine = make_engine()
        engine.on_transcript("hello wrold", is_final=True, now=0.0)
        engine.on_transcript("hello wrold spell that", is_final=True, now=1.0)
        assert "spell" not in engine.desired_text().lower()  # held, not typed
        engine.on_transcript("hello wrold spell that w o r l d", is_final=True, now=2.0)
        assert engine.desired_text() == "Hello world"

    def test_nothing_to_replace(self) -> None:
        result = make_engine().finalize("spell that a b", now=0.0)
        assert result.text == ""

    def test_scratch_after_respell_rewinds_to_the_shifted_boundary(self) -> None:
        engine = make_engine()
        engine.on_transcript("first part", is_final=True, now=0.0)
        engine.on_transcript("first part ngnix", is_final=True, now=1.0)
        engine.on_transcript(
            "first part ngnix spell that n g i n x", is_final=True, now=2.0
        )
        result = engine.finalize(
            "first part ngnix spell that n g i n x more words scratch that", now=3.0
        )
        assert result.text == "First part nginx"


class TestDictionary:
    def test_spelling_is_a_candidate_not_an_override(self) -> None:
        dictionary.record_spelling("Wrold", "world")
        dictionary.record_spelling("wrold", "world")
        data = dictionary.load_dictionary()
        assert data["overrides"] == {}
        assert dictionary.open_candidates([]) == [("wrold", "world", 2)]
        assert ("world", 2) in dictionary.open_hotword_candidates([])

    def test_case_only_and_empty_are_ignored(self) -> None:
        dictionary.record_spelling("World", "world")
        dictionary.record_spelling("", "x")
        assert dictionary.load_dictionary()["spelled"] == {}

    def test_accepted_candidate_disappears(self) -> None:
        dictionary.record_spelling("ngnix", "nginx")
        data = dictionary.load_dictionary()
        data["overrides"]["ngnix"] = "nginx"
        dictionary.save_dictionary(data)
        assert dictionary.open_candidates([]) == []


class TestDaemon:
    @pytest.fixture(autouse=True)
    def inline(self, monkeypatch: pytest.MonkeyPatch):
        async def _to_thread(func, /, *args, **kwargs):
            await asyncio.sleep(0)
            return func(*args, **kwargs)

        monkeypatch.setattr(asyncio, "to_thread", _to_thread)
        monkeypatch.setattr("voice_keyboard.client._show_overlay", lambda *a, **k: None)

    def _run(self, events: list[dict], *, secret: bool = False) -> tuple[str, str]:
        injector = RecordingInjector()
        daemon = _make_daemon(FakeStreamingSTT(events), injector)

        async def run() -> str:
            with daemon._audio_patch, daemon._stt_patch, daemon._probe_patch:
                await daemon._start_recording()
                daemon._session_secret = secret
                assert await wait_until(lambda: injector.screen)
                return await daemon._stop_recording()

        return asyncio.run(run()), injector.screen

    def test_live_spell_that_fixes_the_screen_and_offers_a_candidate(self) -> None:
        events = [
            {"type": "transcript.partial", "text": "hello wrold", "is_final": True},
            {
                "type": "transcript.partial",
                "text": "spell that w o r l d",
                "is_final": True,
            },
        ]
        final, screen = self._run(events)
        assert final == screen == "Hello world"
        assert dictionary.open_candidates([]) == [("wrold", "world", 1)]

    def test_secret_field_offers_nothing(self) -> None:
        events = [
            {"type": "transcript.partial", "text": "hunter", "is_final": True},
            {"type": "transcript.partial", "text": "spell that h u n t e r two", "is_final": True},
        ]
        self._run(events, secret=True)
        assert dictionary.load_dictionary()["spelled"] == {}
