#!/usr/bin/env bash
# HyperFurion VK — macOS installer (BETA). Run from a repo checkout:
#   git clone https://github.com/liamghennigan/HyperFurion-VK
#   cd HyperFurion-VK && ./packaging/macos/install-macos.sh
set -euo pipefail

echo "=== HyperFurion VK macOS installer (beta) ==="
echo ""

REPO_ROOT="$(cd "$(dirname "$0")/../.." && pwd)"

if ! command -v brew >/dev/null 2>&1; then
    echo "ERROR: Homebrew is required (https://brew.sh) — portaudio comes from it." >&2
    exit 1
fi
if ! brew list portaudio >/dev/null 2>&1; then
    echo "Installing portaudio (microphone capture)…"
    brew install portaudio
fi

echo "Installing voice-keyboard (user site)…"
python3 -m pip install --user "$REPO_ROOT"

USER_BASE="$(python3 -m site --user-base)"
BIN="$USER_BASE/bin"
if [ ! -x "$BIN/voice-keyboard-daemon" ]; then
    echo "ERROR: $BIN/voice-keyboard-daemon not found after install." >&2
    exit 1
fi

CONFIG_DIR="${XDG_CONFIG_HOME:-$HOME/.config}/voice-keyboard"
if [ ! -f "$CONFIG_DIR/config.toml" ]; then
    mkdir -p "$CONFIG_DIR"
    cp "$REPO_ROOT/config.toml.example" "$CONFIG_DIR/config.toml"
    chmod 600 "$CONFIG_DIR/config.toml"
    echo "Wrote starter config: $CONFIG_DIR/config.toml (add your API key)"
fi

echo "Installing LaunchAgent…"
mkdir -p "$HOME/Library/LaunchAgents" "$HOME/Library/Logs"
PLIST="$HOME/Library/LaunchAgents/com.hyperfurion.voice-keyboard.plist"
sed -e "s|__BIN__|$BIN|g" -e "s|__HOME__|$HOME|g" \
    "$REPO_ROOT/packaging/macos/com.hyperfurion.voice-keyboard.plist" > "$PLIST"
launchctl bootout "gui/$(id -u)/com.hyperfurion.voice-keyboard" 2>/dev/null || true
launchctl bootstrap "gui/$(id -u)" "$PLIST"

cat <<'EOF'

=== Almost there — macOS needs two permissions ===

1. Accessibility (hotkeys + typing):
   System Settings → Privacy & Security → Accessibility
   → enable your Python 3 (the one at the path printed above).
2. Microphone:
   System Settings → Privacy & Security → Microphone → same binary.
   (macOS will also prompt on the first recording.)

Then restart the daemon:
   launchctl kickstart -k gui/$(id -u)/com.hyperfurion.voice-keyboard

Check:  voice-keyboard status      (add ~/.../bin to PATH if needed)
Logs:   ~/Library/Logs/voice-keyboard-daemon.log

macOS support is BETA: injection uses Quartz CGEvents (full Unicode —
better than the Linux ASCII limit), hotkeys use a listen-only event
tap. No GNOME-style overlay; you get notification-center toasts.
EOF
