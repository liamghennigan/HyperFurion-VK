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
import struct
import sys
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
        monkeypatch.setattr("voice_keyboard.daemon.clipboard.selection_text", lambda: " hello ")

        async def run():
            await daemon._toggle_read_aloud(from_selection=True)
            await daemon._read_task

        asyncio.run(run())
        daemon._tts_client.synthesize.assert_called_once_with("hello")
        daemon._tts_client.play_audio.assert_called_once_with(b"mp3")
        assert self.overlays[-1][0] == "inserted"

    def test_nothing_selected(self, monkeypatch) -> None:
        daemon = self._daemon()
        monkeypatch.setattr("voice_keyboard.daemon.clipboard.selection_text", lambda: "")
        asyncio.run(daemon._toggle_read_aloud(from_selection=True))
        assert self.overlays == [("empty", "Select some text first")]
        daemon._tts_client.synthesize.assert_not_called()

    def test_second_press_stops(self, monkeypatch) -> None:
        daemon = self._daemon()
        monkeypatch.setattr("voice_keyboard.daemon.clipboard.get_text", lambda: "clip")

        async def run():
            started = asyncio.Event()

            def slow_synth(text):
                started.set()
                return b"mp3"

            daemon._tts_client.synthesize.side_effect = slow_synth

            async def never_finishes(func, *args):
                if func is daemon._tts_client.play_audio:
                    await asyncio.sleep(30)
                return func(*args)

            monkeypatch.setattr(asyncio, "to_thread", never_finishes)
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


# ------------------------------------------------------- selection + config


class FakeClip:
    def __init__(self, text_after_copy="selected words", copies=True):
        self.seq = 1
        self.text = "previous"
        self._after = text_after_copy
        self._copies = copies
        self.restored = None

    def sequence_number(self):
        return self.seq

    def snapshot(self):
        return [(13, "previous".encode("utf-16-le") + b"\x00\x00")]

    def get_text(self):
        return self.text

    def restore(self, saved):
        self.restored = saved
        self.text = "previous"

    def copy(self):
        if self._copies:
            self.seq += 1
            self.text = self._after


class TestCopySelection:
    def test_copies_and_restores(self) -> None:
        from voice_keyboard.windows.selection import copy_selection

        clip = FakeClip()
        injector = mock.Mock()
        injector.press_combo.side_effect = lambda keys: clip.copy()
        text = copy_selection(injector=injector, clip=clip, is_terminal=lambda: False)
        assert text == "selected words"
        injector.press_combo.assert_called_once_with(["ctrl", "c"])
        assert clip.restored is not None and clip.text == "previous"

    def test_nothing_selected(self) -> None:
        from voice_keyboard.windows.selection import copy_selection

        clip = FakeClip(copies=False)
        injector = mock.Mock()
        assert copy_selection(injector=injector, clip=clip, is_terminal=lambda: False,
                              timeout=0.05) is None
        assert clip.restored is None

    def test_terminals_are_never_sent_ctrl_c(self) -> None:
        from voice_keyboard.windows.selection import copy_selection

        injector = mock.Mock()
        assert copy_selection(injector=injector, clip=FakeClip(), is_terminal=lambda: True) is None
        injector.press_combo.assert_not_called()

    def test_selection_text_falls_back_to_the_clipboard(self, monkeypatch) -> None:
        from voice_keyboard import clipboard
        from voice_keyboard.windows import selection

        monkeypatch.setattr(sys, "platform", "win32")
        monkeypatch.setattr(selection, "copy_selection", lambda: None)
        monkeypatch.setattr(clipboard, "get_text", lambda: "on the clipboard")
        assert clipboard.selection_text() == "on the clipboard"
        assert clipboard.selection_text(clipboard_fallback=False) == ""
        monkeypatch.setattr(selection, "copy_selection", lambda: "highlighted")
        assert clipboard.selection_text() == "highlighted"


class TestWindowsConfigDefaults:
    def test_read_aloud_hotkey_on_windows_only(self, monkeypatch) -> None:
        monkeypatch.setattr(sys, "platform", "linux")
        assert _default_config_with_paths()["tts"]["hotkey"] == ""
        monkeypatch.setattr(sys, "platform", "win32")
        cfg = _default_config_with_paths()
        assert cfg["tts"]["hotkey"] == "control+alt+r"
        assert cfg["daemon"]["socket_path"] == "tcp:127.0.0.1:48765"
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
