"""The daemon and Kai's on/off state (DESIGN A5–A7, A13).

Kai's brain exists only while Kai is on. Turning Kai off takes effect at
once — over IPC, through a reload, even with an unrelated error in the file
— and drops a question being captured before it is sent. Summoning reads
config.toml first, and re-checks before anything connects. While Kai is off
the orb is hidden, the wake word keeps the mic closed, and Right Ctrl (also
an ordinary Ctrl key) explains itself only on release, at most every ten
minutes.
"""

import asyncio
import json
import os
import sys
import threading
from pathlib import Path
from unittest import mock

import pytest

from voice_keyboard import client
from voice_keyboard.config import _default_config_with_paths
from voice_keyboard.daemon import Daemon

LOCAL_FILE = """\
[providers.openai]
base_url = "http://127.0.0.1:8000/v1"

[stt]
provider = "openai"

[tts]
provider = "openai"

[llm]
provider = "openai"
base_url = "http://127.0.0.1:8080/v1"
model = "qwen"

[assistant]
enabled = {enabled}
"""

ONLINE_FILE = """\
[providers.xai]
api_key = "xai-test-key"

[assistant]
enabled = {enabled}
"""


@pytest.fixture(autouse=True)
def isolated(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))
    return tmp_path / "config" / "voice-keyboard" / "config.toml"


@pytest.fixture(autouse=True)
def inline_to_thread(monkeypatch: pytest.MonkeyPatch):
    async def _to_thread(func, /, *args, **kwargs):
        return func(*args, **kwargs)

    monkeypatch.setattr(asyncio, "to_thread", _to_thread)


@pytest.fixture(autouse=True)
def overlay(monkeypatch: pytest.MonkeyPatch) -> mock.Mock:
    shown = mock.Mock()
    monkeypatch.setattr(client, "_show_overlay", shown)
    monkeypatch.setattr(client, "_set_overlay_button", mock.Mock())
    monkeypatch.setattr("voice_keyboard.earcon.play_earcon", lambda kind: None)
    return shown


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    before = path.stat().st_mtime_ns if path.exists() else 0
    path.write_text(text, encoding="utf-8")
    # A distinct stamp even on coarse file-system clocks.
    stamp = max(before + 2_000_000_000, path.stat().st_mtime_ns)
    os.utime(path, ns=(stamp, stamp))


def _online(**assistant) -> dict:
    cfg = _default_config_with_paths()
    cfg["xai"]["api_key"] = "xai-test-key"
    cfg["providers"]["xai"]["api_key"] = "xai-test-key"
    cfg["assistant"].update(assistant)
    return cfg


def _local(**assistant) -> dict:
    cfg = _online(**assistant)
    cfg["providers"]["openai"]["base_url"] = "http://127.0.0.1:8000/v1"
    cfg["stt"]["provider"] = "openai"
    cfg["tts"]["provider"] = "openai"
    cfg["llm"].update(provider="openai", base_url="http://127.0.0.1:8080/v1", model="qwen")
    return cfg


def _daemon(cfg: dict) -> Daemon:
    return Daemon(config=cfg, injector=mock.Mock(), ipc_server=mock.Mock(), tts_client=mock.Mock())


def _details(shown: mock.Mock) -> list[str]:
    return [c.kwargs.get("detail", "") for c in shown.call_args_list]


def _audio() -> mock.Mock:
    capture = mock.Mock()
    capture.sample_rate = 16000
    capture.read_chunk = mock.Mock(side_effect=lambda: b"\x01\x00" * 160)
    return capture


def _stt() -> mock.AsyncMock:
    stt = mock.AsyncMock()
    stt.supports_streaming = False
    stt.bias_prompt = ""

    async def send_audio(chunk):
        await asyncio.sleep(0.002)  # a real send yields to the loop

    stt.send_audio.side_effect = send_audio

    async def events():
        await asyncio.sleep(60)
        yield {}

    stt.receive_events = events
    return stt


# ── the orb and the wake word follow Kai ────────────────────────────────


