"""[flow] rejoin: a recording that starts soon after the last one, in the
same app and prose register, continues its text instead of gluing itself
to it — a space before the first word, a capital only after a sentence
end. Never in a terminal or code register, never across apps, never
after the window closes."""

import asyncio
from unittest import mock

import pytest
from fakes import FakeStreamingSTT, RecordingInjector, _make_daemon
from waiting import wait_until

from voice_keyboard import daemon as daemon_module
from voice_keyboard.flow.engine import FlowConfig, FlowEngine
from voice_keyboard.flow.grammar import Grammar
from voice_keyboard.flow.registers import PROSE, TERMINAL, continuation_state
from voice_keyboard.focusprobe import FocusInfo


class TestState:
    @pytest.mark.parametrize(
        "tail, register, expected",
        [
            ("Hello world.", PROSE, (False, True)),
            ("Hello world", PROSE, (False, False)),
            ("Really?", PROSE, (False, True)),
            ('He said "no"', PROSE, (False, False)),
            ("ls -la", TERMINAL, (False, False)),
        ],
    )
    def test_space_and_capital(self, tail, register, expected) -> None:
        state = continuation_state(tail, register)
        assert state is not None
        assert (state.at_start, state.capitalize_next) == expected
        assert state.glue_next is False

    @pytest.mark.parametrize("tail", ["", " ", "line\n", "\t"])
    def test_nothing_to_continue(self, tail) -> None:
        assert continuation_state(tail, PROSE) is None

    def test_engine_starts_from_it(self) -> None:
        engine = FlowEngine(
            FlowConfig(), Grammar(), PROSE, initial_state=continuation_state("Hello world", PROSE)
        )
        assert engine.finalize("and more", now=1.0).text == " and more"
        engine = FlowEngine(
            FlowConfig(), Grammar(), PROSE, initial_state=continuation_state("Hello world.", PROSE)
        )
        assert engine.finalize("next sentence", now=1.0).text == " Next sentence"

    def test_scratch_rewinds_to_the_continued_state(self) -> None:
        engine = FlowEngine(
            FlowConfig(), Grammar(), PROSE, initial_state=continuation_state("Hello world", PROSE)
        )
        engine.on_transcript("oops", is_final=True, now=1.0)
        result = engine.finalize("oops scratch that better", now=2.0)
        assert result.text == " better"


class TestDaemon:
    @pytest.fixture(autouse=True)
    def inline(self, tmp_path, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path))

        async def _to_thread(func, /, *args, **kwargs):
            await asyncio.sleep(0)
            return func(*args, **kwargs)

        monkeypatch.setattr(asyncio, "to_thread", _to_thread)
        monkeypatch.setattr("voice_keyboard.client._show_overlay", lambda *a, **k: None)

    def _record_twice(self, first: str, second: str, *, apps=("test-editor", "test-editor"), rejoin=True, elapsed=0.0):
        injector = RecordingInjector()
        fakes = [
            FakeStreamingSTT([{"type": "transcript.partial", "text": first, "is_final": True}]),
            FakeStreamingSTT([{"type": "transcript.partial", "text": second, "is_final": True}]),
        ]
        daemon = _make_daemon(fakes[0], injector, app=apps[0])
        daemon._config["flow"]["rejoin"] = rejoin
        daemon._stt_patch = mock.patch("voice_keyboard.daemon.create_stt_client", side_effect=fakes)
        probes = [FocusInfo(app=app, role="text", x=10, y=10) for app in apps]
        daemon._probe_patch = mock.patch("voice_keyboard.daemon.probe_focus", side_effect=probes)

        async def run() -> list[str]:
            finals = []
            with daemon._audio_patch, daemon._stt_patch, daemon._probe_patch:
                for expect in (first, second):
                    await daemon._start_recording()
                    assert await wait_until(lambda: injector.screen.strip().endswith(expect.split()[-1].strip(".")) or len(injector.screen) > 0)
                    finals.append(await daemon._stop_recording())
                    if elapsed and daemon._landing is not None:
                        daemon._landing["when"] -= elapsed
            return finals

        finals = asyncio.run(run())
        return injector, finals

    def test_second_recording_continues_the_sentence(self) -> None:
        injector, finals = self._record_twice("hello world", "and more")
        assert injector.screen == "Hello world and more"
        assert finals == ["Hello world", " and more"]

    def test_capital_after_a_sentence_end(self) -> None:
        injector, _ = self._record_twice("hello world period", "next one")
        assert injector.screen == "Hello world. Next one"

    def test_terminal_never_gets_a_leading_space(self) -> None:
        injector, _ = self._record_twice("cd", "tmp", apps=("kitty", "kitty"))
        assert injector.screen == "cdtmp"

    def test_another_app_starts_fresh(self) -> None:
        injector, _ = self._record_twice("hello world", "and more", apps=("test-editor", "other-editor"))
        assert injector.screen == "Hello worldAnd more"

    def test_the_window_closes(self) -> None:
        injector, _ = self._record_twice("hello world", "and more", elapsed=daemon_module.REJOIN_WINDOW_S + 1)
        assert injector.screen == "Hello worldAnd more"

    def test_a_trailing_caret_command_ends_the_landing(self) -> None:
        injector = RecordingInjector()
        fakes = [
            FakeStreamingSTT([
                {"type": "transcript.partial", "text": "hello world", "is_final": True},
                {"type": "transcript.partial", "text": "go to start of line", "is_final": True},
            ]),
            FakeStreamingSTT([{"type": "transcript.partial", "text": "next", "is_final": True}]),
        ]
        daemon = _make_daemon(fakes[0], injector)
        daemon._config["nav"]["enabled"] = True
        daemon._stt_patch = mock.patch("voice_keyboard.daemon.create_stt_client", side_effect=fakes)

        async def run() -> None:
            with daemon._audio_patch, daemon._stt_patch, daemon._probe_patch:
                await daemon._start_recording()
                assert await wait_until(lambda: injector.combos)
                await daemon._stop_recording()
                assert daemon._landing is None  # the caret is at the line start, not after the text
                await daemon._start_recording()
                assert await wait_until(lambda: injector.screen.endswith("ext"))
                await daemon._stop_recording()

        asyncio.run(run())
        assert injector.screen == "Hello worldNext"  # typed at the caret, no leading space

    def test_off(self) -> None:
        injector, _ = self._record_twice("hello world", "and more", rejoin=False)
        assert injector.screen == "Hello worldAnd more"
