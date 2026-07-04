"""The intent channel: a spoken request becomes ONE typed command line,
and Enter is refused by the injector itself — physics, not prompt."""

import asyncio
from unittest import mock

import pytest

from voice_keyboard.config import _default_config_with_paths, validate_config
from voice_keyboard.daemon import Daemon
from voice_keyboard.injector import TextInjector, strip_line_breaks
from voice_keyboard.llm import LLMClient


def _valid_config() -> dict:
    cfg = _default_config_with_paths()
    cfg["xai"]["api_key"] = "test-api-key"
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


class FlagRecordingInjector:
    """Records what suppress_enter was at the moment of each type call."""

    def __init__(self):
        self.typed: list[str] = []
        self.suppress_enter = False
        self.paste_chord_shift = False
        self.flag_at_type: list[bool] = []
        self.fail_next = False

    def start(self) -> None:
        pass

    def stop(self) -> None:
        pass

    def type_text(self, text: str) -> None:
        self.flag_at_type.append(self.suppress_enter)
        if self.fail_next:
            raise RuntimeError("injector exploded")
        self.typed.append(text)

    def delete_chars(self, count: int) -> None:
        pass


def _daemon(cfg: dict | None = None) -> Daemon:
    return Daemon(
        config=cfg or _valid_config(),
        injector=FlagRecordingInjector(),
        ipc_server=mock.Mock(),
        tts_client=mock.Mock(),
    )


class TestStripLineBreaks:
    def test_all_break_flavours_become_spaces(self) -> None:
        assert strip_line_breaks("a\r\nb\nc\rd") == "a b c d"

    def test_plain_text_unchanged(self) -> None:
        assert strip_line_breaks("ls -la") == "ls -la"


class TestInjectorNoEnter:
    @pytest.fixture
    def injector(self) -> TextInjector:
        inj = TextInjector()
        inj.suppress_enter = True
        return inj

    def test_newlines_never_press_enter(self, injector: TextInjector) -> None:
        from evdev import ecodes as e

        with mock.patch("voice_keyboard.injector.UInput") as mock_uinput:
            injector.start()
            injector.type_text("ls -la\npwd\n")
            enter = [
                c.args
                for c in mock_uinput.return_value.write.call_args_list
                if len(c.args) == 3 and c.args[1] == e.KEY_ENTER
            ]
            assert not enter

    def test_press_key_refuses_enter_directly(self, injector: TextInjector) -> None:
        from evdev import ecodes as e

        with mock.patch("voice_keyboard.injector.UInput") as mock_uinput:
            injector.start()
            injector._press_key(e.KEY_ENTER)
            assert not mock_uinput.return_value.write.called

    def test_paste_path_carries_no_newline(self, injector: TextInjector) -> None:
        with mock.patch("voice_keyboard.injector.UInput"), \
             mock.patch("voice_keyboard.injector.clipboard.available", return_value=True), \
             mock.patch("voice_keyboard.injector.clipboard.get_text", return_value=None), \
             mock.patch("voice_keyboard.injector.clipboard.set_text", return_value=True) as set_text, \
             mock.patch("voice_keyboard.injector.time.sleep"):
            injector.start()
            injector.type_text("é\né")
            pasted = "".join(c.args[0] for c in set_text.call_args_list)
            assert "\n" not in pasted and "\r" not in pasted

    def test_enter_still_types_when_flag_off(self) -> None:
        from evdev import ecodes as e

        inj = TextInjector()
        with mock.patch("voice_keyboard.injector.UInput") as mock_uinput:
            inj.start()
            inj.type_text("a\n")
            presses = [
                c.args
                for c in mock_uinput.return_value.write.call_args_list
                if len(c.args) == 3 and c.args[1] == e.KEY_ENTER and c.args[2] == 1
            ]
            assert len(presses) == 1


class TestCompileCommand:
    def _client(self) -> LLMClient:
        return LLMClient(base_url="https://api.x.ai/v1", api_key="k", model="m")

    def _response(self, content: str) -> mock.Mock:
        response = mock.Mock()
        response.raise_for_status = mock.Mock()
        response.json.return_value = {
            "choices": [{"message": {"content": content}}]
        }
        return response

    def test_fenced_multiline_reduces_to_first_line(self) -> None:
        with mock.patch(
            "voice_keyboard.llm.requests.post",
            return_value=self._response("```bash\nls -la\nrm -rf /\n```"),
        ):
            assert self._client().compile_command("list files") == "ls -la"

    def test_empty_reply_raises(self) -> None:
        with mock.patch(
            "voice_keyboard.llm.requests.post", return_value=self._response("   ")
        ):
            with pytest.raises(RuntimeError, match="intent"):
                self._client().compile_command("list files")

    def test_network_error_raises_readable(self) -> None:
        import requests

        with mock.patch(
            "voice_keyboard.llm.requests.post",
            side_effect=requests.RequestException("boom"),
        ):
            with pytest.raises(RuntimeError, match="intent request failed"):
                self._client().compile_command("list files")


