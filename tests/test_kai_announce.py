"""Telling people when Kai turns on or off (DESIGN A9, A17; CRITIQUE M3, M4).

The daemon keeps a record of what it last told you about Kai. At start and
at every change it compares the record with its state, shows one
notification when something is new, and writes the record only once the
notification was shown, so a notice that couldn't be shown is tried again.
"""

import asyncio
import json
import sys
from pathlib import Path
from unittest import mock

import pytest

from voice_keyboard import client
from voice_keyboard.assistant import announce
from voice_keyboard.assistant.locality import kai_state, turn_on_hint
from voice_keyboard.config import _default_config_with_paths


def _online(**assistant) -> dict:
    cfg = _default_config_with_paths()
    cfg["xai"]["api_key"] = "xai-test-key"
    cfg["providers"]["xai"]["api_key"] = "xai-test-key"
    cfg["assistant"].update(assistant)
    return cfg


def _local(**assistant) -> dict:
    cfg = _online(**assistant)
    cfg["providers"]["openai"]["base_url"] = "http://127.0.0.1:8000/v1"
    cfg["stt"]["provider"] = "openai"
    cfg["tts"]["provider"] = "openai"
    cfg["llm"].update(provider="openai", base_url="http://127.0.0.1:8080/v1", model="qwen")
    return cfg


@pytest.fixture(autouse=True)
def on_path(monkeypatch: pytest.MonkeyPatch) -> None:
    # `voice-keyboard` is on PATH (otherwise the notice gives its full path).
    monkeypatch.setattr("voice_keyboard.assistant.locality.shutil.which", lambda name: f"/usr/bin/{name}")


def _notice(cfg: dict, record=None, platform: str = "linux"):
    return announce.notice(kai_state(cfg), record, cfg, platform)


# ── what it says ────────────────────────────────────────────────────────


def test_off_under_auto_says_why_and_how() -> None:
    title, body = _notice(_online())
    assert title == "Kai is off"
    assert ("turns on by itself only when everything it uses runs on this computer or your"
            " own network") in body
    assert "xAI for speech-to-text ([stt])" in body
    assert "as its language model ([llm])" in body
    assert body.endswith("To turn it on: voice-keyboard kai on")
    assert " she " not in f" {body} " and " her " not in f" {body} "


def test_off_names_only_the_online_part() -> None:
    cfg = _local()
    cfg["llm"].update(provider="xai", base_url="", model="grok-4.3")
    _title, body = _notice(cfg)
    assert "as its language model ([llm])" in body
    assert "[stt]" not in body and "[tts]" not in body


def test_off_on_windows_points_to_the_tray_and_fits_a_balloon() -> None:
    _title, body = _notice(_online(), platform="win32")
    assert "right-click the tray icon → Turn on Kai…" in body
    assert "voice-keyboard" not in body
    assert "this PC" in body
    assert len(body) <= announce.WINDOWS_BALLOON_MAX


def test_off_mentions_the_wake_word_and_the_voice_agent() -> None:
    cfg = _online(agent_id="agent-1")
    cfg["wake"]["enabled"] = True
    _title, body = _notice(cfg)
    assert "Your wake word doesn't listen until you turn Kai on." in body
    assert "xAI voice agent ([assistant] agent_id)" in body


def test_turned_off_yourself_says_nothing() -> None:
    assert _notice(_online(enabled=False)) == ("", "")


def test_on_because_everything_is_local() -> None:
    title, body = _notice(_local())
    assert title == "Kai is on"
    assert "everything it uses runs on this computer" in body
    assert "To ask it: hold Right Ctrl." in body


def test_on_never_says_right_ctrl_on_macos() -> None:
    _title, body = _notice(_local(), platform="darwin")
    assert "Right Ctrl" not in body
    assert "summon" in body


def test_on_because_you_turned_it_on() -> None:
    title, body = _notice(_online(enabled=True))
    assert title == "Kai is on"
    assert body.startswith("Kai is on (you turned it on): it uses xAI for speech-to-text")
    assert body.endswith("To turn it off: voice-keyboard kai off")


def test_a_new_online_service_is_news() -> None:
    before = announce.record_for(kai_state(_online(enabled=True)))
    cfg = _online(enabled=True)
    cfg["tts"]["provider"] = "elevenlabs"
    title, body = _notice(cfg, before)
    assert title == "Kai now uses xAI and ElevenLabs"
    assert "ElevenLabs for its voice ([tts])" in body


