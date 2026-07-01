#!/usr/bin/env bash
set -euo pipefail

echo "=== voice-keyboard installer ==="
echo ""

config_has_real_api_key() {
    CONFIG_PATH="$1" python3 - <<'PY'
import os
import tomllib
from pathlib import Path

path = Path(os.environ["CONFIG_PATH"])
try:
    with path.open("rb") as f:
        config = tomllib.load(f)
except FileNotFoundError:
    raise SystemExit(1)

key = str(config.get("xai", {}).get("api_key", "")).strip()
raise SystemExit(0 if key and key != "xai-your-api-key-here" else 1)
PY
}

write_api_key() {
    CONFIG_PATH="$1" API_KEY_VALUE="$2" python3 - <<'PY'
import os
import re
from pathlib import Path

path = Path(os.environ["CONFIG_PATH"])
api_key = os.environ["API_KEY_VALUE"]
escaped = api_key.replace("\\", "\\\\").replace('"', '\\"')
text = path.read_text()

pattern = re.compile(r'(?m)^(\s*api_key\s*=\s*)".*"')
if pattern.search(text):
    text = pattern.sub(lambda match: f'{match.group(1)}"{escaped}"', text, count=1)
elif re.search(r'(?m)^\s*\[xai\]\s*$', text):
    text = re.sub(
        r'(?m)^(\s*\[xai\]\s*)$',
        lambda match: f'{match.group(1)}\napi_key = "{escaped}"',
        text,
        count=1,
    )
else:
    text = f'[xai]\napi_key = "{escaped}"\n\n{text}'

path.write_text(text)
PY
}

# ── System dependencies ──────────────────────────────────────────────
echo "[1/5] Installing system dependencies..."
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
echo "[2/5] Configuring uinput..."
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
echo "[3/5] Installing Python package into venv..."
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
    echo "Created $CONFIG_DIR/config.toml — edit it to add your xAI API key."
else
    echo "Config already exists at $CONFIG_DIR/config.toml"
fi

if ! config_has_real_api_key "$CONFIG_DIR/config.toml"; then
    API_KEY="${VOICE_KEYBOARD_API_KEY:-}"
    if [ -z "$API_KEY" ] && [ -t 0 ]; then
        printf "Enter xAI API key (leave blank to skip for now): "
        IFS= read -r -s API_KEY
        printf "\n"
    fi

    if [ -n "$API_KEY" ]; then
        write_api_key "$CONFIG_DIR/config.toml" "$API_KEY"
        chmod 600 "$CONFIG_DIR/config.toml"
        echo "Saved API key to $CONFIG_DIR/config.toml"
    else
        echo "No API key saved; daemon will not be started yet."
    fi
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

if ! config_has_real_api_key "$CONFIG_DIR/config.toml"; then
    echo "Service enabled but not started: edit $CONFIG_DIR/config.toml with your xAI API key first."
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
echo "  Tap Ctrl+Space          # start recording; tap again to stop and type"
echo "  Hold Ctrl+Space         # record until you release it"
echo "  voice-keyboard tts      # reads selected text aloud"
echo ""
echo "Optional shortcuts:"
echo "  Ctrl+Alt+T → $BIN_DIR/voice-keyboard tts"
echo ""
echo "  Check daemon status: systemctl --user status voice-keyboard-daemon"
echo "  Manual test: voice-keyboard start && sleep 3 && voice-keyboard stop"
