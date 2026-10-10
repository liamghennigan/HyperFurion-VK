"""Kai on Windows: the tray's Turn on Kai… / Turn off Kai, the orb, the
welcome balloon and the starter settings (DESIGN §4.2, A14; CRITIQUE P2).

Every Windows string points to the tray icon, not to `voice-keyboard kai`
(tray users often have no CLI on PATH). Turning Kai on with online services
asks first, No by default; turning it off applies at once, even when the
settings file can't be written. The Win32 calls are stubbed.
"""

import json
import sys
import tomllib
from pathlib import Path
from unittest import mock

import pytest

from voice_keyboard.assistant import announce
from voice_keyboard.assistant.locality import kai_state
from voice_keyboard.config import KAI_ENABLED_MARKER, _default_config_with_paths, load_config
from voice_keyboard.windows import app as app_mod
from voice_keyboard.windows import shell as shell_mod
from voice_keyboard.windows.shell import ShellCallbacks, WinShell

LOCAL = """\
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
"""

ONLINE = '[providers.xai]\napi_key = "xai-real"\n'


@pytest.fixture
def app(tmp_path, monkeypatch):
    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.setattr(app_mod, "_config_path", lambda: tmp_path / "config.toml")
    monkeypatch.setattr("voice_keyboard.config._config_dir", lambda: tmp_path)
    return app_mod.WindowsApp(), tmp_path / "config.toml"


class Dialogs:
    def __init__(self, answer: bool = False):
        self.answer = answer
        self.asked: list = []
        self.alerts: list = []

    def confirm(self, title, body):
        self.asked.append((title, body))
        return self.answer

    def alert(self, title, body):
        self.alerts.append((title, body))


def _daemon(cfg_text: str, path: Path, *, on: bool) -> mock.Mock:
    path.write_text(cfg_text, encoding="utf-8")
    state = kai_state(load_config(path))
    return mock.Mock(assistant_enabled=on, kai_state=state)


# ── the starter settings and the orb ────────────────────────────────────


def test_starter_config_has_kai_on_auto() -> None:
    data = tomllib.loads(app_mod.STARTER_CONFIG)
    assert data["assistant"]["enabled"] == "auto"
    lines = app_mod.STARTER_CONFIG.split("\n")
    assert lines[lines.index('enabled = "auto"') - 1] == KAI_ENABLED_MARKER
    assert "tray icon" in app_mod.STARTER_CONFIG


def test_the_orb_starts_hidden() -> None:
    assert WinShell(ShellCallbacks())._orb.want is False


# ── the welcome balloon ─────────────────────────────────────────────────


def _cfg(text: str, tmp_path: Path) -> dict:
    path = tmp_path / "welcome.toml"
    path.write_text(text, encoding="utf-8")
    return load_config(path)


def test_welcome_with_kai_on(tmp_path) -> None:
    text = app_mod.welcome_text(_cfg(LOCAL, tmp_path))
    assert "Hold Right Ctrl to ask Kai." in text


def test_welcome_with_kai_off_points_to_the_tray(tmp_path) -> None:
    text = app_mod.welcome_text(_cfg(ONLINE, tmp_path))
    assert "Hold Right Ctrl" not in text
    assert "Kai, the voice assistant, is off: it would send your questions to xAI." in text
    assert text.endswith("right-click the tray icon → Turn on Kai…")
    assert "voice-keyboard" not in text
    assert len(text) <= 255


def test_welcome_with_kai_turned_off(tmp_path) -> None:
    text = app_mod.welcome_text(_cfg(ONLINE + "[assistant]\nenabled = false\n", tmp_path))
    assert "Kai" not in text


def test_welcome_records_what_it_said(app, kai_notices, monkeypatch, tmp_path) -> None:
    instance, path = app
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "state"))
    instance._shell = mock.Mock()
    instance._welcome(_cfg(ONLINE, tmp_path))
    title, body = instance._shell.notify.call_args.args
    assert "is off" in body
    record = json.loads(announce.record_path().read_text(encoding="utf-8"))
    assert record["on"] is False and record["setting"] == "auto"


# ── status for the tray ─────────────────────────────────────────────────


