"""`voice-keyboard kai [on|off]` and `summon` while Kai is off (DESIGN
§4.5, A16): say whether Kai is on and why, turn it on only after saying
where the questions would go, and turn it off at once."""

import sys
import tomllib
from pathlib import Path
from unittest import mock

import pytest

from voice_keyboard import client

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

ONLINE = """\
[providers.xai]
api_key = "xai-test-key"
"""


class FakeDaemon:
    """Stands in for the IPC client; None = no daemon running."""

    def __init__(self, replies=None):
        self.replies = replies
        self.commands: list[str] = []

    def __call__(self, socket_path, timeout=None):
        return self

    def send_command(self, command, payload=None, timeout=None):
        self.commands.append(command)
        if self.replies is None:
            raise ConnectionRefusedError()
        reply = self.replies.get(command, {"status": "ok"})
        return reply(self) if callable(reply) else reply


@pytest.fixture
def config_path(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))
    path = tmp_path / "config" / "voice-keyboard" / "config.toml"
    path.parent.mkdir(parents=True)
    return path


def _run(monkeypatch, *argv, daemon=None, tty=False, answer="") -> int:
    daemon = daemon or FakeDaemon()
    monkeypatch.setattr(client, "IPCClient", daemon)
    monkeypatch.setattr("sys.argv", ["voice-keyboard", *argv])
    monkeypatch.setattr(sys.stdin, "isatty", lambda: tty, raising=False)
    monkeypatch.setattr("builtins.input", lambda prompt="": answer)
    with pytest.raises(SystemExit) as exit_:
        client.main()
    return exit_.value.code


def _enabled(path: Path):
    return tomllib.loads(path.read_text(encoding="utf-8")).get("assistant", {}).get("enabled")


def test_status_says_off_and_why(monkeypatch, capsys, config_path: Path) -> None:
    config_path.write_text(ONLINE, encoding="utf-8")
    assert _run(monkeypatch, "kai") == 0
    out = capsys.readouterr().out
    assert out.startswith("Kai is off: it would use xAI")
    assert "hears you:" in out and "thinks:" in out and "speaks:" in out
    assert "xAI, online ([stt]): your recorded question" in out
    assert "Turn it on anyway: voice-keyboard kai on" in out


def test_status_says_on_when_local(monkeypatch, capsys, config_path: Path) -> None:
    config_path.write_text(LOCAL, encoding="utf-8")
    assert _run(monkeypatch, "kai") == 0
    out = capsys.readouterr().out
    assert out.startswith("Kai is on: everything Kai uses runs on this computer")
    assert "on this computer ([stt])" in out
    assert "Turn it off: voice-keyboard kai off" in out


def test_status_shows_both_views_when_the_daemon_differs(monkeypatch, capsys, config_path) -> None:
    config_path.write_text(LOCAL, encoding="utf-8")
    daemon = FakeDaemon({"status": {"status": "ok", "assistant": False,
                                    "assistant_why": "off: it would use xAI for speech-to-text ([stt])"}})
    assert _run(monkeypatch, "kai", daemon=daemon) == 0
    out = capsys.readouterr().out
    assert "Now: Kai is off: it would use xAI" in out
    assert "After a restart: Kai is on" in out


def test_off_writes_false_and_tells_the_daemon(monkeypatch, capsys, config_path) -> None:
    config_path.write_text(LOCAL, encoding="utf-8")
    daemon = FakeDaemon({"kai_off": {"status": "ok", "assistant": False}})
    assert _run(monkeypatch, "kai", "off", daemon=daemon) == 0
    assert _enabled(config_path) is False
    assert daemon.commands == ["kai_off"]
    assert "Kai is off now" in capsys.readouterr().out


def test_off_works_with_a_broken_file(monkeypatch, capsys, config_path) -> None:
    config_path.write_text("this is = = not toml\n", encoding="utf-8")
    daemon = FakeDaemon({"kai_off": {"status": "ok", "assistant": False}})
    assert _run(monkeypatch, "kai", "off", daemon=daemon) == 1
    assert daemon.commands == ["kai_off"]  # off now, whatever the file says
    captured = capsys.readouterr()
    assert "Can't change" in captured.err
    assert "Kai is off now" in captured.out


def test_on_when_local_writes_auto_without_asking(monkeypatch, capsys, config_path) -> None:
    config_path.write_text(LOCAL + "\n[assistant]\nenabled = false\n", encoding="utf-8")
    daemon = FakeDaemon({"status": {"status": "ok", "assistant": False},
                         "reload": {"status": "ok", "assistant": True}})
    assert _run(monkeypatch, "kai", "on", daemon=daemon) == 0
    assert _enabled(config_path) == "auto"
    assert daemon.commands == ["status", "reload"]
    out = capsys.readouterr().out
    assert "everything it uses runs on this computer" in out
    assert "The running daemon has Kai on now." in out


def test_on_online_without_a_terminal_asks_for_one(monkeypatch, capsys, config_path) -> None:
    config_path.write_text(ONLINE, encoding="utf-8")
    assert _run(monkeypatch, "kai", "on") == 2
    assert _enabled(config_path) is None
    out = capsys.readouterr().out
    assert "would use online services" in out and "xAI" in out
    assert "in a terminal to confirm" in out


