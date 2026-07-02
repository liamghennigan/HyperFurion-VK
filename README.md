# HyperFurion VK

**Website:** <https://liamghennigan.github.io/HyperFurion-VK/>

A Linux-first open-source voice keyboard with native xAI STT/TTS support,
selectable cloud speech providers, global hotkeys, desktop status feedback, and
uinput keyboard injection.

HyperFurion VK runs a local daemon. When you record speech, the daemon captures
microphone audio, sends it to the configured speech-to-text provider, and types
the returned text into the currently focused app through a virtual keyboard.
When you ask for text-to-speech, it reads the primary selection from your
desktop, sends that selected text to the configured TTS provider, and plays the
returned audio locally.

## Fast Answer

- **Start or stop dictation:** press `Ctrl+Space`, or run `voice-keyboard`
  / `voice-keyboard toggle`.
- **Hold-to-talk:** hold `Ctrl+Space`; release it to stop.
- **Read selected text aloud:** select text in any app, then run
  `voice-keyboard tts`. You can bind this to `Ctrl+Alt+T` in your desktop.
- **Check whether the daemon is recording:** `voice-keyboard status`.
- **Check whether the daemon is running:**
  `systemctl --user status voice-keyboard-daemon`.
- **Config file:** `~/.config/voice-keyboard/config.toml`.
- **Logs:** `journalctl --user -u voice-keyboard-daemon -f`.
- **Core desktop support:** Linux desktop sessions with uinput access.
- **Best overlay support:** GNOME Shell 50 on Wayland. Other desktops fall back
  to ordinary desktop notifications.
- **Cloud required:** speech recognition and speech synthesis currently use
  provider APIs. This is not an offline dictation engine.

## What Gets Installed

The normal installer keeps the app user-local but needs `sudo` for system
packages, uinput setup, and input-device access:

- Python package in `~/.local/share/voice-keyboard-venv`
- CLI symlinks in `~/.local/bin/voice-keyboard` and
  `~/.local/bin/voice-keyboard-daemon`
- Config in `~/.config/voice-keyboard/config.toml` with mode `600`
- User service in `~/.config/systemd/user/voice-keyboard-daemon.service`
- GNOME overlay extension in
  `~/.local/share/gnome-shell/extensions/voice-keyboard-overlay@liam-hennigan`
- uinput module/rules and `input` group membership for your user

The installer enables the user service. It starts the daemon immediately only
when the selected provider API key(s) are configured and the current login
session already has effective `input` group access. If the installer says the
service was enabled but not started, edit the config or log out and back in,
then run:

```bash
systemctl --user start voice-keyboard-daemon
```

## Requirements

- Linux with the `uinput` kernel module
- Python 3.11+
- systemd user services for the default daemon installation
- Access to `/dev/uinput` for virtual keyboard injection
- Read access to `/dev/input/event*` for the built-in global hotkey listener
- PortAudio and PyAudio for microphone capture
- libsndfile, `sounddevice`, and `numpy` for TTS playback
- `notify-send` for fallback status notifications
- `wl-paste` from `wl-clipboard` on Wayland, or `xclip` on X11, for reading
  selected text
- A provider API key for the selected STT provider and the selected TTS provider

GNOME Shell 50 on Wayland is required only for the near-field recording overlay.
Dictation, TTS, manual commands, and fallback notifications are not GNOME-only.

## Quick Install

Download and run the release installer:

```bash
curl -L https://github.com/liamghennigan/HyperFurion-VK/releases/latest/download/install-hyperfurion-vk.sh -o install-hyperfurion-vk.sh
chmod +x install-hyperfurion-vk.sh
./install-hyperfurion-vk.sh
```

The release installer downloads the tagged source archive and runs the bundled
project installer.

If you cloned this repository:

```bash
chmod +x install.sh
./install.sh
```

The installer supports `apt-get`, `dnf`, and `pacman`. On other distributions,
install the system dependencies yourself, then use the Python package and
systemd instructions in this README as a guide.

### Non-Interactive Install

Use provider-specific environment variables when STT and TTS use different
providers:

```bash
VOICE_KEYBOARD_STT_PROVIDER=openai \
VOICE_KEYBOARD_TTS_PROVIDER=elevenlabs \
OPENAI_API_KEY="sk-..." \
ELEVENLABS_API_KEY="..." \
./install.sh
```

