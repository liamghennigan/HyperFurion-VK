from unittest import mock

import pytest

from voice_keyboard import client


class TestGetClipboardText:
    def test_returns_wl_paste_output_when_available(self) -> None:
        result = mock.Mock()
        result.returncode = 0
        result.stdout = "selected text\n"
        with mock.patch("voice_keyboard.client.subprocess.run", return_value=result) as run:
            text = client._get_clipboard_text()
        assert text == "selected text"
        # First call should target wl-paste --primary.
        first_args = run.call_args_list[0].args[0]
        assert first_args[0] == "wl-paste"

    def test_falls_back_to_xclip_when_wl_paste_missing(self) -> None:
        side_effects = [
            FileNotFoundError("wl-paste"),
            mock.Mock(returncode=0, stdout="clip\n"),
        ]
        with mock.patch(
            "voice_keyboard.client.subprocess.run", side_effect=side_effects
        ) as run:
            text = client._get_clipboard_text()
        assert text == "clip"
        second_args = run.call_args_list[1].args[0]
        assert second_args[0] == "xclip"
        assert "-selection" in second_args and "primary" in second_args

    def test_returns_empty_when_all_tools_fail(self) -> None:
        with mock.patch(
            "voice_keyboard.client.subprocess.run",
            side_effect=FileNotFoundError("no tools"),
        ):
            assert client._get_clipboard_text() == ""

    def test_returns_empty_when_stdout_blank(self) -> None:
        result = mock.Mock(returncode=0, stdout="   \n")
        with mock.patch("voice_keyboard.client.subprocess.run", return_value=result):
            assert client._get_clipboard_text() == ""


class TestCommandTimeouts:
    @pytest.mark.parametrize(
        ("provider", "expected"),
        [
            ("xai", 20.0),
            ("openai", 90.0),
            ("groq", 90.0),
            ("deepgram", 90.0),
            ("assemblyai", 270.0),
        ],
    )
    def test_stop_timeout_follows_stt_provider(self, provider: str, expected: float) -> None:
        assert client._stop_timeout_for_config({"stt": {"provider": provider}}) == expected