def test_nothing_new_says_nothing() -> None:
    for cfg in (_online(), _online(enabled=True), _local(), _online(enabled=False)):
        record = announce.record_for(kai_state(cfg))
        assert _notice(cfg, record) is None


def test_off_with_other_services_is_not_news() -> None:
    record = announce.record_for(kai_state(_online()))
    cfg = _online()
    cfg["stt"]["provider"] = "groq"
    assert _notice(cfg, record) is None


def test_auto_after_an_explicit_off_is_news() -> None:
    record = announce.record_for(kai_state(_online(enabled=False)))
    title, _body = _notice(_online(), record)
    assert title == "Kai is off"


# ── the record ──────────────────────────────────────────────────────────


def test_announced_once(kai_notices) -> None:
    state = kai_state(_online())
    assert announce.announce(state, _online()) is True
    assert announce.announce(state, _online()) is True
    assert [title for title, _ in kai_notices] == ["Kai is off"]
    record = json.loads(announce.record_path().read_text(encoding="utf-8"))
    assert record == {"setting": "auto", "on": False,
                      "online": {"[stt]": "xAI", "[llm]": "xAI (grok-4.3)", "[tts]": "xAI"}}


def test_not_recorded_when_the_notice_could_not_be_shown(monkeypatch) -> None:
    shown = []
    monkeypatch.setattr(announce, "show", lambda title, body: shown.append(title) or False)
    state = kai_state(_online())
    assert announce.announce(state, _online()) is False
    assert not announce.record_path().exists()
    assert announce.announce(state, _online()) is False
    assert shown == ["Kai is off", "Kai is off"]  # tried again


def test_announced_again_when_the_services_change(kai_notices) -> None:
    announce.announce(kai_state(_online(enabled=True)), _online(enabled=True))
    cfg = _online(enabled=True)
    cfg["tts"]["provider"] = "elevenlabs"
    announce.announce(kai_state(cfg), cfg)
    assert [title for title, _ in kai_notices] == ["Kai is on", "Kai now uses xAI and ElevenLabs"]


def test_a_broken_record_counts_as_none(kai_notices) -> None:
    announce.record_path().parent.mkdir(parents=True, exist_ok=True)
    announce.record_path().write_text("{not json", encoding="utf-8")
    announce.announce(kai_state(_online()), _online())
    assert [title for title, _ in kai_notices] == ["Kai is off"]


def test_the_notice_is_its_own_notification(monkeypatch) -> None:
    # Not replaced by the next dictation's "Listening…": no -r, no
    # synchronous hint, and its id isn't kept.
    monkeypatch.undo()  # the real announce.show
    monkeypatch.setattr(sys, "platform", "linux")
    notify = mock.Mock(return_value=True)
    monkeypatch.setattr(client, "_notify", notify)
    assert announce.show("Kai is off", "why") is True
    notify.assert_called_once_with("Kai is off", "why", timeout_ms=announce.NOTICE_MS, replace=False)


def test_notify_without_replacing(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(sys, "platform", "linux")
    monkeypatch.setenv("XDG_RUNTIME_DIR", str(tmp_path))
    (tmp_path / "voice-keyboard-notification-id").write_text("41")
    result = mock.Mock(returncode=0, stdout="")
    with mock.patch("voice_keyboard.client.subprocess.run", return_value=result) as run:
        assert client._notify("Kai is off", "why", replace=False) is True
    command = run.call_args.args[0]
    assert "-r" not in command
    assert not any("x-canonical-private-synchronous" in part for part in command)
    assert (tmp_path / "voice-keyboard-notification-id").read_text() == "41"


@pytest.mark.parametrize("outcome", [
    mock.Mock(returncode=1, stdout=""),
    FileNotFoundError("notify-send"),
])
def test_notify_says_when_it_failed(monkeypatch, outcome) -> None:
    monkeypatch.setattr(sys, "platform", "linux")
    run = mock.Mock(side_effect=outcome) if isinstance(outcome, Exception) else mock.Mock(return_value=outcome)
    monkeypatch.setattr("voice_keyboard.client.subprocess.run", run)
    assert client._notify("Kai is off", "why", replace=False) is False


def test_notify_on_windows_without_the_tray_says_it_failed(monkeypatch) -> None:
    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.setattr(client, "_local_shell", None)
    assert client._notify("Kai is off", "why") is False
    shell = mock.Mock(can_notify=True)
    monkeypatch.setattr(client, "_local_shell", shell)
    assert client._notify("Kai is off", "why") is True
    shell.can_notify = False  # the tray icon isn't up yet: the balloon can't show
    assert client._notify("Kai is off", "why") is False


# ── the daemon ──────────────────────────────────────────────────────────


@pytest.fixture()
def isolated(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))
    monkeypatch.setattr(client, "_show_overlay", mock.Mock())
    monkeypatch.setattr(client, "_set_overlay_button", mock.Mock())

    async def _to_thread(func, /, *args, **kwargs):
        return func(*args, **kwargs)

    monkeypatch.setattr(asyncio, "to_thread", _to_thread)
    return tmp_path / "config" / "voice-keyboard" / "config.toml"