If the same provider/key is used for both STT and TTS, the generic fallback is
convenient:

```bash
VOICE_KEYBOARD_API_KEY="xai-..." ./install.sh
```

Supported installer environment variables:

| Variable | Purpose |
| --- | --- |
| `VOICE_KEYBOARD_STT_PROVIDER` | Selects `xai`, `openai`, `groq`, `deepgram`, or `assemblyai`. |
| `VOICE_KEYBOARD_TTS_PROVIDER` | Selects `xai`, `openai`, or `elevenlabs`. |
| `XAI_API_KEY` | API key for xAI STT/TTS. |
| `OPENAI_API_KEY` | API key for OpenAI STT/TTS. |
| `GROQ_API_KEY` | API key for Groq STT. |
| `DEEPGRAM_API_KEY` | API key for Deepgram STT. |
| `ASSEMBLYAI_API_KEY` | API key for AssemblyAI STT. |
| `ELEVENLABS_API_KEY` | API key for ElevenLabs TTS. |
| `VOICE_KEYBOARD_API_KEY` | Generic fallback API key for the selected provider(s). |
| `VOICE_KEYBOARD_VENV` | Override the venv path. |
| `VOICE_KEYBOARD_BIN` | Override the daemon binary used in the systemd unit. |

When env vars are missing, `install.sh` prompts through `/dev/tty`, so prompts
still work when it is launched by the release installer.

## First Run Checklist

1. Make sure `~/.local/bin` is on your shell `PATH`.

   ```bash
   command -v voice-keyboard
   ```

2. If the installer added you to the `input` group, log out and back in. Group
   membership changes do not fully apply to the current desktop session.

3. Start or restart the user service:

   ```bash
   systemctl --user restart voice-keyboard-daemon
   ```

4. Confirm the daemon is reachable:

   ```bash
   voice-keyboard status
   ```

   It prints `idle` or `recording`.

5. Test dictation in a text field:

   ```bash
   voice-keyboard start
   # speak for a few seconds
   voice-keyboard stop
   ```

6. Test TTS by selecting text in any app:

   ```bash
   voice-keyboard tts
   ```

## Usage

### Voice Input

The daemon listens for the configured hotkey. By default:

- Tap `Ctrl+Space` once to start recording.
- Tap `Ctrl+Space` again to stop recording, transcribe, and type the result.
- Hold `Ctrl+Space` for hold-to-talk. Recording starts after the hold threshold
  and stops when you release the keys.

Equivalent CLI commands:

```bash
voice-keyboard start
voice-keyboard stop
voice-keyboard toggle
voice-keyboard status
```

`voice-keyboard` with no command is the same as `voice-keyboard toggle`.

### Text-To-Speech

Select text in an app, then run:

```bash
voice-keyboard tts
```

Important: TTS reads the **primary selection**, not the clipboard. On most Linux
desktops, selecting text with the mouse or keyboard is enough. Copying text with
`Ctrl+C` is not required and may not help if nothing is selected.

### Optional Desktop Shortcut For TTS

The daemon already owns the voice-input hotkey. For TTS, bind a desktop shortcut
to:

```bash
voice-keyboard tts
```

On GNOME, open:

```text
Settings > Keyboard > Keyboard Shortcuts > Custom Shortcuts
```

If GNOME does not inherit your shell `PATH`, use the absolute path reported by:

```bash
command -v voice-keyboard
```

Hyprland example:

```conf
bind = CTRL ALT, T, exec, voice-keyboard tts
```

Sway example:

```conf
bindsym Control+Mod1+t exec voice-keyboard tts
```

## Configuration

Edit:

```bash
~/.config/voice-keyboard/config.toml
```

If `XDG_CONFIG_HOME` is set, the config lives at
`$XDG_CONFIG_HOME/voice-keyboard/config.toml` instead.

The installed config starts from `config.toml.example`. The default config is:

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
# Choices: xai, openai, groq, deepgram, assemblyai
provider = "xai"
# Leave empty for the provider default.
model = ""
language = "en"
interim_results = true

[tts]
# Choices: xai, openai, elevenlabs
provider = "xai"
# Leave empty for the provider default.
model = ""
voice_id = "eve"
language = "en"

[audio]
sample_rate = 16000
chunk_ms = 100
device_name = "default"

[hotkey]
enabled = true
key = "control+space"
# auto = tap to toggle, or hold to record until release
# Other choices: toggle, hold, disabled
mode = "auto"
# Optional; default is 280.
# hold_threshold_ms = 280