@pytest.mark.parametrize("cfg, visible", [
    (_online(), False),
    (_local(), True),
    (_local(button=False), False),
    (_online(enabled=True), True),
    (_local(enabled=False), False),
])
def test_orb_only_while_kai_is_on(cfg: dict, visible: bool) -> None:
    daemon = _daemon(cfg)
    daemon._push_button_visibility()
    client._set_overlay_button.assert_called_with(visible)
    assert daemon._status_response()["assistant_button"] is visible


def test_wake_word_keeps_the_mic_closed_while_kai_is_off(caplog) -> None:
    cfg = _online()
    cfg["wake"]["enabled"] = True
    daemon = _daemon(cfg)
    with mock.patch("voice_keyboard.wake.WakeListener") as listener:
        daemon._start_wake_listener()
    listener.assert_not_called()
    assert daemon._wake_listener is None
    from voice_keyboard.assistant.locality import turn_on_hint

    assert "Kai is off" in caplog.text and turn_on_hint() in caplog.text


def test_wake_word_listens_while_kai_is_on() -> None:
    cfg = _local()
    cfg["wake"]["enabled"] = True
    daemon = _daemon(cfg)
    with mock.patch("voice_keyboard.wake.WakeListener") as listener:
        daemon._start_wake_listener()
        daemon._start_wake_listener()  # idempotent
    listener.assert_called_once()
    listener.return_value.start.assert_called_once()


# ── Right Ctrl while Kai is off (A13) ───────────────────────────────────


def test_hold_while_off_shows_nothing_until_release(overlay: mock.Mock) -> None:
    daemon = _daemon(_online())
    daemon._start_recording = mock.AsyncMock()

    async def scenario() -> None:
        await daemon._handle_hotkey_action("converse_start")
        assert overlay.call_count == 0
        assert daemon._off_hold is True
        await daemon._handle_hotkey_action("converse_stop")

    asyncio.run(scenario())
    daemon._start_recording.assert_not_awaited()
    detail = _details(overlay)[-1]
    assert "Kai is off" in detail and "xAI" in detail
    if sys.platform == "win32":
        assert "tray" in detail
    else:
        assert "kai on" in detail
    assert overlay.call_args.kwargs["timeout_ms"] == 6000


def test_release_hint_shows_at_most_every_ten_minutes(overlay: mock.Mock) -> None:
    daemon = _daemon(_online())

    async def hold_and_release() -> None:
        await daemon._handle_hotkey_action("converse_start")
        await daemon._handle_hotkey_action("converse_stop")

    asyncio.run(hold_and_release())
    asyncio.run(hold_and_release())
    assert overlay.call_count == 1
    daemon._off_hint_at -= 601
    asyncio.run(hold_and_release())
    assert overlay.call_count == 2


def test_hold_then_chord_shows_nothing(overlay: mock.Mock) -> None:
    # Right Ctrl used as Ctrl: the gesture is cancelled by the next key.
    daemon = _daemon(_online())

    async def scenario() -> None:
        await daemon._handle_hotkey_action("converse_start")
        await daemon._handle_hotkey_action("converse_cancel")
        await daemon._handle_hotkey_action("converse_stop")

    asyncio.run(scenario())
    assert overlay.call_count == 0


def test_cancel_with_nothing_live_shows_nothing(overlay: mock.Mock) -> None:
    daemon = _daemon(_local())
    asyncio.run(daemon._handle_hotkey_action("converse_cancel"))
    assert overlay.call_count == 0


def test_explicitly_off_says_only_that(overlay: mock.Mock) -> None:
    daemon = _daemon(_local(enabled=False))

    async def scenario() -> None:
        await daemon._handle_hotkey_action("converse_start")
        await daemon._handle_hotkey_action("converse_stop")

    asyncio.run(scenario())
    assert _details(overlay) == ["⌁ Kai is off"]


def test_summon_while_off_explains_at_once(overlay: mock.Mock) -> None:
    # The orb, `summon`, a tap on a chord binding: asked on purpose.
    daemon = _daemon(_online())
    daemon._start_recording = mock.AsyncMock()
    asyncio.run(daemon._handle_hotkey_action("converse_toggle"))
    asyncio.run(daemon._handle_hotkey_action("converse_toggle"))
    daemon._start_recording.assert_not_awaited()
    assert overlay.call_count == 2  # never rate-limited when asked
    assert "xAI" in _details(overlay)[-1]


