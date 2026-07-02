import io
import os
from pathlib import Path
from unittest import mock

import pytest

from voice_keyboard import config


@pytest.fixture
def tmp_config_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    cfg_dir = tmp_path / "voice-keyboard"
    monkeypatch.setattr(config, "_config_dir", lambda: cfg_dir)
    return cfg_dir


class TestConfigLoading:
    def test_default_config_uses_secure_socket_path(self, tmp_config_dir: Path) -> None:
        cfg = config.load_config()
        expected_socket = str(tmp_config_dir / "socket")
        assert cfg["daemon"]["socket_path"] == expected_socket

    def test_user_config_overrides_defaults(self, tmp_config_dir: Path) -> None:
        cfg_path = tmp_config_dir / "config.toml"
        cfg_dir = tmp_config_dir
        cfg_dir.mkdir(parents=True, exist_ok=True)
        cfg_path.write_text(
            "[xai]\napi_key = \"test-key\"\n\n"
            "[audio]\nsample_rate = 48000\nchunk_ms = 50\n\n"
            "[stt]\nlanguage = \"es\"\ninterim_results = false\n"
        )
        cfg = config.load_config()
        assert cfg["xai"]["api_key"] == "test-key"
        assert cfg["providers"]["xai"]["api_key"] == "test-key"
        assert cfg["audio"]["sample_rate"] == 48000
        assert cfg["audio"]["chunk_ms"] == 50
        assert cfg["stt"]["language"] == "es"
        assert cfg["stt"]["interim_results"] is False
        assert cfg["hotkey"]["key"] == "control+alt+v"
        assert cfg["hotkey"]["mode"] == "auto"
        assert cfg["daemon"]["socket_path"] == str(cfg_dir / "socket")

    def test_user_socket_path_override(self, tmp_config_dir: Path) -> None:
        cfg_path = tmp_config_dir / "config.toml"
        custom_socket = str(tmp_config_dir / "custom.sock")
        tmp_config_dir.mkdir(parents=True, exist_ok=True)
        cfg_path.write_text(f"[daemon]\nsocket_path = \"{custom_socket}\"\n")
        cfg = config.load_config()
        assert cfg["daemon"]["socket_path"] == custom_socket


class TestConfigValidation:
    def test_missing_api_key_raises(self) -> None:
        cfg = config._default_config_with_paths()
        with pytest.raises(RuntimeError, match="providers.xai.api_key is not configured"):
            config.validate_config(cfg)

    def test_blank_api_key_raises(self) -> None:
        cfg = config._default_config_with_paths()
        cfg["xai"]["api_key"] = "   "
        with pytest.raises(RuntimeError, match="providers.xai.api_key is not configured"):
            config.validate_config(cfg)

    def test_placeholder_api_key_raises(self) -> None:
        cfg = config._default_config_with_paths()
        cfg["xai"]["api_key"] = "xai-your-api-key-here"
        with pytest.raises(RuntimeError, match="providers.xai.api_key is not configured"):
            config.validate_config(cfg)

    def test_invalid_sample_rate_raises(self) -> None:
        cfg = config._default_config_with_paths()
        cfg["xai"]["api_key"] = "test-key"
        cfg["audio"]["sample_rate"] = 0
        with pytest.raises(RuntimeError, match="audio.sample_rate must be a positive integer"):
            config.validate_config(cfg)

    def test_valid_config_passes(self) -> None:
        cfg = config._default_config_with_paths()
        cfg["xai"]["api_key"] = "test-key"
        cfg["audio"]["sample_rate"] = 16000
        cfg["audio"]["chunk_ms"] = 100
        config.validate_config(cfg)

    def test_openai_provider_config_passes_with_openai_key(self) -> None:
        cfg = config._default_config_with_paths()
        cfg["stt"]["provider"] = "openai"
        cfg["tts"]["provider"] = "openai"
        cfg["providers"]["openai"]["api_key"] = "openai-key"
        config.validate_config(cfg)

    def test_invalid_stt_provider_raises(self) -> None:
        cfg = config._default_config_with_paths()
        cfg["stt"]["provider"] = "not-real"
        cfg["providers"]["xai"]["api_key"] = "xai-key"
        with pytest.raises(RuntimeError, match="stt.provider"):
            config.validate_config(cfg)

    def test_invalid_hotkey_mode_raises(self) -> None:
        cfg = config._default_config_with_paths()
        cfg["xai"]["api_key"] = "test-key"
        cfg["hotkey"]["mode"] = "press"
        with pytest.raises(RuntimeError, match="hotkey.mode"):
            config.validate_config(cfg)

    def test_invalid_hotkey_enabled_raises(self) -> None:
        cfg = config._default_config_with_paths()
        cfg["xai"]["api_key"] = "test-key"
        cfg["hotkey"]["enabled"] = "yes"
        with pytest.raises(RuntimeError, match="hotkey.enabled"):
            config.validate_config(cfg)


@pytest.mark.parametrize(
    "xdg, expected",
    [
        ("/tmp/cfg", Path("/tmp/cfg/voice-keyboard")),
        ("", Path.home() / ".config" / "voice-keyboard"),
    ],
)
def test_config_dir(monkeypatch: pytest.MonkeyPatch, xdg: str, expected: Path) -> None:
    monkeypatch.setenv("XDG_CONFIG_HOME", xdg)
    assert config._config_dir() == expected
