#!/usr/bin/env bash
set -euo pipefail

echo "=== voice-keyboard installer ==="
echo ""

config_has_provider_api_key() {
    CONFIG_PATH="$1" PROVIDER_NAME="$2" python3 - <<'PY'
import os
import tomllib
from pathlib import Path

path = Path(os.environ["CONFIG_PATH"])
provider = os.environ["PROVIDER_NAME"]
try:
    with path.open("rb") as f:
        config = tomllib.load(f)
except FileNotFoundError:
    raise SystemExit(1)

key = str(config.get("providers", {}).get(provider, {}).get("api_key", "")).strip()
if provider == "xai" and not key:
    key = str(config.get("xai", {}).get("api_key", "")).strip()
placeholders = {
    "xai-your-api-key-here",
    "openai-your-api-key-here",
    "groq-your-api-key-here",
    "deepgram-your-api-key-here",
    "assemblyai-your-api-key-here",
    "elevenlabs-your-api-key-here",
}
raise SystemExit(0 if key and key not in placeholders else 1)
PY
}

config_has_required_api_keys() {
    config_has_provider_api_key "$1" "$2" && config_has_provider_api_key "$1" "$3"
}

write_provider_config() {
    CONFIG_PATH="$1" STT_PROVIDER_VALUE="$2" TTS_PROVIDER_VALUE="$3" STT_API_KEY_VALUE="$4" TTS_API_KEY_VALUE="$5" python3 - <<'PY'
import os
import re
from pathlib import Path

path = Path(os.environ["CONFIG_PATH"])
text = path.read_text()

def toml_quote(value: str) -> str:
    return '"' + value.replace("\\", "\\\\").replace('"', '\\"') + '"'

def set_section_value(src: str, section: str, key: str, value: str) -> str:
    section_header = f"[{section}]"
    header_re = re.compile(rf"(?m)^\s*\[{re.escape(section)}\]\s*$")
    match = header_re.search(src)
    line = f"{key} = {toml_quote(value)}"
    if not match:
        suffix = "" if src.endswith("\n") else "\n"
        return f"{src}{suffix}\n{section_header}\n{line}\n"

    next_header = re.search(r"(?m)^\s*\[[^\]]+\]\s*$", src[match.end():])
    section_end = match.end() + next_header.start() if next_header else len(src)
    section_text = src[match.end():section_end]
    key_re = re.compile(rf"(?m)^(\s*{re.escape(key)}\s*=\s*).*$")
    if key_re.search(section_text):
        section_text = key_re.sub(lambda item: f"{item.group(1)}{toml_quote(value)}", section_text, count=1)
    else:
        insertion = "" if section_text.startswith("\n") else "\n"
        section_text = f"{insertion}{line}{section_text}"
    return src[:match.end()] + section_text + src[section_end:]

stt_provider = os.environ["STT_PROVIDER_VALUE"]
tts_provider = os.environ["TTS_PROVIDER_VALUE"]
text = set_section_value(text, "stt", "provider", stt_provider)
text = set_section_value(text, "tts", "provider", tts_provider)

stt_key = os.environ["STT_API_KEY_VALUE"]
tts_key = os.environ["TTS_API_KEY_VALUE"]
if stt_key:
    text = set_section_value(text, f"providers.{stt_provider}", "api_key", stt_key)
if tts_key:
    text = set_section_value(text, f"providers.{tts_provider}", "api_key", tts_key)

path.write_text(text)
PY
}

provider_env_name() {
    case "$1" in
        xai) echo "XAI_API_KEY" ;;
        openai) echo "OPENAI_API_KEY" ;;
        groq) echo "GROQ_API_KEY" ;;
        deepgram) echo "DEEPGRAM_API_KEY" ;;
        assemblyai) echo "ASSEMBLYAI_API_KEY" ;;
        elevenlabs) echo "ELEVENLABS_API_KEY" ;;
        *) echo "" ;;
    esac
}

is_choice() {
    value="$1"
    choices="$2"
    for choice in $choices; do
        [ "$value" = "$choice" ] && return 0
    done
    return 1
}

prompt_provider() {
    label="$1"
    default_value="$2"
    choices="$3"
    env_value="$4"
    env_value="$(printf "%s" "$env_value" | tr '[:upper:]' '[:lower:]')"
    if [ -n "$env_value" ] && is_choice "$env_value" "$choices"; then
        echo "$env_value"
        return
    fi
    if [ ! -r /dev/tty ] || [ ! -w /dev/tty ]; then
        echo "$default_value"
        return
    fi
    printf "%s provider [%s] (default: %s): " "$label" "$choices" "$default_value" > /dev/tty
    IFS= read -r answer < /dev/tty || answer=""
    answer="$(printf "%s" "${answer:-$default_value}" | tr '[:upper:]' '[:lower:]')"
    if is_choice "$answer" "$choices"; then
        echo "$answer"
    else
        echo "Unknown provider '$answer'; using $default_value." > /dev/tty
        echo "$default_value"
    fi
}