def test_the_hints_never_name_right_ctrl() -> None:
    from voice_keyboard.assistant.locality import turn_off_hint, turn_on_hint

    for platform in ("linux", "darwin", "win32"):
        for hint in (turn_on_hint(platform), turn_off_hint(platform)):
            assert "Right Ctrl" not in hint
    assert "tray" in turn_on_hint("win32")
    assert turn_on_hint("linux").endswith("kai on")


# ── summoning reads the file first, and re-checks (A5, L3) ──────────────


def test_summon_reads_config_first(isolated: Path) -> None:
    daemon = _daemon(_online())
    assert daemon._brain is None
    _write(isolated, ONLINE_FILE.format(enabled="true"))
    daemon._start_recording = mock.AsyncMock()
    asyncio.run(daemon._converse_start())
    assert daemon._brain is not None
    daemon._start_recording.assert_awaited_once_with(reload=False)


def test_summon_after_off_never_opens_mic_or_speech(isolated: Path) -> None:
    daemon = _daemon(_local())
    assert daemon._brain is not None
    _write(isolated, LOCAL_FILE.format(enabled="false"))
    with mock.patch("voice_keyboard.daemon.AudioCapture") as audio, \
            mock.patch("voice_keyboard.daemon.create_stt_client") as stt:
        asyncio.run(daemon._handle_hotkey_action("converse_start"))
    audio.assert_not_called()
    stt.assert_not_called()
    assert daemon._brain is None and daemon._converse_capture is False


def test_off_before_connect_creates_no_speech_client(overlay: mock.Mock) -> None:
    daemon = _daemon(_local())
    capture = _audio()
    capture.start.side_effect = lambda: setattr(daemon, "_brain", None)
    with mock.patch("voice_keyboard.daemon.AudioCapture", return_value=capture), \
            mock.patch("voice_keyboard.daemon.create_stt_client") as stt:
        asyncio.run(daemon._converse_start())
    stt.assert_not_called()
    capture.stop.assert_called_once()
    assert daemon._recording is False and daemon._converse_capture is False
    assert _details(overlay)[-1] == "⌁ Kai is off"


def test_off_while_connecting_sends_nothing() -> None:
    daemon = _daemon(_local())
    stt = _stt()
    stt.connect.side_effect = lambda rate: setattr(daemon, "_brain", None)
    with mock.patch("voice_keyboard.daemon.AudioCapture", return_value=_audio()), \
            mock.patch("voice_keyboard.daemon.create_stt_client", return_value=stt):
        asyncio.run(daemon._converse_start())
    stt.send_audio.assert_not_awaited()
    stt.close.assert_awaited()
    assert daemon._recording is False


def test_off_mid_question_drops_it_before_the_brain() -> None:
    daemon = _daemon(_local())
    stt = _stt()
    brain = daemon._brain
    brain.respond_audio = mock.AsyncMock()

    async def scenario() -> None:
        with mock.patch("voice_keyboard.daemon.AudioCapture", return_value=_audio()), \
                mock.patch("voice_keyboard.daemon.create_stt_client", return_value=stt):
            await daemon._handle_hotkey_action("converse_start")
            assert daemon._recording and daemon._converse_capture
            await asyncio.sleep(0.01)
            sent = stt.send_audio.await_count
            daemon._set_brain(None)  # a reload, the poll or `kai off`
            await asyncio.sleep(0.05)
            for task in list(daemon._kai_tasks):
                await task
            assert stt.send_audio.await_count <= sent + 1
            await daemon._handle_hotkey_action("converse_stop")

    asyncio.run(scenario())
    assert daemon._recording is False and daemon._converse_capture is False
    assert daemon._converse_task is None
    brain.respond_audio.assert_not_awaited()
    stt.send_audio_done.assert_not_awaited()


