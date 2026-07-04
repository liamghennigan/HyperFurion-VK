"""The personal dictionary: mining corrections from the ledger, the
accept/reject gate, and the grammar merge at session start."""

import os
from unittest import mock

import pytest

from voice_keyboard import dictionary
from voice_keyboard.config import _default_config_with_paths, validate_config
from voice_keyboard.daemon import Daemon
from voice_keyboard.flow.registers import resolve_register


@pytest.fixture(autouse=True)
def state_dir(tmp_path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path))
    return tmp_path


def _entry(ts: float, text: str, *, app: str = "editor", register: str = "prose") -> dict:
    return {"ts": ts, "app": app, "register": register, "text": text}


def _valid_config() -> dict:
    cfg = _default_config_with_paths()
    cfg["xai"]["api_key"] = "test-api-key"
    return cfg


class TestMineCorrections:
    def test_single_word_rediction_is_mined(self) -> None:
        entries = [
            _entry(100.0, "fixed the race condition in the audio fred"),
            _entry(110.0, "fixed the race condition in the audio thread"),
        ]
        assert dictionary.mine_corrections(entries) == [("fred", "thread", 1)]

    def test_far_apart_entries_are_not_corrections(self) -> None:
        entries = [
            _entry(100.0, "the audio fred"),
            _entry(100.0 + dictionary.MINE_WINDOW_S + 1, "the audio thread"),
        ]
        assert dictionary.mine_corrections(entries) == []

    def test_different_apps_are_not_corrections(self) -> None:
        entries = [
            _entry(100.0, "the audio fred", app="editor"),
            _entry(105.0, "the audio thread", app="terminal"),
        ]
        assert dictionary.mine_corrections(entries) == []

    def test_dissimilar_sentences_are_not_corrections(self) -> None:
        entries = [
            _entry(100.0, "hello world this is dictation"),
            _entry(105.0, "completely different sentence about lunch plans"),
        ]
        assert dictionary.mine_corrections(entries) == []

    def test_intent_entries_are_skipped(self) -> None:
        entries = [
            _entry(100.0, "grep -rn TODO fred", register="intent"),
            _entry(105.0, "grep -rn TODO thread", register="intent"),
        ]
        assert dictionary.mine_corrections(entries) == []

    def test_instructed_rewrites_touch_too_much_to_mine(self) -> None:
        entries = [
            _entry(100.0, "we fixed a bunch of bugs and it works now"),
            _entry(105.0, "we resolved numerous defects and it functions currently"),
        ]
        assert dictionary.mine_corrections(entries) == []

    def test_case_only_changes_are_skipped(self) -> None:
        entries = [
            _entry(100.0, "ship it to grok today"),
            _entry(105.0, "ship it to Grok today"),
        ]
        assert dictionary.mine_corrections(entries) == []

    def test_repeated_corrections_aggregate_and_rank(self) -> None:
        entries = [
            _entry(100.0, "the seneschal fred"),
            _entry(105.0, "the seneschal thread"),
            _entry(200.0, "another fred here"),
            _entry(205.0, "another thread here"),
            _entry(300.0, "one caisson stands"),
            _entry(305.0, "one kairos stands"),
        ]
        mined = dictionary.mine_corrections(entries)
        assert mined[0] == ("fred", "thread", 2)
        assert ("caisson", "kairos", 1) in mined


class TestMineHotwords:
    def test_recurring_midsentence_names_are_candidates(self) -> None:
        entries = [
            _entry(100.0, "wire the Seneschal gate"),
            _entry(200.0, "the Seneschal holds"),
            _entry(300.0, "ask the Seneschal first"),
        ]
        assert ("Seneschal", 3) in dictionary.mine_hotwords(entries)

    def test_rare_tokens_do_not_qualify(self) -> None:
        entries = [
            _entry(100.0, "wire the Seneschal gate"),
            _entry(200.0, "plain words only here"),
        ]
        assert dictionary.mine_hotwords(entries) == []

    def test_camelcase_and_digit_tokens_qualify(self) -> None:
        entries = [
            _entry(100.0, "check HyperFurion now"),
            _entry(200.0, "check HyperFurion again"),
            _entry(300.0, "check HyperFurion once more"),
        ]
        assert ("HyperFurion", 3) in dictionary.mine_hotwords(entries)


