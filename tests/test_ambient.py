"""Ambient containment: room speech never reaches the engine; only
machine-addressed utterances can ever be typed."""

import asyncio
from unittest import mock

import pytest

from voice_keyboard.ambient import AmbientGate
from voice_keyboard.config import _default_config_with_paths, validate_config
from voice_keyboard.daemon import Daemon
from voice_keyboard.focusprobe import FocusInfo


def _valid_config() -> dict:
    cfg = _default_config_with_paths()
    cfg["xai"]["api_key"] = "test-api-key"
    return cfg


@pytest.fixture(autouse=True)
def no_overlay(monkeypatch: pytest.MonkeyPatch):
    from voice_keyboard import client

    monkeypatch.setattr(client, "_show_overlay", mock.Mock())


class TestAmbientGate:
    def test_unaddressed_partial_shows_nothing(self) -> None:
        gate = AmbientGate("vk")
        assert gate.filter("go grab some lunch", is_final=False) == ""

    def test_unaddressed_final_is_contained_forever(self) -> None:
        gate = AmbientGate("vk")
        assert gate.filter("go grab some lunch", is_final=True) == ""
        assert gate.filter("go grab some lunch more talk", is_final=False) == ""

    def test_addressed_partial_streams_molten(self) -> None:
        gate = AmbientGate("vk")
        assert gate.filter("vk write hello", is_final=False) == "write hello"

    def test_addressed_tail_revises(self) -> None:
        gate = AmbientGate("vk")
        assert gate.filter("vk right hello", is_final=False) == "right hello"
        assert gate.filter("vk write hello", is_final=False) == "write hello"

    def test_addressed_final_keeps_then_room_speech_contained(self) -> None:
        gate = AmbientGate("vk")
        assert gate.filter("vk write hello", is_final=True) == "write hello"
        merged = "vk write hello someone talking in the room"
        assert gate.filter(merged, is_final=False) == "write hello"
        assert gate.filter(merged, is_final=True) == "write hello"

    def test_second_addressed_segment_appends(self) -> None:
        gate = AmbientGate("vk")
        assert gate.filter("vk write hello", is_final=True) == "write hello"
        assert (
            gate.filter("vk write hello vk and more", is_final=True)
            == "write hello and more"
        )

    def test_address_matches_with_caps_and_punctuation(self) -> None:
        gate = AmbientGate("vk")
        assert gate.filter("vk, take a note", is_final=False) == "take a note"

    def test_bare_address_word_alone_is_calm(self) -> None:
        gate = AmbientGate("vk")
        assert gate.filter("vk", is_final=False) == ""
        assert gate.filter("vk", is_final=True) == ""

    def test_empty_address_word_contains_everything(self) -> None:
        gate = AmbientGate("")
        assert gate.filter("anything at all", is_final=True) == ""


class TestDaemonWiring:
    @pytest.fixture(autouse=True)
    def inline_to_thread(self, monkeypatch: pytest.MonkeyPatch):
        async def _to_thread(func, /, *args, **kwargs):
            return func(*args, **kwargs)

        monkeypatch.setattr(asyncio, "to_thread", _to_thread)

    def _daemon(self, cfg: dict) -> Daemon:
        return Daemon(
            config=cfg,
            injector=mock.Mock(),
            ipc_server=mock.Mock(),
            tts_client=mock.Mock(),
        )

    def _setup(self, daemon: Daemon) -> None:
        daemon._stt_client = mock.Mock(supports_streaming=False, bias_prompt="")

        async def run() -> None:
            async def fake_probe() -> FocusInfo:
                return FocusInfo(app="editor", role="text")

            await daemon._setup_flow_session(asyncio.create_task(fake_probe()))

        asyncio.run(run())

    def test_gate_off_by_default(self) -> None:
        daemon = self._daemon(_valid_config())
        self._setup(daemon)
        assert daemon._ambient_gate is None
        assert daemon._status_response()["ambient"] is False

    def test_gate_on_with_wake_word_fallback(self) -> None:
        cfg = _valid_config()
        cfg["ambient"]["enabled"] = True
        daemon = self._daemon(cfg)
        self._setup(daemon)
        assert daemon._ambient_gate is not None
        assert daemon._ambient_gate._address == "vk"
        assert daemon._status_response()["ambient"] is True

    def test_feed_flow_contains_room_speech(self) -> None:
        daemon = self._daemon(_valid_config())
        daemon._ambient_gate = AmbientGate("vk")
        engine = mock.Mock()
        daemon._flow_engine = engine
        daemon._final_text = ""
        daemon._interim_text = "someone talking in the room"
        daemon._feed_flow(is_final=False)
        assert engine.on_transcript.call_args.args[0] == ""

    def test_feed_flow_passes_addressed_speech(self) -> None:
        daemon = self._daemon(_valid_config())
        daemon._ambient_gate = AmbientGate("vk")
        engine = mock.Mock()
        daemon._flow_engine = engine
        daemon._final_text = ""
        daemon._interim_text = "vk write hello"
        daemon._feed_flow(is_final=False)
        assert engine.on_transcript.call_args.args[0] == "write hello"


class TestAmbientConfigValidation:
    def test_enabled_must_be_bool(self) -> None:
        cfg = _valid_config()
        cfg["ambient"]["enabled"] = "yes"
        with pytest.raises(RuntimeError, match="ambient.enabled"):
            validate_config(cfg)

    def test_enabled_needs_some_address_word(self) -> None:
        cfg = _valid_config()
        cfg["ambient"]["enabled"] = True
        cfg["flow"]["wake_word"] = ""
        with pytest.raises(RuntimeError, match="ambient"):
            validate_config(cfg)

    def test_enabled_with_wake_word_fallback_validates(self) -> None:
        cfg = _valid_config()
        cfg["ambient"]["enabled"] = True
        validate_config(cfg)
