"""`voice-keyboard doctor`: what [llm] is for with the settings you have
(Kai, "VK, …" rewrites and pause review use it by default), and on Linux
whether the hotkey can read the keyboard (the input group, joined and
applied to this login)."""

import copy
import os
import sys
from types import SimpleNamespace

import pytest

from voice_keyboard import doctor
from voice_keyboard.config import DEFAULT_CONFIG


def _defaults(**changes) -> dict:
    config = copy.deepcopy(DEFAULT_CONFIG)
    for section, values in changes.items():
        config.setdefault(section, {}).update(values)
    return config


@pytest.fixture
def llm(monkeypatch: pytest.MonkeyPatch):
    import voice_keyboard.llm as llm_module

    state = {"ready": False}
    monkeypatch.setattr(llm_module, "llm_ready", lambda config: state["ready"])
    return state


class TestLLM:
    def test_default_settings_without_llm_say_what_wont_work(self, llm) -> None:
        finding = doctor.check_llm(_defaults())
        assert "nothing needs it" not in finding.detail
        assert finding.status == doctor.WARN
        assert finding.detail == 'not set: Kai and "VK, …" rewrites won\'t work; pause review uses rules only'
        assert "[llm]" in finding.fix and "[assistant] enabled = false" in finding.fix

    def test_default_settings_with_llm_say_what_its_for(self, llm) -> None:
        llm["ready"] = True
        finding = doctor.check_llm(_defaults())
        assert finding.status == doctor.OK
        assert finding.detail == 'ready for Kai, "VK, …" rewrites, pause review'

    def test_a_voice_agent_answers_kai_out_loud(self, llm) -> None:
        config = _defaults(assistant={"agent_id": "agent-1", "api_key": "k"})
        finding = doctor.check_llm(config)
        assert finding.detail.startswith("not set: Kai's terminal commands and")

    def test_nothing_uses_it_is_true_only_when_nothing_does(self, llm) -> None:
        config = _defaults(assistant={"enabled": False}, flow={"wake_word": "", "pause_review": "rules"})
        finding = doctor.check_llm(config)
        assert finding.status == doctor.OK and finding.detail == "not set (nothing needs it)"

    def test_switched_on_features_still_fail(self, llm) -> None:
        finding = doctor.check_llm(_defaults(polish={"map": {"slack": "casual"}}))
        assert finding.status == doctor.FAIL
        assert finding.detail.startswith("[polish.map] need [llm]") and "Kai" in finding.detail

    def test_pause_review_llm_is_a_switched_on_feature(self, llm) -> None:
        config = _defaults(assistant={"enabled": False}, flow={"wake_word": "", "pause_review": "llm"})
        assert doctor.check_llm(config).status == doctor.FAIL

    def test_another_wake_word(self, llm) -> None:
        llm["ready"] = True
        assert '"Computer, …" rewrites' in doctor.check_llm(_defaults(flow={"wake_word": "computer"})).detail


GID_INPUT = 104


@pytest.fixture
def linux(monkeypatch: pytest.MonkeyPatch):
    """A Linux box with two keyboards in the input group; the test says
    how many this session can read and whether it has the group."""
    import grp

    state = SimpleNamespace(keyboards=0, members=["liam"], session_groups=[1000], readable=False)
    monkeypatch.setattr(doctor, "_input_nodes", lambda: ["/dev/input/event0", "/dev/input/event1"])
    monkeypatch.setattr(doctor, "_readable_keyboards", lambda hotkey: state.keyboards)
    monkeypatch.setattr(grp, "getgrnam", lambda name: SimpleNamespace(gr_gid=GID_INPUT, gr_mem=state.members))
    monkeypatch.setattr(os, "getgroups", lambda: state.session_groups)
    monkeypatch.setenv("USER", "liam")
    real_stat = os.stat

    def stat(path, *a, **k):
        if str(path).startswith("/dev/input/"):
            return SimpleNamespace(st_gid=GID_INPUT)
        return real_stat(path, *a, **k)

    monkeypatch.setattr(os, "stat", stat)
    real_access = os.access
    monkeypatch.setattr(
        os, "access", lambda path, mode: state.readable if str(path).startswith("/dev/input/") else real_access(path, mode)
    )
    return state


