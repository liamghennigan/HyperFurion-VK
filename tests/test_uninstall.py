from pathlib import Path
from unittest import mock

import pytest

from voice_keyboard import uninstall


class TestUserTargets:
    def test_default_omits_config_and_state(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("HOME", str(tmp_path / "home"))
        monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
        monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))
        monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "data"))
        paths = uninstall.user_targets(purge=False)
        rendered = [str(p) for p in paths]
        assert not any(str(tmp_path / "config" / "voice-keyboard") == p for p in rendered)
        assert any(p.endswith("voice-keyboard-venv") for p in rendered)
        assert any(p.endswith(uninstall.OVERLAY_UUID) for p in rendered)

    def test_purge_includes_config_and_state(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("HOME", str(tmp_path / "home"))
        monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
        monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))
        monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "data"))
        paths = uninstall.user_targets(purge=True)
        assert tmp_path / "config" / "voice-keyboard" in paths
        assert tmp_path / "state" / "voice-keyboard" in paths


class TestUninstall:
    def test_removes_user_files_and_leaves_system(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        home = tmp_path / "home"
        config = tmp_path / "config"
        data = tmp_path / "data"
        monkeypatch.setenv("HOME", str(home))
        monkeypatch.setenv("XDG_CONFIG_HOME", str(config))
        monkeypatch.setenv("XDG_DATA_HOME", str(data))
        monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))
        monkeypatch.setattr(uninstall, "_stop_systemd", lambda: None)
        monkeypatch.setattr(uninstall, "remove_system_files", mock.Mock())

        bin_dir = home / ".local" / "bin"
        bin_dir.mkdir(parents=True)
        (bin_dir / "voice-keyboard").write_text("x")
        venv = data / "voice-keyboard-venv"
        venv.mkdir(parents=True)
        (venv / "pyvenv.cfg").write_text("x")
        overlay = data / "gnome-shell" / "extensions" / uninstall.OVERLAY_UUID
        overlay.mkdir(parents=True)
        (overlay / "extension.js").write_text("x")
        cfg = config / "voice-keyboard"
        cfg.mkdir(parents=True)
        (cfg / "config.toml").write_text("x")

        log = uninstall.uninstall(purge=False, system=False)
        assert not (bin_dir / "voice-keyboard").exists()
        assert not venv.exists()
        assert not overlay.exists()
        assert (cfg / "config.toml").exists()
        assert any("left udev" in line for line in log)
        uninstall.remove_system_files.assert_not_called()

    def test_purge_removes_config(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        home = tmp_path / "home"
        config = tmp_path / "config"
        monkeypatch.setenv("HOME", str(home))
        monkeypatch.setenv("XDG_CONFIG_HOME", str(config))
        monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "data"))
        monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))
        monkeypatch.setattr(uninstall, "_stop_systemd", lambda: None)
        cfg = config / "voice-keyboard"
        cfg.mkdir(parents=True)
        (cfg / "config.toml").write_text("x")
        uninstall.uninstall(purge=True, system=False)
        assert not cfg.exists()

    def test_system_calls_remove_system_files(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(uninstall, "_stop_systemd", lambda: None)
        monkeypatch.setattr(uninstall, "user_targets", lambda purge: [])
        monkeypatch.setattr(
            uninstall, "remove_system_files", lambda: ["removed /etc/udev/rules.d/99-uinput.rules"]
        )
        log = uninstall.uninstall(purge=False, system=True)
        assert any("99-uinput.rules" in line for line in log)

    def test_cli_dispatch(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr("sys.argv", ["voice-keyboard", "uninstall", "--purge"])
        called = mock.Mock(return_value=0)
        monkeypatch.setattr("voice_keyboard.uninstall.main", called)
        with pytest.raises(SystemExit) as exc:
            from voice_keyboard import client

            client.main()
        assert exc.value.code == 0
        called.assert_called_once_with(purge=True, system=False)