provider_env_api_key() {
    provider="$1"
    env_name="$(provider_env_name "$provider")"
    value=""
    if [ -n "$env_name" ]; then
        eval "value=\${$env_name:-}"
    fi
    if [ -z "$value" ]; then
        value="${VOICE_KEYBOARD_API_KEY:-}"
    fi
    echo "$value"
}

prompt_api_key() {
    provider="$1"
    current_value="$(provider_env_api_key "$provider")"
    if [ -n "$current_value" ]; then
        echo "$current_value"
        return
    fi
    if [ ! -r /dev/tty ] || [ ! -w /dev/tty ]; then
        echo ""
        return
    fi
    printf "Enter %s API key (leave blank to skip for now): " "$provider" > /dev/tty
    IFS= read -r -s answer < /dev/tty || answer=""
    printf "\n" > /dev/tty
    echo "$answer"
}

# ── System dependencies ──────────────────────────────────────────────
echo "[1/6] Installing system dependencies..."
if command -v apt-get &>/dev/null; then
    sudo apt-get update -qq
    sudo apt-get install -y portaudio19-dev python3-dev python3-pip python3-venv python3-tk libsndfile1 libnotify-bin wl-clipboard xclip
elif command -v dnf &>/dev/null; then
    sudo dnf install -y portaudio-devel python3-devel python3-pip python3-venv python3-tkinter libsndfile libnotify wl-clipboard xclip
elif command -v pacman &>/dev/null; then
    sudo pacman -S --noconfirm portaudio python-pip python-virtualenv tk libsndfile libnotify wl-clipboard xclip
else
    echo "WARNING: Unrecognized package manager. Install portaudio, python3-venv, Python dev headers, Tkinter, and notify-send manually."
fi

# ── uinput setup ─────────────────────────────────────────────────────
echo "[2/6] Configuring uinput..."
NEEDS_RELOGIN=0

if ! lsmod | grep -q uinput; then
    sudo modprobe uinput
fi

UDEV_FILE="/etc/modules-load.d/uinput.conf"
if [ ! -f "$UDEV_FILE" ] || ! grep -qx "uinput" "$UDEV_FILE" 2>/dev/null; then
    echo "uinput" | sudo tee "$UDEV_FILE" > /dev/null
fi

UDEV_RULE='KERNEL=="uinput", GROUP="input", MODE="0660"'
UDEV_FILE="/etc/udev/rules.d/99-uinput.rules"
if [ ! -f "$UDEV_FILE" ] || ! grep -qF "$UDEV_RULE" "$UDEV_FILE" 2>/dev/null; then
    echo "$UDEV_RULE" | sudo tee "$UDEV_FILE" > /dev/null
    sudo udevadm control --reload-rules
    sudo udevadm trigger
fi

if ! groups "$USER" | grep -q '\binput\b'; then
    echo "Adding $USER to 'input' group..."
    sudo usermod -a -G input "$USER"
    NEEDS_RELOGIN=1
fi

if ! id -nG | grep -q '\binput\b'; then
    NEEDS_RELOGIN=1
    if command -v setfacl &>/dev/null && [ -e /dev/uinput ]; then
        sudo setfacl -m "u:$USER:rw" /dev/uinput || true
        echo "Granted current session access to /dev/uinput."
    fi
    echo "NOTE: Log out and back in for 'input' group access to apply permanently."
fi

# ── Python package (venv) ──────────────────────────────────────────────
echo "[3/6] Installing Python package into venv..."
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
VENV_DIR="${VOICE_KEYBOARD_VENV:-$HOME/.local/share/voice-keyboard-venv}"
python3 -m venv "$VENV_DIR"
"$VENV_DIR/bin/pip" install --upgrade pip
"$VENV_DIR/bin/pip" install "$SCRIPT_DIR"

# Expose the console scripts on PATH for the user and for systemd.
BIN_DIR="$HOME/.local/bin"
mkdir -p "$BIN_DIR"
for script in voice-keyboard voice-keyboard-daemon; do
    ln -sf "$VENV_DIR/bin/$script" "$BIN_DIR/$script"
done

DAEMON_BIN="$BIN_DIR/voice-keyboard-daemon"
DAEMON_BIN="${VOICE_KEYBOARD_BIN:-$DAEMON_BIN}"

# ── GNOME Shell overlay extension ─────────────────────────────────────
echo "[4/6] Installing GNOME Shell overlay extension..."
OVERLAY_UUID="voice-keyboard-overlay@liam-hennigan"
OVERLAY_SRC="$SCRIPT_DIR/gnome-shell/$OVERLAY_UUID"
OVERLAY_DEST="$HOME/.local/share/gnome-shell/extensions/$OVERLAY_UUID"
if [ -d "$OVERLAY_SRC" ]; then
    mkdir -p "$OVERLAY_DEST"
    cp "$OVERLAY_SRC/metadata.json" "$OVERLAY_DEST/metadata.json"
    cp "$OVERLAY_SRC/extension.js" "$OVERLAY_DEST/extension.js"
    python3 - "$OVERLAY_UUID" <<'PY' || true
