"""Molten diffs: a wake-word rewrite is HELD, not landed — "keep it"
applies, "scratch that" discards, expiry evaporates. No edit is real
until it freezes."""

import asyncio
import time
from unittest import mock

import pytest

from voice_keyboard.config import _default_config_with_paths
from voice_keyboard.daemon import PENDING_REWRITE_TTL_S, Daemon


def _valid_config(pending: bool = True) -> dict:
    cfg = _default_config_with_paths()
    cfg["xai"]["api_key"] = "test-api-key"
    cfg["flow"]["rewrite_pending"] = pending
    return cfg


@pytest.fixture(autouse=True)
def inline_to_thread(monkeypatch: pytest.MonkeyPatch):
    async def _to_thread(func, /, *args, **kwargs):
        return func(*args, **kwargs)

    monkeypatch.setattr(asyncio, "to_thread", _to_thread)


@pytest.fixture(autouse=True)
def no_overlay(monkeypatch: pytest.MonkeyPatch):
    from voice_keyboard import client

    monkeypatch.setattr(client, "_show_overlay", mock.Mock())


class RecordingInjector:
    def __init__(self):
        self.typed: list[str] = []
        self.deleted = 0
        self.suppress_enter = False
        self.paste_chord_shift = False

    def start(self) -> None:
        pass

    def stop(self) -> None:
        pass

    def type_text(self, text: str) -> None:
        self.typed.append(text)

    def delete_chars(self, count: int) -> None:
        self.deleted += count


def _daemon(pending: bool = True) -> Daemon:
    daemon = Daemon(
        config=_valid_config(pending),
        injector=RecordingInjector(),
        ipc_server=mock.Mock(),
        tts_client=mock.Mock(),
    )
    daemon._last_typed = "the old sentence"
    return daemon


def _rewrite_setup(daemon: Daemon, rewritten: str = "The polished sentence.") -> mock.Mock:
    llm = mock.Mock()
    llm.rewrite.return_value = rewritten
    return llm


class TestHoldInsteadOfLand:
    def test_transform_holds_and_leaves_screen_alone(self) -> None:
        daemon = _daemon(pending=True)
        llm = _rewrite_setup(daemon)

        async def run() -> str:
            with mock.patch("voice_keyboard.daemon.create_llm_client", return_value=llm):
                return await daemon._run_transform("make that formal", worker=None)

        result = asyncio.run(run())
        assert result == "the old sentence"          # screen truth unchanged
        assert daemon._injector.typed == []          # nothing landed
        assert daemon._injector.deleted == 0
        assert daemon._pending_rewrite is not None
        assert daemon._pending_rewrite["text"] == "The polished sentence."
        assert daemon._status_response()["pending_rewrite"] is True

    def test_disabled_lands_immediately(self) -> None:
        daemon = _daemon(pending=False)
        llm = _rewrite_setup(daemon)

        async def run() -> str:
            with mock.patch("voice_keyboard.daemon.create_llm_client", return_value=llm):
                return await daemon._run_transform("make that formal", worker=None)

        result = asyncio.run(run())
        assert result == "The polished sentence."
        assert daemon._injector.deleted == len("the old sentence")
        assert daemon._injector.typed == ["The polished sentence."]
        assert daemon._pending_rewrite is None


class TestKeepAndDiscard:
    def _held(self) -> Daemon:
        daemon = _daemon(pending=True)
        daemon._pending_rewrite = {
            "text": "The polished sentence.",
            "target": "the old sentence",
            "expires": time.monotonic() + 60,
        }
        return daemon

    def test_keep_applies_the_diff(self) -> None:
        daemon = self._held()
        result = asyncio.run(daemon._keep_pending())
        assert result == "The polished sentence."
        assert daemon._injector.deleted == len("the old sentence")
        assert daemon._injector.typed == ["The polished sentence."]
        assert daemon._pending_rewrite is None
        assert daemon._last_typed == "The polished sentence."

    def test_keep_without_pending_raises(self) -> None:
        daemon = _daemon(pending=True)
        with pytest.raises(RuntimeError, match="no pending rewrite"):
            asyncio.run(daemon._keep_pending())

    def test_keep_while_recording_refuses(self) -> None:
        daemon = self._held()
        daemon._recording = True
        with pytest.raises(RuntimeError, match="stop recording"):
            asyncio.run(daemon._keep_pending())

    def test_discard_drops_without_touching_screen(self) -> None:
        daemon = self._held()
        assert asyncio.run(daemon._discard_pending()) is True
        assert daemon._pending_rewrite is None
        assert daemon._injector.typed == []
        assert asyncio.run(daemon._discard_pending()) is False

    def test_expired_pending_evaporates(self) -> None:
        daemon = self._held()
        daemon._pending_rewrite["expires"] = time.monotonic() - 1
        with pytest.raises(RuntimeError, match="no pending rewrite"):
            asyncio.run(daemon._keep_pending())
        assert daemon._pending_rewrite is None


class TestVoiceApproval:
    def _held(self) -> Daemon:
        daemon = _daemon(pending=True)
        daemon._pending_rewrite = {
            "text": "The polished sentence.",
            "target": "the old sentence",
            "expires": time.monotonic() + 60,
        }
        return daemon

    def test_spoken_keep_it_applies(self) -> None:
        daemon = self._held()
        result = asyncio.run(daemon._finish_classic("Keep it.", ""))
        assert result == "The polished sentence."
        assert daemon._injector.typed == ["The polished sentence."]

    def test_bare_scratch_that_discards(self) -> None:
        daemon = self._held()
        daemon._last_scratches = 1
        result = asyncio.run(daemon._finish_classic("", ""))
        assert result == ""
        assert daemon._pending_rewrite is None
        assert daemon._injector.typed == []

    def test_ordinary_dictation_types_normally(self) -> None:
        daemon = self._held()
        daemon._last_scratches = 0
        result = asyncio.run(daemon._finish_classic("just more words", ""))
        assert result == "just more words"
        assert daemon._injector.typed == ["just more words"]
        assert daemon._pending_rewrite is not None  # still held

    def test_no_pending_keep_it_is_ordinary_text(self) -> None:
        daemon = _daemon(pending=True)
        result = asyncio.run(daemon._finish_classic("Keep it.", ""))
        assert result == "Keep it."
        assert daemon._injector.typed == ["Keep it."]
