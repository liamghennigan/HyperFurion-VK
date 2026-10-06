"""[snippets]: text typed by name — "VK, my email" alone, or at the end of
a dictation, where it lands after the words instead of rewriting them."""

import asyncio
from unittest import mock

import pytest
from fakes import FakeStreamingSTT, RecordingInjector, _make_daemon
from waiting import wait_until

from voice_keyboard.config import _default_config_with_paths, validate_config


@pytest.fixture(autouse=True)
def inline(tmp_path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path))

    async def _to_thread(func, /, *args, **kwargs):
        await asyncio.sleep(0)
        return func(*args, **kwargs)

    monkeypatch.setattr(asyncio, "to_thread", _to_thread)
    monkeypatch.setattr("voice_keyboard.client._show_overlay", lambda *a, **k: None)


def _dictate(said: str, snippets: dict, *, macro=None):
    injector = RecordingInjector()
    daemon = _make_daemon(
        FakeStreamingSTT([{"type": "transcript.partial", "text": said, "is_final": True}]), injector
    )
    daemon._config["snippets"] = snippets
    llm = mock.Mock()
    llm.rewrite = mock.Mock(return_value="REWRITTEN")

    async def run() -> str:
        with daemon._audio_patch, daemon._stt_patch, daemon._probe_patch, \
                mock.patch("voice_keyboard.daemon.create_llm_client", return_value=llm), \
                mock.patch("voice_keyboard.dictionary.macro_text", return_value=macro):
            await daemon._start_recording()
            await asyncio.sleep(0.15)
            return await daemon._stop_recording()

    final = asyncio.run(run())
    return final, injector.screen, llm.rewrite


def test_alone() -> None:
    final, screen, rewrite = _dictate("vk my email", {"my email": "liam@example.com"})
    assert screen == final == "liam@example.com"
    rewrite.assert_not_called()


def test_after_a_dictation_it_lands_after_the_words() -> None:
    final, screen, rewrite = _dictate("send the invoice to vk my email", {"My Email": "liam@example.com"})
    assert screen == final == "Send the invoice to liam@example.com"
    rewrite.assert_not_called()


def test_punctuation_glues() -> None:
    final, screen, _ = _dictate("thanks vk sig", {"sig": ", Liam"})
    assert screen == "Thanks, Liam"


def test_newlines_are_kept() -> None:
    _, screen, _ = _dictate("vk sign off", {"sign off": "Thanks,\nLiam"})
    assert screen == "Thanks,\nLiam"


def test_a_learned_macro_works_the_same() -> None:
    final, screen, _ = _dictate("ship it vk trailer", {}, macro="Signed-off-by: Liam")
    assert screen == "Ship it Signed-off-by: Liam"


def test_an_unknown_name_is_still_a_rewrite() -> None:
    final, screen, rewrite = _dictate("i think it works now vk make that formal", {"my email": "x"})
    rewrite.assert_called_once()
    assert screen == "REWRITTEN"


def test_config_validation() -> None:
    config = _default_config_with_paths()
    config["xai"]["api_key"] = "test-api-key"
    config["snippets"] = {"my email": "liam@example.com"}
    validate_config(config)
    for bad in ({"my email": ""}, {"": "x"}, {"n": 3}):
        config["snippets"] = bad
        with pytest.raises(RuntimeError, match="snippets"):
            validate_config(config)