def test_status_says_why(app, tmp_path) -> None:
    instance, path = app
    daemon = _daemon(ONLINE, path, on=False)
    daemon.recording = False
    daemon.conversing = False
    instance._daemon = daemon
    status = instance._status()
    assert status["assistant"] is False
    assert status["assistant_setting"] == "auto"
    assert status["assistant_local"] is False
    assert "xAI" in status["assistant_why"]
    assert {"role": "hears you", "service": "xAI", "local": False, "section": "[stt]"} in status["assistant_hops"]


def test_status_without_the_attribute_says_off(app) -> None:
    instance, _ = app

    class Bare:
        recording = False

    instance._daemon = Bare()
    assert instance._status()["assistant"] is False


# ── Turn on Kai… / Turn off Kai ─────────────────────────────────────────


def test_turn_on_online_asks_and_no_writes_nothing(app) -> None:
    instance, path = app
    daemon = _daemon(ONLINE, path, on=False)
    instance._daemon = daemon
    dialogs = Dialogs(answer=False)
    assert instance._apply_kai(True, confirm=dialogs.confirm, alert=dialogs.alert) is False
    title, body = dialogs.asked[0]
    assert title == "Turn on Kai?"
    assert "• Hears you: xAI receives your recorded question" in body
    assert "• Speaks: xAI receives its answer" in body
    assert "highlighted" not in body  # Windows has no primary selection
    assert body.endswith("Turn on Kai and send these to xAI?")
    assert "assistant" not in tomllib.loads(path.read_text(encoding="utf-8"))
    daemon.reload_config.assert_not_called()


def test_turn_on_online_with_yes_writes_true_and_reloads(app) -> None:
    instance, path = app
    daemon = _daemon(ONLINE, path, on=False)
    daemon.reload_config.side_effect = lambda: setattr(daemon, "assistant_enabled", True)
    instance._daemon = daemon
    dialogs = Dialogs(answer=True)
    assert instance._apply_kai(True, confirm=dialogs.confirm, alert=dialogs.alert) is True
    assert tomllib.loads(path.read_text(encoding="utf-8"))["assistant"]["enabled"] is True
    daemon.reload_config.assert_called_once()
    assert dialogs.alerts == []
    record = json.loads(announce.record_path().read_text(encoding="utf-8"))
    assert record["on"] is True and record["setting"] == "on"


def test_turn_on_local_needs_no_question(app) -> None:
    instance, path = app
    daemon = _daemon(LOCAL + "\n[assistant]\nenabled = false\n", path, on=False)
    instance._daemon = daemon
    dialogs = Dialogs()
    assert instance._apply_kai(True, confirm=dialogs.confirm, alert=dialogs.alert) is True
    assert dialogs.asked == []
    assert tomllib.loads(path.read_text(encoding="utf-8"))["assistant"]["enabled"] == "auto"
    daemon.reload_config.assert_called_once()


def test_turn_on_asks_about_what_the_running_daemon_uses(app) -> None:
    # The file is local now, but the running daemon still speaks through
    # xAI until it restarts: that is what Kai would use, so it asks.
    instance, path = app
    online = _daemon(ONLINE, path, on=False)
    path.write_text(LOCAL + "\n[assistant]\nenabled = false\n", encoding="utf-8")
    instance._daemon = online
    dialogs = Dialogs(answer=False)
    instance._apply_kai(True, confirm=dialogs.confirm, alert=dialogs.alert)
    assert dialogs.asked and "• Hears you: xAI receives your recorded question" in dialogs.asked[0][1]


def test_turn_on_says_when_the_daemon_still_has_it_off(app) -> None:
    instance, path = app
    daemon = _daemon(ONLINE, path, on=False)
    instance._daemon = daemon  # reload_config leaves it off
    dialogs = Dialogs(answer=True)
    instance._apply_kai(True, confirm=dialogs.confirm, alert=dialogs.alert)
    title, body = dialogs.alerts[0]
    assert title == "Kai is still off"
    assert "right-click the tray icon → Restart" in body


