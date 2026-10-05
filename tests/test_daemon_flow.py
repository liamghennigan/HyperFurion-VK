"""End-to-end molten dictation through the Daemon with a fake streaming
STT client: words are typed while "recording", repairs happen in place,
and stop reconciles the screen with the finalized transcript."""

import asyncio
from unittest import mock

import pytest
from waiting import wait_until

from voice_keyboard.config import _default_config_with_paths
from voice_keyboard.daemon import Daemon
from voice_keyboard.focusprobe import FocusInfo


def _flow_config() -> dict:
    cfg = _default_config_with_paths()
    cfg["xai"]["api_key"] = "test-api-key"
    cfg["flow"]["stability_ms"] = 10
    cfg["flow"]["stability_updates"] = 1
    cfg["flow"]["adaptive"] = False
    # The test key would make [llm] look usable: no real pause reviews.
    cfg["flow"]["pause_review"] = "rules"
    return cfg


class RecordingInjector:
    def __init__(self):
        self.screen = ""
        self.paste_chord_shift = False

    def start(self) -> None:
        pass

    def stop(self) -> None:
        pass

    def type_text(self, text: str) -> None:
        self.screen += text

    def delete_chars(self, count: int) -> None:
        self.screen = self.screen[: len(self.screen) - count]


class FakeStreamingSTT:
    """Speaks the streaming client interface; emits scripted events."""

    supports_streaming = True
    completion_timeout = 5.0

    def __init__(self, events: list[dict]):
        self._events = events
        self._done = asyncio.Event()

    async def connect(self, sample_rate: int) -> None:
        pass

    async def send_audio(self, data: bytes) -> None:
        pass

    async def send_audio_done(self) -> None:
        self._done.set()

    async def receive_events(self):
        for event in self._events:
            yield event
            await asyncio.sleep(0.02)
        await self._done.wait()
        yield {"type": "transcript.done", "text": self._events[-1]["text"]}

    async def close(self) -> None:
        self._done.set()


@pytest.fixture(autouse=True)
def inline_to_thread(monkeypatch: pytest.MonkeyPatch):
    async def _to_thread(func, /, *args, **kwargs):
        # Yield to the event loop first: the audio mock never raises, so
        # without this the _stream_audio loop would starve every other task.
        await asyncio.sleep(0)
        return func(*args, **kwargs)

    monkeypatch.setattr(asyncio, "to_thread", _to_thread)