def test_turn_keeps_the_brain_it_started_with() -> None:
    daemon = _daemon(_local())
    from voice_keyboard.flow.registers import resolve_register
    from voice_keyboard.focusprobe import FocusInfo

    daemon._session_register = resolve_register("prose")
    daemon._session_focus = FocusInfo(app="gedit")
    daemon._run_tts = mock.AsyncMock()
    first = mock.Mock()
    second = mock.Mock()

    async def respond(*a, **k):
        daemon._brain = second  # a reload mid-turn
        return mock.Mock(text="ok", audio=b"", brain="local")

    first.respond_audio = respond
    daemon._brain = first
    asyncio.run(daemon._run_converse_audio(b"pcm", "hello"))
    first.remember_interaction.assert_called_once_with("hello", "ok")
    second.remember_interaction.assert_not_called()


def test_turn_with_kai_off_is_dropped(overlay: mock.Mock) -> None:
    daemon = _daemon(_online())
    assert asyncio.run(daemon._run_converse_audio(b"pcm", "hello")) == ""
    assert _details(overlay)[-1] == "⌁ Kai is off"


# ── off is unconditional (A6) ───────────────────────────────────────────


def test_kai_off_with_a_broken_file(isolated: Path) -> None:
    daemon = _daemon(_local())
    _write(isolated, "this is not toml = = =\n")
    asyncio.run(daemon._kai_off_request())
    assert daemon._brain is None
    status = daemon._status_response()
    assert status["assistant"] is False and status["assistant_setting"] == "off"


def test_reload_with_off_and_an_unrelated_error_turns_kai_off(isolated: Path) -> None:
    daemon = _daemon(_local())
    _write(isolated, LOCAL_FILE.format(enabled="false") + '\n[hotkey]\nmode = "sideways"\n')
    daemon._maybe_reload_flow_config()
    assert daemon._brain is None
    # ...and the rest of the broken file is not adopted.
    assert daemon._config["hotkey"]["mode"] != "sideways"


def test_a_broken_file_never_turns_kai_on(isolated: Path) -> None:
    daemon = _daemon(_online())
    _write(isolated, LOCAL_FILE.format(enabled="true") + '\n[hotkey]\nmode = "sideways"\n')
    daemon._maybe_reload_flow_config()
    assert daemon._brain is None


def test_kai_off_holds_until_kais_settings_change(isolated: Path) -> None:
    _write(isolated, LOCAL_FILE.format(enabled='"auto"'))
    daemon = _daemon(_local())
    asyncio.run(daemon._kai_off_request())  # say the file couldn't be written
    assert daemon._brain is None
    _write(isolated, LOCAL_FILE.format(enabled='"auto"') + "\n[flow]\nhistory = true\n")
    daemon._maybe_reload_flow_config()
    assert daemon._brain is None  # an unrelated edit doesn't undo `kai off`
    _write(isolated, LOCAL_FILE.format(enabled="true"))
    daemon._maybe_reload_flow_config()
    assert daemon._brain is not None


def test_turning_off_hides_the_orb_and_stops_the_wake_word(isolated: Path) -> None:
    cfg = _local()
    cfg["wake"]["enabled"] = True
    daemon = _daemon(cfg)
    listener = mock.Mock()
    daemon._wake_listener = listener
    _write(isolated, LOCAL_FILE.format(enabled="false") + "\n[wake]\nenabled = true\n")

    async def scenario() -> None:
        daemon._maybe_reload_flow_config()
        for task in list(daemon._kai_tasks):
            await task

    asyncio.run(scenario())
    listener.stop.assert_called_once()
    assert daemon._wake_listener is None
    client._set_overlay_button.assert_called_with(False)


def test_turning_on_shows_the_orb_and_arms_the_wake_word(isolated: Path) -> None:
    cfg = _online()
    cfg["wake"]["enabled"] = True
    daemon = _daemon(cfg)
    _write(isolated, ONLINE_FILE.format(enabled="true") + "\n[wake]\nenabled = true\n")

    async def scenario() -> None:
        with mock.patch("voice_keyboard.wake.WakeListener") as wake:
            daemon._maybe_reload_flow_config()
            for task in list(daemon._kai_tasks):
                await task
        wake.return_value.start.assert_called_once()

    asyncio.run(scenario())
    client._set_overlay_button.assert_called_with(True)


