"""A type-at-stop session (a provider that doesn't stream: OpenAI cloud,
Groq, Deepgram, AssemblyAI, or [flow] live = false) types nothing until
you stop, so it has no focus watchdog. It looks at focus once more before
any key goes out, and follows the live path's rules: another app gets
nothing (the transcript goes to the clipboard), another tab or field of
the same app only ever makes line breaks stricter, and focus it can't
identify gets a space for a line break. A line break never presses Enter
where the start-of-recording choice didn't."""

import asyncio
from types import SimpleNamespace
from unittest import mock

import pytest
from fakes import FakeStreamingSTT, RecordingInjector, _make_daemon

from voice_keyboard.focusprobe import FocusInfo
from voice_keyboard.newline import ENTER, NONE, SHIFT_ENTER


class NewlineInjector(RecordingInjector):
    """Like the real injectors: a newline is Enter, Shift+Enter while
    shift_newline is set, and a space while Enter is refused."""

    def __init__(self):
        super().__init__()
        self.suppress_enter = False
        self.shift_newline = False
        self.enters = 0
        self.shift_enters = 0

    def type_text(self, text: str) -> None:
        if self.suppress_enter:
            text = text.replace("\n", " ")
        elif self.shift_newline:
            self.shift_enters += text.count("\n")
        else:
            self.enters += text.count("\n")
        super().type_text(text)


@pytest.fixture(autouse=True)
def inline(tmp_path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path))

    async def _to_thread(func, /, *args, **kwargs):
        await asyncio.sleep(0)
        return func(*args, **kwargs)

    monkeypatch.setattr(asyncio, "to_thread", _to_thread)
    monkeypatch.setattr("voice_keyboard.client._show_overlay", lambda *a, **k: None)


GEDIT = FocusInfo(app="gedit", role="text")
SLACK = FocusInfo(app="Slack", role="entry")
KITTY = FocusInfo(app="kitty", role="terminal")
GOOGLE_DOC = FocusInfo(app="Google Chrome", role="entry", title="Plan - Google Docs - Google Chrome",
                       url="https://docs.google.com/document/d/1/edit",
                       page_url="https://docs.google.com/document/d/1/edit")
WHATSAPP_TAB = FocusInfo(app="Google Chrome", role="entry", title="WhatsApp - Google Chrome",
                         url="https://web.whatsapp.com/", page_url="https://web.whatsapp.com/")


def dictate_at_stop(said: str, start, stop, *, clipboard_ok=True, live=False, config=None, llm=None):
    """Start a recording with `start` focused, then stop it with `stop`
    focused: every probe after the first sees `stop`."""
    injector = NewlineInjector()
    stt = FakeStreamingSTT([{"type": "transcript.partial", "text": said, "is_final": True}])
    if not live:
        stt.supports_streaming = False  # a batch provider: everything types at stop
    daemon = _make_daemon(stt, injector)
    for section, values in (config or {}).items():
        daemon._config.setdefault(section, {}).update(values)
    probes = {"focus": start, "count": 0}

    def probe():
        probes["count"] += 1
        return probes["focus"]

    overlays: list[tuple[str, str]] = []

    async def overlay(state, detail="", **kw):
        overlays.append((state, detail))

    daemon._show_hotkey_overlay = overlay
    clipped: list[str] = []

    def set_text(text: str) -> bool:
        if clipboard_ok:
            clipped.append(text)
        return clipboard_ok

    async def run() -> str:
        with daemon._audio_patch, daemon._stt_patch, \
                mock.patch("voice_keyboard.daemon.probe_focus", side_effect=probe), \
                mock.patch("voice_keyboard.daemon.clipboard.set_text", side_effect=set_text), \
                mock.patch("voice_keyboard.daemon.llm_ready", return_value=llm is not None), \
                mock.patch("voice_keyboard.daemon.create_llm_client", return_value=llm):
            await daemon._hotkey_start_recording()
            if not live:
                assert daemon._flow_worker is None and daemon._focus_watchdog is None
            probes["focus"] = stop  # the user moved before (or as) they stopped
            await daemon._hotkey_stop_recording()
            return daemon._last_typed

    last = asyncio.run(run())
    return SimpleNamespace(injector=injector, clipped=clipped, overlays=overlays, daemon=daemon,
                           probes=probes["count"], last=last)


