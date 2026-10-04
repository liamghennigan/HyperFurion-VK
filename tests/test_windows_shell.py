"""Windows app-layer tests — the parts provable off Windows.

The Win32 calls themselves (layered windows, the tray icon) need Windows,
but what they draw and decide does not: the overlay/orb/icon pixel math,
pill placement, how overlay and notification calls are routed (in-process
shell vs. forwarded to the daemon vs. a toast), the daemon's overlay /
quit / read-aloud commands, the copy-the-selection dance, and the app's
first-run setup mode.
"""

import asyncio
import json
import pathlib
import struct
import sys
import threading
import time
import tomllib
from unittest import mock

import numpy as np
import pytest

from voice_keyboard import client
from voice_keyboard.config import _default_config_with_paths, validate_config
from voice_keyboard.windows import render


# ------------------------------------------------------------------ render


class TestRender:
    def test_states_mirror_the_gnome_overlay(self) -> None:
        assert set(render.STATE_STYLES) == {
            "starting", "listening", "processing", "inserted", "empty", "error",
        }
        assert render.style_for("listening").accent == (0xE5, 0x48, 0x4D)
        assert render.style_for("nonsense") is render.STATE_STYLES["listening"]

    def test_bar_heights(self) -> None:
        assert render.bar_heights(None) == list(render.BAR_STATIC)
        assert render.bar_heights(0) == [6, 16, 16, 6]
        assert len({tuple(render.bar_heights(p)) for p in range(6)}) > 1

    def test_pill_is_opaque_inside_and_glows_outside(self) -> None:
        margin, w, h = 20, 300, 80
        rgb, alpha = render.pill_layers(
            w + 2 * margin, h + 2 * margin, margin=margin, width=w, height=h,
            scale=1.0, accent=(229, 72, 77), glow_alpha=0.32,
            bars=render.bar_heights(None), bars_x=18,
        )
        centre = (margin + h // 2, margin + w // 2)
        assert alpha[centre] == pytest.approx(render.PILL_ALPHA, abs=0.01)
        assert tuple(np.round(rgb[centre])) == render.INSTRUMENT_BG
        assert alpha[0, 0] < 0.02  # far corner: (almost) nothing
        below = alpha[margin + h + 4, margin + w // 2]
        assert 0.05 < below < 0.4  # the phosphor glow
        assert tuple(np.round(rgb[margin + h + 4, margin + w // 2])) == (229, 72, 77)
        # the rounded corner is antialiased, not a hard square
        corner = alpha[margin, margin]
        assert corner < 0.6

    def test_bars_are_painted_in_the_accent(self) -> None:
        margin, w, h = 10, 260, 70
        rgb, _ = render.pill_layers(
            w + 2 * margin, h + 2 * margin, margin=margin, width=w, height=h,
            scale=1.0, accent=(81, 207, 102), glow_alpha=0.28,
            bars=[22, 22, 22, 22], bars_x=18,
        )
        mid_y = margin + h // 2
        assert tuple(np.round(rgb[mid_y, margin + 19])) == (81, 207, 102)

    def test_animation_frames_reuse_the_glass(self) -> None:
        # The glow is cached; each frame only repaints the bars, and never
        # into the cached base.
        kw = dict(margin=10, width=260, height=70, scale=1.0, accent=(81, 207, 102),
                  glow_alpha=0.28, bars_x=18)
        render._pill_base.cache_clear()
        first, alpha1 = render.pill_layers(280, 90, bars=[22] * 4, **kw)
        second, alpha2 = render.pill_layers(280, 90, bars=[3] * 4, **kw)
        info = render._pill_base.cache_info()
        assert (info.hits, info.misses) == (1, 1)
        assert np.array_equal(alpha1, alpha2)
        assert not np.array_equal(first, second)  # the bars moved
        third, _ = render.pill_layers(280, 90, bars=[22] * 4, **kw)
        assert np.array_equal(first, third)  # the cached glass was not painted on

    def test_only_the_orb_disc_takes_clicks(self) -> None:
        # Review finding: the glow's faint halo was 93% of the window and ate
        # clicks meant for the app below. Now the glow is click-through and
        # the hit window holds the disc alone.
        scale, size, margin = 1.0, 46, 16
        _, glow_alpha = render.orb_layers(size, margin=margin, scale=scale,
                                          ring_rgb=render.ORB_CYAN, glow_alpha=0.26)
        hit = render.orb_hit_bgra(size)
        clickable = int((hit[..., 3] > 0).sum())
        assert clickable == pytest.approx(np.pi * (size / 2) ** 2, rel=0.05)
        assert clickable < int((glow_alpha > 0).sum()) / 3
        assert hit[..., :3].max() == 0 and hit[..., 3].max() == 1  # invisible
        assert hit[0, 0, 3] == 0 and hit[size // 2, size // 2, 3] == 1

    def test_premultiply(self) -> None:
        rgb = np.array([[[200.0, 100.0, 50.0]]])
        alpha = np.array([[0.5]])
        out = render.to_bgra_premultiplied(rgb, alpha)
        assert out[0, 0].tolist() == [25, 50, 100, 128]

    def test_icon(self) -> None:
        icon = render.icon_rgba(32)
        assert icon.shape == (32, 32, 4)
        assert icon[0, 0, 3] < 64  # rounded corner
        assert icon[16, 16, 3] == 255
        recording = render.icon_rgba(32, render.STATE_STYLES["listening"].accent)
        assert not np.array_equal(icon, recording)

    def test_ico_container(self) -> None:
        data = render.ico_bytes(sizes=(16, 32, 256))
        reserved, kind, count = struct.unpack("<HHH", data[:6])
        assert (reserved, kind, count) == (0, 1, 3)
        width, height, _, _, planes, bpp, size, offset = struct.unpack(
            "<BBBBHHII", data[6:22]
        )
        assert (width, height, planes, bpp) == (16, 16, 1, 32)
        assert data[offset : offset + 8] == b"\x89PNG\r\n\x1a\n"
        last = struct.unpack("<BBBBHHII", data[6 + 32 : 6 + 48])
        assert last[0] == 0  # 256 is stored as 0


class TestPlacePill:
    WORK = (0, 0, 1920, 1040)
    WINDOW = (100, 100, 1500, 900)

    def test_centred_above_the_caret(self) -> None:
        x, y = render.place_pill(300, 80, (800, 500), self.WINDOW, self.WORK)
        assert x == 800 - 150
        assert y + 80 < 500

    def test_below_the_caret_at_the_top_edge(self) -> None:
        x, y = render.place_pill(300, 80, (800, 60), (0, 0, 1900, 900), self.WORK)
        assert y > 60

    def test_no_caret_falls_back_to_the_window(self) -> None:
        x, y = render.place_pill(300, 80, (-1, -1), self.WINDOW, self.WORK)
        assert x == (100 + 1500) // 2 - 150
        assert 100 < y < 900

    def test_caret_far_from_the_window_is_ignored(self) -> None:
        x, _ = render.place_pill(300, 80, (1900, 1000), (0, 0, 400, 300), self.WORK)
        assert x == 200 - 150 or x == 18  # window-centred (clamped)

    def test_monitors_left_of_the_primary_have_negative_carets(self) -> None:
        work = (-1920, 0, 0, 1040)
        x, y = render.place_pill(300, 80, (-1000, 500), (-1800, 100, -200, 900), work)
        assert x == -1000 - 150 and y + 80 < 500
        assert render.anchor_known((-1000, 500)) and not render.anchor_known((-1, -1))
        assert not render.anchor_known(None)

    def test_clamped_to_the_work_area(self) -> None:
        x, y = render.place_pill(300, 80, (1915, 1035), None, self.WORK)
        assert x + 300 <= 1920 - 18 and y + 80 <= 1040 - 18
        x, y = render.place_pill(300, 80, None, None, self.WORK)
        assert 0 < x < 1920 and 0 < y < 1040


# --------------------------------------------------------- client routing


@pytest.fixture
def win32(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.setattr(client, "_local_shell", None)
    monkeypatch.setattr(client, "_in_daemon", False)
    monkeypatch.setattr(client, "_daemon_overlay_ok", False)
    monkeypatch.setattr(client, "_focused_anchor", lambda: (10, 20))
    yield


class TestOverlayRouting:
    def test_in_process_shell_draws_it(self, win32) -> None:
        shell = mock.Mock()
        client.register_local_shell(shell)
        client._show_overlay("listening", detail="hi", timeout_ms=0)
        shell.show.assert_called_once_with("listening", detail="hi", timeout_ms=0, anchor=(10, 20))
        client._set_overlay_button(False)
        shell.set_button.assert_called_once_with(False)
        client._notify("t", "b", urgency="critical")
        shell.notify.assert_called_once_with("t", "b", error=True)
        client._stop_overlay()
        shell.hide.assert_called_once()

    def test_cli_forwards_to_the_daemon_without_toasts(self, win32, monkeypatch) -> None:
        sent = []

        class FakeClient:
            def __init__(self, endpoint, timeout):
                sent.append(endpoint)

            def send_command(self, command, payload=None, timeout=None):
                sent.append((command, payload))
                return {"status": "ok"}

        toast = mock.Mock()
        monkeypatch.setattr(client, "IPCClient", FakeClient)
        monkeypatch.setattr(client, "_notify_windows_toast", toast)
        monkeypatch.setattr(client, "_overlay_endpoint", "tcp:127.0.0.1:1")
        client._show_overlay("inserted", detail="Inserted 5 characters", timeout_ms=1800)
        assert sent[1] == (
            "overlay",
            {"state": "inserted", "detail": "Inserted 5 characters",
             "timeout_ms": 1800, "x": 10, "y": 20},
        )
        client._notify("Voice Keyboard", "Inserted 5 characters")
        toast.assert_not_called()

    def test_no_daemon_toasts_outcomes_only(self, win32, monkeypatch) -> None:
        class DownClient:
            def __init__(self, *a, **k):
                pass

            def send_command(self, *a, **k):
                raise ConnectionRefusedError

        toast = mock.Mock()
        monkeypatch.setattr(client, "IPCClient", DownClient)
        monkeypatch.setattr(client, "_notify_windows_toast", toast)
        client._show_overlay("listening", detail="live caption")
        toast.assert_not_called()  # never a toast storm for live states
        client._show_overlay("error", detail="boom")
        toast.assert_called_once()

    def test_daemon_never_forwards_to_itself(self, win32, monkeypatch) -> None:
        monkeypatch.setattr(client, "_in_daemon", True)
        forward = mock.Mock()
        monkeypatch.setattr(client, "_forward_overlay", forward)
        client._show_overlay("listening")
        forward.assert_not_called()

    def test_linux_path_unchanged(self, monkeypatch) -> None:
        monkeypatch.setattr(sys, "platform", "linux")
        call = mock.Mock(return_value=True)
        monkeypatch.setattr(client, "_call_shell_overlay", call)
        client._show_overlay("listening", detail="d", anchor=(1, 2))
        call.assert_called_once_with("Show", "listening", "1", "2", "d", "0")


# ------------------------------------------------------------------ daemon


def _daemon(**config_overrides):
    from voice_keyboard.daemon import Daemon

    cfg = _default_config_with_paths()
    cfg["xai"]["api_key"] = "test-api-key"
    for section, values in config_overrides.items():
        cfg.setdefault(section, {}).update(values)
    return Daemon(
        config=cfg,
        injector=mock.Mock(),
        ipc_server=mock.Mock(required_token=None),
        tts_client=mock.Mock(),
    )


class _Conn:
    def __init__(self, request: dict):
        self._data = [json.dumps(request).encode(), b""]
        self.sent = b""

    def recv(self, n):
        return self._data.pop(0)

    def sendall(self, data):
        self.sent += data

    def close(self):
        pass


def _ipc_roundtrip(daemon, request: dict) -> dict:
    conn = _Conn(request)
    daemon._ipc_server.accept.side_effect = [conn, OSError("closed")]
    daemon._ipc_loop()
    return json.loads(conn.sent.decode())


class TestDaemonWindowsCommands:
    def test_overlay_command_draws_in_process(self, monkeypatch) -> None:
        daemon = _daemon()
        shown = mock.Mock()
        monkeypatch.setattr(client, "_show_overlay", shown)
        response = _ipc_roundtrip(daemon, {
            "command": "overlay",
            "payload": {"state": "error", "detail": "x", "timeout_ms": 3000, "x": 5, "y": 6},
        })
        assert response["status"] == "ok"
        shown.assert_called_once_with("error", detail="x", timeout_ms=3000, anchor=(5, 6))

    def test_quit_command_stops_the_daemon(self) -> None:
        daemon = _daemon()
        stop = mock.Mock()
        daemon.request_stop = stop
        assert _ipc_roundtrip(daemon, {"command": "quit"})["status"] == "ok"
        stop.assert_called_once()

    def test_request_stop_is_thread_safe(self) -> None:
        daemon = _daemon()

        async def run():
            daemon._loop = asyncio.get_running_loop()
            daemon._stop_event = asyncio.Event()
            import threading

            threading.Thread(target=daemon.request_stop).start()
            await asyncio.wait_for(daemon._stop_event.wait(), 2)

        asyncio.run(run())

    def test_hotkey_label_follows_config(self) -> None:
        assert _daemon(hotkey={"key": "control+shift+space"})._hotkey_label() == "Ctrl+Shift+Space"

    def test_no_signal_hint(self, monkeypatch) -> None:
        daemon = _daemon()
        daemon._chunks_seen = 30
        daemon._heard_signal = False
        monkeypatch.setattr(sys, "platform", "win32")
        assert "Privacy" in daemon._no_signal_hint()
        daemon._observe_audio(b"\x00\x01" * 160, 100.0)
        assert daemon._heard_signal and daemon._no_signal_hint() == ""

    def test_short_silent_session_gets_no_hint(self) -> None:
        daemon = _daemon()
        daemon._chunks_seen = 3
        assert daemon._no_signal_hint() == ""

    def test_phone_mic_silence_is_not_blamed_on_this_pc(self, monkeypatch) -> None:
        daemon = _daemon()
        daemon._chunks_seen = 30
        daemon._session_remote_audio = True
        monkeypatch.setattr(sys, "platform", "win32")
        assert daemon._no_signal_hint() == ""


class TestReadAloud:
    @pytest.fixture(autouse=True)
    def quiet_overlay(self, monkeypatch):
        self.overlays = []

        async def show(state, **kw):
            self.overlays.append((state, kw.get("detail", "")))

        self.show = show

    def _daemon(self):
        daemon = _daemon()
        daemon._show_hotkey_overlay = self.show
        return daemon

    def test_reads_the_selection(self, monkeypatch) -> None:
        daemon = self._daemon()
        daemon._tts_client.synthesize.return_value = b"mp3"
        monkeypatch.setattr(
            "voice_keyboard.daemon.clipboard.selection_text", lambda **kw: " hello "
        )

        async def run():
            await daemon._toggle_read_aloud(from_selection=True)
            await daemon._read_task

        asyncio.run(run())
        daemon._tts_client.synthesize.assert_called_once_with("hello")
        daemon._tts_client.play_audio.assert_called_once_with(b"mp3", cancel=mock.ANY)
        assert self.overlays[-1][0] == "inserted"

    def test_nothing_selected(self, monkeypatch) -> None:
        daemon = self._daemon()
        monkeypatch.setattr("voice_keyboard.daemon.clipboard.selection_text", lambda **kw: "")
        asyncio.run(daemon._toggle_read_aloud(from_selection=True))
        assert self.overlays == [("empty", "Select some text first")]
        daemon._tts_client.synthesize.assert_not_called()

    @pytest.mark.parametrize("platform, fallback", [("win32", False), ("linux", False),
                                                    ("darwin", True)])
    def test_only_macos_reads_the_clipboard_when_nothing_is_selected(
        self, monkeypatch, platform, fallback
    ) -> None:
        daemon = self._daemon()
        daemon._config["registers"] = {"map": {"tabby": "terminal"}}
        calls = []
        monkeypatch.setattr(sys, "platform", platform)
        monkeypatch.setattr(
            "voice_keyboard.daemon.clipboard.selection_text",
            lambda **kw: calls.append(kw) or "",
        )
        asyncio.run(daemon._toggle_read_aloud(from_selection=True))
        assert len(calls) == 1
        assert calls[0]["clipboard_fallback"] is fallback
        assert calls[0]["registers"] == {"map": {"tabby": "terminal"}}

    def test_the_overlay_says_why_nothing_was_read(self, monkeypatch) -> None:
        daemon = self._daemon()

        def blocked(**kw):
            kw["notes"].append("Clipboard too big to set aside — copy the text instead")
            return ""

        monkeypatch.setattr("voice_keyboard.daemon.clipboard.selection_text", blocked)
        asyncio.run(daemon._toggle_read_aloud(from_selection=True))
        assert self.overlays == [("empty", "Clipboard too big to set aside — copy the text instead")]

    def test_quit_never_waits_for_an_unneeded_download(self, monkeypatch) -> None:
        # The stop reaches playback, but a thread can't be cancelled in the
        # middle of fetching audio: asyncio.run must not wait for it.
        daemon = self._daemon()
        release = threading.Event()
        daemon._tts_client.synthesize.side_effect = lambda text: release.wait(10) and b"mp3"
        monkeypatch.setattr("voice_keyboard.daemon.clipboard.get_text", lambda **kw: "clip")
        monkeypatch.setattr("voice_keyboard.daemon.clipboard.is_sensitive", lambda: False)
        monkeypatch.setattr("voice_keyboard.client._stop_overlay", lambda: None)

        async def run():
            await daemon._toggle_read_aloud(from_selection=False)
            await asyncio.sleep(0.1)  # the download is under way
            await daemon._shutdown()

        started = time.monotonic()
        asyncio.run(run())
        elapsed = time.monotonic() - started
        release.set()
        assert elapsed < 3, f"quit waited {elapsed:.1f}s for the download"

    def test_a_copied_password_is_not_read(self, monkeypatch) -> None:
        daemon = self._daemon()
        monkeypatch.setattr("voice_keyboard.daemon.clipboard.is_sensitive", lambda: True)
        monkeypatch.setattr("voice_keyboard.daemon.clipboard.get_text", lambda: "hunter2")
        asyncio.run(daemon._toggle_read_aloud(from_selection=False))
        assert self.overlays == [("empty", "Not reading a copied password")]
        daemon._tts_client.synthesize.assert_not_called()

    def test_second_press_stops(self, monkeypatch) -> None:
        daemon = self._daemon()
        monkeypatch.setattr("voice_keyboard.daemon.clipboard.get_text", lambda **kw: "clip")
        monkeypatch.setattr("voice_keyboard.daemon.clipboard.is_sensitive", lambda: False)

        async def run():
            started = asyncio.Event()

            def slow_synth(text):
                started.set()
                return b"mp3"

            daemon._tts_client.synthesize.side_effect = slow_synth

            async def never_finishes(func, *args, **kwargs):
                if func is daemon._tts_client.play_audio:
                    await asyncio.sleep(30)
                return func(*args, **kwargs)

            monkeypatch.setattr("voice_keyboard.daemon._in_daemon_thread", never_finishes)
            await daemon._toggle_read_aloud(from_selection=False)
            await asyncio.wait_for(started.wait(), 2)
            await asyncio.sleep(0)
            await daemon._toggle_read_aloud(from_selection=False)
            assert daemon._read_task is None

        asyncio.run(run())
        daemon._tts_client.stop_playback.assert_called_once()
        assert ("empty", "Stopped reading") in self.overlays

    def test_busy_while_dictating(self) -> None:
        daemon = self._daemon()
        daemon._recording = True
        asyncio.run(daemon._toggle_read_aloud(from_selection=True))
        assert "busy" in self.overlays[0][1]

    def test_a_stop_while_the_audio_is_still_decoding_wins(self, monkeypatch) -> None:
        # The second press lands after synthesis but before playback has
        # started: nothing may play afterwards.
        from voice_keyboard.tts import TTSClient

        played = []
        cancel = threading.Event()

        def read(path):
            cancel.set()  # the stop arrives while decoding
            return np.zeros(10), 24000

        monkeypatch.setitem(sys.modules, "soundfile", mock.Mock(read=read))
        monkeypatch.setitem(sys.modules, "sounddevice", mock.Mock(
            play=lambda *a: played.append(a), wait=lambda: None, stop=lambda: None
        ))
        client = TTSClient(api_key="k")
        client.play_audio(b"mp3", cancel=cancel)
        assert played == []
        cancel.clear()
        client.play_audio(b"mp3", cancel=threading.Event())
        assert len(played) == 1

    def test_a_stop_while_the_device_opens_stops_that_stream(self, monkeypatch) -> None:
        # Kai's barge-in lands while sd.play() is still opening the device:
        # it must stop the stream that is starting, not the previous one.
        from voice_keyboard.tts import TTSClient

        events = []
        opening = threading.Event()

        def play(*args):
            opening.set()
            time.sleep(0.2)  # a Bluetooth headset waking up
            events.append("play")

        monkeypatch.setitem(sys.modules, "sounddevice", mock.Mock(
            play=play, wait=lambda: events.append("wait"), stop=lambda: events.append("stop")
        ))
        client = TTSClient(api_key="k")
        player = threading.Thread(target=client.play_pcm, args=(b"\x00\x00" * 8,))
        player.start()
        assert opening.wait(2)
        client.stop_playback()
        player.join(2)
        assert events.index("play") < events.index("stop")

    def test_kai_barge_in_reaches_an_answer_not_yet_playing(self) -> None:
        daemon = self._daemon()
        flag = threading.Event()
        daemon._converse_cancel_flag = flag
        asyncio.run(daemon._converse_cancel())
        assert flag.is_set()
        daemon._tts_client.stop_playback.assert_called_once()

    def test_shutdown_stops_reading_and_takes_the_pill_down(self, monkeypatch) -> None:
        daemon = self._daemon()
        hidden = []
        monkeypatch.setattr("voice_keyboard.client._stop_overlay", lambda: hidden.append(1))

        async def run():
            daemon._read_cancel = threading.Event()
            daemon._read_task = asyncio.create_task(asyncio.sleep(30))
            await daemon._shutdown()
            return daemon._read_cancel

        cancel = asyncio.run(run())
        assert cancel.is_set()
        daemon._tts_client.stop_playback.assert_called()
        assert hidden == [1]


class TestDaemonLifecycle:
    def test_a_refused_keyboard_hook_is_reported(self) -> None:
        daemon = _daemon()
        shown = []

        async def show(state, **kw):
            shown.append((state, kw.get("detail", "")))

        daemon._show_hotkey_overlay = show
        daemon._hotkey_listener = mock.Mock(hook_error=5)

        async def run():
            daemon._check_hotkey_hooks()
            await asyncio.sleep(0)

        asyncio.run(run())
        assert shown and shown[0][0] == "error" and "error 5" in shown[0][1]
        assert "hotkeys won't work" in daemon._last_error

    def test_shutdown_releases_the_ipc_server_even_when_stopping_fails(self) -> None:
        daemon = _daemon()
        daemon._recording = True

        async def failing_stop():
            raise RuntimeError("stt stream died")

        daemon._stop_recording = failing_stop
        asyncio.run(daemon._shutdown())
        daemon._injector.stop.assert_called_once()
        daemon._ipc_server.stop.assert_called_once()

    def test_a_stop_requested_before_run_is_not_lost(self) -> None:
        daemon = _daemon()
        daemon.request_stop()
        asyncio.run(asyncio.wait_for(daemon.run(), 5))
        daemon._ipc_server.start.assert_not_called()


# ------------------------------------------------------- selection + config


class FakeClip:
    APP = 42  # the focused app's process

    def __init__(self, text_after_copy="selected words", copies=True, complete=True,
                 sensitive_after_copy=False, renders_on_snapshot=False):
        from voice_keyboard.windows.clipboard import Snapshot

        self.seq = 1
        self.text = "previous"
        self.sensitive = False
        self.owner = 7
        self._after = text_after_copy
        self._copies = copies
        self._sensitive_after = sensitive_after_copy
        self._renders = renders_on_snapshot
        self._snapshot = Snapshot([(13, "previous".encode("utf-16-le") + b"\x00\x00")],
                                  complete=complete)
        self.restored = None
        self.restores = 0

    def sequence_number(self):
        return self.seq

    def snapshot(self):
        if self._renders:
            self.seq += 1  # delayed rendering counts as a clipboard change
        return self._snapshot

    def owner_pid(self):
        return self.owner

    def get_text(self, *, unless_sensitive=False):
        return None if unless_sensitive and self.sensitive else self.text

    def restore(self, saved):
        self.restored = saved
        self.restores += 1
        self.text = "previous"
        self.sensitive = False
        self.seq += 1
        return True

    def copy(self, owner=APP):
        if self._copies:
            self.seq += 1
            self.text = self._after
            self.owner = owner
            self.sensitive = self._sensitive_after


def _copy(clip, **kw):
    from voice_keyboard.windows.selection import copy_selection

    injector = kw.pop("injector", None) or mock.Mock()
    if not injector.press_combo.side_effect:
        injector.press_combo.side_effect = lambda keys: clip.copy()
    kw.setdefault("should_skip", lambda: "")
    kw.setdefault("late_copy_s", 0)
    kw.setdefault("foreground_pids", lambda: frozenset({FakeClip.APP}))
    kw.setdefault("user_activity", lambda: 0.0)  # nobody types or clicks
    return copy_selection(injector=injector, clip=clip, **kw), injector


def _settle(clip, restores, seconds=3.0):
    deadline = time.monotonic() + seconds
    while clip.restores < restores and time.monotonic() < deadline:
        time.sleep(0.02)


class TestCopySelection:
    def test_copies_with_ctrl_insert_and_restores(self) -> None:
        clip = FakeClip()
        text, injector = _copy(clip)
        assert text == "selected words"
        # Ctrl+Insert: copy everywhere, and never "interrupt" like Ctrl+C.
        injector.press_combo.assert_called_once_with(["ctrl", "insert"])
        assert clip.restored is not None and clip.text == "previous"

    def test_nothing_selected(self) -> None:
        clip = FakeClip(copies=False)
        text, _ = _copy(clip, timeout=0.05)
        assert text is None
        assert clip.restored is None

    def test_skipped_windows_get_no_key_press(self) -> None:
        from voice_keyboard.windows.selection import NOTE_SKIPPED

        clip = FakeClip()
        notes = []
        text, injector = _copy(clip, should_skip=lambda: NOTE_SKIPPED, notes=notes)
        assert text is None and notes == [NOTE_SKIPPED]
        injector.press_combo.assert_not_called()

    def test_a_failing_focus_probe_means_no_key_press(self) -> None:
        def broken():
            raise OSError("probe failed")

        clip = FakeClip()
        text, injector = _copy(clip, should_skip=broken)
        assert text is None
        injector.press_combo.assert_not_called()

    def test_a_clipboard_that_cant_be_saved_is_left_alone(self) -> None:
        # e.g. a screenshot bigger than the snapshot limit: copying would
        # destroy it, so nothing is pressed at all — and the user is told.
        from voice_keyboard.windows.selection import NOTE_UNSAVABLE

        clip = FakeClip(complete=False)
        notes = []
        text, injector = _copy(clip, notes=notes)
        assert text is None and notes == [NOTE_UNSAVABLE]
        injector.press_combo.assert_not_called()
        assert clip.text == "previous"

    def test_a_busy_clipboard_is_left_alone(self) -> None:
        clip = FakeClip()
        clip.snapshot = lambda: None
        text, injector = _copy(clip)
        assert text is None
        injector.press_combo.assert_not_called()

    def test_delayed_rendering_during_the_snapshot_is_not_the_copy(self) -> None:
        clip = FakeClip(copies=False, renders_on_snapshot=True)
        text, _ = _copy(clip, timeout=0.05)
        assert text is None  # not "previous" read back as the selection

    def test_someone_elses_change_is_neither_read_nor_undone(self) -> None:
        # A cloud-clipboard sync lands right after the copy key: it is not
        # the selection, and restoring would throw it away.
        from voice_keyboard.windows.selection import NOTE_FOREIGN

        clip = FakeClip()
        injector = mock.Mock()
        injector.press_combo.side_effect = lambda keys: clip.copy(owner=99)
        notes = []
        text, _ = _copy(clip, injector=injector, notes=notes)
        assert text is None and notes == [NOTE_FOREIGN]
        assert clip.restores == 0 and clip.text == "selected words"

    def test_a_password_managers_secret_is_never_returned(self) -> None:
        clip = FakeClip(sensitive_after_copy=True)
        text, _ = _copy(clip)
        assert text is None
        assert clip.text == "previous"

    def test_a_late_copy_still_gets_the_clipboard_restored(self) -> None:
        clip = FakeClip()
        injector = mock.Mock()
        injector.press_combo.side_effect = lambda keys: threading.Timer(0.15, clip.copy).start()
        text, _ = _copy(clip, injector=injector, timeout=0.05, late_copy_s=2.0)
        assert text is None
        _settle(clip, 1)
        assert clip.restores == 1 and clip.text == "previous"

    def test_the_users_own_copy_is_never_undone(self) -> None:
        # Nothing was selected; seeing "Select some text first" the user
        # selects and copies something themselves within the watch window.
        clip = FakeClip(copies=False)
        last_action = [0.0]
        _copy(clip, timeout=0.05, late_copy_s=1.0, user_activity=lambda: last_action[0])
        time.sleep(0.1)
        last_action[0] = time.monotonic()  # their click, then Ctrl+C
        clip._copies = True
        clip.copy()  # by the same app
        time.sleep(0.3)
        assert clip.restores == 0 and clip.text == "selected words"

    def test_moving_the_mouse_doesnt_stop_a_late_restore(self) -> None:
        # Only key presses and clicks count as the user acting: the late
        # copy is still put back while the pointer merely moves.
        clip = FakeClip()
        injector = mock.Mock()
        injector.press_combo.side_effect = lambda keys: threading.Timer(0.15, clip.copy).start()
        _copy(clip, injector=injector, timeout=0.05, late_copy_s=2.0,
              user_activity=lambda: 0.0)  # (mouse moves never register)
        _settle(clip, 1)
        assert clip.restores == 1

    def test_an_app_without_a_clipboard_window_still_gets_restored(self) -> None:
        # gVim and friends copy with no owner window: unknown is not foreign.
        clip = FakeClip()
        injector = mock.Mock()
        injector.press_combo.side_effect = lambda keys: threading.Timer(
            0.15, lambda: clip.copy(owner=None)).start()
        _copy(clip, injector=injector, timeout=0.05, late_copy_s=2.0)
        _settle(clip, 1)
        assert clip.restores == 1

    def test_a_late_change_by_another_app_is_not_undone(self) -> None:
        clip = FakeClip(copies=False)
        _copy(clip, timeout=0.05, late_copy_s=1.0)
        clip._copies = True
        clip.copy(owner=99)  # e.g. a password manager or RDP clipboard sync
        time.sleep(0.3)
        assert clip.restores == 0

    def test_the_next_copy_cancels_a_pending_late_restore(self) -> None:
        clip = FakeClip(copies=False)
        _copy(clip, timeout=0.05, late_copy_s=5.0)
        clip._copies = True
        text, _ = _copy(clip)
        assert text == "selected words"
        time.sleep(0.2)
        assert clip.restores == 1  # only the second copy's own restore

    def test_a_skipped_press_keeps_a_pending_late_restore(self) -> None:
        clip = FakeClip()
        slow = mock.Mock()
        slow.press_combo.side_effect = lambda keys: threading.Timer(0.3, clip.copy).start()
        _copy(clip, injector=slow, timeout=0.05, late_copy_s=3.0)
        # A second press in a terminal touches nothing...
        _copy(FakeClip(), should_skip=lambda: "terminal")
        # ...so the slow app's late copy is still put back.
        _settle(clip, 1)
        assert clip.restores == 1 and clip.text == "previous"

    def test_selection_text_falls_back_to_the_clipboard(self, monkeypatch) -> None:
        from voice_keyboard import clipboard
        from voice_keyboard.windows import selection

        monkeypatch.setattr(sys, "platform", "win32")
        monkeypatch.setattr(selection, "copy_selection", lambda **kw: None)
        reads = []

        def get_text(**kw):
            reads.append(kw)
            return "on the clipboard"

        monkeypatch.setattr(clipboard, "get_text", get_text)
        assert clipboard.selection_text() == "on the clipboard"
        assert reads == [{"unless_sensitive": True}]  # a copied password never stands in
        assert clipboard.selection_text(clipboard_fallback=False) == ""
        seen = {}

        def copied(**kw):
            seen.update(kw)
            return "highlighted"

        monkeypatch.setattr(selection, "copy_selection", copied)
        notes = []
        assert clipboard.selection_text(registers={"map": {"x": "terminal"}}, notes=notes) == "highlighted"
        assert seen == {"registers": {"map": {"x": "terminal"}}, "notes": notes}


class TestProcessFamily:
    def test_helpers_and_sandbox_brokers_count_as_the_app(self) -> None:
        # A WebView2 helper the app started copies on its behalf; Acrobat's
        # Protected Mode runs the UI under a broker of the same program.
        from voice_keyboard.windows.selection import _process_family

        table = {
            1: (0, "explorer.exe"),
            10: (1, "acrord32.exe"),     # broker
            11: (10, "acrord32.exe"),    # sandboxed UI (foreground)
            20: (1, "notes.exe"),        # a WinUI app
            21: (20, "msedgewebview2.exe"),
            22: (21, "msedgewebview2.exe"),
            30: (1, "keepass.exe"),
        }
        assert _process_family(11, table) == {10, 11}
        assert _process_family(20, table) == {20, 21, 22}
        assert 1 not in _process_family(20, table)  # never the shell that started it
        assert 30 not in _process_family(20, table) | _process_family(11, table)


class TestFocusSafety:
    @pytest.fixture
    def focus(self, monkeypatch):
        from voice_keyboard import focusprobe

        box = {"focus": None}
        monkeypatch.setattr(focusprobe, "probe_focus", lambda: box["focus"])

        def set_focus(app, role="text"):
            box["focus"] = focusprobe.FocusInfo(app=app, role=role) if app is not None else None

        return set_focus

    def _unsafe(self, registers=None):
        from voice_keyboard.windows.selection import _focus_is_unsafe

        return bool(_focus_is_unsafe(registers))

    def test_unknown_focus_is_unsafe(self, focus) -> None:
        focus(None)
        assert self._unsafe()
        focus("")
        assert self._unsafe()

    def test_terminals_and_password_fields(self, focus) -> None:
        focus("WindowsTerminal.exe")
        assert self._unsafe()
        focus("notepad.exe", role="password text")
        assert self._unsafe()
        focus("notepad.exe")
        assert not self._unsafe()

    def test_the_registers_map_is_honored(self, focus) -> None:
        focus("tabby.exe")
        assert not self._unsafe()
        assert self._unsafe({"map": {"tabby": "terminal"}})
        assert self._unsafe({"map": {"tabby.exe": "shell"}})
        focus("cmd.exe")
        assert not self._unsafe({"map": {"cmd": "prose"}})
        focus("notepad.exe")
        assert self._unsafe({"default": "terminal"})


class TestWindowsConfigDefaults:
    def test_read_aloud_hotkey_on_windows_only(self, monkeypatch) -> None:
        monkeypatch.setattr(sys, "platform", "linux")
        assert _default_config_with_paths()["tts"]["hotkey"] == ""
        monkeypatch.setattr(sys, "platform", "win32")
        cfg = _default_config_with_paths()
        assert cfg["tts"]["hotkey"] == "control+alt+r"
        assert cfg["daemon"]["socket_path"] == "tcp:127.0.0.1:0"  # a free port per daemon
        cfg["xai"]["api_key"] = "k"
        validate_config(cfg)  # the whole Windows default set validates

    def test_tts_hotkey_validation(self, monkeypatch) -> None:
        monkeypatch.setattr(sys, "platform", "win32")
        cfg = _default_config_with_paths()
        cfg["xai"]["api_key"] = "k"
        cfg["tts"]["hotkey"] = "control+alt+nope"
        with pytest.raises(RuntimeError, match="tts.hotkey is invalid"):
            validate_config(cfg)
        cfg["tts"]["hotkey"] = "control+alt+v"
        with pytest.raises(RuntimeError, match="must differ"):
            validate_config(cfg)

    def test_assistant_hotkey_checked_with_windows_table(self, monkeypatch) -> None:
        monkeypatch.setattr(sys, "platform", "win32")
        cfg = _default_config_with_paths()
        cfg["xai"]["api_key"] = "k"
        cfg["assistant"]["hotkey"] = "rightctrl"
        validate_config(cfg)
        cfg["assistant"]["hotkey"] = "rightctrll"
        with pytest.raises(RuntimeError, match="assistant.hotkey is invalid"):
            validate_config(cfg)

    def test_clashes_are_found_however_they_are_spelled(self, monkeypatch) -> None:
        monkeypatch.setattr(sys, "platform", "win32")
        cfg = _default_config_with_paths()
        cfg["xai"]["api_key"] = "k"
        cfg["tts"]["hotkey"] = "Alt + Ctrl + V"  # the dictation chord, reordered
        with pytest.raises(RuntimeError, match="must differ from the dictation"):
            validate_config(cfg)
        cfg["tts"]["hotkey"] = "rightctrl"  # Kai's default summon key
        with pytest.raises(RuntimeError, match="must differ from assistant.hotkey"):
            validate_config(cfg)
        cfg["tts"]["hotkey"] = "control+alt+r"
        cfg["assistant"]["hotkey"] = "ctrl+alt+v"
        with pytest.raises(RuntimeError, match="assistant.hotkey must differ"):
            validate_config(cfg)  # bound even while the assistant is off

    def test_a_default_read_hotkey_never_blocks_startup(self, monkeypatch, tmp_path) -> None:
        from voice_keyboard import config

        monkeypatch.setattr(sys, "platform", "win32")
        monkeypatch.setattr(config, "_config_dir", lambda: tmp_path)
        (tmp_path / "config.toml").write_text(
            '[xai]\napi_key = "k"\n[hotkey]\nkey = "ctrl+alt+r"\n', encoding="utf-8"
        )
        cfg = config.load_config()
        assert cfg["tts"]["hotkey"] == ""  # their Ctrl+Alt+R wins; read-aloud has none
        validate_config(cfg)
        # Written by the user, the same clash is an error to fix.
        (tmp_path / "config.toml").write_text(
            '[xai]\napi_key = "k"\n[hotkey]\nkey = "ctrl+alt+r"\n'
            '[tts]\nhotkey = "control+alt+r"\n', encoding="utf-8"
        )
        with pytest.raises(RuntimeError, match="must differ"):
            validate_config(config.load_config())

    @pytest.mark.parametrize("encoding", ["utf-8-sig", "utf-16", "cp1252"])
    def test_notepad_encodings_load(self, monkeypatch, tmp_path, encoding) -> None:
        import locale

        from voice_keyboard import config

        monkeypatch.setattr(config, "_config_dir", lambda: tmp_path)
        # The ANSI code page even in UTF-8 mode (getpreferredencoding would
        # say UTF-8 there and turn every accent into U+FFFD).
        monkeypatch.setattr(locale, "getencoding", lambda: "cp1252")
        (tmp_path / "config.toml").write_bytes(
            '[xai]\r\napi_key = "k"\r\n[assistant]\r\nname = "José"\r\n'.encode(encoding)
        )
        cfg = config.load_config()
        assert cfg["assistant"]["name"] == "José"

    def test_signing_in_keeps_a_notepad_file_valid(self, monkeypatch, tmp_path) -> None:
        # CRLF in, written back in text mode: "\r\n" must not become
        # "\r\r\n" (which no TOML parser accepts).
        import tomllib

        from voice_keyboard import client, config

        monkeypatch.setattr(config, "_config_dir", lambda: tmp_path)
        (tmp_path / "config.toml").write_bytes(b'[audio]\r\ndevice_name = ""\r\n')
        written = {}
        real_write = pathlib.Path.write_text

        def windows_write(self, text, encoding=None, errors=None, newline=None):
            written["text"] = text.replace("\n", "\r\n")  # what text mode does there
            return real_write(self, written["text"], encoding=encoding)

        monkeypatch.setattr(pathlib.Path, "write_text", windows_write)
        client._write_hosted_login("hfk-real")
        assert tomllib.loads(written["text"].replace("\r\n", "\n"))  # still TOML
        assert "\r\r" not in written["text"]

    def test_defaults_step_aside_for_the_users_own_bindings(self, monkeypatch, tmp_path) -> None:
        from voice_keyboard import config

        monkeypatch.setattr(config, "_config_dir", lambda: tmp_path)
        # Their read-aloud key is Right Ctrl, Kai's default: Kai's goes unbound.
        (tmp_path / "config.toml").write_text(
            '[xai]\napi_key = "k"\n[tts]\nhotkey = "rightctrl"\n', encoding="utf-8"
        )
        cfg = config.load_config()
        assert cfg["tts"]["hotkey"] == "rightctrl" and cfg["assistant"]["hotkey"] == ""
        validate_config(cfg)
        # Both written by the user: that is theirs to fix.
        (tmp_path / "config.toml").write_text(
            '[xai]\napi_key = "k"\n[tts]\nhotkey = "rightctrl"\n'
            '[assistant]\nhotkey = "rightctrl"\n', encoding="utf-8"
        )
        with pytest.raises(RuntimeError, match="must differ"):
            validate_config(config.load_config())

    @pytest.mark.parametrize("section", ["daemon", "xai", "providers"])
    def test_any_section_written_as_a_value_is_reported(self, monkeypatch, tmp_path, section) -> None:
        from voice_keyboard import config

        monkeypatch.setattr(config, "_config_dir", lambda: tmp_path)
        (tmp_path / "config.toml").write_text(f'{section} = "oops"\n', encoding="utf-8")
        with pytest.raises(RuntimeError, match=f"{section} must be a \\[{section}\\] section"):
            validate_config(config.load_config())

    def test_a_section_written_as_a_value_is_named(self, monkeypatch) -> None:
        from voice_keyboard.windows.app import hotkey_labels

        cfg = _default_config_with_paths()
        cfg["xai"]["api_key"] = "k"
        cfg["hotkey"] = "ctrl+alt+v"
        with pytest.raises(RuntimeError, match=r"hotkey must be a \[hotkey\] section"):
            validate_config(cfg)
        # The tray still gets labels (it is built before validation).
        assert hotkey_labels(cfg)["dictation_hotkey"] == "Ctrl+Alt+V"
        assert hotkey_labels({"tts": 3, "assistant": []})["assistant_hotkey"] == "Right Ctrl"
        # Kai without a key (theirs, or a default that stepped aside): the
        # tray and welcome never advertise one.
        assert hotkey_labels({"assistant": {"hotkey": ""}})["assistant_hotkey"] == ""


class TestPrettyBinding:
    def test_labels(self, monkeypatch) -> None:
        from voice_keyboard.hotkey import pretty_binding

        assert pretty_binding("control+alt+v") == "Ctrl+Alt+V"
        assert pretty_binding("rightctrl") == "Right Ctrl"
        monkeypatch.setattr(sys, "platform", "win32")
        assert pretty_binding("super+shift+space") == "Win+Shift+Space"


# --------------------------------------------------------------------- app


class TestAppSetupMode:
    @pytest.fixture
    def app(self, monkeypatch, tmp_path):
        from voice_keyboard.windows import app as app_mod

        monkeypatch.setattr(app_mod, "_config_path", lambda: tmp_path / "config.toml")
        monkeypatch.setattr("voice_keyboard.config._config_dir", lambda: tmp_path)
        return app_mod, app_mod.WindowsApp(), tmp_path

    def test_no_config_is_setup_mode(self, app) -> None:
        _, instance, _ = app
        config, reason = instance._load()
        assert config is None and "sign in" in reason

    def test_starter_config_asks_for_a_key(self, app) -> None:
        app_mod, instance, tmp_path = app
        tomllib.loads(app_mod.STARTER_CONFIG)  # valid TOML
        (tmp_path / "config.toml").write_text(app_mod.STARTER_CONFIG)
        _, reason = instance._load()
        assert reason == "Add your speech provider API key to get started"

    def test_valid_config_starts(self, app) -> None:
        app_mod, instance, tmp_path = app
        (tmp_path / "config.toml").write_text(
            app_mod.STARTER_CONFIG.replace("xai-your-api-key-here", "xai-real")
        )
        config, reason = instance._load()
        assert reason == "" and config["providers"]["xai"]["api_key"] == "xai-real"

    def test_broken_toml_is_reported(self, app) -> None:
        _, instance, tmp_path = app
        (tmp_path / "config.toml").write_text("[stt\n")
        config, reason = instance._load()
        assert config is None and reason.startswith("Settings file has an error")

    def test_status_and_actions_route_to_the_daemon(self, app) -> None:
        _, instance, _ = app
        assert instance._status() == {}
        daemon = mock.Mock(recording=True, conversing=False, assistant_enabled=True)
        instance._daemon = daemon
        assert instance._status() == {"recording": True, "conversing": False, "assistant": True}
        instance._action("toggle")()
        daemon.schedule_action.assert_called_once_with("toggle")
        instance._request_quit()
        assert instance._quit.is_set()
        daemon.request_stop.assert_called_once()

    def test_autostart_command_uses_pythonw(self, app, monkeypatch, tmp_path) -> None:
        app_mod, _, _ = app
        exe = tmp_path / "python.exe"
        (tmp_path / "pythonw.exe").write_text("")
        monkeypatch.setattr(sys, "executable", str(exe))
        assert app_mod.autostart_command() == f'"{tmp_path / "pythonw.exe"}" -m voice_keyboard.windows'

    @pytest.mark.skipif(sys.platform == "win32", reason="on Windows main() runs the app")
    def test_main_refuses_off_windows(self, app) -> None:
        app_mod, _, _ = app
        assert app_mod.main([]) == 2


class TestInstallerConfig:
    """The installer writes the first config through write_config_from_env
    (keys travel in environment variables, never on a command line)."""

    @pytest.fixture
    def env(self, monkeypatch, tmp_path):
        from voice_keyboard.windows import app as app_mod

        monkeypatch.setattr(app_mod, "_config_path", lambda: tmp_path / "config.toml")
        for name in ("HFVK_STT", "HFVK_TTS", "HFVK_STT_KEY", "HFVK_TTS_KEY", "HFVK_BASE_URL"):
            monkeypatch.delenv(name, raising=False)
        return app_mod, monkeypatch, tmp_path / "config.toml"

    def _validated(self, path):
        cfg = _default_config_with_paths()
        from voice_keyboard.config import _deep_merge

        cfg = _deep_merge(cfg, tomllib.loads(path.read_text()))
        validate_config(cfg)
        return cfg

    def test_one_provider_for_both(self, env) -> None:
        app_mod, mp, path = env
        mp.setenv("HFVK_STT", "xai")
        mp.setenv("HFVK_STT_KEY", "xai-real")
        assert app_mod.write_config_from_env() == 0
        cfg = self._validated(path)
        assert cfg["stt"]["provider"] == cfg["tts"]["provider"] == "xai"
        assert cfg["providers"]["xai"]["api_key"] == "xai-real"

    def test_separate_speech_and_voice_providers(self, env) -> None:
        app_mod, mp, path = env
        for name, value in {"HFVK_STT": "groq", "HFVK_STT_KEY": "gsk-1",
                            "HFVK_TTS": "elevenlabs", "HFVK_TTS_KEY": "el-2"}.items():
            mp.setenv(name, value)
        app_mod.write_config_from_env()
        cfg = self._validated(path)
        assert cfg["providers"]["groq"]["api_key"] == "gsk-1"
        assert cfg["providers"]["elevenlabs"]["api_key"] == "el-2"

    def test_local_server_needs_no_key(self, env) -> None:
        app_mod, mp, path = env
        mp.setenv("HFVK_STT", "openai")
        mp.setenv("HFVK_BASE_URL", "http://127.0.0.1:8000/v1")
        app_mod.write_config_from_env()
        cfg = self._validated(path)
        assert cfg["providers"]["openai"]["base_url"] == "http://127.0.0.1:8000/v1"

    def test_never_overwrites(self, env) -> None:
        app_mod, mp, path = env
        path.write_text("# mine\n")
        mp.setenv("HFVK_STT", "xai")
        assert app_mod.write_config_from_env() == 0
        assert path.read_text() == "# mine\n"


class TestHotkeyLabels:
    def test_labels_follow_the_config(self) -> None:
        from voice_keyboard.windows.app import hotkey_labels

        cfg = _default_config_with_paths()
        cfg["hotkey"]["key"] = "control+shift+d"
        cfg["assistant"]["hotkey"] = "rightalt"
        cfg["tts"]["hotkey"] = "control+alt+r"
        assert hotkey_labels(cfg) == {
            "dictation_hotkey": "Ctrl+Shift+D",
            "assistant_hotkey": "Right Alt",
            "read_hotkey": "Ctrl+Alt+R",
        }
        cfg["tts"]["hotkey"] = ""
        assert hotkey_labels(cfg)["read_hotkey"] == ""

    def test_set_labels_is_queued_for_the_ui_thread(self) -> None:
        from voice_keyboard.windows.shell import ShellCallbacks, WinShell

        shell = WinShell(ShellCallbacks())
        shell.set_labels(dictation="Ctrl+Shift+D", assistant="Right Alt", read="")
        assert shell._queue[-1] == ("labels", "Ctrl+Shift+D", "Right Alt", "")
        shell._refresh_tray = lambda: None  # no tray off Windows
        shell._handle(shell._queue.pop())
        assert shell._dictation_hotkey == "Ctrl+Shift+D"
        assert shell._tip_text().endswith("Ctrl+Shift+D to dictate")