def test_turn_on_without_a_daemon_uses_the_file(app) -> None:
    instance, path = app
    path.write_text(ONLINE, encoding="utf-8")
    dialogs = Dialogs(answer=True)
    assert instance._apply_kai(True, confirm=dialogs.confirm, alert=dialogs.alert) is True
    assert "xAI" in dialogs.asked[0][1]
    assert tomllib.loads(path.read_text(encoding="utf-8"))["assistant"]["enabled"] is True


def test_turn_off_is_at_once(app) -> None:
    instance, path = app
    daemon = _daemon(LOCAL, path, on=True)
    instance._daemon = daemon
    dialogs = Dialogs()
    assert instance._apply_kai(False, confirm=dialogs.confirm, alert=dialogs.alert) is True
    assert tomllib.loads(path.read_text(encoding="utf-8"))["assistant"]["enabled"] is False
    daemon.kai_off.assert_called_once()
    assert dialogs.asked == [] and dialogs.alerts == []


def test_turn_off_with_a_broken_file_still_turns_it_off(app) -> None:
    instance, path = app
    path.write_text("[stt\n", encoding="utf-8")
    daemon = mock.Mock(assistant_enabled=True)
    instance._daemon = daemon
    dialogs = Dialogs()
    assert instance._apply_kai(False, confirm=dialogs.confirm, alert=dialogs.alert) is False
    daemon.kai_off.assert_called_once()
    assert dialogs.alerts  # says what to set by hand


def test_turn_off_when_the_file_cannot_be_written(app, monkeypatch) -> None:
    instance, path = app
    daemon = _daemon(LOCAL, path, on=True)
    instance._daemon = daemon

    def refuse(*args, **kwargs):
        raise PermissionError("read-only")

    monkeypatch.setattr("voice_keyboard.config.write_config_text", refuse)
    dialogs = Dialogs()
    assert instance._apply_kai(False, confirm=dialogs.confirm, alert=dialogs.alert) is False
    daemon.kai_off.assert_called_once()
    title, body = dialogs.alerts[0]
    assert "set [assistant] enabled = false where your config comes from" in body
    assert "Kai is off until HyperFurion VK restarts." in body


def test_turn_on_with_a_broken_file_says_so(app) -> None:
    instance, path = app
    path.write_text("[stt\n", encoding="utf-8")
    dialogs = Dialogs(answer=True)
    assert instance._apply_kai(True, confirm=dialogs.confirm, alert=dialogs.alert) is False
    assert dialogs.asked == []
    assert dialogs.alerts[0][0] == "Kai stays off"


def test_set_kai_runs_off_the_ui_thread(app, monkeypatch) -> None:
    instance, _ = app
    seen = []
    monkeypatch.setattr(instance, "_apply_kai", lambda on: seen.append(on))
    started = []

    class FakeThread:
        def __init__(self, target, args, name, daemon):
            started.append(name)
            self._run = lambda: target(*args)

        def start(self):
            self._run()

    monkeypatch.setattr(app_mod.threading, "Thread", FakeThread)
    instance._set_kai(True)
    assert started == ["vk-kai"] and seen == [True]


def test_the_shell_gets_the_kai_callback(app, monkeypatch) -> None:
    instance, _ = app
    made = {}
    monkeypatch.setattr(shell_mod, "WinShell", lambda callbacks, **kwargs: made.setdefault("cb", callbacks))
    instance._make_shell(_default_config_with_paths())
    assert made["cb"].set_kai == instance._set_kai


def test_turn_on_with_a_file_that_fails_validation_says_so(app) -> None:
    # The daemon refuses such a file, so Kai couldn't turn on: say why,
    # instead of "speech settings change at a restart".
    instance, path = app
    path.write_text(ONLINE + '\n[hotkey]\nmode = "sideways"\n', encoding="utf-8")
    dialogs = Dialogs(answer=True)
    assert instance._apply_kai(True, confirm=dialogs.confirm, alert=dialogs.alert) is False
    assert dialogs.asked == []
    assert dialogs.alerts[0][0] == "Kai stays off"
    assert "hotkey" in dialogs.alerts[0][1]
    assert "assistant" not in tomllib.loads(path.read_text(encoding="utf-8"))