class TestHotkey:
    def test_not_in_the_input_group(self, linux) -> None:
        linux.members = []
        finding = doctor.check_hotkey(_defaults())
        assert finding.status == doctor.FAIL
        assert finding.detail == "can't read your keyboard, so Ctrl+Alt+V won't start dictation"
        assert finding.fix == "sudo usermod -aG input $USER, then log out and back in"

    def test_joined_but_not_logged_in_again(self, linux) -> None:
        finding = doctor.check_hotkey(_defaults())
        assert finding.status == doctor.FAIL
        assert finding.fix == "log out and back in (your session predates joining the input group)"

    def test_joined_and_applied(self, linux) -> None:
        linux.session_groups = [1000, GID_INPUT]
        linux.keyboards = 2
        finding = doctor.check_hotkey(_defaults())
        assert finding.status == doctor.OK and finding.detail == "Ctrl+Alt+V (reads 2 keyboards)"

    def test_the_daemon_started_before_the_group_applied(self, linux) -> None:
        linux.session_groups = [1000, GID_INPUT]
        linux.keyboards = 1
        finding = doctor.check_hotkey(_defaults(), {"status": "ok", "hotkey_keyboards": 0})
        assert finding.status == doctor.FAIL and "the running daemon can't" in finding.detail
        assert "log out and back in" in finding.fix
        assert doctor.check_hotkey(_defaults(), {"status": "ok", "hotkey_keyboards": 1}).status == doctor.OK

    def test_a_readable_keyboard_without_those_keys(self, linux) -> None:
        linux.session_groups = [1000, GID_INPUT]
        linux.readable = True
        finding = doctor.check_hotkey(_defaults(hotkey={"key": "control+alt+f13"}))
        assert finding.status == doctor.WARN and "Ctrl+Alt+F13" in finding.detail

    def test_hotkey_off(self, linux) -> None:
        assert doctor.check_hotkey(_defaults(hotkey={"enabled": False})).status == doctor.OK

    def test_doctor_runs_it_on_linux_only(self, linux, monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
        monkeypatch.setattr(doctor, "check_daemon", lambda config, seen=None: doctor.Finding(doctor.OK, "daemon"))
        monkeypatch.setattr(doctor, "check_audio", lambda config: doctor.Finding(doctor.OK, "microphone"))
        monkeypatch.setattr(sys, "platform", "linux")
        labels = [f.label for f in doctor.run(tmp_path / "config.toml", _defaults())]
        assert "hotkey" in labels
        monkeypatch.setattr(sys, "platform", "darwin")
        labels = [f.label for f in doctor.run(tmp_path / "config.toml", _defaults())]
        assert "hotkey" not in labels

    def test_it_counts_keyboards_the_way_the_listener_does(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from voice_keyboard import hotkey

        if hotkey.list_devices is None:
            pytest.skip("no evdev here")
        opened = []

        def open_devices(self, skip_paths=frozenset()):
            opened.append(self._spec.key)
            return [SimpleNamespace(close=lambda: None)]

        monkeypatch.setattr(hotkey.HotkeyListener, "_open_devices", open_devices)
        assert doctor._readable_keyboards({"key": "control+alt+v"}) == 1 and opened == ["control+alt+v"]


class TestDaemonReportsItsKeyboards:
    def test_status_says_how_many_keyboards_the_hotkey_reads(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from unittest import mock

        from fakes import _flow_config

        from voice_keyboard.daemon import Daemon

        daemon = Daemon(config=_flow_config(), injector=mock.Mock(), ipc_server=mock.Mock(), tts_client=mock.Mock())
        monkeypatch.setattr(sys, "platform", "linux")
        assert "hotkey_keyboards" not in daemon._status_response()  # no listener
        daemon._hotkey_listener = SimpleNamespace(_enabled=True, _mode="auto", _devices=[object(), object()])
        assert daemon._status_response()["hotkey_keyboards"] == 2
        daemon._hotkey_listener = SimpleNamespace(_enabled=False, _mode="auto", _devices=[])
        assert "hotkey_keyboards" not in daemon._status_response()
        monkeypatch.setattr(sys, "platform", "win32")
        daemon._hotkey_listener = SimpleNamespace(_enabled=True, _mode="auto", _devices=[])
        assert "hotkey_keyboards" not in daemon._status_response()