class TestFocusAtStop:
    def test_same_app_types_as_chosen_at_start(self) -> None:
        run = dictate_at_stop("dear team new line thanks", GEDIT, GEDIT)
        assert run.injector.screen == "Dear team\nThanks" and run.injector.enters == 1
        assert run.clipped == []
        assert ("inserted", "Inserted 16 characters") in run.overlays

    def test_another_app_gets_nothing_and_the_transcript_goes_to_the_clipboard(self) -> None:
        # Started in a document, stopped in a chat: Enter there would send.
        run = dictate_at_stop("dear team new line thanks", GEDIT, SLACK)
        assert run.injector.screen == "" and run.injector.enters == 0 and run.injector.shift_enters == 0
        assert run.clipped == ["Dear team\nThanks"]
        details = " ".join(detail for _, detail in run.overlays)
        assert "Focus changed" in details and "clipboard" in details
        # Not mistaken for an empty recording.
        assert not any(state == "empty" for state, _ in run.overlays)

    def test_another_app_with_no_clipboard_still_gets_nothing(self) -> None:
        run = dictate_at_stop("dear team new line thanks", GEDIT, SLACK, clipboard_ok=False)
        assert run.injector.screen == "" and run.injector.enters == 0
        details = " ".join(detail for _, detail in run.overlays)
        assert "Focus changed" in details and "nothing typed" in details

    def test_another_tab_of_the_same_app_only_gets_stricter(self) -> None:
        # A Google Doc (Enter) left for WhatsApp Web in the same browser.
        run = dictate_at_stop("dear team new line thanks", GOOGLE_DOC, WHATSAPP_TAB)
        assert run.injector.screen == "Dear team\nThanks"
        assert (run.injector.enters, run.injector.shift_enters) == (0, 1)
        assert (run.injector.suppress_enter, run.injector.shift_newline) == (False, False)  # put back

    def test_never_loosens(self) -> None:
        # Started in a chat tab (Shift+Enter), stopped in a doc tab: still Shift+Enter.
        run = dictate_at_stop("dear team new line thanks", WHATSAPP_TAB, GOOGLE_DOC)
        assert (run.injector.enters, run.injector.shift_enters) == (0, 1)

    def test_focus_it_cant_identify_gets_a_space(self) -> None:
        # A GPU terminal exposes no accessibility: the probe sees nothing,
        # and a line break there would run the line.
        run = dictate_at_stop("make new line make install", GEDIT, None)
        assert run.injector.enters == 0 and "\n" not in run.injector.screen
        assert run.injector.screen == "Make Make install"
        assert any("Line break typed as a space" in detail for _, detail in run.overlays)

    def test_a_terminal_it_knows_is_another_app(self) -> None:
        run = dictate_at_stop("ls new line rm", GEDIT, KITTY)
        assert run.injector.screen == "" and run.injector.enters == 0
        assert run.clipped == ["Ls\nRm"]

    def test_a_scratch_that_in_another_app_backspaces_nothing(self) -> None:
        run = dictate_at_stop("hello there", GEDIT, GEDIT)
        assert run.injector.screen == "Hello there"
        daemon, injector = run.daemon, run.injector
        stt = FakeStreamingSTT([{"type": "transcript.partial", "text": "scratch that", "is_final": True}])
        stt.supports_streaming = False
        probes = iter([GEDIT, SLACK, SLACK, SLACK])

        async def again() -> None:
            with daemon._audio_patch, mock.patch("voice_keyboard.daemon.create_stt_client", return_value=stt), \
                    mock.patch("voice_keyboard.daemon.probe_focus", side_effect=lambda: next(probes)):
                await daemon._start_recording()
                await daemon._stop_recording()

        asyncio.run(again())
        assert injector.screen == "Hello there"

    def test_a_slow_rewrite_looks_again_before_typing(self) -> None:
        # "VK, make it formal" waits on the model for seconds: focus may
        # move meanwhile, and the keys go wherever it is then.
        moves: dict = {}

        class Model:
            def rewrite(self, text, instruction):
                moves["during"] = True
                moves["set"](SLACK)
                daemon._focus_checked_at -= 10.0  # the model took its time
                return "Dear team,\nThanks."

        model = Model()
        injector = NewlineInjector()
        stt = FakeStreamingSTT([{"type": "transcript.partial", "text": "dear team thanks vk make it formal",
                                 "is_final": True}])
        stt.supports_streaming = False
        daemon = _make_daemon(stt, injector)
        box = {"focus": GEDIT}
        moves["set"] = lambda focus: box.update(focus=focus)
        clipped: list[str] = []

        async def overlay(state, detail="", **kw):
            pass

        daemon._show_hotkey_overlay = overlay

        async def run() -> None:
            with daemon._audio_patch, daemon._stt_patch, \
                    mock.patch("voice_keyboard.daemon.probe_focus", side_effect=lambda: box["focus"]), \
                    mock.patch("voice_keyboard.daemon.clipboard.set_text",
                               side_effect=lambda text: clipped.append(text) or True), \
                    mock.patch("voice_keyboard.daemon.llm_ready", return_value=True), \
                    mock.patch("voice_keyboard.daemon.create_llm_client", return_value=model):
                await daemon._start_recording()
                await daemon._stop_recording()

        asyncio.run(run())
        assert moves.get("during")
        assert injector.screen == "" and injector.enters == 0
        assert clipped == ["Dear team,\nThanks."]

    def test_a_quick_rewrite_needs_no_second_look(self) -> None:
        class Model:
            def rewrite(self, text, instruction):
                return "Dear team,\nThanks."

        run = dictate_at_stop("dear team thanks vk make it formal", GEDIT, GEDIT, llm=Model())
        assert run.probes == 2 and run.injector.screen == "Dear team,\nThanks." and run.injector.enters == 1

    def test_unchanged_focus_is_looked_at_once(self) -> None:
        run = dictate_at_stop("hello there", GEDIT, GEDIT)
        assert run.probes == 2  # at start, and once before typing

    def test_no_probe_no_look(self) -> None:
        # [registers] probe = false: focus was never known, line breaks are
        # spaces already, and nothing probes at stop either.
        run = dictate_at_stop("one new line two", GEDIT, SLACK, config={"registers": {"probe": False}})
        assert run.probes == 0 and run.injector.screen == "One Two" and run.injector.enters == 0