def _daemon(cfg: dict):
    from voice_keyboard.daemon import Daemon

    return Daemon(config=cfg, injector=mock.Mock(), ipc_server=mock.Mock(), tts_client=mock.Mock())


def test_startup_announces_without_waiting(isolated, kai_notices, monkeypatch) -> None:
    monkeypatch.setattr("voice_keyboard.daemon.create_hotkey_listener", mock.Mock())
    monkeypatch.setattr("voice_keyboard.daemon.prefetch_enabled", lambda config: False)
    daemon = _daemon(_online())

    async def scenario() -> None:
        daemon._loop = asyncio.get_running_loop()
        daemon._start_services()
        assert kai_notices == []  # scheduled, not awaited
        for _ in range(5):
            await asyncio.sleep(0)
        await daemon._shutdown()

    asyncio.run(scenario())
    assert [title for title, _ in kai_notices] == ["Kai is off"]
    assert kai_notices[0][1].endswith(f"To turn it on: {turn_on_hint()}")


def test_startup_does_not_repeat_itself(isolated, kai_notices) -> None:
    announce.write_record(kai_state(_online()))
    daemon = _daemon(_online())

    async def scenario() -> None:
        await daemon._announce_kai()

    asyncio.run(scenario())
    assert kai_notices == []


def test_a_transition_is_announced(isolated, kai_notices) -> None:
    isolated.parent.mkdir(parents=True, exist_ok=True)
    daemon = _daemon(_local())
    announce.write_record(daemon.kai_state)

    async def scenario() -> None:
        isolated.write_text(
            '[providers.xai]\napi_key = "xai-test-key"\n\n[llm]\nprovider = "xai"\nmodel = "grok-4.3"\n',
            encoding="utf-8",
        )
        daemon._reload_kai_only(force=True)
        for _ in range(5):
            await asyncio.sleep(0)

    asyncio.run(scenario())
    assert daemon._brain is None
    assert [title for title, _ in kai_notices] == ["Kai is off"]
    assert "as its language model ([llm])" in kai_notices[0][1]


def test_kai_off_records_without_a_notice(isolated, kai_notices) -> None:
    daemon = _daemon(_local())
    announce.write_record(daemon.kai_state)

    async def scenario() -> None:
        await daemon._kai_off_request()
        for _ in range(5):
            await asyncio.sleep(0)

    asyncio.run(scenario())
    assert kai_notices == []
    record = json.loads(announce.record_path().read_text(encoding="utf-8"))
    assert record["on"] is False and record["setting"] == "off"


def test_no_listener_no_right_ctrl(isolated, kai_notices) -> None:
    daemon = _daemon(_local())
    daemon._assistant_hotkey_listener = None
    asyncio.run(daemon._announce_kai())
    assert "Right Ctrl" not in kai_notices[0][1]
    assert "summon" in kai_notices[0][1]


# ── the CLI records what it printed ─────────────────────────────────────


def test_kai_off_and_on_record_what_they_printed(isolated, kai_notices, monkeypatch, capsys) -> None:
    monkeypatch.setattr(client, "_kai_daemon", lambda *a, **k: None)
    isolated.parent.mkdir(parents=True, exist_ok=True)
    isolated.write_text('[providers.xai]\napi_key = "xai-test-key"\n', encoding="utf-8")
    assert client._run_kai(["off"], None) == 0
    assert json.loads(announce.record_path().read_text(encoding="utf-8"))["setting"] == "off"
    assert client._run_kai(["on", "--yes"], None) == 0
    record = json.loads(announce.record_path().read_text(encoding="utf-8"))
    assert record["on"] is True and record["setting"] == "on"
    assert record["online"]["[stt]"] == "xAI"
    # The daemon, starting under that file, has nothing new to say.
    daemon = _daemon(_online(enabled=True))
    asyncio.run(daemon._announce_kai())
    assert kai_notices == []
