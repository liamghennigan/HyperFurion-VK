"""Spoken self-corrections ([flow] corrections = "llm"): a dictation with
a correction cue goes to [llm] to have the false start deleted, and the
answer is used only if it deleted words and did nothing else."""

import asyncio
from unittest import mock

import pytest
from fakes import FakeStreamingSTT, RecordingInjector, _make_daemon
from waiting import wait_until

from voice_keyboard.config import _default_config_with_paths, validate_config
from voice_keyboard.flow.corrections import deletion_only, needs_cleanup
from voice_keyboard.focusprobe import FocusInfo
from voice_keyboard.llm import LLMClient


class TestRules:
    @pytest.mark.parametrize("text", [
        "Send it Tuesday, no wait, Wednesday.", "The meeting is at 3, I mean 4.",
        "Call Anna, sorry, Hannah.", "I I think so", "Five, actually six.", "the the end",
    ])
    def test_cues(self, text) -> None:
        assert needs_cleanup(text)

    @pytest.mark.parametrize("text", [
        "We ship on Friday.", "This is not fine", "Make that call today.", "", "x " * 400,
    ])
    def test_no_cue_is_never_sent(self, text) -> None:
        assert not needs_cleanup(text)

    @pytest.mark.parametrize("original, cleaned, ok", [
        ("Send it Tuesday, no wait, Wednesday.", "Send it Wednesday.", True),
        ("The meeting is at 3, I mean 4.", "The meeting is at 4.", True),
        ("I I think so.", "I think so.", True),
        ("Send it Tuesday, no wait, Wednesday.", "Send it on Wednesday.", False),   # added a word
        ("Send it Tuesday, no wait, Wednesday.", "Wednesday send it.", False),      # reordered
        ("Call Anna, sorry, Hannah.", "Call Hanna.", False),                        # respelled
        ("I think so", "I think so", False),                                        # nothing deleted
        ("a b c d e f g h i j", "a b", False),                                      # deleted the message
        ("Send it Tuesday, no wait, Wednesday.", "", False),
    ])
    def test_deletion_only(self, original, cleaned, ok) -> None:
        assert deletion_only(original, cleaned) is ok


def test_the_call(monkeypatch: pytest.MonkeyPatch) -> None:
    client = LLMClient(base_url="http://127.0.0.1:1/v1", api_key="", model="m")
    seen = {}

    def chat(system, user, **kwargs):
        seen.update(system=system, user=user, **kwargs)
        return "tidy: Send it Wednesday.\nanything else"

    monkeypatch.setattr(client, "_chat", chat)
    assert client.clean_corrections("Send it Tuesday, no wait, Wednesday.") == "Send it Wednesday."
    assert seen["user"] == "text: Send it Tuesday, no wait, Wednesday.\ntidy:"
    assert seen["temperature"] == 0.0 and "Delete words ONLY" in seen["system"]


def test_config_validation() -> None:
    config = _default_config_with_paths()
    config["xai"]["api_key"] = "test-api-key"
    config["flow"]["corrections"] = "llm"
    validate_config(config)
    config["flow"]["corrections"] = "always"
    with pytest.raises(RuntimeError, match="flow.corrections"):
        validate_config(config)


# ── the daemon ────────────────────────────────────────────────────────────

@pytest.fixture(autouse=True)
def inline(tmp_path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path))

    async def _to_thread(func, /, *args, **kwargs):
        await asyncio.sleep(0)
        return func(*args, **kwargs)

    monkeypatch.setattr(asyncio, "to_thread", _to_thread)
    monkeypatch.setattr("voice_keyboard.client._show_overlay", lambda *a, **k: None)


def _dictate(said: str, answer, *, mode="llm", app="test-editor", secret=False):
    injector = RecordingInjector()
    daemon = _make_daemon(
        FakeStreamingSTT([{"type": "transcript.partial", "text": said, "is_final": True}]), injector, app=app
    )
    daemon._config["flow"]["corrections"] = mode
    daemon._config["llm"]["base_url"] = "http://127.0.0.1:8081/v1"  # local: ready without a key
    daemon._config["llm"]["model"] = "m"
    daemon._probe_patch = mock.patch(
        "voice_keyboard.daemon.probe_focus",
        return_value=FocusInfo(app=app, role="password text" if secret else "text", x=1, y=1, secret=secret),
    )
    llm = mock.Mock()
    llm.clean_corrections = mock.Mock(side_effect=answer if isinstance(answer, Exception) else None,
                                      return_value=None if isinstance(answer, Exception) else answer)

    async def run() -> str:
        with daemon._audio_patch, daemon._stt_patch, daemon._probe_patch, \
                mock.patch("voice_keyboard.daemon.create_llm_client", return_value=llm):
            await daemon._start_recording()
            assert await wait_until(lambda: injector.screen)
            return await daemon._stop_recording()

    final = asyncio.run(run())
    return final, injector.screen, llm.clean_corrections


def test_the_false_start_is_deleted_on_screen() -> None:
    final, screen, call = _dictate("Send it Tuesday, no wait, Wednesday.", "Send it Wednesday.")
    call.assert_called_once_with("Send it Tuesday, no wait, Wednesday.")
    assert final == screen == "Send it Wednesday."


def test_an_answer_that_adds_a_word_is_ignored() -> None:
    final, screen, _ = _dictate("Send it Tuesday, no wait, Wednesday.", "Send it on Wednesday.")
    assert final == screen == "Send it Tuesday, no wait, Wednesday."


def test_a_failing_model_keeps_the_text() -> None:
    final, screen, _ = _dictate("Five, actually six.", RuntimeError("timeout"))
    assert final == screen == "Five, actually six."


@pytest.mark.parametrize("kwargs", [
    {"mode": "off"}, {"app": "kitty"}, {"secret": True},
], ids=["off", "terminal", "secret"])
def test_never_sent(kwargs) -> None:
    _, _, call = _dictate("Send it Tuesday, no wait, Wednesday.", "Send it Wednesday.", **kwargs)
    call.assert_not_called()


def test_no_cue_no_call() -> None:
    final, _, call = _dictate("We ship on Friday.", "We ship.")
    call.assert_not_called()
    assert final == "We ship on Friday."
