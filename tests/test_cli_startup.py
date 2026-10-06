"""Every hotkey press on GNOME runs the CLI: it must start without the
speech clients' network stacks (they cost ~130 ms to import)."""

import subprocess
import sys


def test_the_cli_imports_no_network_stack() -> None:
    probe = (
        "import sys, voice_keyboard.client;"
        "heavy = [m for m in ('requests', 'websockets', 'urllib3', 'voice_keyboard.stt', 'voice_keyboard.tts', 'importlib.metadata')"
        " if m in sys.modules];"
        "print(','.join(heavy))"
    )
    out = subprocess.run([sys.executable, "-c", probe], capture_output=True, text=True, check=True)
    assert out.stdout.strip() == ""


def test_version_still_answers() -> None:
    out = subprocess.run([sys.executable, "-m", "voice_keyboard.client", "--version"],
                         capture_output=True, text=True, check=True)
    assert out.stdout.startswith("voice-keyboard ")