class TestDictionaryFile:
    def test_missing_file_loads_empty(self) -> None:
        data = dictionary.load_dictionary()
        assert data == {"overrides": {}, "hotwords": [], "rejected": [], "macros": {}}

    def test_corrupt_file_loads_empty(self) -> None:
        path = dictionary.dictionary_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("not json{", encoding="utf-8")
        assert dictionary.load_dictionary() == {
            "overrides": {},
            "hotwords": [],
            "rejected": [],
            "macros": {},
        }

    def test_save_roundtrip_and_mode_600(self) -> None:
        data = dictionary.load_dictionary()
        data["overrides"]["fred"] = "thread"
        data["hotwords"].append("Seneschal")
        dictionary.save_dictionary(data)
        assert dictionary.vocabulary_overrides() == {"fred": "thread"}
        assert dictionary.hotwords() == ["Seneschal"]
        mode = os.stat(dictionary.dictionary_path()).st_mode & 0o777
        assert mode == 0o600

    def test_accept_and_reject_close_candidates(self) -> None:
        entries = [
            _entry(100.0, "the audio fred"),
            _entry(105.0, "the audio thread"),
            _entry(200.0, "one caisson stands"),
            _entry(205.0, "one kairos stands"),
        ]
        assert len(dictionary.open_candidates(entries)) == 2

        data = dictionary.load_dictionary()
        data["overrides"]["fred"] = "thread"
        data["rejected"].append(dictionary.candidate_key("caisson", "kairos"))
        dictionary.save_dictionary(data)

        assert dictionary.open_candidates(entries) == []

    def test_hotword_candidates_close_on_accept_or_reject(self) -> None:
        entries = [
            _entry(100.0, "wire the Seneschal gate"),
            _entry(200.0, "the Seneschal holds"),
            _entry(300.0, "ask the Seneschal first"),
        ]
        assert dictionary.open_hotword_candidates(entries) == [("Seneschal", 3)]
        data = dictionary.load_dictionary()
        data["hotwords"].append("Seneschal")
        dictionary.save_dictionary(data)
        assert dictionary.open_hotword_candidates(entries) == []


class TestGrammarMerge:
    def _daemon(self, cfg: dict) -> Daemon:
        return Daemon(
            config=cfg,
            injector=mock.Mock(),
            ipc_server=mock.Mock(),
            tts_client=mock.Mock(),
        )

    def _write_override(self) -> None:
        data = dictionary.load_dictionary()
        data["overrides"]["fred"] = "thread"
        dictionary.save_dictionary(data)

    def test_accepted_overrides_reach_the_grammar(self) -> None:
        self._write_override()
        daemon = self._daemon(_valid_config())
        grammar = daemon._build_grammar(resolve_register("prose"))
        assert grammar._phrases[("fred",)] == ("vocab", "thread")

    def test_explicit_config_vocabulary_wins(self) -> None:
        self._write_override()
        cfg = _valid_config()
        cfg["flow"]["vocabulary"] = {"fred": "Fred"}
        daemon = self._daemon(cfg)
        grammar = daemon._build_grammar(resolve_register("prose"))
        assert grammar._phrases[("fred",)] == ("vocab", "Fred")

    def test_disabled_personal_dictionary_does_not_merge(self) -> None:
        self._write_override()
        cfg = _valid_config()
        cfg["flow"]["personal_dictionary"] = False
        daemon = self._daemon(cfg)
        grammar = daemon._build_grammar(resolve_register("prose"))
        assert ("fred",) not in grammar._phrases

    def test_personal_dictionary_must_be_bool(self) -> None:
        cfg = _valid_config()
        cfg["flow"]["personal_dictionary"] = "yes"
        with pytest.raises(RuntimeError, match="flow.personal_dictionary"):
            validate_config(cfg)
