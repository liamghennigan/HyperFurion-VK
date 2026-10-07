"""`voice-keyboard doctor`: whether Kai is on, and why (DESIGN §4.8)."""

import copy
from pathlib import Path

import pytest

from voice_keyboard import doctor
from voice_keyboard.config import DEFAULT_CONFIG

PROXIES = ("HTTPS_PROXY", "HTTP_PROXY", "ALL_PROXY", "https_proxy", "http_proxy", "all_proxy")


@pytest.fixture(autouse=True)
def isolated(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path))
    for name in PROXIES:
        monkeypatch.delenv(name, raising=False)
    return tmp_path


def _online(**assistant) -> dict:
    config = copy.deepcopy(DEFAULT_CONFIG)
    config["providers"]["xai"]["api_key"] = "xai-test-key"
    config["assistant"].update(assistant)
    return config


def _local(**assistant) -> dict:
    config = _online(**assistant)
    config["providers"]["openai"]["base_url"] = "http://127.0.0.1:8000/v1"
    config["stt"]["provider"] = "openai"
    config["tts"]["provider"] = "openai"
    config["llm"].update(provider="openai", base_url="http://127.0.0.1:8080/v1", model="qwen")
    return config


def test_on_and_local() -> None:
    finding = doctor.check_kai(_local())
    assert finding.status == doctor.OK
    assert finding.label == "kai"
    assert finding.detail == "on: everything Kai uses runs on this computer"


def test_turned_on_with_online_services() -> None:
    finding = doctor.check_kai(_online(enabled=True))
    assert finding.status == doctor.OK
    assert finding.detail.startswith("on (you turned it on): it uses xAI")


def test_turned_off() -> None:
    finding = doctor.check_kai(_local(enabled=False))
    assert finding.status == doctor.OK
    assert finding.detail == "off ([assistant] enabled = false)"


def test_off_by_default_says_where_and_how() -> None:
    finding = doctor.check_kai(_online())
    assert finding.status == doctor.OK
    assert finding.detail.startswith("off: it would use xAI")
    assert "to turn it on:" in finding.detail


def test_off_with_no_language_model() -> None:
    config = _local()
    config["llm"].update(provider="custom", base_url="", model="")
    assert doctor.check_kai(config).detail == "off: no language model is set up for it ([llm])"


def test_wake_word_on_while_kai_is_off_is_a_warning() -> None:
    config = _online()
    config["wake"]["enabled"] = True
    finding = doctor.check_kai(config)
    assert finding.status == doctor.WARN
    assert "the wake word is on, but Kai is off, so it isn't listening" in finding.detail
    assert "[wake] enabled = false" in finding.fix


def test_running_daemon_disagrees() -> None:
    finding = doctor.check_kai(_local(), {"assistant": False, "assistant_why": "off: it would use xAI"})
    assert finding.status == doctor.WARN
    assert finding.detail.startswith("the running daemon has Kai off")
    assert "restart" in finding.fix


def test_running_daemon_agrees() -> None:
    assert doctor.check_kai(_local(), {"assistant": True}).status == doctor.OK


def test_memory_is_kept_while_off(isolated: Path) -> None:
    from voice_keyboard.assistant.memory import AssistantMemory

    AssistantMemory().remember("tea, no sugar")
    finding = doctor.check_kai(_online())
    assert "its memory is kept at" in finding.detail
    assert "assistant-memory.sqlite3" in finding.detail


def test_online_recall_falls_back_to_keywords_under_auto() -> None:
    config = _local()
    config["recall"].update(base_url="https://api.openai.com/v1", model="text-embedding-3-small")
    finding = doctor.check_kai(config)
    assert finding.detail.endswith("its history search uses keywords: [recall] is online")


def test_a_proxy_with_local_kai_is_a_warning(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("HTTPS_PROXY", "http://proxy.example:3128")
    finding = doctor.check_kai(_local())
    assert finding.status == doctor.WARN
    assert "HTTPS_PROXY is set" in finding.detail and "directly" in finding.detail
    assert doctor.check_kai(_online()).status == doctor.OK


def test_run_includes_the_kai_check(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(doctor, "check_daemon", lambda config, seen=None: doctor.Finding(doctor.OK, "daemon"))
    monkeypatch.setattr(doctor, "check_audio", lambda config: doctor.Finding(doctor.OK, "microphone"))
    labels = [f.label for f in doctor.run(tmp_path / "config.toml", _online())]
    assert labels.index("kai") == labels.index("llm") + 1


# ── speech: the same local test Kai uses ────────────────────────────────


def test_a_relay_on_localhost_is_cloud() -> None:
    config = {
        "stt": {"provider": "hyperfurion"},
        "providers": {"hyperfurion": {"base_url": "http://127.0.0.1:8787", "api_key": "hfk-1"}},
    }
    finding = doctor.check_speech(config)
    assert finding.status == doctor.OK and "cloud" in finding.detail


def test_a_lan_speech_server_is_local() -> None:
    config = {"stt": {"provider": "openai"}, "providers": {"openai": {"base_url": "http://192.168.1.5:8000/v1"}}}
    assert "local" in doctor.check_speech(config).detail


def test_a_lookalike_host_is_cloud() -> None:
    config = {"stt": {"provider": "openai"},
              "providers": {"openai": {"base_url": "http://127.evil.com/v1", "api_key": "k"}}}
    assert "cloud" in doctor.check_speech(config).detail