class TestIntentRouting:
    def test_disabled_by_default(self) -> None:
        daemon = _daemon()
        assert daemon._intent_request("run the flaky tests") is False

    def test_verb_routes_when_enabled(self) -> None:
        cfg = _valid_config()
        cfg["intent"]["enabled"] = True
        daemon = _daemon(cfg)
        assert daemon._intent_request("run the flaky tests") is True
        assert daemon._intent_request("Run, the tests") is True
        assert daemon._intent_request("make that formal") is False

    def test_custom_verbs(self) -> None:
        cfg = _valid_config()
        cfg["intent"]["enabled"] = True
        cfg["intent"]["verbs"] = ["do"]
        daemon = _daemon(cfg)
        assert daemon._intent_request("do the thing") is True
        assert daemon._intent_request("run the thing") is False


class TestRunIntent:
    def test_types_with_no_enter_armed_and_restores(self) -> None:
        daemon = _daemon()
        llm = mock.Mock()
        llm.compile_command.return_value = "grep -rn TODO ."

        async def run() -> None:
            with mock.patch(
                "voice_keyboard.daemon.create_llm_client", return_value=llm
            ):
                result = await daemon._run_intent("run find all todos")
            assert result == "grep -rn TODO ."

        asyncio.run(run())
        injector = daemon._injector
        assert injector.typed == ["grep -rn TODO ."]
        assert injector.flag_at_type == [True]
        assert injector.suppress_enter is False
        assert daemon._last_typed == "grep -rn TODO ."

    def test_flag_restored_when_typing_fails(self) -> None:
        daemon = _daemon()
        daemon._injector.fail_next = True
        llm = mock.Mock()
        llm.compile_command.return_value = "ls"

        async def run() -> None:
            with mock.patch(
                "voice_keyboard.daemon.create_llm_client", return_value=llm
            ):
                with pytest.raises(RuntimeError, match="injector exploded"):
                    await daemon._run_intent("run list files")

        asyncio.run(run())
        assert daemon._injector.suppress_enter is False

    def test_no_llm_configured_raises(self) -> None:
        daemon = _daemon()

        async def run() -> None:
            with mock.patch(
                "voice_keyboard.daemon.create_llm_client", return_value=None
            ):
                with pytest.raises(RuntimeError, match=r"\[llm\]"):
                    await daemon._run_intent("run list files")

        asyncio.run(run())

    def test_intent_last_refuses_while_recording(self) -> None:
        daemon = _daemon()
        daemon._recording = True

        async def run() -> None:
            with pytest.raises(RuntimeError, match="stop recording"):
                await daemon._intent_last("run list files")

        asyncio.run(run())

    def test_standalone_wake_word_routes_to_intent(self) -> None:
        cfg = _valid_config()
        cfg["intent"]["enabled"] = True
        daemon = _daemon(cfg)
        daemon._run_intent = mock.AsyncMock(return_value="CMD")
        daemon._run_transform = mock.AsyncMock(return_value="REWRITE")

        result = asyncio.run(daemon._transform_previous_or_report("run the tests"))
        assert result == "CMD"
        daemon._run_intent.assert_awaited_once()
        daemon._run_transform.assert_not_awaited()

    def test_standalone_wake_word_still_transforms_without_verb(self) -> None:
        cfg = _valid_config()
        cfg["intent"]["enabled"] = True
        daemon = _daemon(cfg)
        daemon._run_intent = mock.AsyncMock(return_value="CMD")
        daemon._run_transform = mock.AsyncMock(return_value="REWRITE")

        result = asyncio.run(daemon._transform_previous_or_report("make that formal"))
        assert result == "REWRITE"
        daemon._run_transform.assert_awaited_once()
        daemon._run_intent.assert_not_awaited()


class TestIntentConfigValidation:
    def test_defaults_validate(self) -> None:
        validate_config(_valid_config())

    def test_enabled_must_be_bool(self) -> None:
        cfg = _valid_config()
        cfg["intent"]["enabled"] = "yes"
        with pytest.raises(RuntimeError, match="intent.enabled"):
            validate_config(cfg)

    def test_verbs_must_be_string_list(self) -> None:
        cfg = _valid_config()
        cfg["intent"]["verbs"] = "run"
        with pytest.raises(RuntimeError, match="intent.verbs"):
            validate_config(cfg)

    def test_verbs_reject_empty_entries(self) -> None:
        cfg = _valid_config()
        cfg["intent"]["verbs"] = ["run", " "]
        with pytest.raises(RuntimeError, match="intent.verbs"):
            validate_config(cfg)