[daemon]
# Defaults to ~/.config/voice-keyboard/socket if unset.
# socket_path = "/run/user/1000/voice-keyboard.sock"
```

After changing provider, API key, audio, hotkey, or daemon settings, restart the
daemon:

```bash
systemctl --user restart voice-keyboard-daemon
```

## Providers

HyperFurion VK is provider-selectable and model-configurable. Model IDs are
plain config values, so a compatible provider model can be changed without code
changes.

### Speech-To-Text

| Provider | Config value | Behavior | Default model |
| --- | --- | --- | --- |
| xAI | `xai` | Streaming WebSocket. Audio is sent while you speak and transcript events arrive during the session. | Provider default. |
| OpenAI | `openai` | Buffered REST. Audio is recorded locally, then submitted as a WAV after you stop. | `gpt-4o-transcribe` |
| Groq | `groq` | Buffered REST using an OpenAI-compatible transcription endpoint. | `whisper-large-v3-turbo` |
| Deepgram | `deepgram` | Buffered REST. | `nova-3` |
| AssemblyAI | `assemblyai` | Buffered upload plus polling. Usually the slowest stop/finalization path. | Provider default. |

`stt.language` is sent to each provider. For AssemblyAI, `en` is sent as
`en_us`. `stt.interim_results` currently affects xAI only.

### Text-To-Speech

| Provider | Config value | Request shape | Default model | Default voice |
| --- | --- | --- | --- | --- |
| xAI | `xai` | Sends `text`, `voice_id`, and `language`. | Provider default. | `eve` |
| OpenAI | `openai` | Sends `model`, `input`, `voice`, and MP3 response format. | `gpt-4o-mini-tts` | `coral` |
| ElevenLabs | `elevenlabs` | Sends `text`, `model_id`, and `voice_id`. | `eleven_multilingual_v2` | `JBFqnCBsd6RMkjVDRZzb` |

If you switch from xAI to OpenAI or ElevenLabs and leave `voice_id = "eve"`,
the code uses that provider's default voice instead. Set `voice_id` explicitly
when you want a specific voice.

## Hotkeys

The built-in hotkey listener reads Linux input events directly. This is why your
user needs `input` group access and why logging out/in may be required after
installation.

Config:

```toml
[hotkey]
enabled = true
key = "control+space"
mode = "auto"
hold_threshold_ms = 280
```

Supported modifier names:

- `control` or `ctrl`
- `shift`
- `alt`
- `super` or `meta`

Supported trigger keys include common aliases such as `space`, `enter`,
`return`, and `tab`, plus names that map to Linux `KEY_*` codes through
`evdev`.

Modes:

| Mode | Behavior |
| --- | --- |
| `auto` | Tap toggles recording. Hold records until release. |
| `toggle` | Every hotkey press toggles start/stop. |
| `hold` | Press starts recording and release stops. |
| `disabled` | Built-in hotkey listener does not start. Manual CLI commands still work. |

## Overlay And Notifications

On GNOME Shell 50 Wayland, the installer copies and enables a Shell extension
that exposes the D-Bus name `org.voicekeyboard.Overlay`. The CLI and daemon ask
that extension to show recording state near the focused text field.

The anchor comes from AT-SPI focus/caret coordinates, collected with
`/usr/bin/python3` so it can use the system `gi` and `Atspi` packages. If AT-SPI
cannot expose a useful focused field, the extension falls back to the focused
window or monitor. If the extension is unavailable, HyperFurion VK falls back to
`notify-send`.

Newly installed GNOME Shell extensions may not load in the current Wayland
session. If the overlay does not appear after install, log out and back in.

Useful checks:

```bash
gnome-extensions list | grep voice-keyboard
gsettings get org.gnome.shell enabled-extensions
gdbus call --session \
  --dest org.voicekeyboard.Overlay \
  --object-path /org/voicekeyboard/Overlay \
  --method org.voicekeyboard.Overlay.Hide
