# HyperFurion VK — your voice is the keyboard

[![Latest release](https://img.shields.io/github/v/release/liamghennigan/HyperFurion-VK)](https://github.com/liamghennigan/HyperFurion-VK/releases)
[![License: MIT](https://img.shields.io/badge/license-MIT-blue.svg)](LICENSE)
[![Linux-first](https://img.shields.io/badge/Linux-first%20·%20Wayland%20%26%20X11-informational)](https://liamghennigan.github.io/HyperFurion-VK/)
[![Stars](https://img.shields.io/github/stars/liamghennigan/HyperFurion-VK?style=social)](https://github.com/liamghennigan/HyperFurion-VK/stargazers)

**Type with your voice in any Linux app — including the terminal.** HyperFurion
VK is a system-wide voice keyboard: your speech becomes real keystrokes,
pressed into whatever app your cursor is already in. Words land *while you
speak* and repair themselves as the sentence firms up ("molten dictation"). It
runs **fully offline with a local model**, and it **never presses Enter — only
you do.**

▶ **See it work:** **<https://liamghennigan.github.io/HyperFurion-VK/>** — the
landing page dictates itself, live, in your browser.

<!-- TODO(demo): drop a 30–45s screen capture here once recorded — it is the
     single most shareable asset. See launch/demo-shot-list.md for the script.
![HyperFurion VK typing into a terminal by voice](docs/media/demo.gif)
-->

### Install on Linux — one line

```bash
curl -fsSL https://github.com/liamghennigan/HyperFurion-VK/releases/latest/download/install-hyperfurion-vk.sh | bash
```

Sets up a local daemon, the `uinput` virtual keyboard, and a systemd user
service. Works offline with a local Whisper/OpenAI-compatible server, or point
it at a cloud provider (xAI, OpenAI, Groq, Deepgram, AssemblyAI, ElevenLabs) —
or the hosted tier: `voice-keyboard login <email>`, no key to manage.

### Why it's different

- **Everywhere, natively.** Real keystrokes via `uinput` — works in editors,
  browsers, chat, and the **terminal**, not just a textbox in one app.
- **Molten dictation.** With a streaming provider, words appear as you speak
  and self-correct in place, then freeze — you watch the text think.
- **You keep the trigger.** In the terminal it *drafts* the command and stops;
  **Enter is always yours.** Nothing is captured until you press to talk.
- **Private by default.** Run it 100% offline with a local model; zero
  analytics; your keys stay on your machine.
- **A voice assistant in the keyboard** — "Kai" (hold Right Ctrl, click the
  orb, or the opt-in wake word), model-agnostic and local-first.

---

HyperFurion VK runs a local daemon. When you record speech, the daemon captures
microphone audio, sends it to the configured speech-to-text provider, and types
the returned text into the currently focused app through a virtual keyboard.
When you ask for text-to-speech, it reads the primary selection from your
desktop, sends that selected text to the configured TTS provider, and plays the
returned audio locally.

**New in 2.0: Kai — a voice assistant in the keyboard.** Summon Kai three
ways — **hold Right Ctrl** walkie-talkie style and release to send (a bare
modifier, so nothing ever leaks into the focused app — configurable),
**click** the always-on Kai orb on screen, or (opt-in) say the **wake word
"Kai"** — and it routes your spoken query by where you are:
focused on a terminal, it turns your words into a command and types it at
the prompt — never pressing Enter, only you can; anywhere else, it answers
or searches the web, spoken back. An earcon confirms the mic is live, the
turn runs off the hotkey path (so a second tap cuts Kai off mid-answer), and
the `[llm]` brain is model-agnostic and local-first (a ~1 GB model handles
the command work). On by default and push-to-talk — nothing is captured
until you summon it. See `[assistant]` / `[wake]` in `config.toml.example`.

**Flow — [molten dictation](#flow--molten-dictation).** With a streaming
provider, words appear in the focused field *while you speak* and repair
themselves in place as the transcript firms up. A spoken edit grammar
("scratch that", "new line", "period", `literal`), per-app context registers
(prose / terminal / verbatim / python / shell, picked by probing the focused
app), spoken
numbers as digits, silence auto-stop, full Unicode on Linux via clipboard
paste, and a wake-word rewrite channel ("… VK, make that formal") that
routes the just-typed text through an LLM and repairs it on screen.

## Fast Answer

- **Start or stop dictation:** press `Ctrl+Alt+V`, or run `voice-keyboard`
  / `voice-keyboard toggle`.
- **Hold-to-talk:** hold `Ctrl+Alt+V`; release it to stop.
- **Watch words appear as you speak:** on by default with a streaming
  provider — see [Flow](#flow--molten-dictation). Say "scratch that",
  "new line", "period"; say "VK, make that formal" to rewrite in place.
- **Read selected text aloud:** select text in any app, then run
  `voice-keyboard tts`. You can bind this to `Ctrl+Alt+T` in your desktop.
- **Type a command without running it:** `voice-keyboard intent "find every
  TODO in this repo"` — one line lands at your prompt, Enter stays yours
  (enable the voice trigger with `[intent] enabled`).
- **Teach it your vocabulary:** `voice-keyboard learned` reviews corrections
  mined from the opt-in ledger; accept what is right.
- **Hold rewrites for approval:** `[flow] rewrite_pending = true`, then
  "keep it" / "scratch that" (or `voice-keyboard keep` / `discard`).
- **Check whether the daemon is recording:** `voice-keyboard status`.
- **Check whether the daemon is running:**
  `systemctl --user status voice-keyboard-daemon`.
- **Config file:** `~/.config/voice-keyboard/config.toml`.
- **Logs:** `journalctl --user -u voice-keyboard-daemon -f`.
- **Core desktop support:** Linux desktop sessions with uinput access.
- **macOS (beta):** Quartz keystroke injection + event-tap hotkeys —
  `./packaging/macos/install-macos.sh` from a checkout. See [macOS](#macos-beta).
- **Best overlay support:** GNOME Shell 50 on Wayland. Other desktops fall back
  to ordinary desktop notifications.
- **Works fully offline** — bring your own local provider. A cloud provider
  is the default, but point `providers.openai.base_url` at any local
  OpenAI-compatible server (Whisper, Parakeet, Voxtral, Kokoro) and speech
  recognition and synthesis run entirely on your machine, no key and no
  network required. See [Fully Offline](#fully-offline-local-models).

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

## macOS (Beta)

The daemon runs on macOS with native backends: keystroke injection uses
Quartz CGEvents (full Unicode — accents, CJK, emoji, which the Linux uinput
backend cannot do), the global hotkey uses a listen-only keyboard event tap,
and the daemon runs as a launchd agent. From a checkout:

```bash
git clone https://github.com/liamghennigan/HyperFurion-VK
cd HyperFurion-VK
./packaging/macos/install-macos.sh
```

macOS will require two permissions for your Python binary under
System Settings → Privacy & Security: **Accessibility** (hotkeys and
typing) and **Microphone**. There is no GNOME-style overlay; status
arrives as notification-center toasts.

Beta means beta: the platform layer is unit-tested, but it has not had the
months of daily driving the Linux build has. Issues welcome.

## Windows (Beta)

Also native, also pure standard library: injection uses `SendInput` with
Unicode events, the hotkey uses a low-level keyboard hook (so hold-to-talk
works, and the daemon's own typing can never re-trigger it), IPC runs on
loopback TCP (`127.0.0.1:48765` — Windows Python has no Unix sockets)
guarded by a per-session token file (mode 600) so other local processes
can't drive typing, and status arrives as toasts. From a checkout, in
PowerShell:

```powershell
git clone https://github.com/liamghennigan/HyperFurion-VK
cd HyperFurion-VK
powershell -ExecutionPolicy Bypass -File packaging\windows\install-windows.ps1
```

That installs to your user site, writes a starter config, and adds a
Startup-folder launcher (no admin required). Same beta caveat as macOS.

## iOS — Why Not (Yet)

Honestly: a system-wide voice keyboard **cannot exist on iOS**. Apps are
sandboxed away from other apps' input; there is no uinput, no SendInput, no
event taps. The only sanctioned path is a **custom keyboard extension** — a
separate Swift app distributed through the App Store, which is a different
product with a different codebase, not a port of this daemon. It's a
plausible future project (the relay and hosted tier would slot right in as
its backend); it is not a checkbox. Nothing on this page will claim iOS
support until that app exists.

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

- Tap `Ctrl+Alt+V` once to start recording.
- Tap `Ctrl+Alt+V` again to stop recording, transcribe, and type the result.
- Hold `Ctrl+Alt+V` for hold-to-talk. Recording starts after the hold threshold
  and stops when you release the keys.

Equivalent CLI commands:

```bash
voice-keyboard start
voice-keyboard stop
voice-keyboard toggle
voice-keyboard status
```

`voice-keyboard` with no command is the same as `voice-keyboard toggle`.

Flow commands (see [Flow — Molten Dictation](#flow--molten-dictation)):

```bash
voice-keyboard transform "make that more formal"   # rewrite last dictation in place
voice-keyboard history 10                          # list the dictation ledger (opt-in)
voice-keyboard recall 2                            # re-type the 2nd-most-recent entry
```

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

## Flow — Molten Dictation

Flow is on by default. It has two halves:

- **The pipeline** (all providers): a spoken edit grammar and per-app
  rendering registers applied to every transcript before it is typed.
- **Live molten injection** (streaming providers: `xai`, `hyperfurion`, and
  local REST via `live_rest`): text streams into the focused field while you
  speak. Words stay *molten* for a stability window (default 1.5 s); when the
  provider revises a molten word, the daemon backspaces to the divergence
  point and retypes — the text repairs itself in front of you. Once a word
  survives the window it *freezes* and is never touched again, so repairs
  stay short and your caret never runs away. `flow.enabled = false` restores
  the old record → wait → paste behavior exactly.

While recording, the GNOME overlay pill becomes a live caption: a small VU
meter plus the molten tail of the transcript, updating as you speak.

### The spoken grammar

| You say | You get |
| --- | --- |
| `scratch that` / `delete that` | deletes the last utterance segment (works on already-typed text) |
| `new line` / `new paragraph` | `\n` / `\n\n` |
| `period`, `comma`, `question mark`, `em dash`, `open quote`, … | the glyph, correctly spaced |
| `literal period` | the word "period" |
| `twenty three` (terminal register, or `numbers = "always"`) | `23` — also decimals ("three point one four") and digit runs ("one two seven" → `127`) |
| `VK, make that formal` (end of an utterance, or alone) | rewrites the preceding dictation in place via `[llm]` |

Every phrase is remappable and removable in config (`[flow.commands]`,
`[flow.punctuation]`), `[flow.vocabulary]` expands your own phrases
("hyper furion" → "HyperFurion"), and the wake word is configurable.

### Context registers

At recording start the daemon probes the focused app — AT-SPI on Linux,
Quartz on macOS, Win32 on Windows — and picks a register:

| Register | Behavior |
| --- | --- |
| `prose` (default) | smart capitalization and punctuation spacing |
| `terminal` | no auto-caps, numbers as digits, pastes with `Ctrl+Shift+V` |
| `verbatim` | grammar off; words exactly as recognized |
| `python` | compiles speech: "for i in range ten colon" → `for i in range(10):` |
| `shell` | compiles speech: "pipe grep dash i error" → `| grep -i error` |

Known terminals (kitty, alacritty, foot, konsole, GNOME Terminal, wezterm,
Windows Terminal, iTerm2, …) map to `terminal` automatically; override or
extend per app in `[registers.map]` (that is also where you opt an editor
into `python` or `shell`). If focus moves to a different app
mid-dictation, typing freezes immediately and the transcript lands on the
clipboard instead — dictation never types into the wrong window. On Linux
the probe also sees the focused *widget*: a password field always forces
`verbatim`, is never written to the history ledger, and never contributes
biasing context.

### The next-level channels

Seven capabilities landed together, each config-gated and off by default
where behavior could change (see `ROADMAP.md` for the doctrine and
`config.toml.example` for every key):

- **Hotword biasing** (`[stt] hotword_bias`) — recognition is biased
  toward the vocabulary you accepted via `voice-keyboard learned`, on
  REST providers (OpenAI-style `prompt`, Deepgram `keyterm`/`keywords`,
  AssemblyAI `word_boost`). Curated words only — screen text is never
  harvested; dictation is new thought, not a continuation of what is on
  screen. Assembled per session, never stored.
- **A keyboard that learns you** (`voice-keyboard learned`) — corrections
  are mined from the opt-in history ledger; nothing applies until you
  accept it, then it merges into the grammar vocabulary
  (`[flow] personal_dictionary`). All of it lives in
  `~/.local/state/voice-keyboard/dictionary.json`, mode 600.
- **Semantic registers** (`python`, `shell`) — deterministic spoken-code
  compilation, no model in the loop.
- **Molten diffs** (`[flow] rewrite_pending`) — a "VK, …" rewrite is
  held pending; say "keep it" or "scratch that" (CLI: `keep` /
  `discard`). No edit is real until it freezes.
- **Type, never execute** (`[intent]`, `voice-keyboard intent "…"`) —
  "VK, run …" types ONE command line at your prompt and cannot press
  Enter: the refusal is enforced inside the keystroke injector on every
  path (keycode, newline, clipboard paste). Your keypress is the consent.
- **Ambient containment** (`[ambient]`, experimental) — in a long-open
  session, only utterances that start with the address word are typed;
  room speech never reaches the engine.
- **Kai — the voice assistant** (`[assistant]`) — the keyboard grows a voice
  assistant, on by default and push-to-talk. Summon it three ways: **hold
  Right Ctrl** and release to send (a bare modifier never reaches the
  focused app, so nothing leaks into a terminal; configurable — chords like
  `control+alt+.` work but terminals see escape codes when they're held),
  **click** the always-on Kai orb the overlay draws on screen, or (opt-in
  `[wake]`) say the local **wake word "Kai"**. (On Wayland the daemon often
  can't see the focused app — GPU terminals expose no accessibility — so
  when focus is unknown Kai still compiles commands from clearly-runnable
  requests and answers everything else; toggle with `terminal_fallback`.) Kai routes your query by where you are: **in a
  terminal**, it turns your words into a command, types it at the prompt,
  and **never presses Enter — only you can**; **anywhere else**, it searches
  the web / answers you, spoken back through your xAI Voice Agent Builder
  agent (memory unified with the dictation ledger). An earcon confirms the
  mic is live; the turn runs off the hotkey path, so a second tap barges in
  and cuts Kai off. Voice in, voice or a drafted command out — you never
  type to it. Frontier brain, local hands, you own the Enter key.
- **Wake word "Kai"** (`[wake]`, opt-in, default off) — a tiny **local**
  openWakeWord detector summons Kai hands-free; nothing is transcribed and
  nothing leaves the box until it fires. It's the one path that keeps the
  mic warm, so it stays behind an explicit switch (`pip install
  'hyperfurion-vk[wake]'`); the hotkey remains the hard mute.
- **Total recall** (`[recall]`, `voice-keyboard find "…"`) — search
  everything you ever dictated. Keyword search out of the box; point it
  at a local Ollama `/embeddings` endpoint and it becomes semantic,
  fully on-box. "VK, recall the relay caps" speaks the best match.
- **The multiplayer keyboard** (`[remote_mic]`, experimental) — the
  daemon serves a one-page LAN mic over self-signed HTTPS; your phone
  streams audio into a normal dictation session on the desktop. Audio
  never leaves your network.
- **Procedural memory** (`voice-keyboard learned`) — texts you dictate
  again and again surface as macro candidates; name one
  (`learned macro N trailer`) and "VK, trailer" types it verbatim.
  Offered, never imposed — the same consent gate as corrections.
- **Speculative TTS** (`[tts] prefetch`) — the primary selection is
  synthesized *while you are still highlighting it*, so `voice-keyboard
  tts` starts instantly on a cache hit. `"auto"` prefetches only against
  a local endpoint (free); `"always"` opts in cloud (spends tokens on
  selections never played, and sends selection text before you ask).
- **Preedit is molten** (`SPOKEN-INPUT-PROTOCOL.md`) — the input-method
  mapping layer (`voice_keyboard/imethod.py`) that renders molten text as
  IM preedit and freeze as commit; host integration is a deliberate,
  separate opt-in.

### Unicode on Linux

The uinput injector now types anything: plain ASCII goes through the fast
key path, and any other run (accents, CJK, emoji, em-dashes) is pasted via
the clipboard — `wl-copy` (Wayland) or `xclip` (X11) — with your previous
clipboard contents restored afterwards. Terminals get the `Ctrl+Shift+V`
chord via the register. Clipboard managers may briefly see the transient
entry; if no clipboard tool is installed, non-ASCII is dropped with a
warning as before.

### Hands-free and recall

- `auto_stop_ms = 1200` under `[flow]` ends recording by itself after ~1.2 s
  of silence: tap, speak, done.
- `voice-keyboard transform "make it friendlier"` rewrites the last
  dictation in place, any time.
- `history = true` under `[flow]` keeps an append-only local ledger
  (`~/.local/state/voice-keyboard/history.jsonl`, mode 600).
  `voice-keyboard history` lists; `voice-keyboard recall 2` re-types the
  second-most-recent entry. Off by default.
- `voice-keyboard status` now reports provider, register, flow state,
  focused app, and the last error.

`[flow]`, `[registers]`, and `[llm]` edits hot-reload at the next recording
— no daemon restart.

### Flow limitations (honest ones)

- Live repairs assume nothing else edits the field mid-dictation: if you
  type or click into the text while speaking, repairs can land in the wrong
  place. The stability window and `max_molten_chars` bound the damage.
- "scratch that" declines to delete across complex Unicode (emoji, combining
  marks) — backspace-per-character is not reliable there.
- A finalize that retro-revises an already-frozen word keeps the frozen
  form; only the still-molten tail adopts late revisions.
- `live_rest = "always"` re-bills cloud REST providers on every interim
  probe; the default `"auto"` only pseudo-streams against local endpoints.

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

# Hosted subscription — one key, no provider accounts. See relay/README.md.
[providers.hyperfurion]
api_key = "hfk-your-subscription-key-here"
# Only set base_url if you run your own relay.
# base_url = "https://api.hyperfurion.com"

[providers.openai]
api_key = "openai-your-api-key-here"
# Point at any OpenAI-compatible server (a local Whisper/Kokoro server
# makes dictation fully offline; no api_key needed then).
# base_url = "http://localhost:8000/v1"

[providers.groq]
api_key = "groq-your-api-key-here"

[providers.deepgram]
api_key = "deepgram-your-api-key-here"

[providers.assemblyai]
api_key = "assemblyai-your-api-key-here"

[providers.elevenlabs]
api_key = "elevenlabs-your-api-key-here"

[stt]
# Choices: xai, hyperfurion, openai, groq, deepgram, assemblyai
provider = "xai"
# Leave empty for the provider default.
model = ""
language = "en"
interim_results = true

[tts]
# Choices: xai, hyperfurion, openai, elevenlabs
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
key = "control+alt+v"
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

**xAI is the default provider** for both speech-to-text and text-to-speech —
it is what HyperFurion VK is built and daily-driven on. Any provider below
works: HyperFurion VK is provider-selectable and model-configurable, and model
IDs are plain config values, so a compatible provider model can be changed
without code changes.

### Speech-To-Text

| Provider | Config value | Default model |
| --- | --- | --- |
| xAI (default) | `xai` | Provider default. |
| HyperFurion (subscription) | `hyperfurion` | Provider default. |
| OpenAI | `openai` | `gpt-4o-transcribe` |
| Groq | `groq` | `whisper-large-v3-turbo` |
| Deepgram | `deepgram` | `nova-3` |
| AssemblyAI | `assemblyai` | Provider default. |

`stt.language` is sent to each provider. For AssemblyAI, `en` is sent as
`en_us`.

### Text-To-Speech

| Provider | Config value | Default model | Default voice |
| --- | --- | --- | --- |
| xAI (default) | `xai` | Provider default. | `eve` |
| HyperFurion (subscription) | `hyperfurion` | Provider default. | `eve` |
| OpenAI | `openai` | `gpt-4o-mini-tts` | `coral` |
| ElevenLabs | `elevenlabs` | `eleven_multilingual_v2` | `JBFqnCBsd6RMkjVDRZzb` |

If you switch from xAI to OpenAI or ElevenLabs and leave `voice_id = "eve"`,
the code uses that provider's default voice instead. Set `voice_id` explicitly
when you want a specific voice.

### Fully Offline (Local Models)

The `openai` provider accepts a `base_url`, so any OpenAI-compatible server
counts as a provider — including one on `localhost`. No API key is required
when `base_url` is set, and nothing leaves your machine:

```toml
[providers.openai]
base_url = "http://localhost:8000/v1"

[stt]
provider = "openai"
# set model to whatever id your server exposes, e.g.
# model = "Systran/faster-whisper-large-v3"

[tts]
provider = "openai"
```

The strongest open models to serve locally right now:

| What | Why |
| --- | --- |
| [Speaches](https://github.com/speaches-ai/speaches) | Easiest single server: OpenAI-compatible STT **and** TTS in one process (faster-whisper + Kokoro). |
| [Whisper large-v3-turbo](https://huggingface.co/openai/whisper-large-v3-turbo) | The default open STT workhorse; great accuracy/speed balance. |
| [NVIDIA Parakeet TDT](https://huggingface.co/nvidia/parakeet-tdt-0.6b-v3) | Tops the [Open ASR leaderboard](https://huggingface.co/spaces/hf-audio/open_asr_leaderboard); extremely fast on a GPU. |
| [Voxtral Mini 3B](https://huggingface.co/mistralai/Voxtral-Mini-3B-2507) | Apache-2.0 speech model; vLLM serves it OpenAI-compatible. |
| [whisper.cpp](https://github.com/ggml-org/whisper.cpp) | CPU-only and edge boxes; no GPU required. |
| [Kokoro-82M](https://huggingface.co/hexgrad/Kokoro-82M) | Small, high-quality open TTS voice (what Speaches serves). |

### The HyperFurion Subscription Provider

`hyperfurion` buys convenience, not capability. Every feature of
HyperFurion VK is open source and free forever — with your own provider
API key you have all of it, and paying unlocks nothing. The subscription
is a single `hfk_` key instead of a provider account, with xAI STT/TTS
behind a metered relay; what's left over after upstream costs funds the
project's development. It speaks the same streaming protocol as `xai`, so
behavior is identical from the daemon's side. Audio for this provider
transits the relay on its way to xAI; it is held in memory only and never
written to disk. The relay is in `relay/` and is fully self-hostable —
see `relay/README.md` for tiers, quotas, and deployment.

## Hotkeys

The built-in hotkey listener reads Linux input events directly. This is why your
user needs `input` group access and why logging out/in may be required after
installation.

Config:

```toml
[hotkey]
enabled = true
key = "control+alt+v"
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

### `Ctrl+Alt+V` Does Nothing

Check the daemon and logs first:

```bash
voice-keyboard status
journalctl --user -u voice-keyboard-daemon -n 100 --no-pager
```

If logs say no readable keyboard devices were found, your current session
probably lacks access to `/dev/input/event*`. Log out and back in after the
installer adds your user to the `input` group.

Also check whether your desktop or focused app already captures `Ctrl+Alt+V`.
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
keyboard focus. ASCII is typed through the uinput key path; anything else
(accents, CJK, emoji, smart quotes) is pasted through the clipboard, which
requires `wl-copy` (Wayland) or `xclip` (X11). If neither tool is installed,
non-ASCII characters are skipped with warnings in the journal.

### Stop Takes A While

Stop-to-text latency depends on the selected provider and your connection.
Check the journal for provider errors if it seems stuck:

```bash
journalctl --user -u voice-keyboard-daemon -n 100 --no-pager
```

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

Yes — bring your own local provider. By default STT/TTS are cloud-backed, but
the `openai` provider accepts a `base_url`, so pointing it at any local
OpenAI-compatible server (Whisper, Parakeet, Voxtral, Kokoro; e.g. via
[Speaches](https://github.com/speaches-ai/speaches)) keeps everything on your
machine — no API key and no network required. See
[Fully Offline (Local Models)](#fully-offline-local-models). Capture, hotkeys,
status UI, IPC, playback, and keyboard injection are already local.

## Security And Privacy

- Provider APIs receive the audio you dictate for STT.
- Provider APIs receive selected text when you run TTS.
- Recorded audio is held in memory while it is being transcribed; it is not
  written to disk.
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

- Linux is the primary, battle-tested platform; macOS and Windows support is
  beta. iOS is not possible as a system-wide keyboard (see iOS — Why Not).
- No speech model ships in the box: bring a provider key, the subscription, or
  a local OpenAI-compatible server (see Fully Offline above).
- uinput injection on Linux types ASCII directly and everything else via a
  clipboard paste (needs `wl-copy`/`xclip`; the macOS and Windows backends
  type full Unicode natively).
- Live molten injection assumes the field is not edited by hand mid-dictation
  (see Flow limitations above).
- The built-in global hotkey requires readable Linux input devices (Linux),
  Accessibility permission (macOS), or a keyboard hook (Windows).
- The near-field overlay is GNOME Shell 50 Wayland specific.
- Other desktops use notification fallback unless they implement the same D-Bus
  overlay interface.
- Stop-to-text latency depends on the selected provider.
- IPC commands are handled one at a time. A long TTS request or slow provider
  finalization can delay another simultaneous command.

## Architecture

```text
voice-keyboard/
|-- voice_keyboard/
|   |-- daemon.py          # Main daemon, recording state, IPC handling, hotkeys
|   |-- client.py          # CLI, primary-selection reading, overlay calls
|   |-- flow/
|   |   |-- engine.py      # Molten dictation state machine (pure logic)
|   |   |-- grammar.py     # Spoken commands, punctuation, vocabulary, wake word
|   |   |-- registers.py   # register rendering (+ semantic compilers in code.py)
|   |   |-- numbers.py     # Spoken cardinals -> digits
|   |   |-- vad.py         # RMS levels, VU meter, silence auto-stop
|   |   `-- worker.py      # Injection convergence loop (type/backspace bursts)
|   |-- transcript.py      # Streaming transcript merge heuristics
|   |-- focusprobe.py      # Focused-app probe (AT-SPI / Quartz / Win32)
|   |-- clipboard.py       # Clipboard get/set (wl-copy, xclip, pbcopy, ...)
|   |-- llm.py             # OpenAI-compatible chat client for voice transform
|   |-- history.py         # Opt-in dictation ledger
|   |-- audio_capture.py   # PyAudio microphone capture
|   |-- stt.py             # STT provider clients (+ pseudo-streaming adapter)
|   |-- tts.py             # TTS provider clients and playback
|   |-- injector.py        # UInput virtual keyboard + clipboard paste fallback
|   |-- ipc.py             # Unix socket / loopback-TCP server & client
|   |-- hotkey.py          # Linux input-event global hotkey listener
|   `-- config.py          # Config loading and validation
|-- gnome-shell/
|   `-- voice-keyboard-overlay@liam-hennigan/
|       |-- extension.js   # GNOME Shell near-field overlay + live caption
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
