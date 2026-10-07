#!/usr/bin/env bash
# HyperFurion VK: macOS dev setup from a checkout. One command:
#
#   ./scripts/macos-dev-setup.sh          Homebrew deps, .venv (editable install),
#                                         config, Mac hotkeys, permission prompts
#   ./scripts/macos-dev-setup.sh --run    the same, then run the daemon here
#
# More:
#   --run-only        just run the daemon in this terminal (log teed to the log file)
#   --agent           install a login agent (launchd) that runs THIS checkout
#   --remove-agent    stop and remove the login agent
#   --local-whisper   offline speech: whisper.cpp (Metal) + a model + a login
#                     agent serving it on 127.0.0.1:2022; points the config at it
#   --bug-report      write a bug-report file to ~/Desktop (doctor, permissions,
#                     versions, the last 400 log lines; API keys masked)
#
# Safe to re-run: every step only does what is missing. bash 3.2 (the one
# macOS ships) is enough. Nothing here needs sudo.
set -euo pipefail

REPO="$(cd "$(dirname "$0")/.." && pwd)"
VENV="$REPO/.venv"
LOG="$HOME/Library/Logs/voice-keyboard-daemon.log"
LABEL="com.hyperfurion.voice-keyboard"
PLIST="$HOME/Library/LaunchAgents/$LABEL.plist"
WHISPER_LABEL="com.hyperfurion.whisper-server"
WHISPER_PLIST="$HOME/Library/LaunchAgents/$WHISPER_LABEL.plist"
WHISPER_PORT=2022
MODELS="$HOME/Library/Application Support/voice-keyboard/models"
WHISPER_MODEL="ggml-large-v3-turbo-q5_0.bin"
CONFIG="${XDG_CONFIG_HOME:-$HOME/.config}/voice-keyboard/config.toml"
GUI="gui/$(id -u)"

bold() { printf '\n\033[1m%s\033[0m\n' "$*"; }
note() { printf '  %s\n' "$*"; }
die() { printf '\nERROR: %s\n' "$*" >&2; exit 1; }

case "${1:-}" in
    -h|--help) sed -n '2,20p' "$0"; exit 0 ;;
esac
[ "$(uname -s)" = "Darwin" ] || die "this is the macOS setup; on Linux run ./install.sh"

ensure_brew() {
    if ! command -v brew >/dev/null 2>&1; then
        for candidate in /opt/homebrew/bin/brew /usr/local/bin/brew; do
            if [ -x "$candidate" ]; then
                eval "$("$candidate" shellenv)"
                break
            fi
        done
    fi
    command -v brew >/dev/null 2>&1 || die "Homebrew is needed (https://brew.sh). Install it with:
  /bin/bash -c \"\$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)\"
then run this script again."
}

brew_install() {  # brew_install FORMULA: only when missing
    if ! brew list --formula "$1" >/dev/null 2>&1; then
        note "brew install $1"
        brew install "$1"
    fi
}

pick_python() {
    # Never /usr/bin/python3: the Command Line Tools one is 3.9 (we need 3.11+).
    local prefix version
    prefix="$(brew --prefix)"
    for version in 3.12 3.13 3.11; do
        if [ -x "$prefix/bin/python$version" ]; then
            PYTHON="$prefix/bin/python$version"
            return
        fi
    done
    brew_install python@3.12
    PYTHON="$prefix/bin/python3.12"
    [ -x "$PYTHON" ] || die "python3.12 not found after brew install python@3.12"
}

setup_venv() {
    bold "1/5  Homebrew packages (PortAudio, Python)"
    ensure_brew
    brew_install portaudio
    pick_python
    note "Python: $PYTHON"

    bold "2/5  Virtualenv at $VENV (editable install of this checkout)"
    if [ -x "$VENV/bin/python" ] && ! "$VENV/bin/python" -c 'import sys; sys.exit(sys.version_info < (3, 11))'; then
        note "the existing .venv is older than Python 3.11: recreating it"
        rm -rf "$VENV"
    fi
    [ -x "$VENV/bin/python" ] || "$PYTHON" -m venv "$VENV"
    # PyAudio builds from source on macOS; point it at Homebrew's PortAudio.
    export CFLAGS="-I$(brew --prefix portaudio)/include ${CFLAGS:-}"
    export LDFLAGS="-L$(brew --prefix portaudio)/lib ${LDFLAGS:-}"
    "$VENV/bin/python" -m pip install --quiet --upgrade pip
    "$VENV/bin/python" -m pip install --quiet -e "$REPO[dev]" ruff
    note "installed: $("$VENV/bin/python" -c 'import importlib.metadata as m; print("voice-keyboard", m.version("voice-keyboard"))')"
}

setup_config() {
    bold "3/5  Settings ($CONFIG)"
    if [ ! -f "$CONFIG" ]; then
        if [ -t 0 ]; then
            note "No settings yet: starting the walkthrough (speech provider, hotkey, Kai)."
            "$VENV/bin/voice-keyboard" setup || note "(walkthrough skipped; run: $VENV/bin/voice-keyboard setup)"
        else
            note "No settings yet. Run: $VENV/bin/voice-keyboard setup"
        fi
    else
        note "keeping your settings (change them with: voice-keyboard setup)"
    fi
    "$VENV/bin/python" -m voice_keyboard.macos.devsetup "$CONFIG" || true
}