```

If the `gdbus` command reports `ServiceUnknown`, the extension is not loaded in
the current session. The app should still work with notification fallback.

## Daemon Management

```bash
systemctl --user start voice-keyboard-daemon
systemctl --user stop voice-keyboard-daemon
systemctl --user restart voice-keyboard-daemon
systemctl --user status voice-keyboard-daemon
journalctl --user -u voice-keyboard-daemon -f
```

Run the daemon in the foreground for debugging:

```bash
voice-keyboard-daemon
```

The daemon listens on a Unix socket. By default:

```text
~/.config/voice-keyboard/socket
```

The CLI also accepts a custom socket:

```bash
voice-keyboard --socket /path/to/socket status
```

## Troubleshooting

### `voice-keyboard: command not found`

Make sure `~/.local/bin` is on your `PATH`, then open a new shell:

```bash
export PATH="$HOME/.local/bin:$PATH"
command -v voice-keyboard
```

For desktop shortcuts, prefer the absolute path from `command -v
voice-keyboard` because desktop environments often use a smaller `PATH` than
your interactive shell.

### `Failed to connect to daemon`

The user service is not running, the socket path is different from the config,
or the daemon failed during startup.

```bash
systemctl --user status voice-keyboard-daemon
journalctl --user -u voice-keyboard-daemon -n 100 --no-pager
```

Common causes are missing API keys, placeholder API keys still in the config, no
effective `input` group access, or `/dev/uinput` access failure.

### The Installer Enabled The Service But Did Not Start It

This is expected when the installer cannot safely start the daemon yet.

- If an API key is missing, edit `~/.config/voice-keyboard/config.toml`.
- If `input` group access is not effective, log out and back in.
- Then run `systemctl --user start voice-keyboard-daemon`.

### `Ctrl+Space` Does Nothing

Check the daemon and logs first:

```bash
voice-keyboard status
journalctl --user -u voice-keyboard-daemon -n 100 --no-pager
```

If logs say no readable keyboard devices were found, your current session
probably lacks access to `/dev/input/event*`. Log out and back in after the
installer adds your user to the `input` group.

Also check whether your desktop or focused app already captures `Ctrl+Space`.
You can change the hotkey in config or use manual commands:

```bash
voice-keyboard toggle
```

### Recording Starts But Text Is Not Typed

Check provider errors and uinput errors in the journal:

```bash
journalctl --user -u voice-keyboard-daemon -f
```

The daemon types through a virtual keyboard, so the destination app must have
keyboard focus when transcription finishes. The current injector supports
printable ASCII plus newline and tab. Accents, CJK text, emoji, smart quotes,
and other non-ASCII characters are skipped with warnings in the journal.

### Stop Takes A While

xAI is the only streaming STT path. OpenAI, Groq, Deepgram, and AssemblyAI
buffer audio locally and submit it after you stop recording. AssemblyAI also
uploads and polls for completion, so it can take noticeably longer.

### `voice-keyboard tts` Says No Selected Text

HyperFurion VK reads the primary selection, not the clipboard. Select the text
you want spoken and run `voice-keyboard tts` while it remains selected.

On Wayland, install/check `wl-paste`. On X11, install/check `xclip`.

```bash
wl-paste --primary
xclip -selection primary -o
```

Some sandboxed or remote apps may not expose a primary selection in the usual
way.

### TTS Synthesizes But Does Not Play

The first playback backend is `sounddevice` plus `soundfile`/libsndfile. If that
fails, the app tries `pygame` if installed:

```bash
~/.local/share/voice-keyboard-venv/bin/pip install 'voice-keyboard[pygame]'
systemctl --user restart voice-keyboard-daemon
```

Also confirm your system audio output works outside HyperFurion VK.

### The Overlay Does Not Appear Or Is Not Near The Text Field

Log out and back in after installation so GNOME loads the user extension. Then
check the D-Bus service:

```bash
gdbus call --session \
  --dest org.voicekeyboard.Overlay \
  --object-path /org/voicekeyboard/Overlay \
  --method org.voicekeyboard.Overlay.Hide
```

If that fails, the app falls back to notifications. If the overlay appears but
not near the caret, the focused app may not expose useful AT-SPI coordinates.
The extension then falls back to the focused window or monitor.

### Microphone Device Not Found

The default is:

```toml
[audio]
device_name = "default"
```

Set `device_name` to a full device name or a unique substring of the PortAudio
input device name.

One way to list PortAudio input devices from the installed venv:

```bash
~/.local/share/voice-keyboard-venv/bin/python - <<'PY'
import pyaudio

pa = pyaudio.PyAudio()
for index in range(pa.get_device_count()):
    info = pa.get_device_info_by_index(index)
    if info.get("maxInputChannels", 0) > 0:
        print(index, info.get("name"))