@pytest.fixture(autouse=True)
def no_overlay(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(
        "voice_keyboard.client._show_overlay",
        lambda *args, **kwargs: None,
    )


def _make_daemon(stt_client, injector) -> Daemon:
    daemon = Daemon(
        config=_flow_config(),
        injector=injector,
        ipc_server=mock.Mock(),
        tts_client=mock.Mock(),
    )
    audio_capture = mock.Mock()
    audio_capture.read_chunk = mock.Mock(return_value=b"\x00" * 320)
    audio_capture.sample_rate = 16000
    audio_capture.running = True
    daemon._audio_patch = mock.patch(
        "voice_keyboard.daemon.AudioCapture", return_value=audio_capture
    )
    daemon._stt_patch = mock.patch(
        "voice_keyboard.daemon.create_stt_client", return_value=stt_client
    )
    daemon._probe_patch = mock.patch(
        "voice_keyboard.daemon.probe_focus",
        return_value=FocusInfo(app="test-editor", role="text", x=10, y=10),
    )
    return daemon


class TestLiveFlow:
    def test_words_stream_in_and_reconcile_at_stop(self) -> None:
        async def run() -> None:
            events = [
                {"type": "transcript.partial", "text": "hello", "is_final": False},
                {"type": "transcript.partial", "text": "hello world", "is_final": False},
                {
                    "type": "transcript.partial",
                    "text": "hello world how are you",
                    "is_final": False,
                },
            ]
            injector = RecordingInjector()
            stt = FakeStreamingSTT(events)
            daemon = _make_daemon(stt, injector)
            with daemon._audio_patch, daemon._stt_patch, daemon._probe_patch:
                await daemon._start_recording()
                assert daemon._flow_worker is not None, "live worker expected"
                # Let interims flow: something must be typed BEFORE stop.
                assert await wait_until(lambda: injector.screen), (
                    "molten text should stream in while recording"
                )

                final = await daemon._stop_recording()
                assert final == "Hello world how are you"
                assert injector.screen == final

        asyncio.run(run())

    def test_live_repair_converges_on_revision(self) -> None:
        async def run() -> None:
            events = [
                {"type": "transcript.partial", "text": "eye scream", "is_final": False},
                {"type": "transcript.partial", "text": "eye scream cone", "is_final": False},
                {"type": "transcript.partial", "text": "ice cream cone", "is_final": False},
            ]
            injector = RecordingInjector()
            stt = FakeStreamingSTT(events)
            daemon = _make_daemon(stt, injector)
            # A horizon longer than the event cadence: the revision lands
            # while the words are still molten, so it repairs in place.
            daemon._config["flow"]["stability_ms"] = 5000
            daemon._config["flow"]["stability_updates"] = 50
            with daemon._audio_patch, daemon._stt_patch, daemon._probe_patch:
                await daemon._start_recording()
                seen = {"wrong": False}

                def repaired() -> bool:
                    if injector.screen.startswith("Eye scream"):
                        seen["wrong"] = True
                    return seen["wrong"] and injector.screen == "Ice cream cone"

                done = await wait_until(repaired)
                assert seen["wrong"], "misheard text should have been typed molten"
                assert done, "revision should repair the typed text in place"

                final = await daemon._stop_recording()
                assert final == "Ice cream cone"
                assert injector.screen == final

        asyncio.run(run())

    def test_terminal_register_resolved_from_probe(self) -> None:
        async def run() -> None:
            events = [
                {"type": "transcript.partial", "text": "git status period", "is_final": False},
            ]
            injector = RecordingInjector()
            stt = FakeStreamingSTT(events)
            daemon = _make_daemon(stt, injector)
            daemon._probe_patch = mock.patch(
                "voice_keyboard.daemon.probe_focus",
                return_value=FocusInfo(app="kitty", role="terminal"),
            )
            with daemon._audio_patch, daemon._stt_patch, daemon._probe_patch:
                await daemon._start_recording()
                assert daemon._session_register.name == "terminal"
                assert injector.paste_chord_shift is True
                final = await daemon._stop_recording()
                # No prose capitalization in a terminal.
                assert final == "git status."

        asyncio.run(run())

    def test_flow_disabled_keeps_legacy_behavior(self) -> None:
        async def run() -> None:
            events = [
                {"type": "transcript.partial", "text": "hello there", "is_final": False},
            ]
            injector = RecordingInjector()
            stt = FakeStreamingSTT(events)
            daemon = _make_daemon(stt, injector)
            daemon._config["flow"]["enabled"] = False
            with daemon._audio_patch, daemon._stt_patch, daemon._probe_patch:
                await daemon._start_recording()
                assert daemon._flow_worker is None
                final = await daemon._stop_recording()
                assert final == "hello there"  # raw: no grammar, no caps
                assert injector.screen == final

        asyncio.run(run())


def _partial(text: str, is_final: bool = False, speech_final: bool = False) -> dict:
    return {
        "type": "transcript.partial",
        "text": text,
        "is_final": is_final,
        "speech_final": speech_final,
    }


# A real grok-voice-transcribe-2.0 session (captured 2026-10-05): one
# sentence spoken with two short thinking pauses. Each pause ends a chunk,
# and every chunk is punctuated as a sentence of its own.
PAUSED_SENTENCE_LIVE = [
    _partial("So I was"),
    _partial("So I was thinking about the project."),
    _partial("So I was thinking about the project.", is_final=True),
    _partial("And"),
    _partial("And how we could make the"),
    _partial("And how we could make the setup simpler."),
    _partial("And how we could make the setup simpler.", is_final=True),
    _partial("For"),
    _partial("For people who have never used."),
    _partial("For people who have never used it before."),
]
PAUSED_SENTENCE_AFTER_STOP = [
    _partial("For people who have never used it before.", is_final=True),
    _partial(
        "So I was thinking about the project. And how we could make the setup "
        "simpler. For people who have never used it before.",
        is_final=True,
        speech_final=True,
    ),
    {"type": "transcript.done", "text": "", "duration": 8.03},
]


class FakeChunkedSTT(FakeStreamingSTT):
    """The xAI 2.0 protocol: some events only arrive after audio.done."""

    def __init__(self, live: list[dict], after_stop: list[dict]):
        super().__init__(live)
        self._after_stop = after_stop

    async def receive_events(self):
        for event in self._events:
            yield event
            await asyncio.sleep(0.02)
        await self._done.wait()
        for event in self._after_stop:
            yield event


class PauseReviewer:
    """Answers every pause with "the sentence goes on"."""

    def __init__(self, fail: bool = False):
        self.calls: list[tuple[str, str]] = []
        self._fail = fail

    def review_pause(self, before: str, after: str) -> str:
        self.calls.append((before, after))
        if self._fail:
            raise RuntimeError("review request failed: 503")
        return f"{before.split()[-1].rstrip('.')} {after.split()[0].lower()}"


class TestPausePunctuation:
    def _run(
        self, live, after_stop, *, mode: str, reviewer=None, typing_live: bool = True
    ) -> tuple[str, str, list]:
        async def run():
            injector = RecordingInjector()
            daemon = _make_daemon(FakeChunkedSTT(live, after_stop), injector)
            daemon._config["flow"]["pause_review"] = mode
            daemon._config["flow"]["live"] = typing_live
            daemon._config["flow"]["stability_ms"] = 1500
            daemon._config["flow"]["stability_updates"] = 2
            screens: list[str] = []
            with daemon._audio_patch, daemon._stt_patch, daemon._probe_patch, \
                 mock.patch("voice_keyboard.daemon.llm_ready", return_value=reviewer is not None), \
                 mock.patch("voice_keyboard.daemon.create_llm_client", return_value=reviewer):
                await daemon._start_recording()
                for _ in range(30):
                    await asyncio.sleep(0.03)
                    if not screens or screens[-1] != injector.screen:
                        screens.append(injector.screen)
                final = await daemon._stop_recording()
            return final, injector.screen, screens

        return asyncio.run(run())

    def test_off_types_the_recognizers_sentence_per_pause(self) -> None:
        final, screen, _ = self._run(
            PAUSED_SENTENCE_LIVE, PAUSED_SENTENCE_AFTER_STOP, mode="off"
        )
        assert final == screen == (
            "So I was thinking about the project. And how we could make the "
            "setup simpler. For people who have never used it before."
        )

    def test_rules_join_a_pause_before_and(self) -> None:
        final, screen, screens = self._run(
            PAUSED_SENTENCE_LIVE, PAUSED_SENTENCE_AFTER_STOP, mode="rules"
        )
        assert final == screen == (
            "So I was thinking about the project and how we could make the "
            "setup simpler. For people who have never used it before."
        )
        # The period was typed at the pause, then repaired while speaking.
        assert "So I was thinking about the project." in screens
        assert any(s.startswith("So I was thinking about the project and") for s in screens)

    def test_the_reviewer_settles_the_unclear_pause(self) -> None:
        reviewer = PauseReviewer()
        final, screen, _ = self._run(
            PAUSED_SENTENCE_LIVE, PAUSED_SENTENCE_AFTER_STOP, mode="auto", reviewer=reviewer
        )
        assert final == screen == (
            "So I was thinking about the project and how we could make the "
            "setup simpler for people who have never used it before."
        )
        # Only the pause the rules couldn't call went to the reviewer, with
        # the first decision already applied to its context.
        [(before, after)] = reviewer.calls
        assert before == (
            "So I was thinking about the project and how we could make the setup simpler."
        )
        assert after.startswith("For people who")

    def test_a_failing_reviewer_leaves_the_rules_call(self) -> None:
        reviewer = PauseReviewer(fail=True)
        final, _, _ = self._run(
            PAUSED_SENTENCE_LIVE, PAUSED_SENTENCE_AFTER_STOP, mode="llm", reviewer=reviewer
        )
        assert reviewer.calls
        assert final == (
            "So I was thinking about the project and how we could make the "
            "setup simpler. For people who have never used it before."
        )

    def test_a_repeated_chunk_is_never_swallowed(self) -> None:
        # Also captured: the same phrase twice, a short pause between. The
        # old overlap merging dropped the second copy until the utterance
        # final restored it — after it had been typed.
        live = [
            _partial("So I was thinking about the project.", is_final=True),
            _partial("So"),
            _partial("So I was thinking about"),
            _partial("So I was thinking about the project."),
            _partial("So I was thinking about the project.", is_final=True),
        ]
        after_stop = [
            _partial(
                "So I was thinking about the project. So I was thinking about the project.",
                is_final=True,
                speech_final=True,
            ),
            {"type": "transcript.done", "text": "", "duration": 5.7},
        ]
        final, screen, screens = self._run(live, after_stop, mode="rules")
        twice = "So I was thinking about the project. So I was thinking about the project."
        assert final == screen == twice
        assert all(twice.startswith(s) for s in screens), screens

    def test_type_at_stop_sessions_are_reviewed_too(self) -> None:
        reviewer = PauseReviewer()
        final, screen, screens = self._run(
            PAUSED_SENTENCE_LIVE,
            PAUSED_SENTENCE_AFTER_STOP,
            mode="auto",
            reviewer=reviewer,
            typing_live=False,
        )
        assert screens == [""]  # nothing typed while speaking
        assert final == screen == (
            "So I was thinking about the project and how we could make the "
            "setup simpler for people who have never used it before."
        )

