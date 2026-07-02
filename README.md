# HyperFurion VK

Universal Linux voice keyboard with selectable cloud speech providers for speech-to-text and text-to-speech.

## How it works

- **Voice input**: Tap Ctrl+Space once to start, tap it again to stop, or hold Ctrl+Space to record only while held — text is typed into the focused app via a virtual keyboard (uinput).
- **Text-to-speech**: Select text in any app, press Ctrl+Alt+T — the daemon reads it aloud using your configured TTS provider.

The local daemon handles audio capture, keyboard injection, hotkeys, and the desktop overlay. Speech recognition and synthesis run through whichever provider you configure.

## Providers

HyperFurion VK is provider-selectable and model-configurable. Model IDs are plain config values, so you can switch to a provider's newer compatible model without changing the app code.

| Feature | Supported providers |
|---------|---------------------|
| Speech-to-text | [xAI](https://x.ai/api), [OpenAI](https://platform.openai.com/docs/guides/speech-to-text), [Groq](https://console.groq.com/docs/speech-to-text), [Deepgram](https://developers.deepgram.com/docs/pre-recorded-audio), [AssemblyAI](https://www.assemblyai.com/docs/api-reference/transcripts/submit) |
| Text-to-speech | [xAI](https://x.ai/api), [OpenAI](https://platform.openai.com/docs/guides/text-to-speech), [ElevenLabs](https://elevenlabs.io/docs/api-reference/text-to-speech/convert) |

## Requirements

- Linux with uinput kernel module
- Python 3.11+
- PortAudio (for PyAudio) and libsndfile (for `sounddevice` TTS playback)
- `notify-send` for desktop status notifications
- GNOME Shell 50 on Wayland for the compositor-native near-field overlay
- [Optional] `pygame` for a fallback TTS playback backend (install with `pip install voice-keyboard[pygame]`)
- API key for whichever speech provider(s) you choose

## Quick install

Download the release installer:

```bash
curl -L https://github.com/liamghennigan/HyperFurion-VK/releases/latest/download/install-hyperfurion-vk.sh -o install-hyperfurion-vk.sh
chmod +x install-hyperfurion-vk.sh
./install-hyperfurion-vk.sh
```

The release installer downloads the tagged source bundle, then runs the bundled
project installer so the Python package, config template, and GNOME Shell
overlay extension are all installed together.

If you cloned the repository instead:

```bash
chmod +x install.sh
./install.sh
```

The installer:
1. Installs system dependencies (portaudio, libsndfile, Python venv/dev headers,
   and primary-selection helpers for TTS)
2. Configures uinput (udev rule, kernel module, group membership)
3. Installs the Python package into an isolated venv at `~/.local/share/voice-keyboard-venv` and symlinks the `voice-keyboard` / `voice-keyboard-daemon` scripts into `~/.local/bin`
4. Creates `~/.config/voice-keyboard/config.toml` with `600` permissions, prompts
   for STT/TTS providers, and lets you enter the selected provider API key(s)
5. Sets up and enables a systemd user service for the daemon. It starts the
   service automatically when the config has the selected provider API key(s)
   and your current session already has `input` group access.

The installer also installs a user-local GNOME Shell extension for the recording
status overlay. On GNOME Wayland, newly installed extensions load after logging
out and back in.

For non-interactive setup:

```bash
VOICE_KEYBOARD_STT_PROVIDER=openai \
VOICE_KEYBOARD_TTS_PROVIDER=openai \
OPENAI_API_KEY="sk-..." \
./install.sh
```

Provider-specific API key env vars are supported: `XAI_API_KEY`,
`OPENAI_API_KEY`, `GROQ_API_KEY`, `DEEPGRAM_API_KEY`, `ASSEMBLYAI_API_KEY`, and
`ELEVENLABS_API_KEY`. `VOICE_KEYBOARD_API_KEY` is also accepted as a generic
fallback when the same key should be used for the selected provider.

The installer prompts for provider choices and missing API keys when they are
not already configured. It reads from `/dev/tty` when needed, so prompting still
works when launched by the release installer.

If the daemon binary is not in `~/.local/bin`, set `VOICE_KEYBOARD_BIN` before running `install.sh`. Override the venv location with `VOICE_KEYBOARD_VENV`.

## Configuration

Edit `~/.config/voice-keyboard/config.toml`:

```toml
[providers.xai]
api_key = "xai-your-api-key-here"

[providers.openai]
api_key = "openai-your-api-key-here"

[providers.groq]
api_key = "groq-your-api-key-here"

[providers.deepgram]
api_key = "deepgram-your-api-key-here"

[providers.assemblyai]
api_key = "assemblyai-your-api-key-here"

[providers.elevenlabs]
api_key = "elevenlabs-your-api-key-here"

[stt]
provider = "xai" # xai, openai, groq, deepgram, assemblyai
model = ""       # provider default; examples: gpt-4o-transcribe, whisper-large-v3-turbo, nova-3
language = "en"
interim_results = true

[tts]
provider = "xai" # xai, openai, elevenlabs
model = ""       # provider default; examples: gpt-4o-mini-tts, eleven_multilingual_v2
voice_id = "eve" # examples: eve, coral, or an ElevenLabs voice id
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
- The config file should be readable only by your user (`chmod 600 ~/.config/voice-keyboard/config.toml`). It contains your selected provider API key(s).
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
│   ├── stt.py             # STT provider clients
│   ├── tts.py             # TTS provider clients + playback
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