def test_an_unrelated_edit_keeps_the_same_brain(isolated: Path) -> None:
    _write(isolated, LOCAL_FILE.format(enabled='"auto"'))
    daemon = _daemon(_local())
    daemon._maybe_reload_flow_config(force=True)
    brain = daemon._brain
    _write(isolated, LOCAL_FILE.format(enabled='"auto"') + "\n[flow]\nhistory = true\n")
    daemon._maybe_reload_flow_config()
    assert daemon._brain is brain


# ── reload (A7) ─────────────────────────────────────────────────────────


def test_stamp_is_mtime_ns_and_size(isolated: Path) -> None:
    daemon = _daemon(_online())
    assert daemon._current_config_mtime() == (0, 0)
    _write(isolated, "[flow]\n")
    stamp = daemon._current_config_mtime()
    assert stamp == (isolated.stat().st_mtime_ns, isolated.stat().st_size)


def test_poll_applies_a_hand_edit(isolated: Path) -> None:
    daemon = _daemon(_local())
    _write(isolated, LOCAL_FILE.format(enabled="false"))
    daemon._reload_kai_only()
    assert daemon._brain is None
    # The full reload still happens at the next recording.
    assert daemon._config_mtime != daemon._kai_stamp


def test_forced_reload_during_dictation_leaves_flow_alone(isolated: Path) -> None:
    daemon = _daemon(_local())
    _write(isolated, LOCAL_FILE.format(enabled="false") + "\n[flow]\nlive = false\n")
    daemon._recording = True
    asyncio.run(daemon._reload_request())
    assert daemon._brain is None
    assert daemon._config["flow"]["live"] is True


def test_forced_reload_when_idle_is_a_full_reload(isolated: Path) -> None:
    daemon = _daemon(_online())
    _write(isolated, ONLINE_FILE.format(enabled="true") + "\n[flow]\nlive = false\n")
    asyncio.run(daemon._reload_request())
    assert daemon._brain is not None
    assert daemon._config["flow"]["live"] is False


# ── IPC ─────────────────────────────────────────────────────────────────


class _Conn:
    def __init__(self, request: dict, broken: bool = False):
        self._data = [json.dumps(request).encode(), b""]
        self.sent = b""
        self.broken = broken

    def recv(self, n):
        return self._data.pop(0)

    def sendall(self, data):
        if self.broken:
            raise BrokenPipeError()
        self.sent += data

    def close(self):
        pass


def _with_loop(daemon: Daemon, fn):
    loop = asyncio.new_event_loop()
    thread = threading.Thread(target=loop.run_forever, daemon=True)
    thread.start()
    daemon._loop = loop
    try:
        return fn()
    finally:
        loop.call_soon_threadsafe(loop.stop)
        thread.join(timeout=5)
        loop.close()


def _ipc(daemon: Daemon, request: dict, broken: bool = False) -> dict:
    conn = _Conn(request, broken)
    daemon._ipc_server = mock.Mock(accept=mock.Mock(side_effect=[conn, OSError()]),
                                   required_token=None)
    _with_loop(daemon, daemon._ipc_loop)
    return json.loads(conn.sent.decode()) if conn.sent else {}


def test_ipc_converse_while_off_says_why_and_how(overlay: mock.Mock) -> None:
    daemon = _daemon(_online())
    response = _ipc(daemon, {"command": "converse"})
    assert response["status"] == "error"
    assert "Kai is off" in response["message"] and "xAI" in response["message"]
    if sys.platform != "win32":
        assert "kai on" in response["message"]


def test_ipc_converse_while_on_toggles() -> None:
    daemon = _daemon(_local())
    daemon._handle_hotkey_action = mock.AsyncMock()
    response = _ipc(daemon, {"command": "converse"})
    assert response == {"status": "ok", "message": "converse toggled"}


def test_ipc_kai_off(isolated: Path) -> None:
    daemon = _daemon(_local())
    response = _ipc(daemon, {"command": "kai_off"})
    assert response["status"] == "ok" and response["assistant"] is False
    assert daemon._brain is None


