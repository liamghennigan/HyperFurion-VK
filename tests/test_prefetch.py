"""Speculative TTS: the selection stability gate, cost gating, and the
instant-playback cache path."""

import asyncio
from unittest import mock

import pytest

from voice_keyboard import prefetch
from voice_keyboard.config import _default_config_with_paths, validate_config
from voice_keyboard.daemon import Daemon
from voice_keyboard.prefetch import PrefetchGate, SelectionWatcher, prefetch_enabled
from voice_keyboard.tts import TTSClient


def _valid_config() -> dict:
    cfg = _default_config_with_paths()
    cfg["xai"]["api_key"] = "test-api-key"
    return cfg


class TestPrefetchGate:
    def test_fires_after_selection_holds_still(self) -> None:
        gate = PrefetchGate(stable_s=0.8)
        assert gate.feed("read this aloud", 0.0) is None
        assert gate.feed("read this aloud", 1.0) == "read this aloud"

    def test_mid_drag_churn_never_fires(self) -> None:
        gate = PrefetchGate(stable_s=0.8)
        assert gate.feed("read", 0.0) is None
        assert gate.feed("read this", 0.4) is None
        assert gate.feed("read this al", 0.8) is None
        assert gate.feed("read this aloud", 1.2) is None
        assert gate.feed("read this aloud", 2.1) == "read this aloud"

    def test_fires_exactly_once(self) -> None:
        gate = PrefetchGate(stable_s=0.5)
        gate.feed("text", 0.0)
        assert gate.feed("text", 1.0) == "text"
        assert gate.feed("text", 2.0) is None
        assert gate.feed("text", 99.0) is None

    def test_empty_selection_resets(self) -> None:
        gate = PrefetchGate(stable_s=0.5)
        gate.feed("text", 0.0)
        gate.feed("", 0.3)
        assert gate.feed("text", 0.6) is None       # clock restarted
        assert gate.feed("text", 1.2) == "text"

    def test_new_text_after_fire_fires_again(self) -> None:
        gate = PrefetchGate(stable_s=0.5)
        gate.feed("one", 0.0)
        assert gate.feed("one", 1.0) == "one"
        gate.feed("two", 2.0)
        assert gate.feed("two", 3.0) == "two"

    def test_oversize_selection_is_skipped_without_looping(self) -> None:
        gate = PrefetchGate(stable_s=0.5, max_chars=10)
        big = "x" * 50
        gate.feed(big, 0.0)
        assert gate.feed(big, 1.0) is None
        assert gate.feed(big, 2.0) is None          # marked fired, no loop


class TestPrefetchEnabled:
    def test_off_by_default(self) -> None:
        assert prefetch_enabled(_valid_config()) is False

    def test_always_opts_in(self) -> None:
        cfg = _valid_config()
        cfg["tts"]["prefetch"] = "always"
        assert prefetch_enabled(cfg) is True

    def test_auto_requires_local_openai_endpoint(self) -> None:
        cfg = _valid_config()
        cfg["tts"]["prefetch"] = "auto"
        assert prefetch_enabled(cfg) is False        # provider is xai
        cfg["tts"]["provider"] = "openai"
        assert prefetch_enabled(cfg) is False        # no base_url = cloud
        cfg["providers"]["openai"]["base_url"] = "http://localhost:8880/v1"
        assert prefetch_enabled(cfg) is True

    def test_validation_rejects_unknown_mode(self) -> None:
        cfg = _valid_config()
        cfg["tts"]["prefetch"] = "sometimes"
        with pytest.raises(RuntimeError, match="tts.prefetch"):
            validate_config(cfg)


class TestSelectionWatcherStep:
    def _watcher(self, tts: mock.Mock, busy: bool = False):
        stored: list[tuple[str, bytes]] = []
        watcher = SelectionWatcher(
            tts_client=tts,
            store=lambda text, audio: stored.append((text, audio)),
            is_busy=lambda: busy,
            gate=PrefetchGate(stable_s=0.5),
        )
        return watcher, stored

    def test_stable_selection_synthesizes_and_stores(self) -> None:
        tts = mock.Mock()
        tts.synthesize.return_value = b"AUDIO"
        watcher, stored = self._watcher(tts)
        with mock.patch.object(prefetch.clipboard, "get_primary_text", return_value="hello"):
            watcher.step(0.0)
            watcher.step(1.0)
        assert stored == [("hello", b"AUDIO")]
        tts.synthesize.assert_called_once_with("hello")

    def test_busy_daemon_skips_polling(self) -> None:
        tts = mock.Mock()
        watcher, stored = self._watcher(tts, busy=True)
        with mock.patch.object(
            prefetch.clipboard, "get_primary_text", return_value="hello"
        ) as reader:
            watcher.step(0.0)
        reader.assert_not_called()
        assert stored == []

    def test_synthesis_failure_backs_off(self) -> None:
        tts = mock.Mock()
        tts.synthesize.side_effect = RuntimeError("boom")
        watcher, stored = self._watcher(tts)
        with mock.patch.object(prefetch.clipboard, "get_primary_text", return_value="hello"), \
             mock.patch.object(prefetch.time, "monotonic", return_value=100.0):
            watcher.step(0.0)
            watcher.step(1.0)                        # fails, sets backoff
            watcher.step(1.5)                        # inside backoff window
        assert tts.synthesize.call_count == 1
        assert stored == []


class TestRunTTSCache:
    @pytest.fixture(autouse=True)
    def inline_to_thread(self, monkeypatch: pytest.MonkeyPatch):
        async def _to_thread(func, /, *args, **kwargs):
            return func(*args, **kwargs)

        monkeypatch.setattr(asyncio, "to_thread", _to_thread)

    def _daemon(self) -> Daemon:
        return Daemon(
            config=_valid_config(),
            injector=mock.Mock(),
            ipc_server=mock.Mock(),
            tts_client=mock.Mock(),
        )

    def test_cache_hit_plays_without_synthesis(self) -> None:
        daemon = self._daemon()
        daemon._tts_cache = ("read this", b"CACHED")
        asyncio.run(daemon._run_tts("read this"))
        daemon._tts_client.play_audio.assert_called_once_with(b"CACHED")
        daemon._tts_client.synthesize_and_play.assert_not_called()

    def test_cache_miss_synthesizes_normally(self) -> None:
        daemon = self._daemon()
        daemon._tts_cache = ("something else", b"CACHED")
        asyncio.run(daemon._run_tts("read this"))
        daemon._tts_client.synthesize_and_play.assert_called_once_with("read this")
        daemon._tts_client.play_audio.assert_not_called()

    def test_store_callback_swaps_cache(self) -> None:
        daemon = self._daemon()
        daemon._store_tts_prefetch("text", b"A")
        assert daemon._tts_cache == ("text", b"A")


class TestTTSSplit:
    def test_synthesize_and_play_routes_through_play_audio(self) -> None:
        client = TTSClient(api_key="k")
        with mock.patch.object(client, "synthesize", return_value=b"X") as synth, \
             mock.patch.object(client, "play_audio") as play:
            client.synthesize_and_play("hi")
        synth.assert_called_once_with("hi")
        play.assert_called_once_with(b"X")
