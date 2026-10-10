"""The Mac-friendly bindings the dev setup script writes into a config, and
the script's own launchd plists and bug-report masking."""

import plistlib
import re
import shutil
import subprocess
import sys
import tomllib
from pathlib import Path

import pytest

from voice_keyboard.macos import devsetup

SCRIPT = Path(__file__).resolve().parent.parent / "scripts" / "macos-dev-setup.sh"


def _plist_heredocs() -> list[str]:
    text = SCRIPT.read_text()
    return re.findall(r"<<EOF\n(<\?xml.*?)\nEOF\n", text, flags=re.S)


def test_the_scripts_launchd_plists_are_valid() -> None:
    blocks = _plist_heredocs()
    assert len(blocks) == 2  # the daemon agent and the whisper-server agent
    for block in blocks:
        assert re.search(r"<key>Label</key><string>\$(LABEL|WHISPER_LABEL)</string>", block)
        filled = re.sub(r"\$\([^)]*\)", "/opt/homebrew", block)
        filled = re.sub(r"\$[A-Z_]+", "/Users/me/x", filled)
        plist = plistlib.loads(filled.encode())
        assert plist["ProgramArguments"] and plist["RunAtLoad"] is True
    text = SCRIPT.read_text()
    assert 'LABEL="com.hyperfurion.voice-keyboard"' in text
    assert 'WHISPER_LABEL="com.hyperfurion.whisper-server"' in text
    daemon = plistlib.loads(re.sub(r"\$[A-Z_]+|\$\([^)]*\)", "/x", blocks[0]).encode())
    # "voice-keyboard quit" must stick: restart only after a crash.
    assert daemon["KeepAlive"] == {"SuccessfulExit": False}
    assert daemon["LimitLoadToSessionType"] == "Aqua"


def test_the_installers_plist_template_is_valid() -> None:
    template = SCRIPT.parent.parent / "packaging" / "macos" / "com.hyperfurion.voice-keyboard.plist"
    filled = (template.read_text().replace("__BIN__", "/Users/me/venv/bin")
              .replace("__HOME__", "/Users/me").replace("__BREW__", "/opt/homebrew"))
    plist = plistlib.loads(filled.encode())
    assert plist["Label"] == "com.hyperfurion.voice-keyboard"
    assert plist["ProgramArguments"] == ["/Users/me/venv/bin/voice-keyboard-daemon"]
    assert plist["KeepAlive"] == {"SuccessfulExit": False}
    assert "__" not in filled


# The dev setup script is a macOS shell script: its shell-level checks need a
# POSIX shell and sed (Windows' bash.exe is the WSL launcher, not a shell).
POSIX_SHELL = pytest.mark.skipif(sys.platform == "win32", reason="POSIX shell script")


@POSIX_SHELL
@pytest.mark.skipif(shutil.which("sed") is None, reason="needs sed")
def test_bug_reports_mask_api_keys() -> None:
    expression = re.search(r"sed -E '([^']+)'", SCRIPT.read_text()).group(1)
    sample = "api_key xai-AbCdEf123456 sk-proj-abcdefgh1234 hfk_live_0123456789 gsk_zzzzzzzzzz ok-short"
    masked = subprocess.run(["sed", "-E", expression], input=sample, capture_output=True,
                            text=True, check=True).stdout
    for secret in ("AbCdEf123456", "abcdefgh1234", "live_0123456789", "zzzzzzzzzz"):
        assert secret not in masked
    assert "xai-[masked]" in masked and "ok-short" in masked


@POSIX_SHELL
def test_the_script_parses() -> None:
    if shutil.which("bash") is None:
        pytest.skip("needs bash")
    subprocess.run(["bash", "-n", str(SCRIPT)], check=True)


def test_an_empty_config_gets_both(tmp_path) -> None:
    path = tmp_path / "config.toml"
    changed = devsetup.apply(path)
    assert len(changed) == 2
    data = tomllib.loads(path.read_text())
    assert data["assistant"]["hotkey"] == "rightcmd"
    assert data["tts"]["hotkey"] == "control+alt+r"
    if sys.platform != "win32":  # POSIX file modes
        assert path.stat().st_mode & 0o777 == 0o600


def test_the_starter_right_ctrl_is_replaced_but_comments_stay(tmp_path) -> None:
    path = tmp_path / "config.toml"
    path.write_text(
        '# my notes\n[providers.xai]\napi_key = "k"\n\n[assistant]\n# summon key\nhotkey = "rightctrl"\n'
        'mode = "auto"\n'
    )
    devsetup.apply(path)
    text = path.read_text()
    assert text.startswith("# my notes\n") and "# summon key" in text
    data = tomllib.loads(text)
    assert data["assistant"] == {"hotkey": "rightcmd", "mode": "auto"}
    assert data["providers"]["xai"]["api_key"] == "k"


def test_the_users_own_choices_are_kept(tmp_path) -> None:
    path = tmp_path / "config.toml"
    original = '[assistant]\nhotkey = "fn"\n\n[tts]\nhotkey = ""\n'
    path.write_text(original)
    assert devsetup.apply(path) == []
    assert path.read_text() == original  # untouched


def test_a_clash_with_the_users_binding_is_skipped(tmp_path) -> None:
    path = tmp_path / "config.toml"
    path.write_text('[hotkey]\nkey = "ctrl+alt+r"\n')
    changed = devsetup.apply(path)
    assert len(changed) == 1 and "Right Command" in changed[0]
    data = tomllib.loads(path.read_text())
    assert "tts" not in data and data["assistant"]["hotkey"] == "rightcmd"


def test_local_speech_points_stt_at_the_server(tmp_path) -> None:
    from voice_keyboard.config import load_config
    from voice_keyboard.stt import _live_rest_enabled

    path = tmp_path / "config.toml"
    path.write_text('[providers.xai]\napi_key = "k"\n\n[stt]\nprovider = "xai"\nlanguage = "en"\n')
    assert devsetup.main(["--local-speech", "http://127.0.0.1:2022/v1", str(path)]) == 0
    data = tomllib.loads(path.read_text())
    assert data["stt"] == {"provider": "openai", "language": "en"}
    assert data["providers"]["openai"]["base_url"] == "http://127.0.0.1:2022/v1"
    assert data["providers"]["xai"]["api_key"] == "k"  # TTS keeps its provider
    # A local endpoint turns on live (molten) dictation over REST by itself.
    config = load_config(path)
    assert _live_rest_enabled(config, provider="openai", base_url=config["providers"]["openai"]["base_url"])
    assert devsetup.main(["--local-speech"]) == 2


def test_main_reports(tmp_path, capsys) -> None:
    path = tmp_path / "config.toml"
    assert devsetup.main([str(path)]) == 0
    assert "Right Command" in capsys.readouterr().out
    assert devsetup.main([str(path)]) == 0
    assert "keeping yours" in capsys.readouterr().out
    (tmp_path / "bad.toml").write_text("[[[")
    assert devsetup.main([str(tmp_path / "bad.toml")]) == 1
