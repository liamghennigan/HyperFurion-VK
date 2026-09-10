from pathlib import Path
from unittest import mock

import pytest

from voice_keyboard import doctor
from voice_keyboard.config import _default_config_with_paths
from voice_keyboard.doctor import Check


def _ok_config() -> dict:
    cfg = _default_config_with_paths()
    cfg["xai"]["api_key"] = "test-api-key"
    cfg["providers"]["xai"]["api_key"] = "test-api-key"
    return cfg


class TestFormatReport:
    def test_includes_fix_for_failures_and_exit_tally(self) -> None:
        text = doctor.format_report(
            [
                Check("cli", "OK", "on PATH"),
                Check("config", "FAIL", "missing file", "run install.sh"),
                Check("overlay", "WARN", "notifications only"),
            ]
        )
        assert "OK    cli" in text
        assert "→ run install.sh" in text
        assert "1 ok, 1 warning(s), 1 failed" in text


class TestCliOnPath:
    def test_ok_when_which_finds_it(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(doctor.shutil, "which", lambda name: "/home/u/.local/bin/voice-keyboard")
        result = doctor.check_cli_on_path()
        assert result.status == "OK"
        assert "/home/u/.local/bin/voice-keyboard" in result.detail

    def test_fail_when_local_bin_exists_but_not_on_path(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        home = tmp_path / "home"
        local = home / ".local" / "bin"
        local.mkdir(parents=True)
        (local / "voice-keyboard").write_text("#!/bin/sh\n")
        monkeypatch.setattr(doctor.Path, "home", classmethod(lambda cls: home))
        monkeypatch.setattr(doctor.shutil, "which", lambda name: None)
        monkeypatch.setenv("PATH", "/usr/bin")
        result = doctor.check_cli_on_path()
        assert result.status == "FAIL"
        assert "not on PATH" in result.detail
        assert "export PATH" in result.fix


class TestConfig:
    def test_missing_file_fails(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(doctor, "_config_dir", lambda: tmp_path / "voice-keyboard")
        result = doctor.check_config(_ok_config())
        assert result.status == "FAIL"
        assert "missing" in result.detail

    def test_placeholder_key_names_local_endpoint(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        cfg_dir = tmp_path / "voice-keyboard"
        cfg_dir.mkdir()
        (cfg_dir / "config.toml").write_text("[stt]\nprovider = \"xai\"\n")
        monkeypatch.setattr(doctor, "_config_dir", lambda: cfg_dir)
        cfg = _default_config_with_paths()
        result = doctor.check_config(cfg)
        assert result.status == "FAIL"
        assert "api_key is not configured" in result.detail
        assert "base_url" in result.fix

    def test_valid_config_ok(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        cfg_dir = tmp_path / "voice-keyboard"
        cfg_dir.mkdir()
        (cfg_dir / "config.toml").write_text("[stt]\nprovider = \"xai\"\n")
        monkeypatch.setattr(doctor, "_config_dir", lambda: cfg_dir)
        result = doctor.check_config(_ok_config())
        assert result.status == "OK"
        assert "stt=xai" in result.detail


class TestSystemd:
    def test_skip_off_linux(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(doctor, "_linux", lambda: False)
        result = doctor.check_systemd()
        assert result.status == "WARN"

    def test_active_and_enabled(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(doctor, "_linux", lambda: True)

        def fake_run(argv, **kwargs):
            query = argv[-2]
            stdout = "enabled" if query == "is-enabled" else "active"
            return mock.Mock(returncode=0, stdout=stdout, stderr="")

        monkeypatch.setattr(doctor, "_run", fake_run)
        result = doctor.check_systemd()
        assert result.status == "OK"

    def test_inactive_fails(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(doctor, "_linux", lambda: True)

        def fake_run(argv, **kwargs):
            query = argv[-2]
            if query == "is-enabled":
                return mock.Mock(returncode=0, stdout="enabled", stderr="")
            return mock.Mock(returncode=3, stdout="inactive", stderr="")

        monkeypatch.setattr(doctor, "_run", fake_run)
        result = doctor.check_systemd()
        assert result.status == "FAIL"
        assert "systemctl --user enable --now" in result.fix


class TestDaemon:
    def test_unreachable(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(
            doctor,
            "IPCClient",
            mock.Mock(side_effect=FileNotFoundError("socket")),
        )
        result = doctor.check_daemon(_ok_config())
        assert result.status == "FAIL"
        assert "not reachable" in result.detail

    def test_idle_ok(self, monkeypatch: pytest.MonkeyPatch) -> None:
        client = mock.Mock()
        client.send_command.return_value = {"status": "ok", "recording": False}
        monkeypatch.setattr(doctor, "IPCClient", mock.Mock(return_value=client))
        result = doctor.check_daemon(_ok_config())
        assert result.status == "OK"
        assert "idle" in result.detail


class TestUinput:
    def test_missing(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
        monkeypatch.setattr(doctor, "_linux", lambda: True)
        monkeypatch.setattr(doctor, "Path", lambda p: tmp_path / "nope" if p == "/dev/uinput" else Path(p))
        # Simpler: patch Path.exists on the instance via a fake path
        fake = mock.Mock()
        fake.exists.return_value = False
        monkeypatch.setattr(doctor, "Path", mock.Mock(return_value=fake))
        result = doctor.check_uinput()
        assert result.status == "FAIL"
        assert "modprobe" in result.fix

    def test_writable(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(doctor, "_linux", lambda: True)
        fake = mock.Mock()
        fake.exists.return_value = True
        monkeypatch.setattr(doctor, "Path", mock.Mock(return_value=fake))
        monkeypatch.setattr(doctor.os, "access", lambda path, mode: True)
        result = doctor.check_uinput()
        assert result.status == "OK"


class TestInputGroup:
    def test_relogin_cliff(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(doctor, "_linux", lambda: True)
        monkeypatch.setattr(doctor, "_input_group_gid", lambda: 999)
        monkeypatch.setattr(doctor, "_input_group_members", lambda: {"alice"})
        monkeypatch.setenv("USER", "alice")
        monkeypatch.setattr(doctor.os, "getgroups", lambda: [1000])
        result = doctor.check_input_group()
        assert result.status == "FAIL"
        assert "log out" in result.fix

    def test_effective(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(doctor, "_linux", lambda: True)
        monkeypatch.setattr(doctor, "_input_group_gid", lambda: 999)
        monkeypatch.setattr(doctor, "_input_group_members", lambda: {"alice"})
        monkeypatch.setenv("USER", "alice")
        monkeypatch.setattr(doctor.os, "getgroups", lambda: [999])
        result = doctor.check_input_group()
        assert result.status == "OK"


class TestInputDevices:
    def test_readable_events(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
        monkeypatch.setattr(doctor, "_linux", lambda: True)
        root = tmp_path / "input"
        root.mkdir()
        (root / "event0").write_text("")
        monkeypatch.setattr(
            doctor,
            "Path",
            lambda p: root if p == "/dev/input" else Path(p),
        )
        monkeypatch.setattr(doctor.os, "access", lambda path, mode: True)
        result = doctor.check_input_devices()
        assert result.status == "OK"


class TestMicrophone:
    def test_no_pyaudio(self, monkeypatch: pytest.MonkeyPatch) -> None:
        import builtins

        real_import = builtins.__import__

        def fake_import(name, *args, **kwargs):
            if name == "pyaudio":
                raise ImportError("nope")
            return real_import(name, *args, **kwargs)

        monkeypatch.setattr(builtins, "__import__", fake_import)
        result = doctor.check_microphone()
        assert result.status == "FAIL"
        assert "PyAudio" in result.detail

    def test_lists_inputs(self, monkeypatch: pytest.MonkeyPatch) -> None:
        pa = mock.Mock()
        pa.get_device_count.return_value = 2
        pa.get_device_info_by_index.side_effect = [
            {"maxInputChannels": 0, "name": "speakers"},
            {"maxInputChannels": 2, "name": "USB Mic"},
        ]
        module = mock.Mock(PyAudio=mock.Mock(return_value=pa))
        monkeypatch.setitem(__import__("sys").modules, "pyaudio", module)
        result = doctor.check_microphone()
        assert result.status == "OK"
        assert "USB Mic" in result.detail


class TestClipboard:
    def test_linux_missing(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(doctor.sys, "platform", "linux")
        monkeypatch.setattr(doctor.shutil, "which", lambda name: None)
        result = doctor.check_clipboard()
        assert result.status == "FAIL"
        assert "wl-clipboard" in result.fix

    def test_linux_wl_copy(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(doctor.sys, "platform", "linux")
        monkeypatch.setattr(
            doctor.shutil, "which", lambda name: f"/usr/bin/{name}" if name.startswith("wl-") else None
        )
        result = doctor.check_clipboard()
        assert result.status == "OK"


class TestAtspi:
    def test_import_ok(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
        monkeypatch.setattr(doctor, "_linux", lambda: True)
        python = tmp_path / "python3"
        python.write_text("")
        real_path = doctor.Path

        def path_factory(p):
            if p == "/usr/bin/python3":
                return python
            return real_path(p)

        monkeypatch.setattr(doctor, "Path", path_factory)
        monkeypatch.setattr(
            doctor,
            "_run",
            lambda argv, **kwargs: mock.Mock(returncode=0, stdout="", stderr=""),
        )
        result = doctor.check_atspi()
        assert result.status == "OK"

    def test_import_fail(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
        monkeypatch.setattr(doctor, "_linux", lambda: True)
        python = tmp_path / "python3"
        python.write_text("")
        real_path = doctor.Path

        def path_factory(p):
            if p == "/usr/bin/python3":
                return python
            return real_path(p)

        monkeypatch.setattr(doctor, "Path", path_factory)
        monkeypatch.setattr(
            doctor,
            "_run",
            lambda argv, **kwargs: mock.Mock(
                returncode=1, stdout="", stderr="ModuleNotFoundError: No module named 'gi'\n"
            ),
        )
        result = doctor.check_atspi()
        assert result.status == "FAIL"
        assert "python3-gi" in result.fix


class TestOverlayAndNotify:
    def test_non_gnome_warns(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(doctor, "_linux", lambda: True)
        monkeypatch.delenv("XDG_CURRENT_DESKTOP", raising=False)
        monkeypatch.setattr(
            doctor,
            "_run",
            lambda argv, **kwargs: mock.Mock(returncode=1, stdout="", stderr=""),
        )
        result = doctor.check_overlay()
        assert result.status == "WARN"
        assert "notifications" in result.detail

    def test_overlay_ok(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(doctor, "_linux", lambda: True)
        monkeypatch.setattr(
            doctor,
            "_run",
            lambda argv, **kwargs: mock.Mock(returncode=0, stdout="()", stderr=""),
        )
        result = doctor.check_overlay()
        assert result.status == "OK"

    def test_notify_missing(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(doctor, "_linux", lambda: True)
        monkeypatch.setattr(doctor.shutil, "which", lambda name: None)
        result = doctor.check_notify_send()
        assert result.status == "FAIL"


class TestRunChecks:
    def test_any_fail_makes_main_exit_1(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(
            doctor,
            "load_config",
            lambda: _ok_config(),
        )
        monkeypatch.setattr(
            doctor,
            "run_checks",
            lambda config=None: [Check("cli", "FAIL", "nope", "fix it")],
        )
        monkeypatch.setattr("builtins.print", lambda *a, **k: None)
        assert doctor.main() == 1

    def test_all_ok_exits_0(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(doctor, "load_config", lambda: _ok_config())
        monkeypatch.setattr(
            doctor,
            "run_checks",
            lambda config=None: [Check("cli", "OK", "yes")],
        )
        monkeypatch.setattr("builtins.print", lambda *a, **k: None)
        assert doctor.main() == 0

    def test_dispatches_from_cli(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr("sys.argv", ["voice-keyboard", "doctor"])
        called = mock.Mock(return_value=0)
        monkeypatch.setattr("voice_keyboard.doctor.main", called)
        with pytest.raises(SystemExit) as exc:
            from voice_keyboard import client

            client.main()
        assert exc.value.code == 0
        called.assert_called_once()