def test_on_online_asks_and_yes_writes_true(monkeypatch, capsys, config_path) -> None:
    config_path.write_text(ONLINE, encoding="utf-8")
    daemon = FakeDaemon({"status": {"status": "ok", "assistant": False},
                         "reload": {"status": "ok", "assistant": True}})
    assert _run(monkeypatch, "kai", "on", daemon=daemon, tty=True, answer="y") == 0
    assert _enabled(config_path) is True
    assert daemon.commands[-1] == "reload"
    out = capsys.readouterr().out
    assert "your question" in out and "its answer" in out
    assert "Kai is on (you turned it on)" in out


def test_on_online_no_writes_nothing(monkeypatch, capsys, config_path) -> None:
    config_path.write_text(ONLINE, encoding="utf-8")
    assert _run(monkeypatch, "kai", "on", tty=True, answer="") == 0
    assert _enabled(config_path) is None
    assert "Kai stays off." in capsys.readouterr().out


def test_on_yes_flag_is_the_answer(monkeypatch, config_path) -> None:
    config_path.write_text(ONLINE, encoding="utf-8")
    assert _run(monkeypatch, "kai", "on", "--yes") == 0
    assert _enabled(config_path) is True


def test_consent_covers_what_the_running_daemon_uses(monkeypatch, capsys, config_path) -> None:
    config_path.write_text(ONLINE, encoding="utf-8")
    daemon = FakeDaemon({"status": {"status": "ok", "assistant": False, "assistant_hops": [
        {"role": "speaks", "service": "ElevenLabs", "local": False, "section": "[tts]"},
    ]}})
    assert _run(monkeypatch, "kai", "on", daemon=daemon) == 2
    assert "ElevenLabs" in capsys.readouterr().out


def test_bad_usage(monkeypatch, config_path) -> None:
    assert _run(monkeypatch, "kai", "sideways") == 2


def test_summon_while_off_prints_why_and_fails(monkeypatch, capsys, config_path) -> None:
    config_path.write_text(ONLINE, encoding="utf-8")
    message = "Kai is off: it would use xAI. To turn it on: voice-keyboard kai on"
    daemon = FakeDaemon({"converse": {"status": "error", "message": message}})
    assert _run(monkeypatch, "summon", daemon=daemon) == 1
    assert message in capsys.readouterr().err


def test_status_line_names_kai(capsys) -> None:
    client._print_status_details({"stt_provider": "xai", "assistant": False})
    assert "kai: off" in capsys.readouterr().out


def test_write_failure_names_the_value(monkeypatch, capsys, config_path) -> None:
    config_path.write_text(ONLINE, encoding="utf-8")
    with mock.patch("voice_keyboard.config.write_config_text", side_effect=PermissionError("read-only")):
        assert _run(monkeypatch, "kai", "on", "--yes") == 1
    assert "set [assistant] enabled = true" in capsys.readouterr().err


ONLINE_RECALL = ONLINE + """
[recall]
base_url = "https://api.openai.com/v1"
model = "text-embedding-3-small"
api_key = "sk-test"
"""


def test_consent_names_the_history_search_that_true_turns_on(monkeypatch, capsys, config_path) -> None:
    # Off, an online [recall] isn't used (keyword search). Turned on with
    # online services it is: the question names it before Kai uses it.
    config_path.write_text(ONLINE_RECALL, encoding="utf-8")
    prompts: list = []
    monkeypatch.setattr(client, "IPCClient", FakeDaemon())
    monkeypatch.setattr("sys.argv", ["voice-keyboard", "kai", "on"])
    monkeypatch.setattr(sys.stdin, "isatty", lambda: True, raising=False)
    monkeypatch.setattr("builtins.input", lambda prompt="": prompts.append(prompt) or "")
    with pytest.raises(SystemExit):
        client.main()
    out = capsys.readouterr().out
    assert "looks up your history:" in out
    assert "your question and up to 200 lines of your dictation history" in out
    assert prompts == ["Turn on Kai and send these to xAI and OpenAI? (y/N) "]
    assert _enabled(config_path) is None


def test_a_lan_server_is_on_your_network(monkeypatch, capsys, config_path) -> None:
    config_path.write_text(LOCAL.replace("127.0.0.1:8000", "192.168.1.5:8000"), encoding="utf-8")
    assert _run(monkeypatch, "kai") == 0
    out = capsys.readouterr().out
    assert out.startswith("Kai is on: everything Kai uses runs on this computer or your own network")
    assert "192.168.1.5:8000, on your network ([stt])" in out
    assert "127.0.0.1:8080, on this computer ([llm])" in out


def test_on_with_a_file_that_fails_validation_says_why(monkeypatch, capsys, config_path) -> None:
    # The daemon refuses such a file: Kai can't turn on until it is fixed.
    config_path.write_text(LOCAL + '\n[hotkey]\nmode = "sideways"\n', encoding="utf-8")
    assert _run(monkeypatch, "kai", "on") == 1
    err = capsys.readouterr().err
    assert "hotkey" in err and "voice-keyboard doctor" in err
    assert _enabled(config_path) is None
