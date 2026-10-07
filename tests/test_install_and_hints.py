"""What the installers, the example config and the setup hints tell people
must be true: the right package and extra to pip install, package names the
distribution actually has, the hosted subscription shown as not on sale, and
installer comments that match what the installers do.

install.sh is exercised for real: its blocks run under bash with stub
package managers that, like dnf5, refuse the whole transaction over one
unknown package name.
"""

import re
import subprocess
import sys
import tomllib
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
INSTALL_SH = ROOT / "install.sh"
EXAMPLE = ROOT / "config.toml.example"

linux_only = pytest.mark.skipif(
    not sys.platform.startswith("linux"), reason="install.sh is the Linux installer"
)


def _pyproject() -> dict:
    return tomllib.loads((ROOT / "pyproject.toml").read_text())


# ── the [wake] extra ─────────────────────────────────────────────────

PIP_EXTRA = re.compile(r"pip install\s+['\"]?([A-Za-z0-9_.-]+)\[([A-Za-z0-9_,-]+)\]")


@pytest.mark.parametrize(
    "rel", ["voice_keyboard/config.py", "config.toml.example", "voice_keyboard/wake.py"]
)
def test_wake_hint_names_the_real_package_and_extra(rel):
    project = _pyproject()["project"]
    text = (ROOT / rel).read_text()
    hits = PIP_EXTRA.findall(text)
    assert hits, f"{rel} has no pip install hint for the [wake] extra"
    for name, extras in hits:
        assert name == project["name"], f"{rel}: pip install {name}[...]"
        for extra in extras.split(","):
            assert extra in project["optional-dependencies"], f"{rel}: no [{extra}] extra"


def test_wake_extra_is_openwakeword():
    wake = _pyproject()["project"]["optional-dependencies"]["wake"]
    assert any(dep.startswith("openwakeword") for dep in wake)


def test_wake_load_failure_log_names_the_real_install(caplog, monkeypatch):
    from voice_keyboard.wake import WakeListener

    def missing():
        raise ImportError("No module named 'openwakeword'")

    wl = WakeListener(config={"wake": {"enabled": True}}, on_wake=lambda: None, is_busy=lambda: False)
    monkeypatch.setattr(wl, "_load_model", missing)
    with caplog.at_level("ERROR", logger="voice_keyboard.wake"):
        wl.start()
    assert wl._thread is None
    message = caplog.records[-1].getMessage()
    assert 'pip install "voice-keyboard[wake]"' in message
    # openWakeWord 0.6 ships no models: they are downloaded once.
    assert "download_models()" in message
    assert sys.prefix in message


def test_example_wake_steps_cover_the_model_download_and_new_pythons():
    section = EXAMPLE.read_text().split("[wake]\n", 1)[1].split("\nenabled", 1)[0]
    assert 'pip install "voice-keyboard[wake]"' in section
    assert "download_models()" in section
    # openWakeWord 0.6 requires tflite-runtime on Linux, which has no wheels
    # past Python 3.11; the documented way round it must be there.
    assert "pip install --no-deps openwakeword" in section
    assert "bundled" not in EXAMPLE.read_text().split("[wake]\n", 1)[1].split("[daemon]", 1)[0]


# ── the hosted subscription in config.toml.example ───────────────────


def test_example_lists_the_hosted_provider_last_and_not_on_sale():
    text = EXAMPLE.read_text()
    providers = list(tomllib.loads(text)["providers"])
    assert providers[-1] == "hyperfurion", providers
    block = text.split("[providers.hyperfurion]", 1)[0].rstrip().split("\n\n")[-1]
    comment = " ".join(line.lstrip("# ") for line in block.splitlines())
    assert "not on sale right now" in comment
    assert "existing subscribers only" in comment


@pytest.mark.parametrize("table", ["stt", "tts"])
def test_example_choices_put_the_hosted_service_last(table):
    text = EXAMPLE.read_text()
    body = text.split(f"\n[{table}]\n", 1)[1].split("\nprovider", 1)[0]
    choices = " ".join(line.lstrip("# ") for line in body.splitlines())
    assert choices.startswith("Choices: xai")
    names = re.findall(r"\b(xai|hyperfurion|openai|groq|deepgram|assemblyai|elevenlabs)\b", choices)
    assert names[-1] == "hyperfurion" and names.count("hyperfurion") == 1
    assert "existing subscribers only" in choices


# ── install.sh: system packages ──────────────────────────────────────