def test_ipc_reload_applies_the_file(isolated: Path) -> None:
    daemon = _daemon(_online())
    _write(isolated, ONLINE_FILE.format(enabled="true"))
    response = _ipc(daemon, {"command": "reload"})
    assert response["assistant"] is True
    assert response["assistant_setting"] == "on"
    assert response["config_path"] == str(isolated)


def test_ipc_reply_to_a_closed_socket_is_quiet(caplog) -> None:
    daemon = _daemon(_online())
    _ipc(daemon, {"command": "status"}, broken=True)
    assert "Error handling IPC command" not in caplog.text


def test_thread_safe_entry_points(isolated: Path) -> None:
    daemon = _daemon(_local())
    _write(isolated, LOCAL_FILE.format(enabled="false"))
    assert _with_loop(daemon, daemon.reload_config) is True
    assert daemon._brain is None
    _write(isolated, LOCAL_FILE.format(enabled='"auto"'))
    assert _with_loop(daemon, daemon.reload_config) is True
    assert daemon._brain is not None
    assert _with_loop(daemon, daemon.kai_off) is True
    assert daemon._brain is None


def test_startup_logs_kai_and_polls_the_file(caplog, monkeypatch: pytest.MonkeyPatch) -> None:
    import logging

    monkeypatch.setattr("voice_keyboard.daemon.create_hotkey_listener", mock.Mock())
    monkeypatch.setattr("voice_keyboard.daemon.prefetch_enabled", lambda config: False)
    daemon = _daemon(_online())

    async def scenario() -> None:
        daemon._loop = asyncio.get_running_loop()
        with caplog.at_level(logging.INFO, logger="voice_keyboard.daemon"):
            daemon._start_services()
        assert daemon._kai_poll_task is not None and not daemon._kai_poll_task.done()
        await daemon._shutdown()
        assert daemon._kai_poll_task is None

    asyncio.run(scenario())
    assert "Kai: off: it would use xAI" in caplog.text
    client._set_overlay_button.assert_called_with(False)


# ── review fixes ─────────────────────────────────────────────────────────

GPU_BOX_FILE = """\
[providers.openai]
base_url = "http://gpu-box:8000/v1"
api_key = "box-key"

[stt]
provider = "openai"

[tts]
provider = "openai"

[llm]
provider = "openai"
base_url = "http://127.0.0.1:8080/v1"
model = "qwen"

[assistant]
local_hosts = {hosts}
"""


def test_kai_voice_follows_local_hosts_added_while_running(isolated: Path) -> None:
    # [assistant] local_hosts hot-reloads, so Kai can turn on while running.
    # Its answer must then reach the speech server directly too, never
    # through a proxy: the speaking client was built at the start.
    from voice_keyboard.config import load_config
    from voice_keyboard.netpolicy import NO_PROXIES

    _write(isolated, GPU_BOX_FILE.format(hosts="[]"))
    daemon = Daemon(config=load_config(), injector=mock.Mock(), ipc_server=mock.Mock())
    assert daemon._brain is None  # gpu-box isn't known to be yours yet
    _write(isolated, GPU_BOX_FILE.format(hosts='["gpu-box"]'))
    daemon._reload_kai_only()
    assert daemon._brain is not None
    response = mock.Mock(status_code=200, headers={}, content=b"MP3")
    with mock.patch.object(daemon._tts_client.session, "post", return_value=response) as post:
        daemon._tts_client.synthesize("the answer")
    assert post.call_args.kwargs["proxies"] == NO_PROXIES
    assert post.call_args.kwargs["allow_redirects"] is False
    # ...and back: not yours any more, so not direct (and Kai is off).
    _write(isolated, GPU_BOX_FILE.format(hosts="[]"))
    daemon._maybe_reload_flow_config()
    assert daemon._brain is None
    with mock.patch.object(daemon._tts_client.session, "post", return_value=response) as post:
        daemon._tts_client.synthesize("read aloud")
    assert "proxies" not in post.call_args.kwargs


