"""The wake word "Kai": local detection, scoring, cooldown, and the
opt-in / graceful-degrade contract.

No real openWakeWord model or microphone is used — a fake detector drives
the pure scoring/gating logic, and the missing-dependency path is exercised
directly.
"""

import pytest

from voice_keyboard.config import _validate_wake_config
from voice_keyboard.wake import WakeListener, wake_enabled


class FakeModel:
    def __init__(self, preds: dict):
        self._preds = preds

    def predict(self, samples):
        return self._preds


def _listener(**wake) -> WakeListener:
    cfg = {"wake": {"enabled": True, "word": "kai", **wake}}
    return WakeListener(config=cfg, on_wake=lambda: None, is_busy=lambda: False)


class TestScoring:
    def test_prefers_head_matching_the_wake_word(self) -> None:
        wl = _listener()
        wl._model = FakeModel({"hey_jarvis": 0.9, "kai_v1": 0.31})
        assert wl.score(b"") == pytest.approx(0.31)

    def test_falls_back_to_max_when_no_name_matches(self) -> None:
        wl = _listener()
        wl._model = FakeModel({"hey_jarvis": 0.9, "alexa": 0.4})
        assert wl.score(b"") == pytest.approx(0.9)

    def test_empty_predictions_score_zero(self) -> None:
        wl = _listener()
        wl._model = FakeModel({})
        assert wl.score(b"") == 0.0


class TestCooldown:
    def test_recent_fire_is_not_ready(self) -> None:
        wl = _listener(cooldown_s=2.0)
        wl._last_fire = 100.0
        assert wl._ready(101.0) is False
        assert wl._ready(102.5) is True


class TestOptInContract:
    def test_wake_enabled_flag(self) -> None:
        assert wake_enabled({"wake": {"enabled": True}}) is True
        assert wake_enabled({"wake": {"enabled": False}}) is False
        assert wake_enabled({}) is False

    def test_start_is_graceful_without_openwakeword(self) -> None:
        # The optional dep is absent in the test env: start() must log and
        # return, never raise, and never spin up a thread.
        wl = _listener()
        wl.start()
        assert wl._thread is None


class TestValidation:
    def test_threshold_range(self) -> None:
        with pytest.raises(RuntimeError, match="wake.threshold"):
            _validate_wake_config({"wake": {"threshold": 2.0}})

    def test_cooldown_non_negative(self) -> None:
        with pytest.raises(RuntimeError, match="wake.cooldown_s"):
            _validate_wake_config({"wake": {"cooldown_s": -1}})

    def test_enabled_needs_a_word(self) -> None:
        with pytest.raises(RuntimeError, match="wake.word"):
            _validate_wake_config({"wake": {"enabled": True, "word": "  "}})

    def test_defaults_validate(self) -> None:
        _validate_wake_config(
            {"wake": {"enabled": False, "word": "kai", "threshold": 0.5, "cooldown_s": 2.0}}
        )