# Names checked against Fedora's package metadata (mdapi.fedoraproject.org,
# f43 and f44) on 2026-10-07. python3-venv is not among them: venv ships in
# python3-libs, which python3 pulls in.
FEDORA_PACKAGES = {
    "gcc", "portaudio-devel", "python3", "python3-devel", "python3-libs",
    "python3-pip", "python3-tkinter", "python3-virtualenv", "libsndfile",
    "libnotify", "wl-clipboard", "xclip",
}
# archlinux.org/packages, same day.
ARCH_PACKAGES = {
    "gcc", "portaudio", "python", "python-pip", "python-virtualenv", "tk",
    "libsndfile", "libnotify", "wl-clipboard", "xclip",
}
# Ubuntu 24.04 / Debian.
APT_PACKAGES = {
    "portaudio19-dev", "python3-dev", "python3-pip", "python3-venv", "python3-tk",
    "libsndfile1", "libnotify-bin", "wl-clipboard", "xclip", "build-essential", "gcc",
}

STRICT_MANAGER = r"""#!/bin/bash
# Like dnf5: one unknown name fails the whole transaction.
for arg in "$@"; do
    case "$arg" in -*|install|update) continue ;; esac
    case " $KNOWN " in
        *" $arg "*) ;;
        *) echo "No match for argument: $arg" >&2; exit 1 ;;
    esac
done
for arg in "$@"; do
    case "$arg" in -*|install|update) continue ;; esac
    echo "$arg" >> "$LOG"
done
"""


def _block(start: str, end: str) -> str:
    text = INSTALL_SH.read_text()
    return text[text.index(start):text.index(end)]


def _run_deps_step(tmp_path: Path, manager: str, known: set[str]) -> tuple[int, str, list[str]]:
    stubs = tmp_path / "bin"
    stubs.mkdir()
    (stubs / "sudo").write_text('#!/bin/bash\nexec "$@"\n')
    (stubs / manager).write_text(STRICT_MANAGER)
    for stub in stubs.iterdir():
        stub.chmod(0o755)
    script = tmp_path / "deps.sh"
    script.write_text(
        "set -euo pipefail\n"
        + _block("# ── System dependencies", "# ── uinput setup")
    )
    log = tmp_path / "installed.txt"
    result = subprocess.run(
        ["/bin/bash", str(script)],
        env={"PATH": str(stubs), "KNOWN": " ".join(sorted(known)), "LOG": str(log)},
        capture_output=True,
        text=True,
    )
    installed = log.read_text().split() if log.exists() else []
    return result.returncode, result.stdout + result.stderr, installed


@linux_only
def test_dnf_line_installs_on_fedora(tmp_path):
    code, output, installed = _run_deps_step(tmp_path, "dnf", FEDORA_PACKAGES)
    assert code == 0, output
    assert "python3-venv" not in installed
    # PyAudio and evdev have no Linux wheels: pip compiles them.
    assert "gcc" in installed
    assert {"portaudio-devel", "python3-devel"} <= set(installed)


@linux_only
def test_pacman_line_installs_on_arch(tmp_path):
    code, output, installed = _run_deps_step(tmp_path, "pacman", ARCH_PACKAGES)
    assert code == 0, output
    assert "gcc" in installed
    assert {"portaudio", "python", "tk"} <= set(installed)


@linux_only
def test_apt_line_is_unchanged_and_installs(tmp_path):
    code, output, installed = _run_deps_step(tmp_path, "apt-get", APT_PACKAGES)
    assert code == 0, output
    # python3-pip recommends build-essential, which apt installs by default.
    assert {"portaudio19-dev", "python3-dev", "python3-pip", "python3-venv"} <= set(installed)


# ── install.sh: VOICE_KEYBOARD_API_KEY ───────────────────────────────


def _config_step(tmp_path: Path) -> tuple[Path, dict]:
    """The installer's config step, runnable on its own: its helper functions
    and step 5, with a fake venv whose `voice-keyboard setup` only records
    that it ran."""
    venv_bin = tmp_path / "venv" / "bin"
    venv_bin.mkdir(parents=True)
    (venv_bin / "python").symlink_to(sys.executable)
    (venv_bin / "voice-keyboard").write_text(
        '#!/bin/sh\necho "$@" > "$SETUP_MARK"\n'
    )
    (venv_bin / "voice-keyboard").chmod(0o755)
    script = tmp_path / "config.sh"
    script.write_text(
        "set -euo pipefail\n"
        + _block("config_has_provider_api_key() {", "# ── System dependencies")
        + _block("# ── Config", "# ── systemd user service")
    )
    env = {
        "PATH": f"{Path(sys.executable).parent}:/usr/bin:/bin",
        "HOME": str(tmp_path),
        "PYTHONPATH": str(ROOT),
        "VENV_DIR": str(tmp_path / "venv"),
        "CONFIG_DIR": str(tmp_path / "config"),
        "SCRIPT_DIR": str(ROOT),
        "SETUP_MARK": str(tmp_path / "setup-ran"),
    }
    return script, env


