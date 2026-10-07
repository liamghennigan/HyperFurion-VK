#!/usr/bin/env bash
# HyperFurion VK — macOS installer (BETA). Run from a repo checkout:
#   git clone https://github.com/liamghennigan/HyperFurion-VK
#   cd HyperFurion-VK && ./packaging/macos/install-macos.sh
#
# Installs into a virtualenv (Homebrew's Python refuses `pip install --user`,
# and the Command Line Tools' python3 is 3.9, too old), puts voice-keyboard
# on ~/.local/bin, and starts the daemon at login with a launchd agent.
# Hacking on the code instead? scripts/macos-dev-setup.sh (editable install,
# run in a terminal). See MACOS.md.
set -euo pipefail

echo "=== HyperFurion VK macOS installer (beta) ==="

REPO_ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
VENV="${VOICE_KEYBOARD_VENV:-$HOME/.local/share/voice-keyboard-venv}"
BIN_DIR="$HOME/.local/bin"
LABEL="com.hyperfurion.voice-keyboard"
PLIST="$HOME/Library/LaunchAgents/$LABEL.plist"
CONFIG_DIR="${XDG_CONFIG_HOME:-$HOME/.config}/voice-keyboard"

if ! command -v brew >/dev/null 2>&1; then
    for candidate in /opt/homebrew/bin/brew /usr/local/bin/brew; do
        [ -x "$candidate" ] && eval "$("$candidate" shellenv)" && break
    done
fi
if ! command -v brew >/dev/null 2>&1; then
    echo "ERROR: Homebrew is required (https://brew.sh): PortAudio and Python come from it." >&2
    exit 1
fi
for formula in portaudio python@3.12; do
    if ! brew list --formula "$formula" >/dev/null 2>&1; then
        echo "Installing $formula…"
        brew install "$formula"
    fi
done
PYTHON="$(brew --prefix)/bin/python3.12"

echo "Installing voice-keyboard into $VENV…"
[ -x "$VENV/bin/python" ] || "$PYTHON" -m venv "$VENV"
export CFLAGS="-I$(brew --prefix portaudio)/include ${CFLAGS:-}"
export LDFLAGS="-L$(brew --prefix portaudio)/lib ${LDFLAGS:-}"
"$VENV/bin/python" -m pip install --quiet --upgrade pip
"$VENV/bin/python" -m pip install --quiet --upgrade "$REPO_ROOT"

mkdir -p "$BIN_DIR"
ln -sf "$VENV/bin/voice-keyboard" "$BIN_DIR/voice-keyboard"
ln -sf "$VENV/bin/voice-keyboard-daemon" "$BIN_DIR/voice-keyboard-daemon"

if [ ! -f "$CONFIG_DIR/config.toml" ]; then
    if [ -t 0 ]; then
        "$VENV/bin/voice-keyboard" setup || true
    fi
    if [ ! -f "$CONFIG_DIR/config.toml" ]; then
        mkdir -p "$CONFIG_DIR"
        cp "$REPO_ROOT/config.toml.example" "$CONFIG_DIR/config.toml"
        chmod 600 "$CONFIG_DIR/config.toml"
        echo "Wrote starter config: $CONFIG_DIR/config.toml (add your API key, or run voice-keyboard setup)"
    fi
fi
# Kai on Right Command (MacBooks have no Right Control), read-aloud on
# Control+Option+R — only where you haven't chosen something else.
"$VENV/bin/python" -m voice_keyboard.macos.devsetup "$CONFIG_DIR/config.toml" || true

echo "Installing the login agent…"
mkdir -p "$HOME/Library/LaunchAgents" "$HOME/Library/Logs"
sed -e "s|__BIN__|$VENV/bin|g" -e "s|__HOME__|$HOME|g" -e "s|__BREW__|$(brew --prefix)|g" \
    "$REPO_ROOT/packaging/macos/com.hyperfurion.voice-keyboard.plist" > "$PLIST"
launchctl bootout "gui/$(id -u)/$LABEL" 2>/dev/null || true
launchctl bootstrap "gui/$(id -u)" "$PLIST"

INTERPRETER="$("$VENV/bin/python" -c 'import os, sys; print(os.path.realpath(getattr(sys, "_base_executable", sys.executable)))')"
cat <<EOF

=== Almost there: macOS needs three permissions ===

The daemon runs at login as Python, so the switches are for Python:
  $INTERPRETER
(listed as "Python" or "python3.12"; if it's missing, click + and press
Command+Shift+G to paste that path). In System Settings → Privacy & Security:

  1. Accessibility     typing, the focused field, the hotkey
  2. Input Monitoring  the hotkey
  3. Microphone        recording (macOS asks on the first one)

Then restart the daemon:
  launchctl kickstart -k gui/\$(id -u)/$LABEL

Check:  voice-keyboard doctor     (open a new terminal if it isn't found:
                                   ~/.local/bin must be on your PATH)
Logs:   ~/Library/Logs/voice-keyboard-daemon.log

If recordings stay silent (NO SIGNAL) under the login agent, run the daemon
from a terminal instead; MACOS.md explains why and what comes next.
EOF