class TestToggleCommand:
    def test_toggle_starts_when_idle(self, monkeypatch: pytest.MonkeyPatch) -> None:
        # Avoid argparse reading sys.argv from the test runner.
        monkeypatch.setattr("sys.argv", ["voice-keyboard", "toggle"])

        responses = [
            {"status": "ok", "recording": False},  # status query
            {"status": "ok", "message": "recording started"},  # start response
        ]
        fake_client = mock.Mock()
        fake_client.send_command = mock.Mock(side_effect=responses)
        with mock.patch("voice_keyboard.client.load_config", return_value={"daemon": {"socket_path": "/x"}}), \
             mock.patch("voice_keyboard.client.IPCClient", return_value=fake_client), \
             mock.patch("voice_keyboard.client._show_overlay") as overlay, \
             mock.patch("voice_keyboard.client._notify") as notify, \
             mock.patch("builtins.print"):
            client.main()

        assert fake_client.send_command.call_args_list[0].args[0] == "status"
        assert fake_client.send_command.call_args_list[1].args[0] == "start"
        assert overlay.call_args_list == [
            mock.call("starting"),
            mock.call("listening"),
        ]
        notify.assert_called_once_with(
            "Voice Keyboard",
            "Listening... press Ctrl+Space again to stop",
            timeout_ms=5000,
        )

    def test_toggle_stops_when_recording(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr("sys.argv", ["voice-keyboard", "toggle"])

        responses = [
            {"status": "ok", "recording": True},
            {"status": "ok", "message": "recording stopped", "text": "hi"},
        ]
        fake_client = mock.Mock()
        fake_client.send_command = mock.Mock(side_effect=responses)
        with mock.patch("voice_keyboard.client.load_config", return_value={"daemon": {"socket_path": "/x"}}), \
             mock.patch("voice_keyboard.client.IPCClient", return_value=fake_client), \
             mock.patch("voice_keyboard.client._show_overlay") as overlay, \
             mock.patch("voice_keyboard.client._notify") as notify, \
             mock.patch("builtins.print"):
            client.main()

        assert fake_client.send_command.call_args_list[1].args[0] == "stop"
        assert overlay.call_args_list == [
            mock.call("processing"),
            mock.call("inserted", detail="Inserted 2 characters", timeout_ms=1800),
        ]
        assert notify.call_args_list == [
            mock.call("Voice Keyboard", "Processing speech...", timeout_ms=5000),
            mock.call("Voice Keyboard", "Inserted 2 characters", timeout_ms=4000),
        ]

    def test_toggle_falls_back_to_start_when_daemon_unreachable(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr("sys.argv", ["voice-keyboard", "toggle"])

        # status query raises; the fallback path then sends "start".
        fake_client = mock.Mock()
        fake_client.send_command = mock.Mock(
            side_effect=[ConnectionRefusedError("no daemon"), {"status": "ok"}]
        )
        with mock.patch("voice_keyboard.client.load_config", return_value={"daemon": {"socket_path": "/x"}}), \
             mock.patch("voice_keyboard.client.IPCClient", return_value=fake_client), \
             mock.patch("voice_keyboard.client._show_overlay"), \
             mock.patch("voice_keyboard.client._notify"), \
             mock.patch("builtins.print"):
            client.main()

        assert fake_client.send_command.call_args_list[1].args[0] == "start"

    def test_toggle_does_not_swallow_non_connection_errors(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A malformed daemon response must surface, not silently trigger start."""
        monkeypatch.setattr("sys.argv", ["voice-keyboard", "toggle"])

        fake_client = mock.Mock()
        fake_client.send_command = mock.Mock(side_effect=ValueError("malformed"))
        with mock.patch("voice_keyboard.client.load_config", return_value={"daemon": {"socket_path": "/x"}}), \
             mock.patch("voice_keyboard.client.IPCClient", return_value=fake_client), \
             mock.patch("voice_keyboard.client._show_overlay"), \
             mock.patch("voice_keyboard.client._notify"), \
             mock.patch("builtins.print"):
            with pytest.raises(ValueError, match="malformed"):
                client.main()


class TestNotifications:
    def test_notify_sends_and_stores_notification_id(
        self, tmp_path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("XDG_RUNTIME_DIR", str(tmp_path))
        result = mock.Mock(returncode=0, stdout="42\n")
        with mock.patch("voice_keyboard.client.subprocess.run", return_value=result) as run:
            client._notify("Voice Keyboard", "Listening...", timeout_ms=5000)

        command = run.call_args.args[0]
        assert command[:2] == ["notify-send", "-a"]
        assert "Voice Keyboard" in command
        assert "Listening..." in command
        assert "-p" in command
        assert (tmp_path / "voice-keyboard-notification-id").read_text() == "42"

    def test_notify_replaces_previous_notification(
        self, tmp_path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("XDG_RUNTIME_DIR", str(tmp_path))
        (tmp_path / "voice-keyboard-notification-id").write_text("41")
        result = mock.Mock(returncode=0, stdout="42\n")
        with mock.patch("voice_keyboard.client.subprocess.run", return_value=result) as run:
            client._notify("Voice Keyboard", "Processing...")

        command = run.call_args.args[0]
        assert "-r" in command
        assert command[command.index("-r") + 1] == "41"


class TestOverlayControl:
    def test_show_overlay_calls_shell_extension_with_focused_anchor(self) -> None:
        with mock.patch("voice_keyboard.client._focused_anchor", return_value=(320, 240)), \
             mock.patch("voice_keyboard.client._call_shell_overlay", return_value=True) as shell_call, \
             mock.patch("voice_keyboard.client._notify") as notify:
            client._show_overlay("listening", detail="Ready", timeout_ms=1000)

        shell_call.assert_called_once_with(
            "Show",
            "listening",
            "320",
            "240",
            "Ready",
            "1000",
        )
        notify.assert_not_called()

    def test_show_overlay_falls_back_to_notification_when_extension_missing(self) -> None:
        with mock.patch("voice_keyboard.client._focused_anchor", return_value=(-1, -1)), \
             mock.patch("voice_keyboard.client._call_shell_overlay", return_value=False), \
             mock.patch("voice_keyboard.client._notify") as notify:
            client._show_overlay("processing")

        notify.assert_called_once_with("Voice Keyboard", "Processing")

    def test_stop_overlay_calls_shell_extension_hide(self) -> None:
        with mock.patch("voice_keyboard.client._call_shell_overlay") as shell_call:
            client._stop_overlay()

        shell_call.assert_called_once_with("Hide", timeout=0.4)

    def test_call_shell_overlay_uses_gdbus(self) -> None:
        result = mock.Mock(returncode=0)
        with mock.patch("voice_keyboard.client.subprocess.run", return_value=result) as run:
            assert client._call_shell_overlay("Hide") is True

        command = run.call_args.args[0]
        assert command[:7] == [
            "gdbus",
            "call",
            "--session",
            "--dest",
            "org.voicekeyboard.Overlay",
            "--object-path",
            "/org/voicekeyboard/Overlay",
        ]
        assert command[-1] == "org.voicekeyboard.Overlay.Hide"

    def test_call_shell_overlay_separates_negative_coordinates_from_options(self) -> None:
        result = mock.Mock(returncode=0)
        with mock.patch("voice_keyboard.client.subprocess.run", return_value=result) as run:
            assert client._call_shell_overlay("Show", "listening", "-1", "-1", "", "0") is True

        command = run.call_args.args[0]
        method_index = command.index("org.voicekeyboard.Overlay.Show")
        assert command[method_index + 1:] == ["--", "listening", "-1", "-1", "", "0"]

    def test_focused_anchor_returns_fallback_on_error(self) -> None:
        with mock.patch(
            "voice_keyboard.client.subprocess.run",
            side_effect=FileNotFoundError("python3"),
        ):
            assert client._focused_anchor() == (-1, -1)