@linux_only
def test_documented_unattended_install_writes_the_generic_key(tmp_path):
    # README: VOICE_KEYBOARD_STT_PROVIDER=xai VOICE_KEYBOARD_TTS_PROVIDER=xai
    #         VOICE_KEYBOARD_API_KEY="xai-..." ./install.sh
    script, env = _config_step(tmp_path)
    env.update(
        VOICE_KEYBOARD_STT_PROVIDER="xai",
        VOICE_KEYBOARD_TTS_PROVIDER="xai",
        VOICE_KEYBOARD_API_KEY="xai-from-the-environment",
    )
    result = subprocess.run(
        ["/bin/bash", str(script)], env=env, capture_output=True, text=True,
        start_new_session=True,  # no controlling terminal
    )
    assert result.returncode == 0, result.stdout + result.stderr
    config = tomllib.loads((tmp_path / "config" / "config.toml").read_text())
    assert config["providers"]["xai"]["api_key"] == "xai-from-the-environment"
    assert config["stt"]["provider"] == config["tts"]["provider"] == "xai"
    assert not (tmp_path / "setup-ran").exists()


# Run in a fresh, single-threaded Python: forking the (threaded) test
# process itself could deadlock the child.
PTY_RUNNER = r"""
import os, pty, sys
pid, fd = pty.fork()
if pid == 0:  # a new session, with the pty as its controlling terminal
    os.execv("/bin/bash", ["bash", sys.argv[1]])
chunks = []
while True:
    try:
        data = os.read(fd, 4096)
    except OSError:  # EIO: the terminal closed
        break
    if not data:
        break
    chunks.append(data)
_, status = os.waitpid(pid, 0)
sys.stdout.write(b"".join(chunks).decode(errors="replace"))
sys.exit(os.waitstatus_to_exitcode(status))
"""


def _run_in_terminal(script: Path, env: dict) -> tuple[int, str]:
    result = subprocess.run(
        [sys.executable, "-c", PTY_RUNNER, str(script)],
        env=env, capture_output=True, text=True, timeout=120,
    )
    return result.returncode, result.stdout + result.stderr


@linux_only
def test_terminal_install_with_only_the_generic_key_says_it_is_unused(tmp_path):
    # With no provider named, a terminal install runs the walkthrough, which
    # asks for the key itself (README says so). It must not drop the key
    # without a word.
    script, env = _config_step(tmp_path)
    env["VOICE_KEYBOARD_API_KEY"] = "xai-from-the-environment"
    code, output = _run_in_terminal(script, env)
    assert code == 0, output
    assert (tmp_path / "setup-ran").read_text().strip() == "setup"
    assert "VOICE_KEYBOARD_API_KEY is used only with VOICE_KEYBOARD_STT_PROVIDER" in output
    assert "xai-from-the-environment" not in (tmp_path / "config" / "config.toml").read_text()


# ── installer comments ───────────────────────────────────────────────


def test_windows_installer_header_says_a_checkout_needs_source():
    text = (ROOT / "packaging/windows/install-hyperfurion-vk.ps1").read_text()
    header = text.split("\nparam(", 1)[0]
    checkout = re.search(r"# From a checkout.*?\n#\s+(powershell [^\n]*)", header, re.S)
    assert checkout and "-Source" in checkout.group(1)
    # ...because without -Source it really installs the latest release.
    assert "if (-not $Version) { $Version = Get-LatestTag" in text


def test_build_downloads_does_not_call_the_cmd_pinned():
    text = (ROOT / "packaging/build-downloads.sh").read_text()
    assert "pinned" not in text
    assert "installs the latest release" in text


# ── relay/README.md ──────────────────────────────────────────────────


def test_relay_readme_says_not_on_sale_before_any_price():
    text = (ROOT / "relay/README.md").read_text()
    assert text.index("not on sale right now") < text.index("$5")
    tiers = text.split("## Tiers", 1)[1].split("\n## ", 1)[0]
    assert "not on sale right now" in tiers
    assert "## Selling subscriptions" not in text
    assert not re.search(r"\$\d+/mo\b", text), "a per-month price tag reads as an offer"
    assert "GitHub Sponsors" not in text
    # The operator documentation is still there.
    assert "hyperfurion-relay-admin issue" in text
    assert "STRIPE_WEBHOOK_SECRET" in text