setup_permissions() {
    bold "4/5  Permissions"
    note "macOS asks about the app that runs HyperFurion VK: from here, this terminal app."
    note "Click Allow / Open System Settings on each prompt, turn the switch on, then come back."
    "$VENV/bin/python" -m voice_keyboard.macos.permissions --request || true
    cat <<'EOF'

  The three switches (System Settings → Privacy & Security):
    • Accessibility     typing, reading the focused field, the hotkey
    • Input Monitoring  the hotkey (only if Accessibility alone isn't enough)
    • Microphone        recording (macOS asks on the first recording)
  Open them directly:
    open "x-apple.systempreferences:com.apple.preference.security?Privacy_Accessibility"
    open "x-apple.systempreferences:com.apple.preference.security?Privacy_ListenEvent"
    open "x-apple.systempreferences:com.apple.preference.security?Privacy_Microphone"
  After turning a switch on, quit and reopen the terminal app (macOS applies
  most grants to new processes only).
EOF
}

next_steps() {
    bold "5/5  Ready"
    cat <<EOF
  Run the daemon in this terminal (Ctrl+C stops it):
    ./scripts/macos-dev-setup.sh --run-only
  In another tab:
    source .venv/bin/activate
    voice-keyboard doctor        # every check, with the fix for each
    voice-keyboard status        # idle / recording
  Then click into TextEdit and press Control+Option+V (tap to start/stop,
  or hold and talk). Kai: hold Right Command. Read aloud: select text,
  press Control+Option+R.
  Dogfood checklist and bug reports: MACOS.md ("Dogfooding").
EOF
}

agent_loaded() { launchctl print "$GUI/$LABEL" >/dev/null 2>&1; }

run_foreground() {
    [ -x "$VENV/bin/voice-keyboard-daemon" ] || die "no .venv yet: run ./scripts/macos-dev-setup.sh first"
    if agent_loaded; then
        die "the login agent is running (two daemons would fight over the socket).
  Stop it first: ./scripts/macos-dev-setup.sh --remove-agent"
    fi
    mkdir -p "$(dirname "$LOG")"
    bold "HyperFurion VK daemon (Ctrl+C to stop) — also logging to $LOG"
    printf '\n=== %s foreground run ===\n' "$(date)" >> "$LOG"
    PYTHONUNBUFFERED=1 "$VENV/bin/voice-keyboard-daemon" 2>&1 | tee -a "$LOG"
}

base_python() {
    "$VENV/bin/python" -c 'import os, sys; print(os.path.realpath(getattr(sys, "_base_executable", sys.executable)))'
}

install_agent() {
    [ -x "$VENV/bin/voice-keyboard-daemon" ] || die "no .venv yet: run ./scripts/macos-dev-setup.sh first"
    ensure_brew
    mkdir -p "$(dirname "$PLIST")" "$(dirname "$LOG")"
    cat > "$PLIST" <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key><string>$LABEL</string>
  <key>ProgramArguments</key>
  <array><string>$VENV/bin/voice-keyboard-daemon</string></array>
  <key>EnvironmentVariables</key>
  <dict>
    <key>PYTHONUNBUFFERED</key><string>1</string>
    <key>PATH</key><string>/usr/bin:/bin:/usr/sbin:/sbin:$(brew --prefix)/bin</string>
  </dict>
  <key>RunAtLoad</key><true/>
  <!-- Restart after a crash, not after "voice-keyboard quit". -->
  <key>KeepAlive</key><dict><key>SuccessfulExit</key><false/></dict>
  <key>ThrottleInterval</key><integer>10</integer>
  <key>ProcessType</key><string>Interactive</string>
  <key>LimitLoadToSessionType</key><string>Aqua</string>
  <key>StandardOutPath</key><string>$LOG</string>
  <key>StandardErrorPath</key><string>$LOG</string>
</dict>
</plist>
EOF
    launchctl bootout "$GUI/$LABEL" 2>/dev/null || true
    launchctl bootstrap "$GUI" "$PLIST"
    bold "Login agent installed: $PLIST"
    cat <<EOF
  It runs $VENV/bin/voice-keyboard-daemon at login, as Python, NOT as your
  terminal: macOS needs its own Accessibility, Input Monitoring and Microphone
  switches for it. The interpreter is:
    $(base_python)
  In System Settings it may be listed as "Python" or "python3.x"; if it isn't
  there, click + and press Command+Shift+G to paste that path.
  If the microphone stays silent under the agent (NO SIGNAL), run the daemon
  from the terminal instead (--remove-agent, then --run-only): MACOS.md explains.
  Restart:  launchctl kickstart -k $GUI/$LABEL
  Logs:     tail -f "$LOG"
EOF
}

remove_agent() {
    launchctl bootout "$GUI/$LABEL" 2>/dev/null || true
    rm -f "$PLIST"
    note "login agent removed"
}

local_whisper() {
    ensure_brew
    [ -x "$VENV/bin/python" ] || die "no .venv yet: run ./scripts/macos-dev-setup.sh first"
    bold "Offline speech: whisper.cpp with Metal"
    brew_install whisper.cpp
    local server
    server="$(command -v whisper-server || true)"
    [ -n "$server" ] || die "whisper-server not found after brew install whisper.cpp"
    mkdir -p "$MODELS"
    if [ ! -s "$MODELS/$WHISPER_MODEL" ]; then
        note "downloading $WHISPER_MODEL (~574 MB) into $MODELS"
        curl -L --fail --continue-at - -o "$MODELS/$WHISPER_MODEL.part" \
            "https://huggingface.co/ggerganov/whisper.cpp/resolve/main/$WHISPER_MODEL"
        mv "$MODELS/$WHISPER_MODEL.part" "$MODELS/$WHISPER_MODEL"
    fi
    mkdir -p "$(dirname "$WHISPER_PLIST")" "$(dirname "$LOG")"
    cat > "$WHISPER_PLIST" <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key><string>$WHISPER_LABEL</string>
  <key>ProgramArguments</key>
  <array>
    <string>$server</string>
    <string>--host</string><string>127.0.0.1</string>
    <string>--port</string><string>$WHISPER_PORT</string>
    <string>--model</string><string>$MODELS/$WHISPER_MODEL</string>
    <string>--inference-path</string><string>/v1/audio/transcriptions</string>
    <string>--language</string><string>auto</string>
  </array>
  <key>RunAtLoad</key><true/>
  <key>KeepAlive</key><true/>
  <key>StandardOutPath</key><string>$HOME/Library/Logs/whisper-server.log</string>
  <key>StandardErrorPath</key><string>$HOME/Library/Logs/whisper-server.log</string>
</dict>
</plist>
EOF
    launchctl bootout "$GUI/$WHISPER_LABEL" 2>/dev/null || true
    launchctl bootstrap "$GUI" "$WHISPER_PLIST"
    "$VENV/bin/python" -m voice_keyboard.macos.devsetup --local-speech "http://127.0.0.1:$WHISPER_PORT/v1" "$CONFIG"
    cat <<EOF
  whisper-server runs at login on 127.0.0.1:$WHISPER_PORT (log: ~/Library/Logs/whisper-server.log).
  Check it (after the model loads, a few seconds):
    curl -s http://127.0.0.1:$WHISPER_PORT/v1/audio/transcriptions -F file=@some.wav -F response_format=json
  Restart the daemon to use it. Text-to-speech keeps its provider (MACOS.md
  has an all-local option with mlx-audio and Kokoro).
EOF
}

bug_report() {
    local out
    out="$HOME/Desktop/hfvk-bug-report-$(date +%Y%m%d-%H%M%S).txt"
    {
        echo "== HyperFurion VK bug report, $(date)"
        echo "== system"; sw_vers 2>/dev/null; uname -m
        echo "== checkout"; git -C "$REPO" log -1 --format='%h %s (%cd)' 2>/dev/null || true
        git -C "$REPO" status --short 2>/dev/null | head -20 || true
        if [ -x "$VENV/bin/python" ]; then
            echo "== python"; "$VENV/bin/python" --version
            "$VENV/bin/python" -m pip list 2>/dev/null | grep -iE '^(voice-keyboard|pyobjc-core|pyobjc-framework-(quartz|applicationservices)|pyaudio|sounddevice|numpy) ' || true
            echo "== voice-keyboard doctor"; "$VENV/bin/voice-keyboard" doctor 2>&1 || true
            echo "== permissions (as seen from this terminal)"; "$VENV/bin/python" -m voice_keyboard.macos.permissions 2>&1 || true
            echo "== status"; "$VENV/bin/voice-keyboard" status 2>&1 || true
        fi
        echo "== login agent"; launchctl print "$GUI/$LABEL" 2>/dev/null | grep -E 'state|pid|last exit' || echo "not loaded"
        echo "== last 400 log lines ($LOG)"
        tail -n 400 "$LOG" 2>/dev/null || echo "(no log yet)"
    } 2>&1 | sed -E 's/(xai-|sk-|gsk_|hfk_|hfk-|dg_|el_)[A-Za-z0-9_-]{6,}/\1[masked]/g' > "$out"
    bold "Wrote $out"
    note "It includes recent log lines, which can contain what you dictated: read it before sharing."
}

case "${1:-}" in
    "") setup_venv; setup_config; setup_permissions; next_steps ;;
    --run) setup_venv; setup_config; setup_permissions; next_steps; run_foreground ;;
    --run-only) run_foreground ;;
    --agent) install_agent ;;
    --remove-agent) remove_agent ;;
    --local-whisper) local_whisper ;;
    --bug-report) bug_report ;;
    *) die "unknown option $1 (see --help)" ;;
esac