import ast
import subprocess
import sys

uuid = sys.argv[1]
try:
    current = subprocess.check_output(
        ["gsettings", "get", "org.gnome.shell", "enabled-extensions"],
        text=True,
    ).strip()
    enabled = ast.literal_eval(current)
    if uuid not in enabled:
        enabled.append(uuid)
        subprocess.check_call(
            ["gsettings", "set", "org.gnome.shell", "enabled-extensions", repr(enabled)]
        )
except Exception:
    raise SystemExit(0)
PY
    echo "Installed overlay extension: $OVERLAY_UUID"
    echo "NOTE: GNOME Wayland loads newly installed extensions after log out/in."
else
    echo "Overlay extension source not found at $OVERLAY_SRC; skipping."
fi

# ── Config ────────────────────────────────────────────────────────────
echo "[5/6] Setting up config..."
CONFIG_DIR="${XDG_CONFIG_HOME:-$HOME/.config}/voice-keyboard"
mkdir -p -m 700 "$CONFIG_DIR"
if [ ! -f "$CONFIG_DIR/config.toml" ]; then
    cp "$SCRIPT_DIR/config.toml.example" "$CONFIG_DIR/config.toml"
    chmod 600 "$CONFIG_DIR/config.toml"
    echo "Created $CONFIG_DIR/config.toml"
else
    echo "Config already exists at $CONFIG_DIR/config.toml"
fi

STT_PROVIDER="$(prompt_provider "Speech-to-text" "xai" "xai openai groq deepgram assemblyai" "${VOICE_KEYBOARD_STT_PROVIDER:-}")"
TTS_PROVIDER="$(prompt_provider "Text-to-speech" "xai" "xai openai elevenlabs" "${VOICE_KEYBOARD_TTS_PROVIDER:-}")"
STT_API_KEY=""
TTS_API_KEY=""

if ! config_has_provider_api_key "$CONFIG_DIR/config.toml" "$STT_PROVIDER"; then
    STT_API_KEY="$(prompt_api_key "$STT_PROVIDER")"
fi
if ! config_has_provider_api_key "$CONFIG_DIR/config.toml" "$TTS_PROVIDER"; then
    if [ "$TTS_PROVIDER" = "$STT_PROVIDER" ] && [ -n "$STT_API_KEY" ]; then
        TTS_API_KEY="$STT_API_KEY"
    else
        TTS_API_KEY="$(prompt_api_key "$TTS_PROVIDER")"
    fi
fi

write_provider_config "$CONFIG_DIR/config.toml" "$STT_PROVIDER" "$TTS_PROVIDER" "$STT_API_KEY" "$TTS_API_KEY"
chmod 600 "$CONFIG_DIR/config.toml"
echo "Configured STT provider: $STT_PROVIDER"
echo "Configured TTS provider: $TTS_PROVIDER"
if ! config_has_required_api_keys "$CONFIG_DIR/config.toml" "$STT_PROVIDER" "$TTS_PROVIDER"; then
    echo "No complete API key setup saved; daemon will not be started yet."
fi

# ── systemd user service ──────────────────────────────────────────────
echo "[6/6] Installing systemd user service..."
SYSTEMD_DIR="${XDG_CONFIG_HOME:-$HOME/.config}/systemd/user"
mkdir -p "$SYSTEMD_DIR"

# %h expands to the user's home directory inside systemd unit files, so the
# symlinked daemon path (under ~/.local/bin) resolves correctly for the user.
cat > "$SYSTEMD_DIR/voice-keyboard-daemon.service" << SERVICE
[Unit]
Description=Voice Keyboard Daemon
After=default.target

[Service]
Type=simple
ExecStart=${DAEMON_BIN}
Restart=on-failure
RestartSec=5
Environment=PYTHONUNBUFFERED=1

[Install]
WantedBy=default.target
SERVICE

systemctl --user daemon-reload
systemctl --user enable voice-keyboard-daemon.service

if ! config_has_required_api_keys "$CONFIG_DIR/config.toml" "$STT_PROVIDER" "$TTS_PROVIDER"; then
    echo "Service enabled but not started: edit $CONFIG_DIR/config.toml with the selected provider API key(s) first."
elif [ "$NEEDS_RELOGIN" -eq 1 ]; then
    echo "Service enabled but not started: log out and back in so 'input' group access applies."
else
    systemctl --user start voice-keyboard-daemon.service
    echo "Daemon started."
fi

echo ""
echo "=== Installation complete ==="
echo ""
echo "Use it:"
echo "  Tap Ctrl+Alt+V          # start recording; tap again to stop and type"
echo "  Hold Ctrl+Alt+V         # record until you release it"
echo "  voice-keyboard tts      # reads selected text aloud"
echo ""
echo "Optional shortcuts:"
echo "  Ctrl+Alt+T → $BIN_DIR/voice-keyboard tts"
echo ""
echo "  Check daemon status: systemctl --user status voice-keyboard-daemon"
echo "  Manual test: voice-keyboard start && sleep 3 && voice-keyboard stop"