def test_kai_off_with_an_unreadable_file_stays_off_once_it_is_fixed(isolated: Path) -> None:
    # `kai off` couldn't write a broken file; the CLI says Kai stays off
    # until the daemon restarts. Fixing the typo elsewhere must not turn it
    # back on: the file still says what it said before `kai off`.
    daemon = _daemon(_local())
    _write(isolated, LOCAL_FILE.format(enabled='"auto"') + "\n[flow\n")
    asyncio.run(daemon._kai_off_request())
    assert daemon._brain is None
    _write(isolated, LOCAL_FILE.format(enabled='"auto"'))
    daemon._reload_kai_only()
    assert daemon._brain is None
    daemon._maybe_reload_flow_config()
    assert daemon._brain is None
    # A change to Kai's own settings after that is a new decision.
    _write(isolated, LOCAL_FILE.format(enabled="true"))
    daemon._reload_kai_only()
    assert daemon._brain is not None


def test_kai_on_lifts_kai_off_even_when_the_file_already_says_on(isolated: Path) -> None:
    # `kai off` couldn't write the file (read-only), so it still says on.
    # `kai on` (and the tray's Turn on Kai…) then asks for a reload: Kai is
    # on again, without waiting for a restart.
    _write(isolated, LOCAL_FILE.format(enabled='"auto"'))
    daemon = _daemon(_local())
    asyncio.run(daemon._kai_off_request())
    assert daemon._brain is None
    asyncio.run(daemon._reload_request())
    assert daemon._brain is not None
    assert daemon._status_response()["assistant"] is True


def test_off_while_the_question_is_being_finished_uploads_nothing() -> None:
    # Released, then turned off while the mic closes: a REST speech server
    # gets the whole recording at send_audio_done, so it must not be sent.
    daemon = _daemon(_online(enabled=True))
    stt = _stt()
    capture = _audio()
    capture.stop.side_effect = lambda: daemon._set_brain(None)
    brain = daemon._brain
    brain.respond_audio = mock.AsyncMock()

    async def scenario() -> None:
        with mock.patch("voice_keyboard.daemon.AudioCapture", return_value=capture), \
                mock.patch("voice_keyboard.daemon.create_stt_client", return_value=stt):
            await daemon._handle_hotkey_action("converse_start")
            await asyncio.sleep(0.01)
            await daemon._handle_hotkey_action("converse_stop")
            for task in list(daemon._kai_tasks):
                await task

    asyncio.run(scenario())
    stt.send_audio_done.assert_not_awaited()
    brain.respond_audio.assert_not_awaited()
    assert daemon._converse_task is None
    assert daemon._recording is False and daemon._converse_capture is False


@pytest.mark.parametrize("reload", ["_reload_kai_only", "_maybe_reload_flow_config"])
def test_off_in_a_file_that_doesnt_parse_still_turns_kai_off(isolated: Path, reload: str) -> None:
    # A typo elsewhere breaks the whole file, but [assistant] alone still
    # reads: an explicit `enabled = false` there turns Kai off.
    daemon = _daemon(_local())
    _write(isolated, LOCAL_FILE.format(enabled="false") + '\n[flow]\nlanguage = "en\n')
    getattr(daemon, reload)()
    assert daemon._brain is None
    assert daemon._status_response()["assistant_setting"] == "off"


def test_a_file_that_doesnt_parse_never_turns_kai_on_or_off_otherwise(isolated: Path) -> None:
    daemon = _daemon(_local())
    _write(isolated, LOCAL_FILE.format(enabled="true") + '\n[flow]\nlanguage = "en\n')
    daemon._reload_kai_only()
    assert daemon._brain is not None
    off = _daemon(_online())
    off._reload_kai_only()
    assert off._brain is None


def test_release_after_kai_turned_on_says_nothing_about_off(overlay: mock.Mock) -> None:
    daemon = _daemon(_online())
    asyncio.run(daemon._handle_hotkey_action("converse_start"))
    assert daemon._off_hold
    daemon._brain = mock.Mock()  # turned on (the poll) during the hold
    asyncio.run(daemon._handle_hotkey_action("converse_stop"))
    assert not any("is off" in detail for detail in _details(overlay))
    assert daemon._off_hold is False
