"""`voice-keyboard try` shows what the keyboard would type: a register
written inside the quotes counts, and the personal dictionary (`learned`
overrides and macros) applies exactly as it does when you dictate."""

import asyncio
import io
from unittest import mock

import pytest
from fakes import FakeStreamingSTT, RecordingInjector, _flow_config, _make_daemon

from voice_keyboard import dictionary, trial
from voice_keyboard.config import DEFAULT_CONFIG


@pytest.fixture(autouse=True)
def state(tmp_path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path))


def _config(**flow):
    return {**DEFAULT_CONFIG, "flow": {**DEFAULT_CONFIG["flow"], **flow}}


def _dictate(said: str, *, snippets=None) -> str:
    """What the daemon types for `said`, end to end."""
    injector = RecordingInjector()
    daemon = _make_daemon(FakeStreamingSTT([{"type": "transcript.partial", "text": said, "is_final": True}]), injector)
    if snippets is not None:
        daemon._config["snippets"] = snippets

    async def run() -> None:
        with daemon._audio_patch, daemon._stt_patch, daemon._probe_patch, \
                mock.patch("voice_keyboard.client._show_overlay", lambda *a, **k: None):
            await daemon._start_recording()
            await asyncio.sleep(0.1)
            await daemon._stop_recording()

    asyncio.run(run())
    return injector.screen


class TestRegisterInQuotes:
    def test_a_register_inside_the_quotes(self) -> None:
        # The shell hands `try "python: x equals five"` over as one word.
        assert trial.run(_config(), ["python: x equals five"]) == "x = 5"
        assert trial.run(_config(), ["Shell: pipe grep dash i error"]) == "| grep -i error"

    def test_outside_the_quotes_still_works(self) -> None:
        assert trial.run(_config(), ["python:", "x equals five"]) == "x = 5"

    def test_with_a_pause_in_the_quotes(self) -> None:
        assert trial.run(_config(), ["python: x equals five | y equals six"]) == "x = 5 y = 6"

    def test_a_word_that_isnt_a_register_is_dictated(self) -> None:
        assert trial.run(_config(), ["note: buy milk"]) == "Note: buy milk"

    def test_only_a_register_is_still_nothing_said(self) -> None:
        with pytest.raises(ValueError):
            trial.run(_config(), ["python:  "])


class TestPersonalDictionary:
    def test_a_learned_word_applies(self) -> None:
        dictionary.add_word("hyper furion", "HyperFurion")
        assert trial.run(_config(), "i use hyper furion daily".split()) == "I use HyperFurion daily"
        assert _dictate("i use hyper furion daily") == "I use HyperFurion daily"  # same as the daemon

    def test_the_config_vocabulary_wins(self) -> None:
        dictionary.add_word("hyper furion", "HyperFurion")
        cfg = _config(vocabulary={"hyper furion": "Hyper-Furion"})
        assert trial.run(cfg, "hyper furion".split()) == "Hyper-Furion"

    def test_personal_dictionary_off(self) -> None:
        dictionary.add_word("hyper furion", "HyperFurion")
        assert trial.run(_config(personal_dictionary=False), "hyper furion".split()) == "Hyper furion"

    def test_the_repl_too(self) -> None:
        dictionary.add_word("hyper furion", "HyperFurion")
        out = io.StringIO()
        trial.repl(_config(), io.StringIO("hyper furion\n"), out)
        assert out.getvalue() == "HyperFurion\n"

    def test_the_daemon_and_try_share_one_builder(self) -> None:
        from voice_keyboard.daemon import Daemon
        from voice_keyboard.flow.registers import PROSE

        dictionary.add_word("vee kay", "VK")
        cfg = _flow_config()
        daemon = Daemon(config=cfg, injector=mock.Mock(), ipc_server=mock.Mock(), tts_client=mock.Mock())
        assert ("vee", "kay") in daemon._build_grammar(PROSE)._phrases
        assert daemon._build_grammar(PROSE)._phrases == trial.dictation_grammar(cfg, PROSE)._phrases


class TestSnippetsAndMacros:
    def test_a_snippet_after_words(self) -> None:
        cfg = {**_config(), "snippets": {"my email": "liam@example.com"}}
        said = "send the invoice to vk my email"
        assert trial.run(cfg, said.split()) == "Send the invoice to liam@example.com\n[snippet: my email]"
        assert _dictate(said, snippets={"my email": "liam@example.com"}) == "Send the invoice to liam@example.com"

    def test_a_learned_macro_alone(self) -> None:
        data = dictionary.load_dictionary()
        data["macros"]["sign off"] = "Best,\nLiam"
        dictionary.save_dictionary(data)
        assert trial.run(_config(), "vk sign off".split()) == "Best,\nLiam\n[snippet: sign off]"

    def test_punctuation_glues(self) -> None:
        cfg = {**_config(), "snippets": {"sig": ", Liam"}}
        assert trial.run(cfg, "thanks vk sig".split()).startswith("Thanks, Liam\n")

    def test_other_instructions_are_still_noted(self) -> None:
        assert trial.run(_config(), "vk make that formal".split()).endswith("[instruction: make that formal]")