pa.terminate()
PY
```

### Provider Authentication Or Quota Errors

Check that the active provider in `[stt]` and `[tts]` has a real API key in the
matching `[providers.<name>]` section. Placeholder keys intentionally fail
validation.

If STT and TTS use different providers, configure both keys.

### Can I Use This Offline?

Not currently. Audio transcription and speech synthesis are provider-backed.
The local parts are capture, hotkeys, status UI, IPC, playback, and keyboard
injection.

## Security And Privacy

- Provider APIs receive the audio you dictate for STT.
- Provider APIs receive selected text when you run TTS.
- The REST STT clients buffer recorded audio in memory and submit a WAV after
  recording stops. xAI STT streams audio over a WebSocket while recording.
- TTS audio is written to a temporary MP3 file for playback, then deleted.
- API keys live in `~/.config/voice-keyboard/config.toml`; keep it mode `600`.
- The daemon socket is created with mode `600` under your config directory by
  default.
- Anyone who can run the daemon as your user or connect to its socket can type
  into your focused app through uinput.
- Membership in the `input` group is broad desktop input access. This is needed
  for global hotkey capture.
- Default logging records operational state and transcript lengths, not full
  transcript text. If you raise logging to `DEBUG`, interim/final transcript
  details may appear in the user journal.

## Limitations

- Linux only.
- Cloud STT/TTS only.
- uinput injection is currently printable ASCII, newline, and tab only.
- The built-in global hotkey requires readable Linux input devices.
- The near-field overlay is GNOME Shell 50 Wayland specific.
- Other desktops use notification fallback unless they implement the same D-Bus
  overlay interface.
- REST STT providers finalize only after recording stops, so they feel less
  live than xAI streaming STT.
- IPC commands are handled one at a time. A long TTS request or slow provider
  finalization can delay another simultaneous command.

## Architecture

```text
voice-keyboard/
|-- voice_keyboard/
|   |-- daemon.py          # Main daemon, recording state, IPC handling, hotkeys
|   |-- client.py          # CLI, primary-selection reading, overlay calls
|   |-- audio_capture.py   # PyAudio microphone capture
|   |-- stt.py             # STT provider clients
|   |-- tts.py             # TTS provider clients and playback
|   |-- injector.py        # UInput virtual keyboard
|   |-- ipc.py             # Unix socket server/client
|   |-- hotkey.py          # Linux input-event global hotkey listener
|   `-- config.py          # Config loading and validation
|-- gnome-shell/
|   `-- voice-keyboard-overlay@liam-hennigan/
|       |-- extension.js   # GNOME Shell near-field overlay
|       `-- metadata.json
|-- tests/
|-- config.toml.example
|-- install.sh
|-- packaging/install-hyperfurion-vk.sh
|-- pyproject.toml
`-- README.md
```

The daemon owns long-lived resources: uinput, microphone capture, provider
clients, hotkey listener, and the IPC socket. The CLI is intentionally small:
it reads config, sends one IPC command, and displays overlay/notification state.

## Development

Create a development environment:

```bash
python3 -m venv .venv
.venv/bin/pip install -U pip
.venv/bin/pip install -e '.[dev]'
```

Run the test suite and lightweight syntax checks:

```bash
python -m pytest -q
python -m compileall -q voice_keyboard tests
bash -n install.sh
bash -n packaging/install-hyperfurion-vk.sh
```

Some tests intentionally skip when the environment cannot create Unix sockets
or when host input/uinput access is unavailable.

## Uninstall

There is no dedicated uninstall script yet. To remove the user-local app files:

```bash
systemctl --user disable --now voice-keyboard-daemon.service
rm -f ~/.config/systemd/user/voice-keyboard-daemon.service
systemctl --user daemon-reload
rm -f ~/.local/bin/voice-keyboard ~/.local/bin/voice-keyboard-daemon
rm -rf ~/.local/share/voice-keyboard-venv
rm -rf ~/.local/share/gnome-shell/extensions/voice-keyboard-overlay@liam-hennigan
```

Optional user data/config removal:

```bash
rm -rf ~/.config/voice-keyboard
```

The installer also may have added a uinput module-load file, a udev rule, and
your user to the `input` group. Those are system-level changes and may be shared
with other tools, so remove them only if you are sure nothing else needs them.
