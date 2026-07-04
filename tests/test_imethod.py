"""The preedit mapper: molten↔preedit, freeze↔commit, scratch↔delete."""

from voice_keyboard.imethod import PreeditMapper


class TestPreeditMapper:
    def test_first_molten_words_become_preedit(self) -> None:
        mapper = PreeditMapper()
        assert mapper.update("", "fixed the") == [("preedit", "fixed the")]

    def test_unchanged_state_produces_no_ops(self) -> None:
        mapper = PreeditMapper()
        mapper.update("", "fixed the")
        assert mapper.update("", "fixed the") == []

    def test_molten_repair_touches_only_preedit(self) -> None:
        mapper = PreeditMapper()
        mapper.update("", "fixed the race addition")
        assert mapper.update("", "fixed the race condition") == [
            ("preedit", "fixed the race condition")
        ]

    def test_freeze_commits_delta_and_shrinks_preedit(self) -> None:
        mapper = PreeditMapper()
        mapper.update("", "fixed the race")
        ops = mapper.update("fixed ", "the race")
        assert ops == [("commit", "fixed "), ("preedit", "the race")]

    def test_full_freeze_empties_preedit(self) -> None:
        mapper = PreeditMapper()
        mapper.update("fixed ", "the race")
        ops = mapper.update("fixed the race", "")
        assert ops == [("commit", "the race"), ("preedit", "")]

    def test_scratch_that_rewinds_committed_text(self) -> None:
        mapper = PreeditMapper()
        mapper.update("one two three ", "")
        ops = mapper.update("one ", "")
        assert ops == [("delete", len("two three "))]

    def test_rewind_then_new_text_commits_remainder(self) -> None:
        mapper = PreeditMapper()
        mapper.update("one two ", "")
        ops = mapper.update("one four ", "next")
        assert ops == [
            ("delete", len("two ")),
            ("commit", "four "),
            ("preedit", "next"),
        ]

    def test_finalize_evaporates_pending_text(self) -> None:
        mapper = PreeditMapper()
        mapper.update("kept ", "still molten")
        assert mapper.finalize() == [("preedit", "")]
        assert mapper.finalize() == []

    def test_session_walkthrough_reconstructs_screen(self) -> None:
        """Applying the ops like an IM host must reproduce the engine's
        desired text at every step."""
        mapper = PreeditMapper()
        committed_text = ""
        preedit_text = ""
        steps = [
            ("", "fixed the"),
            ("", "fixed the race addition"),
            ("fixed ", "the race addition"),
            ("fixed ", "the race condition"),
            ("fixed the race condition", ""),
            ("fixed ", ""),                      # scratch that
            ("fixed the audio thread", ""),
        ]
        for committed, molten in steps:
            for op, value in mapper.update(committed, molten):
                if op == "commit":
                    committed_text += value
                elif op == "delete":
                    committed_text = committed_text[: len(committed_text) - int(value)]
                elif op == "preedit":
                    preedit_text = str(value)
            assert committed_text == committed
            assert preedit_text == molten