class TestStopCommand:
    """`voice-keyboard stop` (a desktop shortcut running the CLI) must not
    report "no speech" when the transcript went to the clipboard."""

    def test_the_daemon_says_why_nothing_was_typed(self) -> None:
        run = dictate_at_stop("dear team new line thanks", GEDIT, SLACK)
        assert run.daemon._stop_note.startswith("Focus changed")

    def test_a_new_recording_clears_it(self) -> None:
        run = dictate_at_stop("dear team new line thanks", GEDIT, SLACK)
        daemon = run.daemon
        stt = FakeStreamingSTT([{"type": "transcript.partial", "text": "hello", "is_final": True}])
        stt.supports_streaming = False

        async def again() -> str:
            with daemon._audio_patch, mock.patch("voice_keyboard.daemon.create_stt_client", return_value=stt), \
                    mock.patch("voice_keyboard.daemon.probe_focus", return_value=GEDIT):
                await daemon._start_recording()
                return await daemon._stop_recording()

        assert asyncio.run(again()) == "Hello" and daemon._stop_note == ""

    def test_the_ipc_stop_answer_carries_it(self) -> None:
        import json
        import threading

        injector = NewlineInjector()
        stt = FakeStreamingSTT([{"type": "transcript.partial", "text": "dear team new line thanks", "is_final": True}])
        stt.supports_streaming = False
        daemon = _make_daemon(stt, injector)
        box = {"focus": GEDIT}

        async def overlay(state, detail="", **kw):
            pass

        daemon._show_hotkey_overlay = overlay
        loop = asyncio.new_event_loop()
        thread = threading.Thread(target=loop.run_forever, daemon=True)
        thread.start()
        daemon._loop = loop
        sent: list[dict] = []

        class Conn:
            def __init__(self):
                self._data = [json.dumps({"command": "stop"}).encode(), b""]

            def recv(self, size):
                return self._data.pop(0)

            def sendall(self, data):
                sent.append(json.loads(data.decode()))

            def close(self):
                pass

        conns = [Conn()]

        def accept():
            if not conns:
                raise OSError("closed")
            return conns.pop(0)

        daemon._ipc_server = mock.Mock(accept=accept, required_token=None)
        try:
            with daemon._audio_patch, daemon._stt_patch, \
                    mock.patch("voice_keyboard.daemon.probe_focus", side_effect=lambda: box["focus"]), \
                    mock.patch("voice_keyboard.daemon.clipboard.set_text", return_value=True):
                asyncio.run_coroutine_threadsafe(daemon._start_recording(), loop).result(timeout=5)
                box["focus"] = SLACK
                daemon._ipc_loop()
        finally:
            loop.call_soon_threadsafe(loop.stop)
            thread.join(timeout=5)
            loop.close()
        assert injector.screen == ""
        assert sent == [{"status": "ok", "message": "recording stopped", "text": "",
                         "note": "Focus changed — nothing typed; the transcript is on the clipboard"}]

    def test_the_cli_passes_it_on(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from voice_keyboard import client

        monkeypatch.setattr("sys.argv", ["voice-keyboard", "stop"])
        note = "Focus changed — nothing typed; the transcript is on the clipboard"
        fake = mock.Mock()
        fake.send_command = mock.Mock(return_value={"status": "ok", "message": "recording stopped",
                                                     "text": "", "note": note})
        printed: list[str] = []
        with mock.patch("voice_keyboard.client.load_config", return_value={"daemon": {"socket_path": "/x"}}), \
                mock.patch("voice_keyboard.client.IPCClient", return_value=fake), \
                mock.patch("voice_keyboard.client._show_overlay") as overlay, \
                mock.patch("voice_keyboard.client._notify") as notify, \
                mock.patch("builtins.print", side_effect=lambda *a, **k: printed.append(" ".join(map(str, a)))):
            client.main()
        assert overlay.call_args_list == [mock.call("processing")]  # the daemon's own overlay stays up
        assert mock.call("Voice Keyboard", note, timeout_ms=4000) in notify.call_args_list
        assert not any("No speech" in str(c) for c in notify.call_args_list)
        assert printed == [note]


class TestLiveSessionsUnchanged:
    def test_a_live_session_still_types_as_it_goes(self) -> None:
        run = dictate_at_stop("dear team new line thanks", GEDIT, GEDIT, live=True)
        assert run.injector.screen == "Dear team\nThanks" and run.injector.enters == 1


def test_choices_used_here() -> None:
    from voice_keyboard.newline import choose_newline

    assert choose_newline(GEDIT).key == ENTER
    assert choose_newline(WHATSAPP_TAB).key == SHIFT_ENTER
    assert choose_newline(None).key == NONE
