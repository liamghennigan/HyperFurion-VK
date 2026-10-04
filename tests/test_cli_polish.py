"""CLI polish: --version, and a platform-specific way forward when the
daemon isn't running (instead of a bare ConnectionRefusedError)."""

import sys

import pytest

from voice_keyboard import client


class TestVersion:
    def test_version_flag(self, monkeypatch, capsys) -> None:
        monkeypatch.setattr(sys, "argv", ["voice-keyboard", "--version"])
        monkeypatch.setattr(client, "_package_version", lambda: "9.9.9")
        with pytest.raises(SystemExit) as exit_info:
            client.main()
        assert exit_info.value.code == 0
        assert "9.9.9" in capsys.readouterr().out


class TestDaemonDownHint:
    @pytest.mark.parametrize(
        "platform, expected",
        [
            ("linux", "systemctl --user start voice-keyboard-daemon"),
            ("win32", "Start menu"),
            ("darwin", "launchctl kickstart"),
        ],
    )
    def test_hint_per_platform(self, monkeypatch, capsys, platform, expected) -> None:
        monkeypatch.setattr(sys, "platform", platform)
        client._print_connect_failure(ConnectionRefusedError(111, "Connection refused"))
        err = capsys.readouterr().err
        assert "Failed to connect to daemon" in err
        assert "not running" in err and expected in err

    def test_other_errors_get_no_hint(self, capsys) -> None:
        client._print_connect_failure(TimeoutError("timed out"))
        err = capsys.readouterr().err
        assert "timed out" in err and "not running" not in err

    def test_status_with_the_daemon_down(self, monkeypatch, capsys) -> None:
        monkeypatch.setattr(sys, "platform", "linux")
        monkeypatch.setattr(sys, "argv", ["voice-keyboard", "status"])
        monkeypatch.setattr(client, "load_config", lambda: {"daemon": {"socket_path": "tcp:127.0.0.1:1"}})
        with pytest.raises(SystemExit) as exit_info:
            client.main()
        assert exit_info.value.code == 1
        assert "systemctl --user start" in capsys.readouterr().err


class TestDevices:
    def test_lists_inputs_with_the_default_marked(self, monkeypatch, capsys) -> None:
        monkeypatch.setattr(sys, "argv", ["voice-keyboard", "devices"])
        monkeypatch.setattr(client, "load_config", lambda: {"daemon": {"socket_path": "x"}})
        monkeypatch.setattr(client, "_list_input_devices", lambda: [
            {"name": "Microphone (Realtek(R) Audio)", "channels": 2, "rate": 44100, "default": True},
            {"name": "Headset Microphone (Jabra Evolve2 65)", "channels": 1, "rate": 16000, "default": False},
        ])
        client.main()
        out = capsys.readouterr().out
        assert "* Microphone (Realtek(R) Audio)" in out
        assert "  Headset Microphone (Jabra Evolve2 65)" in out
        assert "device_name" in out

    def test_list_skips_outputs(self, monkeypatch) -> None:
        import types

        infos = [
            {"index": 0, "name": "Speakers", "maxInputChannels": 0, "defaultSampleRate": 48000},
            {"index": 1, "name": "Mic", "maxInputChannels": 1, "defaultSampleRate": 16000},
        ]

        class FakePA:
            def get_default_input_device_info(self):
                return infos[1]

            def get_device_count(self):
                return len(infos)

            def get_device_info_by_index(self, index):
                return infos[index]

            def terminate(self):
                pass

        monkeypatch.setitem(sys.modules, "pyaudio", types.SimpleNamespace(PyAudio=FakePA))
        assert client._list_input_devices() == [
            {"name": "Mic", "channels": 1, "rate": 16000, "default": True}
        ]
