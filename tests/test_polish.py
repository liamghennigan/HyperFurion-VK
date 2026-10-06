"""[polish] map: a prose dictation in a mapped app is rewritten in that
style through [llm]; anywhere else, or on failure, it stays as dictated."""

import asyncio
from unittest import mock

import pytest
from fakes import FakeStreamingSTT, RecordingInjector, _make_daemon


@pytest.fixture(autouse=True)
def inline(tmp_path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path))

    async def _to_thread(func, /, *args, **kwargs):
        await asyncio.sleep(0)
        return func(*args, **kwargs)

    monkeypatch.setattr(asyncio, "to_thread", _to_thread)
    monkeypatch.setattr("voice_keyboard.client._show_overlay", lambda *a, **k: None)


def _dictate(said: str, *, app: str, styles: dict, rewrite=None):
    injector = RecordingInjector()
    daemon = _make_daemon(
        FakeStreamingSTT([{"type": "transcript.partial", "text": said, "is_final": True}]), injector, app=app
    )
    daemon._config["polish"] = {"map": styles}
    llm = mock.Mock()
    llm.rewrite = mock.Mock(side_effect=rewrite or (lambda text, instruction: "Polished text."))

    async def run() -> str:
        with daemon._audio_patch, daemon._stt_patch, daemon._probe_patch, \
                mock.patch("voice_keyboard.daemon.create_llm_client", return_value=llm), \
                mock.patch("voice_keyboard.daemon.llm_ready", return_value=True):
            await daemon._start_recording()
            await asyncio.sleep(0.15)
            return await daemon._stop_recording()

    final = asyncio.run(run())
    return final, injector.screen, llm.rewrite


def test_a_mapped_app_gets_its_style() -> None:
    final, screen, rewrite = _dictate("hey can you send me the report", app="slack", styles={"Slack": "casual"})
    assert screen == final == "Polished text."
    assert rewrite.call_args.args[1] == "polish this dictation: casual"


def test_other_apps_and_short_dictations_are_left_alone() -> None:
    final, screen, rewrite = _dictate("hey can you send me the report", app="gedit", styles={"slack": "casual"})
    assert screen == final == "Hey can you send me the report"
    rewrite.assert_not_called()
    final, _, rewrite = _dictate("on my way", app="slack", styles={"slack": "casual"})
    assert final == "On my way"
    rewrite.assert_not_called()


def test_a_failed_polish_keeps_the_dictation() -> None:
    def boom(text, instruction):
        raise RuntimeError("model down")

    final, screen, _ = _dictate("hey can you send me the report", app="slack", styles={"slack": "casual"}, rewrite=boom)
    assert screen == final == "Hey can you send me the report"


def test_config_validation() -> None:
    from voice_keyboard.config import _default_config_with_paths, validate_config

    config = _default_config_with_paths()
    config["providers"]["xai"]["api_key"] = "k"
    config["polish"] = {"map": {"slack": ""}}
    with pytest.raises(RuntimeError, match="polish.map"):
        validate_config(config)


def test_an_implausible_polish_keeps_the_dictation() -> None:
    from voice_keyboard.daemon import polish_plausible

    assert polish_plausible("hey can you send me the report", "Hey, could you send me the report?")
    assert not polish_plausible("hey can you send me the report", "")
    assert not polish_plausible("short one here ok", "Sure! Here's a friendlier version: ...")
    assert not polish_plausible("short one here ok", "x" * 200)
    final, screen, _ = _dictate("hey can you send me the report", app="slack", styles={"slack": "casual"},
                                rewrite=lambda text, instruction: "Sure, here's a casual take: hey!")
    assert screen == final == "Hey can you send me the report"
