# HyperFurion VK

Universal Linux voice keyboard using the [xAI API](https://x.ai/api) for speech-to-text and text-to-speech.

## How it works

- **Voice input**: Tap Ctrl+Space once to start, tap it again to stop, or hold Ctrl+Space to record only while held — text is typed into the focused app via a virtual keyboard (uinput).
- **Text-to-speech**: Select text in any app, press Ctrl+Alt+T — the daemon reads it aloud using xAI TTS.

All intelligence runs in the xAI cloud. The local daemon handles audio capture, streaming, and keyboard injection.

## Requirements

- Linux with uinput kernel module
- Python 3.11+
- PortAudio (for PyAudio) and libsndfile (for `sounddevice` TTS playback)
- `notify-send` for desktop status notifications
- GNOME Shell 50 on Wayland for the compositor-native near-field overlay
- [Optional] `pygame` for a fallback TTS playback backend (install with `pip install voice-keyboard[pygame]`)
- xAI API key

## Quick install

```bash
chmod +x install.sh
./install.sh
```

The installer:
1. Installs system dependencies (portaudio, libsndfile, Python venv/dev headers,
   and primary-selection helpers for TTS)
2. Configures uinput (udev rule, kernel module, group membership)
3. Installs the Python package into an isolated venv at `~/.local/share/voice-keyboard-venv` and symlinks the `voice-keyboard` / `voice-keyboard-daemon` scripts into `~/.local/bin`
4. Creates `~/.config/voice-keyboard/config.toml` with `600` permissions and
   prompts for your xAI API key if one is not already configured
5. Sets up and enables a systemd user service for the daemon. It starts the
   service automatically when the config has an API key and your current session
   already has `input` group access.

The installer also installs a user-local GNOME Shell extension for the recording
status overlay. On GNOME Wayland, newly installed extensions load after logging
out and back in.

For non-interactive setup:

```bash
VOICE_KEYBOARD_API_KEY="xai-..." ./install.sh
```

If the daemon binary is not in `~/.local/bin`, set `VOICE_KEYBOARD_BIN` before running `install.sh`. Override the venv location with `VOICE_KEYBOARD_VENV`.

## Configuration

Edit `~/.config/voice-keyboard/config.toml`:

```toml
[xai]
api_key = "xai-..."

[stt]
language = "en"
interim_results = true

[tts]
voice_id = "eve"
language = "en"

[audio]
sample_rate = 16000
chunk_ms = 100
device_name = "default"

[hotkey]
enabled = true
key = "control+space"
# "auto" = tap to toggle, or hold to record until release.
# You can still force "toggle" or "hold" if you want only one gesture.
mode = "auto"

[daemon]
# Defaults to ~/.config/voice-keyboard/socket if unset.
# socket_path = "/run/user/1000/voice-keyboard.sock"
```

## Security notes

- The daemon listens on a Unix socket under `~/.config/voice-keyboard/socket` with `0600` permissions. Only your user can connect.
- The config file should be readable only by your user (`chmod 600 ~/.config/voice-keyboard/config.toml`). It contains your xAI API key.
- The daemon uses `uinput` to inject keystrokes. Anyone who can run the daemon (or connect to its socket) can type into the focused application, so keep your user session secure.
- The `voice-keyboard tts` command reads the **primary selection** (currently selected text), not the clipboard, on both Wayland and X11.
- The daemon logs to the systemd user journal. By default it logs only transcript lengths, not the transcribed text. If you raise the log level to `DEBUG`, full transcripts (and audio chunk details) will be written to the journal.

## Hotkey

By default, the daemon listens for Ctrl+Space directly through Linux input
events. This supports both press-to-toggle and hold-to-talk:

```toml
[hotkey]
enabled = true
key = "control+space"
mode = "auto"
```

Because this reads `/dev/input/event*`, your user session needs access to the
`input` group. The installer configures this together with uinput access.

## Optional System Shortcuts

The daemon hotkey handles voice input. You can still configure text-to-speech in
your desktop environment:

| Shortcut | Command |
|----------|---------|
| Ctrl+Alt+T | `voice-keyboard tts` |

### GNOME

Settings → Keyboard → Keyboard Shortcuts → Custom Shortcuts

If `voice-keyboard` is installed under `~/.local/bin`, use the absolute path
reported by `command -v voice-keyboard` for the TTS command. GNOME custom
shortcuts may not inherit your shell `PATH`.

### Hyprland

```conf
bind = CTRL ALT, T, exec, voice-keyboard tts
```

### Sway

```conf
bindsym Control+Mod1+t exec voice-keyboard tts
```

## Manual usage

```bash
# Press once to start recording, again to stop and type into the focused app
voice-keyboard toggle

# Read selected text aloud
voice-keyboard tts

# Check daemon status
voice-keyboard status
```

## Daemon management

```bash
systemctl --user start voice-keyboard-daemon
systemctl --user status voice-keyboard-daemon
systemctl --user restart voice-keyboard-daemon
journalctl --user -u voice-keyboard-daemon -f
```

## Architecture

```
voice-keyboard/
├── voice_keyboard/
│   ├── daemon.py          # Main daemon process
│   ├── client.py          # CLI client
│   ├── audio_capture.py   # PyAudio mic streaming
│   ├── stt.py             # xAI STT WebSocket client
│   ├── tts.py             # xAI TTS REST client + playback
│   ├── injector.py        # UInput virtual keyboard
│   ├── ipc.py             # Unix socket server/client
│   └── config.py          # Config loading
├── config.toml.example
├── pyproject.toml
├── install.sh
└── README.md
```

## Limitations

- **ASCII-only injection.** `injector.py` maps characters to physical key codes via `evdev`, so only printable ASCII (letters, digits, common punctuation, space, newline, tab) is supported. Non-ASCII transcripts (accents, CJK, emoji, smart quotes produced by STT) are silently skipped with a warning in the journal. A Unicode-capable backend (e.g. `ydotool` with a virtual IME, or an IBus/Fcitx bridge) is the path to broader coverage; contributions welcome.
- **Single concurrent IPC command.** The daemon's IPC loop serves one command at a time, so a long `tts` (up to a 30s timeout) will block a simultaneous `status`/`stop` until it finishes.
