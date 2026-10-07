# HyperFurion VK — your voice is the keyboard

[![Latest release](https://img.shields.io/github/v/release/liamghennigan/HyperFurion-VK)](https://github.com/liamghennigan/HyperFurion-VK/releases)
[![License: MIT](https://img.shields.io/badge/license-MIT-blue.svg)](LICENSE)
[![Linux and Windows](https://img.shields.io/badge/Linux%20·%20Windows-Wayland%20%26%20X11%20·%20Windows%2010%2F11-informational)](https://liamghennigan.github.io/HyperFurion-VK/)
[![CI](https://github.com/liamghennigan/HyperFurion-VK/actions/workflows/ci.yml/badge.svg)](https://github.com/liamghennigan/HyperFurion-VK/actions/workflows/ci.yml)
[![Stars](https://img.shields.io/github/stars/liamghennigan/HyperFurion-VK?style=social)](https://github.com/liamghennigan/HyperFurion-VK/stargazers)

**Type with your voice in any app on Linux or Windows — including the terminal.** HyperFurion
VK is a system-wide voice keyboard: your speech becomes real keystrokes,
pressed into whatever app your cursor is already in. Words land *while you
speak* (with xAI, the default, or your own local speech server) and repair
themselves as the sentence firms up ("molten dictation"). No speech model
ships in the box: bring a key for a cloud speech service (xAI by default), or
run your own speech server and a local language model and it works **fully
offline**. The mic opens only when you ask, and it **never presses Enter in a
terminal it recognizes — that stays yours.**

▶ **See it work:** **<https://liamghennigan.github.io/HyperFurion-VK/>** — the
landing page is a working instance: an open-source speech model runs in
your browser, the words land in a real text field, and a 3D keyboard
presses every key — the spelling fixes, the caret commands, never Enter.
Its engine is a port of the daemon's, and CI replays the same corpus
through both (`tests/flow_corpus.json`).

<!-- TODO(demo): drop a 30–45s screen capture here once recorded — it is the
     single most shareable asset.
![HyperFurion VK typing into a terminal by voice](docs/media/demo.gif)
-->

### Download and run

| | Download | Run it |
| --- | --- | --- |
| **Linux** | [HyperFurion-VK-Setup.run](https://github.com/liamghennigan/HyperFurion-VK/releases/latest/download/HyperFurion-VK-Setup.run) | `sh ~/Downloads/HyperFurion-VK-Setup.run`, or make it executable and double-click it |
| **Windows** | [HyperFurion-VK-Setup.cmd](https://github.com/liamghennigan/HyperFurion-VK/releases/latest/download/HyperFurion-VK-Setup.cmd) | double-click it (if Windows warns about a downloaded file: **More info › Run anyway**) |

Run it as yourself, not with `sudo` or as administrator. On Linux it asks for
your password for the system steps (audio and clipboard packages, the virtual
keyboard, adding you to the `input` group), and after the first install you
log out and back in once. It installs for your user, then walks you through
the settings: how to transcribe your speech (a key for a cloud provider, or
your own server), your language model, the hotkey, the language, Kai,
dictation history, caret commands and self-corrections. If a
[llama.cpp](https://github.com/ggml-org/llama.cpp) server (`llama-server`) is
already running on your computer, setup finds it and asks whether to make
its model your default language model (rewrites, Kai, pause
punctuation). Change any of it later with `voice-keyboard setup`.

### Install on Linux — one line

```bash
curl -fsSL https://github.com/liamghennigan/HyperFurion-VK/releases/latest/download/install-hyperfurion-vk.sh | bash
```

Sets up a local daemon, the `uinput` virtual keyboard, and a systemd user
service. Speech goes to a cloud provider with your key (xAI by default;
OpenAI, Groq, Deepgram or AssemblyAI for speech-to-text, OpenAI or ElevenLabs
for read-aloud) or to your own local Whisper/OpenAI-compatible server, which
keeps it offline. The hosted subscription (`voice-keyboard login <email>`, no
key to manage) is for existing subscribers; it isn't on sale right now.

### Install on Windows — one line

In PowerShell (no administrator rights needed):

```powershell
irm https://raw.githubusercontent.com/liamghennigan/HyperFurion-VK/main/packaging/windows/install-hyperfurion-vk.ps1 | iex
```

A tray app with a native overlay and an on-screen Kai orb; it installs Python
for you if you don't have it. Press **Ctrl+Alt+V** in any app. See
[Windows](#windows).

### Why it's different

- **Everywhere, natively.** Real keystrokes (`uinput` on Linux, `SendInput` on
  Windows, Quartz on macOS) — works in editors, browsers, chat, and the
  **terminal**, not just a textbox in one app.
- **Molten dictation.** With xAI (the default) or your own local speech server,
  words appear as you speak and self-correct in place, then freeze — you watch
  the text think. (OpenAI, Groq, Deepgram and AssemblyAI type when you stop.)
- **Nothing starts until you ask.** The mic opens only when you press to
  talk or summon Kai, unless you turn on Kai's wake word. In a terminal it
  recognizes it never presses Enter: a spoken "new line" types nothing, and
  Kai (or "VK, run …", once you turn on `[intent]`) *drafts* the command and
  stops — **Enter is yours.** A word you spell or correct is offered to your
  dictionary, never learned on its own.
- **Private by design.** Zero analytics; your keys stay in your config file.
  Your audio goes only to the speech provider you pick, and the text a
  rewrite or Kai needs only to your language model — or run all of it 100%
  offline with your own speech server and a local model. Kai keeps your
  questions and its answers on your computer until you turn its memory off
  (`[assistant] memory_enabled`).
- **An editor you can talk to.** "Correct monday to friday", "cap that",
  quotes, lists, headings and to-dos, plus "select that" and "undo that" once
  you turn on caret commands (`[nav]`) — and prose
  that writes 25%, $5.50, 3:30 PM and October 6 the way you would. Spoken
  punctuation, "new line" and "scratch that" in Spanish, French or German
  too (`[flow] language`); `voice-keyboard commands` lists everything you
  can say, `voice-keyboard try` shows what any phrase would type.
- **A voice assistant in the keyboard** — "Kai" (hold Right Ctrl, click the
  orb, or the opt-in wake word), on by default and push-to-talk, running on the
  language model you choose (xAI's Grok by default, or a local one).

---

HyperFurion VK runs a local daemon. When you record speech, the daemon captures
microphone audio, sends it to the configured speech-to-text provider, and types
the returned text into the currently focused app through a virtual keyboard.
When you ask for text-to-speech, it reads the text you have selected (the
primary selection on Linux; on Windows the read-aloud key copies the
selection and puts your clipboard back, while `voice-keyboard tts` reads the
clipboard; on macOS it reads the clipboard), sends it to the configured TTS
provider, and plays the returned audio locally.

**Kai — a voice assistant in the keyboard** (since 2.0). Summon Kai three
ways — **hold Right Ctrl** walkie-talkie style and release to send (a bare
modifier, so nothing ever leaks into the focused app — configurable),
**click** the always-on Kai orb on screen (GNOME on Linux, and Windows), or
(opt-in, with a "Kai" wake-word model you train) say the **wake word "Kai"** —
and it routes your spoken query by where you are: focused on a terminal, a
request it can run becomes a command typed at the prompt — never pressing
Enter, only you can; a question, or anything asked elsewhere, it answers
aloud. An earcon confirms the mic is live, and the turn runs off the hotkey
path (so a second tap cuts Kai off mid-answer). Kai thinks with your `[llm]`
model: xAI's Grok with your xAI key by default, or any local
OpenAI-compatible model (a ~1 GB model handles the command work). On by
default and push-to-talk — nothing is captured until you summon it — and it
keeps your questions and its answers on your computer until you turn its
memory off (`memory_enabled = false`). See `[assistant]` / `[wake]` in
`config.toml.example`.

**Flow — [molten dictation](#flow--molten-dictation).** With xAI (the
default) or your own local speech server, words appear in the focused field
*while you speak* and repair themselves in place as the transcript firms up.
A spoken edit grammar ("scratch that", "new line", "period", `literal`),
per-app context registers (prose, terminal for a terminal it recognizes, and
verbatim for a password field on Linux or in a classic Windows edit box,
picked by probing the focused app; python / shell / javascript for the apps
you map in `[registers.map]`), numbers as digits in terminals and code and,
in prose, wherever a unit, date or time makes them certain, opt-in silence
auto-stop, full Unicode on Linux via clipboard paste, and a wake-word
rewrite channel ("… VK, make that formal") that routes the just-typed text
through your `[llm]` and repairs it on screen.

## Fast Answer

- **Start or stop dictation:** press `Ctrl+Alt+V`, or run `voice-keyboard`
  / `voice-keyboard toggle`.
- **Not working?** `voice-keyboard doctor` checks the config, your speech
  provider's key, the microphone, the daemon, the language model (and what
  uses it: Kai, "VK, …" rewrites and pause review by default), typing
  permissions, the clipboard tool, the focus probe and, on Linux, whether
  the hotkey can read your keyboard, and prints the fix for each problem it
  finds.
- **Hold-to-talk:** hold `Ctrl+Alt+V`; release it to stop.
- **Watch words appear as you speak:** on by default with xAI (the default
  provider) or your own local speech server; with OpenAI, Groq, Deepgram or
  AssemblyAI the words land when you stop — see [Flow](#flow--molten-dictation).
  Say "scratch that", "new line", "period"; say "VK, make that formal" to
  rewrite in place through your `[llm]`.
- **Read selected text aloud:** on Windows, select text and press
  `Ctrl+Alt+R`. On Linux, select text and run `voice-keyboard tts` (bind it
  to a desktop shortcut), or set `[tts] hotkey = "control+alt+r"` and just
  press it. See [Text-To-Speech](#text-to-speech).
- **Type a command without running it:** `voice-keyboard intent "find every
  TODO in this repo"` — your `[llm]` writes one line, it lands at your prompt,
  and Enter stays yours ("VK, run …" is the voice trigger, off until you set
  `[intent] enabled = true`).
- **Teach it your vocabulary:** `voice-keyboard learned` lists the words you
  spelled or corrected by voice and the corrections mined from the opt-in
  history; nothing applies until you accept it. Or add a word now:
  `voice-keyboard learned add hyper furion = HyperFurion` (a one-word
  written form is also saved as a hotword, which OpenAI, Groq, Deepgram and
  AssemblyAI lean toward once you set `[stt] hotword_bias = true`).
- **Polish per app:** `[polish.map]` maps an app to a style (`slack =
  "casual"`, `thunderbird = "a clear, polite email"`); each prose dictation
  of four words or more there is rewritten in that style through `[llm]`.
- **Hold rewrites for approval:** `[flow] rewrite_pending = true`, then
  "keep it" / "scratch that" (or `voice-keyboard keep` / `discard`).
- **Check whether the daemon is recording:** `voice-keyboard status`.
- **Check whether the daemon is running (Linux):**
  `systemctl --user status voice-keyboard-daemon`.
- **Config file:** `~/.config/voice-keyboard/config.toml` (Windows:
  `%APPDATA%\voice-keyboard\config.toml`).
- **Logs (Linux):** `journalctl --user -u voice-keyboard-daemon -f`; on
  Windows, tray › Open logs folder.
- **Linux:** first-class, on Wayland and X11 desktop sessions with uinput
  access.
- **Windows 10/11:** first-class too: a tray app with its own overlay, Kai
  orb, and one-line installer. See [Windows](#windows).
- **macOS (beta):** Quartz keystroke injection + event-tap hotkeys —
  `./packaging/macos/install-macos.sh` from a checkout. See [macOS](#macos-beta).
- **Best overlay support:** GNOME Shell 45–50 on Wayland (developed on 50), and
  Windows (native). Other desktops fall back to ordinary desktop notifications.
- **Works fully offline** — bring your own local servers; no speech model
  ships in the box. A cloud provider (xAI) is the default, but set `[stt]`
  and `[tts]` `provider = "openai"`, point `providers.openai.base_url` at any
  local OpenAI-compatible server (Whisper, Parakeet, Voxtral, Kokoro), and
  point `[llm]` at a local model too (e.g. `llama-server`): then recognition,
  speech, rewrites and Kai run entirely on your machine, no key and no
  network required. See [Fully Offline](#fully-offline-local-models).

## What Gets Installed

The Linux installer keeps the app user-local but needs `sudo` for system
packages, uinput setup, and input-device access (Windows: see
[Windows](#windows)):

- Python package in `~/.local/share/voice-keyboard-venv`
- CLI symlinks in `~/.local/bin/voice-keyboard` and
  `~/.local/bin/voice-keyboard-daemon`
- Config in `~/.config/voice-keyboard/config.toml` with mode `600`
- User service in `~/.config/systemd/user/voice-keyboard-daemon.service`
- GNOME overlay extension in
  `~/.local/share/gnome-shell/extensions/voice-keyboard-overlay@liam-hennigan`
- Audio and clipboard packages from `apt-get`, `dnf` or `pacman` (PortAudio,
  libsndfile, libnotify, wl-clipboard, xclip, Tk, Python venv and headers),
  and a C compiler, because PyAudio and evdev are compiled from source: gcc
  on Fedora and Arch; on Debian and Ubuntu it comes with `python3-pip`
  (its recommended `build-essential`)
- The `uinput` module (loaded now and at boot), a udev rule giving the `input`
  group `/dev/uinput`, and `input` group membership for your user (the global
  hotkey needs it; that group can read every keyboard and mouse)

The installer enables the user service. It starts the daemon immediately only
when your speech settings are complete (a key for each selected provider, or a
local server) and the current login session already has effective `input`
group access, so after a first install you log out and back in once and it
starts with your session. If it says the service was enabled but not started
because no speech provider is set up yet, run `voice-keyboard setup` (or edit
the config), then run:

```bash
systemctl --user start voice-keyboard-daemon
```

## Requirements

Linux (Windows needs only Windows 10/11 — see [Windows](#windows)):

- Linux with the `uinput` kernel module
- Python 3.11+
- systemd user services for the default daemon installation
- Access to `/dev/uinput` for virtual keyboard injection
- Read access to `/dev/input/event*` for the built-in global hotkey listener
- PortAudio and PyAudio for microphone capture (PyAudio and evdev are
  compiled at install time: a C compiler and the PortAudio and Python
  headers; the installer adds them)
- libsndfile, `sounddevice`, and `numpy` for TTS playback
- `notify-send` for fallback status notifications
- `wl-clipboard` on Wayland, or `xclip` on X11, for reading selected text and
  for typing accents and emoji (pasted through the clipboard)
- AT-SPI bindings for the system `python3` (`python3-gi` and
  `gir1.2-atspi-2.0` on Debian/Ubuntu; usually present on GNOME) for the focus
  probe that tells a terminal from a chat or a document; without them every
  spoken line break types a space
- An API key for the selected STT provider and the selected TTS provider, or
  your own local OpenAI-compatible server for either (no key needed)

GNOME Shell 45–50 on Wayland is required only for the near-field recording overlay
and the on-screen Kai orb (Linux).
Dictation, TTS, manual commands, and fallback notifications are not GNOME-only.

## Quick Install

Download and run the release installer:

```bash
curl -L https://github.com/liamghennigan/HyperFurion-VK/releases/latest/download/install-hyperfurion-vk.sh -o install-hyperfurion-vk.sh
chmod +x install-hyperfurion-vk.sh
./install-hyperfurion-vk.sh
```

The release installer downloads the tagged source archive and runs the bundled
project installer. (`HyperFurion-VK-Setup.run`, above, carries the source
itself instead.) In a terminal, the installer ends in the settings
walkthrough, `voice-keyboard setup`, which you can re-run any time; it
offers a running llama.cpp server's model as your default `[llm]`. It looks
on ports 8080, 8081, 8000 and 8012 (and `$LLAMA_ARG_PORT`); set
`HFVK_LLAMA_URL=http://host:port` to point it elsewhere.

If you cloned this repository:

```bash
chmod +x install.sh
./install.sh
```

The installer supports `apt-get`, `dnf`, and `pacman`. On other distributions
it skips the package step with a warning: install a C compiler (gcc),
PortAudio (with headers), Python's venv and dev headers, Tk, libsndfile,
notify-send and wl-clipboard or xclip yourself first, and it does the rest.

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
convenient (name the providers too: with neither set, an install in a terminal
runs the interactive settings walkthrough and ignores the key):

```bash
VOICE_KEYBOARD_STT_PROVIDER=xai VOICE_KEYBOARD_TTS_PROVIDER=xai \
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
| `VOICE_KEYBOARD_NONINTERACTIVE` | `1` skips the settings walkthrough; providers and keys not set in the environment are still asked for in a terminal (without one, providers default to `xai`). |

With no provider variable set, `install.sh` runs the settings walkthrough
(`voice-keyboard setup`) on `/dev/tty`; with only some set, it asks for the
rest there. Either way its questions still work when it is launched by the
release installer or `curl … | bash`.

## macOS (Beta)

The daemon runs on macOS with native backends: keystroke injection uses
Quartz CGEvents (full Unicode — accents, CJK, emoji — typed as keystrokes,
where the Linux build pastes anything non-ASCII through the clipboard), the
global hotkey uses a listen-only keyboard event tap,
and the daemon runs as a launchd agent. From a checkout:

```bash
git clone https://github.com/liamghennigan/HyperFurion-VK
cd HyperFurion-VK
./packaging/macos/install-macos.sh
```

It needs Homebrew (for PortAudio) and, as `python3`, a Python 3.11 or newer
that allows `pip install --user` (python.org's installer does; Homebrew's
Python refuses it under PEP 668). The commands land in
`$(python3 -m site --user-base)/bin`, which may not be on your `PATH`. The
config starts as a copy of `config.toml.example` in
`~/.config/voice-keyboard/config.toml`: add your speech key (or point it at a
local server), or run `voice-keyboard setup`, then restart the agent with
`launchctl kickstart -k gui/$(id -u)/com.hyperfurion.voice-keyboard`.

macOS will require two permissions for your Python binary under
System Settings → Privacy & Security: **Accessibility** (hotkeys and
typing) and **Microphone**. There is no GNOME-style overlay or Kai orb; status
arrives as notification-center toasts. The focus check sees only the front
app's name, so password fields aren't detected and the register and what a
spoken "new line" presses are chosen by app name alone. Read-aloud reads the
clipboard on macOS: copy first.

Beta means beta: the platform layer is unit-tested, but it has not had the
months of daily driving the Linux build has. Issues welcome.

## Windows

A first-class port, not a wrapper: native `SendInput` typing (full Unicode, and
Enter/Tab are real keys), a low-level keyboard hook for the hotkeys, and a tray
app that draws the same overlay instrument as the GNOME build. Pure Python +
`ctypes` — no extra dependencies, no administrator rights. Windows 10 (1809+)
or 11.

### Install

In PowerShell:

```powershell
irm https://raw.githubusercontent.com/liamghennigan/HyperFurion-VK/main/packaging/windows/install-hyperfurion-vk.ps1 | iex
```

(From cmd: `powershell -ExecutionPolicy Bypass -c "irm https://raw.githubusercontent.com/liamghennigan/HyperFurion-VK/main/packaging/windows/install-hyperfurion-vk.ps1 | iex"`.)
From a checkout, `powershell -ExecutionPolicy Bypass -File
packaging\windows\install-hyperfurion-vk.ps1 -Source .` installs that checkout
(without `-Source` it installs the latest release). The installer:

- uses your 64-bit Python 3.11–3.13, or installs Python 3.12 for your user
  (winget, falling back to the python.org installer);
- installs into `%LOCALAPPDATA%\HyperFurion-VK` and puts a `voice-keyboard`
  command on your PATH (open a **new** terminal to use it);
- adds **HyperFurion VK** to the Start menu, to startup, and to
  **Settings › Apps** (where you can uninstall it);
- walks you through the settings (`voice-keyboard setup`): a running
  llama.cpp model offered as your default language model (otherwise xAI's
  Grok or a local server you name), how to transcribe — paste your own API
  key(s) (typed hidden), point at your own local speech server (with the
  model ids it serves), sign in with an
  existing hosted subscription (it isn't on sale right now), or skip for
  now — the hotkey, the language, Kai, dictation history, caret commands and
  self-corrections, then starts the app. Re-running it upgrades in place and
  keeps your settings (it offers the walkthrough again; Enter skips it).

The double-click download, `HyperFurion-VK-Setup.cmd`, is this same installer
(it installs the latest release, or the one it shipped with if GitHub can't be
asked); it honors the same environment variables.

Unattended: `-NonInteractive -Provider xai -ApiKey ...` (groq, deepgram, and
assemblyai have no voice: add `-TtsProvider xai -TtsApiKey ...`); also
`-Version vX.Y.Z`, `-Source <checkout>`, `-NoLaunch`, `-NoAutostart`. With
`irm | iex`, set `HYPERFURION_VK_VERSION` / `HYPERFURION_VK_SOURCE` /
`HYPERFURION_VK_NONINTERACTIVE=1` instead. Coming from the early beta? The installer stops it, removes its
startup launcher, and brings your settings and history along.

### Using it

| Do this | To |
| --- | --- |
| Tap **Ctrl+Alt+V** | start dictating; tap again to stop |
| Hold **Ctrl+Alt+V** | talk while held; release to stop |
| Hold **Right Ctrl** (or click the orb) | ask Kai; release to send — a tap cuts Kai off |
| Select text, press **Ctrl+Alt+R** | read it aloud; press again to stop |
| Left-click the tray icon | start/stop dictation in the app you were just in |
| Right-click the tray icon | the menu |

The V of Ctrl+Alt+V is consumed, so apps never see it (in Office it would open
Paste Special). **Ctrl+Alt+R** copies the selection with Ctrl+Insert (a copy key
apps honor that, unlike Ctrl+C, never means "interrupt") and then puts your
clipboard back exactly. It never presses anything in a terminal (yours too:
it follows `[registers.map]`), a password field it can recognize (a classic
Windows edit box; browser, Electron and UWP password boxes look like ordinary
fields to it), or a window it can't identify, and if the clipboard holds
something it can't save whole (a huge image) it leaves it alone. The copied
text does appear in clipboard history (Win+V), like any copy. The overlay pill appears next to the text cursor — STARTING,
LISTENING with the live caption and level meter, PROCESSING, INSERTED,
NO SIGNAL, ERROR — and never takes focus. The **Kai orb** sits bottom-right:
click to summon, drag to move (the spot is remembered); it never steals focus,
so Kai still knows which app you were in. The tray icon is cyan when idle, red
while recording, and amber when setup is needed.

Tray menu: Start/Stop dictation · Ask Kai · Read clipboard aloud · Show Kai
orb · Open settings file · Open logs folder · Start with Windows · Help ·
Restart · Quit.

### First run and settings

If there's no usable config yet (no key, or you skipped the question), the app
still starts: the tray icon is amber and its menu offers **Open settings file**
and **Sign in (existing hosted-service subscribers)** (the subscription isn't on
sale right now), or run `voice-keyboard setup` in a new terminal. Save a valid
config and dictation starts on its own — no restart. Provider, audio, and
hotkey changes later need **Restart** from the menu; Flow/register/LLM changes
apply at the next recording.

| What | Where |
| --- | --- |
| Settings | `%APPDATA%\voice-keyboard\config.toml` |
| Logs | `%LOCALAPPDATA%\voice-keyboard\logs\daemon.log` |
| History (when on), dictionary, Kai's memory (on by default) | `%LOCALAPPDATA%\voice-keyboard\` |
| The app | `%LOCALAPPDATA%\HyperFurion-VK\` |

The CLI works as on Linux (`voice-keyboard status`, `intent`, `ask`,
`history`, `devices`, `quit`, …). It talks to the app over loopback TCP, on a
free port the app picks each time it starts and publishes with a per-session
token in `%LOCALAPPDATA%\voice-keyboard\ipc-token-<session>` (other standard
users can't read it) — so nobody else can drive your keyboard, and every
signed-in session (even the same person's, over Remote Desktop) gets its own.
To watch the log live, quit the tray app and run
`%LOCALAPPDATA%\HyperFurion-VK\venv\Scripts\voice-keyboard-daemon.exe`: the
same app, with the log streaming in that terminal.

### Uninstall

**Settings › Apps › HyperFurion VK › Uninstall**, or:

```powershell
powershell -ExecutionPolicy Bypass -File "$env:LOCALAPPDATA\HyperFurion-VK\uninstall.ps1"
```

Add `-Purge` to also delete your settings, history, dictionary and Kai's
memory.

### Windows troubleshooting

- **Nothing types into an admin window.** Windows blocks input from normal
  apps into elevated (administrator) windows, and hides their keystrokes from
  the hotkey too. Use a normal window, or run HyperFurion VK as administrator
  for that session. The same goes for the UAC prompt and the lock screen.
- **NO SIGNAL every time.** If the pill says the mic sent pure silence, turn on
  *Settings › Privacy & security › Microphone › Let desktop apps access your
  microphone*. To pick a specific mic, run `voice-keyboard devices` and set
  `[audio] device_name`.
- **The overlay sits low in the window instead of at the cursor.** That app
  draws its own text cursor (some UWP and Electron apps) — dictation still goes
  to the right place.
- **`voice-keyboard` isn't found.** Open a new terminal (PATH changes reach new
  ones only), or run `%LOCALAPPDATA%\HyperFurion-VK\bin\voice-keyboard.cmd`.
- **Antivirus warning.** A global keyboard hook plus synthetic typing is what
  keyloggers do too; some antivirus tools flag the pattern. The code is all
  here to read; allow `pythonw.exe` under `%LOCALAPPDATA%\HyperFurion-VK`.
- **Still stuck?** Run `voice-keyboard doctor`: it checks the settings, the
  microphone and the app, and names the fix. Otherwise Tray › Open logs folder,
  and attach `daemon.log` to an issue.

## iOS — Why Not (Yet)

Honestly: a system-wide voice keyboard **cannot exist on iOS**. Apps are
sandboxed away from other apps' input; there is no uinput, no SendInput, no
event taps. The only sanctioned path is a **custom keyboard extension** — a
separate Swift app distributed through the App Store, which is a different
product with a different codebase, not a port of this daemon. It's a
plausible future project (the relay in `relay/` would slot right in as its
backend); it is not a checkbox. Nothing on this page will claim iOS
support until that app exists.

## First Run Checklist

On Linux (Windows: [First run and settings](#first-run-and-settings); macOS:
[macOS (Beta)](#macos-beta)):

1. Make sure `~/.local/bin` is on your shell `PATH`.

   ```bash
   command -v voice-keyboard
   ```

2. If the installer added you to the `input` group, log out and back in. Group
   membership changes do not fully apply to the current desktop session.

3. If setup was skipped or saved no speech key or local server, run
   `voice-keyboard setup` first: the daemon can't start without one. Then
   start or restart the user service:

   ```bash
   systemctl --user restart voice-keyboard-daemon
   ```

4. Confirm the daemon is reachable:

   ```bash
   voice-keyboard status
   ```

   It prints `idle` or `recording`, then a line with the providers, register
   and focused app (and the last error, if there was one).

5. Test dictation: click into a text field (a text editor, say), tap
   `Ctrl+Alt+V`, speak for a few seconds, and tap it again. From a terminal
   instead, speak while this waits: it prints what it heard, and the words land
   at your next prompt, unrun:

   ```bash
   voice-keyboard start && sleep 5 && voice-keyboard stop
   ```

   Nothing typed? `voice-keyboard doctor` checks the config, microphone,
   typing access, daemon and speech provider, and names the fix.

6. Test TTS by selecting text in any app:

   ```bash
   voice-keyboard tts
   ```

## Usage

### Voice Input

The daemon listens for the configured hotkey. By default:

- Tap `Ctrl+Alt+V` once to start recording.
- Tap `Ctrl+Alt+V` again to stop. With xAI (the default), the hosted
  subscription or a local server, the words have been typing as you spoke and the last ones
  land now; with OpenAI, Groq, Deepgram or AssemblyAI the whole dictation is
  transcribed and typed when you stop.
- Hold `Ctrl+Alt+V` for hold-to-talk. Recording starts after the hold threshold
  and stops when you release the keys.

Nothing starts until you ask: the microphone opens only on this hotkey, Kai's
(hold Right Ctrl), the on-screen orb or the commands below. Nothing listens in
the background unless you turn on Kai's wake word (`[wake]`).

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

`transform` needs a language model (`[llm]`: xAI's Grok with your xAI key, or
a local one). `transform` and `recall` act on whatever has focus when they run,
so bind them to a desktop shortcut and press it in the app you dictated into.
From a terminal, `recall` types into the terminal, and `transform` puts the
rewrite on your clipboard when it sees that focus has moved.

### Text-To-Speech

Select text in an app, then run:

```bash
voice-keyboard tts
```

Important: on Linux, TTS reads the **primary selection**, not the clipboard. On
most Linux desktops, selecting text with the mouse or keyboard is enough.
Copying text with `Ctrl+C` is not required and may not help if nothing is
selected. Windows and macOS have no primary selection: there
`voice-keyboard tts` reads the clipboard (copy first), and Windows' `Ctrl+Alt+R`
copies the selection for you. It speaks with your `[tts]` provider: xAI by
default, OpenAI, ElevenLabs, or a local OpenAI-compatible server.

### A Hotkey For TTS

Let the daemon own it — this works on any desktop. It is the default on
Windows; on Linux and macOS there is no read-aloud hotkey until you set one:

```toml
[tts]
hotkey = "control+alt+r"   # press to read the selection aloud, again to stop
```

Or bind a desktop shortcut to:

```bash
voice-keyboard tts
```

On GNOME, open:

```text
Settings > Keyboard > View and Customize Shortcuts > Custom Shortcuts
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
- **Live molten injection** (streaming providers: `xai`, a local
  OpenAI-compatible server via `live_rest`, and `hyperfurion`, the hosted
  subscription, for existing subscribers): text streams into the focused
  field while you speak; other providers type the whole dictation when you
  stop. Words stay *molten* for a stability window (default 1.5 s); when the
  provider revises a molten word, the daemon backspaces to the divergence point and
  retypes — the text repairs itself in front of you. Once a word survives
  the window it *freezes*: a later guess from the recognizer never retypes
  it (your own spoken edits still can), so repairs stay short and your
  caret never runs away. `enabled = false` under `[flow]` turns all of this
  off: the transcript is typed once, as recognized, when you stop.

While recording, the overlay pill (GNOME and Windows) becomes a live
caption: a small VU meter plus the molten tail of the transcript, updating
as you speak.

**Pauses.** Streaming recognizers end a sentence wherever you pause — xAI's
grok-voice-transcribe-2.0 punctuates each pause-delimited chunk as a sentence
of its own, so "I think … we should wait" arrives as "I think. We should
wait." Flow keeps the period at a pause molten until the next words decide
it: clear cases by rule ("… the. Project", "… X. And Y"), the rest by a quick
review through `[llm]` when it's usable (only the two words around the pause
are sent). `[flow] pause_review = "rules"` sends nothing; `"off"` keeps the
recognizer's periods.

### The spoken grammar

Commands and punctuation are English by default; `[flow] language = "es"`,
`"fr"` or `"de"` (setup sets it from the language you dictate in) adds that
language's punctuation, "new line", "new paragraph" and "scratch that" on
top ("punto", "abre interrogación" → `¿`, "virgule", "à la ligne",
"Komma", "neue Zeile", "borra eso" / "efface ça" / "streich das"). The
other commands, and numbers, dates and units, stay English.

| You say | You get |
| --- | --- |
| `scratch that` / `delete that` | deletes the last utterance segment (works on already-typed text); said alone in a new recording, takes back the previous dictation — same app, within 30 seconds |
| `new line` / `new paragraph` | `\n` / `\n\n`, pressed the way the field needs it ([line breaks](#line-breaks-enter-or-shiftenter)): Enter in documents and most apps, Shift+Enter in chats and web pages, and nothing in a terminal it recognizes (where a line break is Enter and would run the line), a one-line field or a spreadsheet |
| `quote ship it unquote`, `quote … end quote` | `"ship it"` — only with words between ("his quote unquote friend" stays prose), within one utterance |
| `correct monday to friday` (said on its own) | the last "monday" in this dictation becomes "Friday", capitals kept — and the pair goes to `voice-keyboard learned`; with nothing to correct, it is typed as words |
| `cap that`, `uppercase that`, `lowercase that` (said on their own) | the last utterance in Title Case, UPPER or lower, fixed in place |
| `new bullet` | `- ` on a new line (right there after a line break); nothing in a terminal or in code |
| `new heading`, `new subheading`, `new checkbox` | `# `, `## `, `- [ ] ` on a new line — markdown, and live headings and to-dos in Notion and Obsidian; nothing in a terminal or in code |
| `new number` | `1. `, then `2. `, … on a new line; a new paragraph starts the count again; nothing in a terminal or in code |
| `emoji rocket`, `emoji thumbs up`, `emoji fire`, … | 🚀 👍 🔥 — 27 built in (`DEFAULT_EMOJI` in `flow/grammar.py`); `[flow.vocabulary]` adds more; in a terminal or code they stay words |
| `period`, `comma`, `question mark`, `em dash`, `open quote`, … | the glyph, correctly spaced |
| `literal period` | the word "period" |
| `twenty three` (terminal register, or `numbers = "always"`) | `23` — also decimals ("three point one four") and digit runs ("one two seven" → `127`) |
| `twenty five percent`, `five dollars`, `three thirty pm` (prose) | `25%`, `$5` (and "… and fifty cents" → `$5.50`; euros `€`, yen `¥`; `$100,000`; "one point five dollars" → `$1.50`; "three point two billion dollars" → `$3.2 billion`; "a hundred and fifty dollars" → `$150`), `3:30 PM`; `october sixth` → `October 6`; seven or ten digits read one by one → `555-1234`, `415-555-1212` — prose keeps other numbers as words; a unit right after makes the reading certain |
| `nineteen eighty four`, `june fifth nineteen ninety nine` (prose) | `1984`, `June 5, 1999` — a 20xx year needs all three words ("twenty twenty six" → `2026`; "twenty twenty vision" stays words), and a count noun after keeps it words ("nineteen forty people") |
| `at three thirty`, `five o'clock` (prose) | `at 3:30`, `5 o'clock` — a bare hour stays a word ("at three") |
| `room four oh two`, `page twenty five`, `version three point one point four`, `q three`, `two point five megabytes` (prose) | `room 402`, `page 25`, `version 3.1.4`, `Q3`, `2.5 MB` — a number after a noun that names (room, page, chapter, floor, gate, flight, step, version, …) |
| `example dot com slash docs` | `example.com/docs` — a path only follows a whole domain |
| "the the meeting", "I I think" (prose) | "The meeting", "I think" — a stutter on a word speakers restart on; never a repeat a sentence means ("had had", "told you you were") |
| `VK, make that formal` (end of an utterance, or alone) | rewrites the preceding dictation in place via `[llm]` |
| `VK, my email` (alone, or at the end: "send it to VK, my email") | types the text saved under that name in `[snippets]` (or a macro you named via `voice-keyboard learned`), exactly, after your words; `{date}`, `{isodate}`, `{time}`, `{weekday}` in a snippet fill in when typed |
| `VK, make this shorter` with text **selected** (alone) | rewrites the selection in any app on Linux and Windows (not terminals) via `[llm]` and types the answer over it; your app's undo brings the original back |
| `spell that n g i n x` | replaces the last word with the spelled one — letters, NATO words ("november golf"), `capital k`, spoken digits, or one capitalized token when the recognizer merged them ("NGINX") |
| `spell k eight s` | types the spelled word (`k8s`) |
| `snake case user id`, `camel case get user name`, `pascal case http client` (in code and terminal registers; `[flow] formatters = "everywhere"` for prose too) | `user_id`, `getUserName`, `HttpClient` — also `kebab case`, `constant case`, `dot case`, `title case`, `all caps`, `no space`; the words up to a pause, punctuation or another command (in code, an operator word, a keyword or a callable ends the run too: "for snake case row count in range ten colon" → `for row_count in range(10):`) |
| `liam at example dot com`, `docs dot python dot org` | `liam@example.com`, `docs.python.org` — never auto-capitalized; only runs ending in a known top-level domain (`[flow] addresses`) |
| "um", "uh" and the like | nothing — hesitation sounds the recognizer writes down are dropped, with the commas around them (`[flow] fillers`) |
| "Tuesday, no wait, Wednesday" | "Wednesday" — with `[flow] corrections = "llm"`, a dictation with a correction cue is tidied by `[llm]` at stop; the answer may only delete words |

The commands (`scratch that`, `new line`, `cap that`, `new bullet`, …)
and the punctuation words are remappable and removable in config
(`[flow.commands]`, `[flow.punctuation]`), `[flow.vocabulary]` expands your
own phrases ("hyper furion" → "HyperFurion"), and the wake word is
configurable.
Each `spell that …` fix is offered under `voice-keyboard learned` as a
correction (and hotword) candidate. It is never applied on its own: one
spelled word is not a rule ("their" → "there"). `spelling = false` under
`[flow]` turns spelling off.

**Stop, think, go again.** A recording that starts within 30 seconds of
the last one, in the same app and the same prose register, continues its
text — a space before the first word, a capital only after a sentence
end — instead of gluing itself to it. Terminals and code registers never
get a leading space. `[flow] rejoin = false` turns it off.

**Rewrite any selection.** Highlight text in any editor, browser field or
chat box, press the hotkey and say only the instruction — "VK, make this
shorter", "VK, fix the grammar", "VK, translate to German". The selection
and the instruction go to `[llm]`, and the answer is typed over the
selection (typing replaces a selection everywhere); the app's own undo
restores the original. One paragraph at a time: a multi-line selection is
refused, because typing its line breaks would press Enter. On Linux the selection is read from the focused
widget through accessibility, and only when you ask — the probe that runs
at every recording never reads the text in a field (only the app, the
window title and a browser page's address, to choose how a line break
is pressed), and the PRIMARY
selection, which can belong to another window, is never used — and on
Windows it is copied from the focused app with your clipboard put back. Terminals and password fields never
take part, a single-line selection never gains an Enter (in a chat box
that would send it), and a selection over 4000 characters is refused. Not on macOS
yet; there, an instruction alone rewrites your previous dictation, as it
does everywhere when nothing is selected.

### Caret commands (hands-free navigation)

Off by default: `enabled = true` under `[nav]`, or say yes when
`voice-keyboard setup` asks. Say a command as an
utterance of its own, with a pause before and after. In the middle of a
sentence, "delete the previous word" is just typed as words.

| You say | Editors: Linux and Windows / macOS | Terminals: Linux / macOS / Windows |
| --- | --- | --- |
| `go left` / `go right [N] [words]` | arrows, `ctrl+←/→` / `option+←/→` | arrows, `alt+b/f` / `Esc b`, `Esc f` / `ctrl+←/→` |
| `go up` / `go down [N] lines` | arrows | refused (that's shell history: say `press up`) |
| `go to start` / `end of line` | `home` / `end`, or `cmd+←/→` | `ctrl+a` / `ctrl+e`, or `home` / `end` on Windows |
| `go to start` / `end of document` | `ctrl+home` / `ctrl+end`, or `cmd+↑/↓` | — |
| `select previous` / `next [N] words`, `select all`, `select line` | `shift` + the motion, `ctrl+a` / `cmd+a` | refused: a terminal has no selection to extend |
| `delete previous` / `next [N] words`, `delete line` | `ctrl+backspace` / `ctrl+delete`, or `option+backspace` / `option+delete` | `ctrl+w` / `alt+d` (`Esc d` on macOS), or `ctrl+backspace` / `ctrl+delete` on Windows |
| `press tab`, `press escape twice`, `press page down` | that key | that key |
| `select that` | `shift+left` once per character of what you just said (up to 400; never across emoji) — then dictate over it, or say "VK, make that formal" | refused: a terminal has no selection |
| `undo that`, `redo that`, `paste that` (`twice`, `three times`) | `ctrl+z` / `ctrl+shift+z` (`ctrl+y` on Windows) / `ctrl+v`; `cmd` on a Mac | refused: a paste can carry a line break, which runs the line |

- Text you dictated before the command is on screen before the keys are
  pressed. After the keys, dictation starts a new segment: repairs and
  `scratch that` never reach behind the caret's old position.
- After `select …`, `delete …` or `go to start of …`, the next word has
  no leading space. After `press tab`, `press escape` or `press page
  up`/`down`, dictation starts as if in a new field.
- With hold-to-talk, the keys fire after you let go of the hotkey, so the
  hotkey's modifiers never combine with them (if a modifier is still held
  at stop, the command is skipped). The overlay shows each command as it
  fires; a command that can't run in this app changes nothing.
- **Enter is never pressed.** No command produces it, and a `[nav.keys]`
  override that would press it (`enter`, `ctrl+m`, `ctrl+j`, `ctrl+o`) is
  rejected, as is an unknown key name.
- Remap or disable a command per kind of app with `[nav.keys.editor]` and
  `[nav.keys.terminal]`, for example `"move:word:left" = "ctrl+left"`.
- On macOS the chords go through Quartz like typed text does; in a
  terminal the readline word motions are sent as `Esc b` / `Esc f` so
  they work whether or not Option is set to send Meta. Accessibility
  permission covers it (the same one typing needs).

### Context registers

At recording start the daemon probes the focused app — AT-SPI on Linux,
Quartz on macOS, Win32 on Windows — and picks a register:

| Register | Behavior |
| --- | --- |
| `prose` (default) | smart capitalization and punctuation spacing |
| `terminal` | no auto-caps, numbers as digits; on Linux, accents and emoji are pasted with `Ctrl+Shift+V` |
| `verbatim` | grammar off; words exactly as recognized |
| `python` | compiles speech: "for i in range ten colon" → `for i in range(10):`, "for i in range len xs colon" → `for i in range(len(xs)):`, "if a double equals b colon" → `if a == b:` (also `!=`, `<`, `>`, `+=`, `<=`), "if x is not none colon" → `if x is not None:`, "raise value error" → `raise ValueError`, "print open paren f quote hi unquote close paren" → `print(f"hi")`, "arrow str colon" → `-> str:` |
| `javascript` | compiles speech: "const add equals open paren a comma b close paren arrow a plus b" → `const add = (a, b) => a + b`, "x triple equals null and and y not equals z" → `x === null && y !== z`, "a or or b" → `a || b`, "console dot log open paren" → `console.log(` |
| `shell` | compiles speech: "pipe grep dash i error" → `| grep -i error`, "docker run dash dash rm dash p eight zero eight zero colon eighty" → `docker run --rm -p 8080:80`, "cd dot dot slash src and and ls" → `cd ../src && ls`, "find dot dash name star dot py" → `find . -name *.py`, "chmod plus x dot slash run dot sh" → `chmod +x ./run.sh`, "echo hi greater than out dot txt" → `echo hi > out.txt` |

Known terminals (kitty, alacritty, foot, konsole, GNOME Terminal, wezterm,
Windows Terminal, iTerm2, …) map to `terminal` automatically; override or
extend per app in `[registers.map]` (that is also the only way to get
`python`, `javascript` or `shell`: nothing picks a code register on its
own). A line break is never pressed in a terminal it recognizes (whatever
register you map it to), or wherever the focused app couldn't be
identified — there "new line" types a space. If that happens everywhere
on Linux, the AT-SPI bindings are missing; `voice-keyboard doctor` says
so. A terminal it doesn't know (PuTTY, say) looks like any other app and
gets Enter, which runs the line: map it to `terminal` in
`[registers.map]`.

While words type live, a focus check looks every 1.5 seconds: once it
sees a different app, typing freezes and at stop the whole transcript goes
to the clipboard, replacing what was on it (a few words can land in the
other app before it looks). A dictation typed when you stop (a provider
that doesn't stream, or `[flow] live = false`) looks at focus once more
right before it types: if another app has focus, nothing is typed and the
whole transcript goes to the clipboard, replacing what was on it (with no
clipboard tool, nothing is typed at all). On Linux, and in classic Win32
edit boxes on Windows, the probe also sees the focused *widget*: a
password field there forces `verbatim`, is never written to the history
ledger, and never contributes biasing context.

#### Line breaks: Enter or Shift+Enter

Every line break dictation types (`new line`, `new paragraph`, a new bullet,
number, checkbox or heading) is a key press, and in many fields Enter does
something else: it sends a chat message or submits a form. So each recording
first works out what it is typing into and presses, for a line break:

1. **Nothing** in a terminal it recognizes or where the focused app
   couldn't be identified (a space is typed, as above), and **nothing** in
   a one-line field (a search box, a form input, an address bar), where
   Enter would submit it.
2. **Your rules** in `[registers.newline]` (below), and `[registers] chat_apps`.
3. **Shift+Enter** in chat apps where Enter sends (Slack, Discord, Teams,
   Signal, Telegram, WhatsApp, Element, Mattermost, Messenger, Zoom, the
   ChatGPT and Claude desktop apps, …), so "new line" never sends half a
   message or prompt.
4. **Nothing** in a spreadsheet (Excel, LibreOffice Calc, Gnumeric; Google
   Sheets, Excel for the web, Zoho Sheet and Smartsheet in a browser):
   there Enter, and Shift+Enter, commit the cell and move the selection,
   and the rest of the dictation would replace the next cell.
5. In a **web browser** (Chrome, Chromium, Edge, Firefox, Brave, Vivaldi,
   Opera, LibreWolf, Zen, GNOME Web, Safari, …), by the page: **Shift+Enter**
   on chat sites (WhatsApp Web, Slack, Discord, Teams, Messenger, Telegram
   Web, Google Chat and Messages, Element, Mattermost, Zulip, Rocket.Chat,
   ChatGPT, Claude, Gemini, Copilot, Perplexity, LinkedIn, X, Instagram);
   **Enter** in document editors (Google Docs and Slides, Notion, Word for
   the web, Confluence, Dropbox Paper, Coda, Quip), so paragraphs and lists
   work; and **Shift+Enter** on any other page: a plain line break in a text
   box, a soft break in a rich editor, and not a send in a chat box.
6. **Enter** in every other app.

The page is recognized by its address where the browser exposes it to
accessibility (Linux: Firefox and Chromium), otherwise by the tab title in
the window title (Windows, and Linux as a fallback), matching a site's name
only as a whole part of the title, so a Google Doc called "Slack notes"
stays a document. When a page could be more than one of these, the safer
choice wins: a sheet over a chat, a chat over a document. While words type
live, if you switch to another tab or field of the same app, line breaks
only ever get stricter (Enter → Shift+Enter → nothing) for the rest of
that recording, once the focus check notices (it looks every 1.5
seconds); a switch to another app freezes typing, as above. A dictation
typed when you stop looks once more just before it types: another tab or
field of the same app only makes line breaks stricter, focus it can't
identify gets a space, and another app gets nothing (the transcript goes
to the clipboard). One-line
fields (and search boxes with suggestions) are recognized on Linux through
accessibility and on Windows in classic Win32 edit boxes; elsewhere (most
web forms and the address bar on Windows) a browser's Shift+Enter can
still submit a one-line field. On macOS (beta) only the app is known: chat
apps and browsers get Shift+Enter, spreadsheets nothing, other apps Enter.
The title and address are used for this choice only, and never logged or
stored. Text typed outside
a recording (`voice-keyboard recall` and the `type` IPC command) follows
the same rules, including a space where focus can't be identified;
`voice-keyboard transform` types every line break as a space; an
integrator presses Enter itself with the `key` IPC command.

It goes by what it can see, and can guess wrong: a chat app it doesn't
know gets Enter, which sends (add it to `chat_apps`); a terminal it
doesn't know (PuTTY, an editor's terminal panel, a shell in a browser
tab) can run the line; and a web editor's own comment or chat box gets
Enter, like the document around it.

Set your own with `[registers.newline]`: an app, a site, or a name from a
browser tab's title, mapped to `"enter"`, `"shift+enter"` or `"none"`. A
site rule needs the page's address, which only Linux reads (Firefox and
Chromium); on Windows, use the name the tab's title shows. A site
beats a title, a title beats an app, and rules can't make a terminal or a
one-line field press Enter. A title rule that says `"enter"` is skipped on
a chat page unless it names that chat: `notion = "enter"` doesn't send in
a Slack workspace called Notion, while `slack = "enter"` applies in Slack.

```toml
[registers.newline]
"notion.so" = "shift+enter"             # soft breaks in Notion
"mail.google.com" = "none"
obsidian = "shift+enter"
excel = "enter"                         # "new line" moves down a row
firefox = "enter"                       # every Firefox tab: Enter, as before 2.4
```

`chat_apps = ["mychat"]` under `[registers]` still works, as shorthand for
`mychat = "shift+enter"`.

### The next-level channels

These are off by default wherever they would change behavior, with one
exception: Kai is on (push-to-talk, so the mic still opens only when you
ask), and it keeps a local memory. See `ROADMAP.md` for the doctrine and
`config.toml.example` for every key:

- **Hotword biasing** (`[stt] hotword_bias`, off by default) — recognition
  is biased toward the vocabulary you accepted via `voice-keyboard learned`,
  on REST providers (OpenAI-style `prompt`, Deepgram `keyterm`/`keywords`,
  AssemblyAI `word_boost`); the streaming xAI connection has no such
  parameter. Curated words only — screen text is never
  harvested; dictation is new thought, not a continuation of what is on
  screen. Assembled per session, never stored.
- **A keyboard that learns you** (`voice-keyboard learned`) — corrections
  are mined from the opt-in history ledger, and every `spell that` or
  `correct … to …` fix is offered too, ledger or not; nothing applies until
  you accept it, then it merges into the grammar vocabulary
  (`[flow] personal_dictionary`). All of it lives in
  `~/.local/state/voice-keyboard/dictionary.json`, mode 600.
- **Semantic registers** (`python`, `shell`, `javascript`) — deterministic
  spoken-code compilation, no model in the loop; map an app to one in
  `[registers.map]`.
- **Molten diffs** (`[flow] rewrite_pending`, off by default) — a "VK, …"
  rewrite is held pending; say "keep it" or "scratch that" (CLI: `keep` /
  `discard`). No edit is real until it freezes.
- **Type, never execute** (`[intent]`, `voice-keyboard intent "…"`) —
  with `enabled = true` under `[intent]` (off by default; the CLI always
  works), "VK, run …" has `[llm]` draft ONE command line and types it at
  your prompt, and cannot press Enter: the refusal is enforced inside the
  keystroke injector on every path (keycode, newline, clipboard paste).
  Your keypress is the consent.
- **Ambient containment** (`[ambient]`, experimental, off by default) — in
  a long-open session, only utterances that start with the address word
  are typed; room speech is still transcribed by your provider, but never
  reaches the grammar, the screen or the ledger.
- **Kai — the voice assistant** (`[assistant]`) — the keyboard grows a voice
  assistant, on by default and push-to-talk. Summon it three ways: **hold
  Right Ctrl** and release to send (a bare modifier types nothing, so
  nothing leaks into a terminal; configurable — chords like
  `control+alt+.` work but terminals see escape codes when they're held),
  **click** the always-on Kai orb the overlay draws on screen (GNOME and
  Windows), or (opt-in `[wake]`, with a wake model you train) say the local
  **wake word "Kai"**. (On Wayland the daemon often
  can't see the focused app — GPU terminals expose no accessibility — so
  when focus is unknown Kai still compiles commands from clearly-runnable
  requests and answers everything else; toggle with `terminal_fallback`.) Kai routes your query by where you are: **in a
  terminal it recognizes**, a request that reads as a command becomes one,
  typed at the prompt, and Kai **never presses Enter — that is yours** (a
  question is answered instead); **anywhere else**, it answers you aloud:
  through your xAI Voice Agent Builder agent when you set `agent_id`,
  otherwise through `[llm]` and your read-aloud voice. Kai keeps your
  questions and its answers on this computer
  (`assistant-memory.sqlite3` in `~/.local/state/voice-keyboard/`, or
  `%LOCALAPPDATA%\voice-keyboard\` on Windows); set
  `memory_enabled = false` under `[assistant]` and nothing is stored (not
  even the file). When dictation history is on, Kai also looks up the
  ledger entries related to your question and sends them to `[llm]` with
  it, as context.
  An earcon confirms the mic is live; the turn runs off the hotkey path, so
  a second tap barges in
  and cuts Kai off. Voice in, voice or a drafted command out — you never
  type to it. Frontier brain, local hands, you own the Enter key.
- **Wake word "Kai"** (`[wake]`, opt-in, default off) — a tiny **local**
  openWakeWord detector summons Kai hands-free; nothing is transcribed and
  nothing leaves the box until it fires. It's the one path that keeps the
  mic warm, so it stays behind an explicit switch; the hotkey remains the
  hard mute. No "Kai" model ships yet: make one with
  `scripts/train_kai_wakeword.py` and set `model_path` (until then
  openWakeWord's pretrained words stand in, for testing). It needs
  openWakeWord in the keyboard's own environment, and openWakeWord's
  models, downloaded once; after the Linux installer, with
  `VENV=~/.local/share/voice-keyboard-venv`, that is
  `$VENV/bin/pip install "voice-keyboard[wake]"` then
  `$VENV/bin/python -c "import openwakeword.utils as u; u.download_models()"`.
  On Linux with Python 3.12 or newer the first step fails (openWakeWord 0.6
  needs `tflite-runtime`, which stops at 3.11): `config.toml.example`'s
  `[wake]` section has the route that works there.
- **Total recall** (`[recall]`, `voice-keyboard find "…"`) — search what
  you dictated while `history = true` (the last 500 ledger entries).
  Keyword search out of the box; point it at a local Ollama `/embeddings`
  endpoint and it becomes semantic, fully on-box. With `enabled = true`
  under `[recall]`, "VK, recall the relay caps" speaks the best match.
- **The multiplayer keyboard** (`[remote_mic]`, experimental) — the
  daemon serves a one-page LAN mic over self-signed HTTPS; your phone
  streams audio into a normal dictation session on the desktop. The phone
  talks only to your desktop (no relay, no account); from there the audio
  goes to your `[stt]` provider like any dictation.
- **Procedural memory** (`voice-keyboard learned`) — texts you dictate
  again and again (in the history ledger) surface as macro candidates;
  name one (`learned macro N trailer`) and "VK, trailer" types it
  verbatim.
  Offered, never imposed — the same consent gate as corrections.
- **Speculative TTS** (`[tts] prefetch`, Linux) — the primary selection is
  synthesized *while you are still highlighting it*, so `voice-keyboard
  tts` starts instantly on a cache hit. Off by default; `"auto"` prefetches
  only against a local endpoint (free); `"always"` opts in cloud (spends
  tokens on selections never played, and sends selection text before you
  ask).
- **Preedit is molten** (`SPOKEN-INPUT-PROTOCOL.md`) — the input-method
  mapping layer (`voice_keyboard/imethod.py`) that renders molten text as
  IM preedit and freeze as commit. It is tested but not wired to any input
  method yet; hooking into the desktop's typing stack will be a deliberate,
  separate opt-in.

### Unicode on Linux

The uinput injector types anything: plain ASCII goes through the fast key
path, and any other run (accents, CJK, emoji, em-dashes) is pasted via the
clipboard — `wl-copy` (Wayland) or `xclip` (X11), which the installer
adds — with your previous clipboard text put back afterwards. Terminals
get the `Ctrl+Shift+V` chord via the register. Clipboard managers may
briefly see the transient entry; if no clipboard tool is installed,
non-ASCII is dropped with a warning.

### Hands-free and recall

- `auto_stop_ms = 1200` under `[flow]` ends recording by itself after ~1.2 s
  of silence: tap, speak, done. Off by default (`0`).
- `voice-keyboard transform "make it friendlier"` rewrites the last
  dictation in place through `[llm]`: it backspaces over that text, so use
  it while the caret is still right after it.
- `history = true` under `[flow]` keeps an append-only local ledger
  (`~/.local/state/voice-keyboard/history.jsonl`, mode 600).
  `voice-keyboard history` lists; `voice-keyboard recall 2` re-types the
  second-most-recent entry. Off by default.
- `voice-keyboard status` reports the speech and read-aloud providers,
  the register, flow state, the focused app and the last error.
- `voice-keyboard try [register:] <words…>` prints what the keyboard would
  type for those words with your config — no microphone needed; `|` is a
  pause, so quote it in a shell
  (`try "see you monday | correct monday to friday"`), and a leading
  register picks one, inside the quotes or outside them
  (`try "python: x equals five"`). Words you accepted with
  `voice-keyboard learned` apply, as in dictation (with `[flow]
  personal_dictionary` on, the default; `[flow.vocabulary]` wins), and
  "VK, my email" shows the snippet or macro it would type, with a
  `[snippet: my email]` note. With no words it reads one
  dictation per line: a prompt to play at, or a batch from a pipe
  (`voice-keyboard try < phrases.txt`).
- `voice-keyboard commands [filter]` prints everything you can say, built
  from the same tables the engine uses with your config, the words you
  taught it and your named macros merged in —
  `voice-keyboard commands percent` shows just the matching lines.
- `voice-keyboard stats` shows latency percentiles (p50/p95/max) over the
  last 200 dictations:
  - speech → first transcript (the recognizer);
  - first transcript → first keystroke (VK itself);
  - speech → screen;
  - stop → settled.

  Add `--json` for the raw numbers. `latency_log = true` under `[flow]`
  also appends them to `latency.jsonl` (numbers, the app and the register;
  never text).

`[flow]`, `[registers]`, `[nav]`, `[llm]`, `[intent]`, `[ambient]`,
`[ask]`, `[recall]` and `[assistant]` edits hot-reload at the next
recording — no daemon restart. Providers, audio, the hotkeys (Kai's
included), `[snippets]` and `[polish.map]` still need one (see
[Configuration](#configuration)).

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
- A navigation command needs its own final segment. Recognizers that
  finalize on fixed time chunks can split a command or merge it with the
  words around it. When that happens it is typed as words.
- Old Win32 EDIT controls (classic dialogs) don't understand
  `ctrl+backspace`; "delete previous word" types a box there. Modern apps,
  browsers and Notepad handle it.

## Configuration

Edit:

```bash
~/.config/voice-keyboard/config.toml
```

On Windows it is `%APPDATA%\voice-keyboard\config.toml`. If
`XDG_CONFIG_HOME` is set, the config lives at
`$XDG_CONFIG_HOME/voice-keyboard/config.toml` instead. `voice-keyboard setup`
walks through the common settings and writes them there.

On Linux and macOS the installer starts it from `config.toml.example`, which
documents every option; on Windows it starts from a short file that links to
it. Anything you leave out keeps its default. The provider, audio, hotkey and
daemon sections:

```toml
[providers.xai]
api_key = "xai-your-api-key-here"

[providers.openai]
api_key = "openai-your-api-key-here"
# Point at any OpenAI-compatible server; one at a local address needs no
# api_key. Fully offline also needs [stt], [tts] and [llm] pointed at local
# servers (see Fully Offline below).
# base_url = "http://localhost:8000/v1"

[providers.groq]
api_key = "groq-your-api-key-here"

[providers.deepgram]
api_key = "deepgram-your-api-key-here"

[providers.assemblyai]
api_key = "assemblyai-your-api-key-here"

[providers.elevenlabs]
api_key = "elevenlabs-your-api-key-here"

# Hosted subscription: not on sale right now (existing subscribers only),
# so it comes last. See relay/README.md.
[providers.hyperfurion]
api_key = "hfk-your-subscription-key-here"
# Only set base_url if you run your own relay.
# base_url = "https://api.hyperfurion.com"

[stt]
# Choices: xai, openai, groq, deepgram, assemblyai, and hyperfurion (the
# hosted service; existing subscribers only)
provider = "xai"
# Leave empty for the provider default.
model = ""
language = "en"
interim_results = true

[tts]
# Choices: xai, openai, elevenlabs, and hyperfurion (the hosted service;
# existing subscribers only)
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

Edits to `[flow]`, `[registers]`, `[llm]`, `[intent]`, `[nav]`, `[ambient]`,
`[ask]`, `[recall]` and most of `[assistant]` (not its hotkey) apply at the
next recording. Anything else — providers, API keys, audio, hotkeys, `[snippets]`,
`[polish.map]`, the daemon socket — needs a restart (on Windows, **Restart** in
the tray menu; on macOS, `launchctl kickstart -k
gui/$(id -u)/com.hyperfurion.voice-keyboard`):

```bash
systemctl --user restart voice-keyboard-daemon
```

## Providers

**xAI is the default provider** for speech-to-text, text-to-speech and the
`[llm]` language model (Grok, on the same key) — it is what HyperFurion VK is
built and daily-driven on. Any provider below
works: HyperFurion VK is provider-selectable and model-configurable, and model
IDs are plain config values, so a compatible provider model can be changed
without code changes.

### Speech-To-Text

| Provider | Config value | Default model |
| --- | --- | --- |
| xAI (default) | `xai` | Provider default. |
| HyperFurion (hosted; existing subscribers) | `hyperfurion` | Provider default. |
| OpenAI | `openai` | `gpt-4o-transcribe` |
| Groq | `groq` | `whisper-large-v3-turbo` |
| Deepgram | `deepgram` | `nova-3` |
| AssemblyAI | `assemblyai` | Provider default. |

`stt.language` is sent to each provider. For AssemblyAI, `en` is sent as
`en_us`.

Words land while you speak with `xai` and `hyperfurion`, which stream, and
with `openai` pointed at a local server, which is re-transcribed every 2.5
seconds (`[flow] live_rest = "auto"`). With OpenAI's cloud, Groq, Deepgram or
AssemblyAI the text is typed when you stop; `live_rest = "always"` makes them
re-transcribe while you speak too, and every pass is billed.

### Text-To-Speech

| Provider | Config value | Default model | Default voice |
| --- | --- | --- | --- |
| xAI (default) | `xai` | Provider default. | `eve` |
| HyperFurion (hosted; existing subscribers) | `hyperfurion` | Provider default. | `eve` |
| OpenAI | `openai` | `gpt-4o-mini-tts` | `coral` |
| ElevenLabs | `elevenlabs` | `eleven_multilingual_v2` | `JBFqnCBsd6RMkjVDRZzb` |

If you switch from xAI to OpenAI or ElevenLabs and leave `voice_id = "eve"`,
the code uses that provider's default voice instead. Set `voice_id` explicitly
when you want a specific voice.

### Fully Offline (Local Models)

The `openai` provider accepts a `base_url`, so any OpenAI-compatible server
counts as a provider — including one on `localhost`. A server at a local
address (`localhost`, or a private-network address such as `192.168.x`) needs
no API key. Point `[stt]` and `[tts]` at it, and `[llm]` at a local model too
(it handles "VK, …" rewrites, Kai and the punctuation at your pauses), and
nothing leaves your machine:

```toml
[providers.openai]
base_url = "http://localhost:8000/v1"    # your speech server

[stt]
provider = "openai"
model = "Systran/faster-whisper-large-v3"   # an id your server serves

[tts]
provider = "openai"
model = "speaches-ai/Kokoro-82M-v1.0-ONNX"
voice_id = "af_heart"

[llm]
provider = "openai"
base_url = "http://localhost:8080/v1"    # e.g. llama-server
model = "your-model-id"                  # what its /v1/models lists
```

The ids above are [Speaches](https://github.com/speaches-ai/speaches)'
Whisper and Kokoro. Leave `model` or `voice_id` unset and OpenAI's own names
(`gpt-4o-transcribe`, `gpt-4o-mini-tts`, `coral`) are sent, which a local
server may not serve. Words still land while you speak: the recording is
re-transcribed every 2.5 seconds (`[flow] live_rest = "auto"`).
`voice-keyboard setup` can write the speech-server part for you, model ids
included, and sets `[llm]` to a running llama-server's model, or to a local
server you name.

Open models and servers that work well locally:

| What | Why |
| --- | --- |
| [Speaches](https://github.com/speaches-ai/speaches) | Easiest single server: OpenAI-compatible STT **and** TTS in one process (faster-whisper + Kokoro). |
| [Whisper large-v3-turbo](https://huggingface.co/openai/whisper-large-v3-turbo) | The default open STT workhorse; great accuracy/speed balance. |
| [NVIDIA Parakeet TDT](https://huggingface.co/nvidia/parakeet-tdt-0.6b-v3) | Near the top of the [Open ASR leaderboard](https://huggingface.co/spaces/hf-audio/open_asr_leaderboard); extremely fast on a GPU. |
| [Voxtral Mini 3B](https://huggingface.co/mistralai/Voxtral-Mini-3B-2507) | Apache-2.0 speech model; vLLM serves it OpenAI-compatible. |
| [whisper.cpp](https://github.com/ggml-org/whisper.cpp) | CPU-only and edge boxes; no GPU required. Start `whisper-server` with `--inference-path /v1/audio/transcriptions` so it answers at the path OpenAI's API uses. |
| [Kokoro-82M](https://huggingface.co/hexgrad/Kokoro-82M) | Small, high-quality open TTS voice (what Speaches serves). |

### The HyperFurion Subscription Provider

`hyperfurion` is the hosted subscription, and it isn't on sale right now:
this provider is for existing subscribers, and for anyone who runs their own
relay. It buys convenience, not capability. Every feature of HyperFurion VK
is open source and free forever — with your own provider API key or a local
server you have all of it, and paying unlocks nothing. A subscription is a
single `hfk_` key instead of a provider account (`voice-keyboard login
<email>` emails a subscriber a sign-in code and writes the key), with xAI
STT/TTS behind a metered relay; what's left over after upstream costs funds
the project's development. It speaks the same streaming protocol as `xai`, so
behavior is identical from the daemon's side. Audio for this provider
transits the relay on its way to xAI; it is held in memory only and never
written to disk. The relay is in `relay/` and is fully self-hostable —
see `relay/README.md` for tiers, quotas, and deployment.

## Hotkeys

On Linux the built-in hotkey listener reads input events directly
(`/dev/input/event*`). This is why your user needs `input` group access and
why you log out and back in once after the first install. Windows uses a
low-level keyboard hook, and macOS (beta) an event tap that needs
Accessibility permission.

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
`evdev` (`f9`, `period`, `rightctrl`, …). The same names work on Windows.
Keyboards plugged in after the daemon starts are picked up within a few
seconds.

Two more bindings use the same names: `[assistant] hotkey` (default
`rightctrl`, held to talk to Kai) and `[tts] hotkey` (read the selection aloud;
`control+alt+r` on Windows, off by default on Linux and macOS, where you can
bind a desktop shortcut to `voice-keyboard tts` instead).

Modes:

| Mode | Behavior |
| --- | --- |
| `auto` | Tap toggles recording. Hold records until release. |
| `toggle` | Every hotkey press toggles start/stop. |
| `hold` | Press starts recording and release stops. |
| `disabled` | Built-in hotkey listener does not start. Manual CLI commands still work. |

## Overlay And Notifications

On Linux with GNOME Shell 45–50 (developed on 50, Wayland), the installer copies and enables a Shell extension
that exposes the D-Bus name `org.voicekeyboard.Overlay`. The CLI and daemon ask
that extension to show recording state near the focused text field, and to
draw the Kai orb.

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

Windows draws its own overlay and Kai orb (see [Windows](#windows)); macOS
(beta) shows notification-center toasts.

## Daemon Management

On Linux the daemon runs as a systemd user service:

```bash
systemctl --user start voice-keyboard-daemon
systemctl --user stop voice-keyboard-daemon
systemctl --user restart voice-keyboard-daemon
systemctl --user status voice-keyboard-daemon
journalctl --user -u voice-keyboard-daemon -f
```

Run the daemon in the foreground for debugging (stop the service first; a
second daemon refuses to start on the same socket):

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

On macOS (beta) the daemon is a launchd agent: `launchctl kickstart -k
gui/$(id -u)/com.hyperfurion.voice-keyboard` restarts it, and it logs to
`~/Library/Logs/voice-keyboard-daemon.log`. On Windows the tray app hosts it:
**Restart** and **Quit** are in its menu, and the CLI reaches it over loopback
TCP instead of a socket; to watch its log live, see
[First run and settings](#first-run-and-settings).

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
or the daemon failed during startup. The CLI prints how to start it when
nothing is listening.

```bash
systemctl --user status voice-keyboard-daemon
journalctl --user -u voice-keyboard-daemon -n 100 --no-pager
```

Common causes are missing API keys, placeholder API keys still in the config,
or no write access to `/dev/uinput` (usually `input` group membership that
hasn't applied yet: log out and back in). `voice-keyboard doctor` checks
each of these and names the fix.

### The Installer Enabled The Service But Did Not Start It

This is expected when the installer cannot safely start the daemon yet.

- If no speech provider is set up yet, run `voice-keyboard setup` (or edit
  `~/.config/voice-keyboard/config.toml`).
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
installer adds your user to the `input` group. `voice-keyboard doctor`
checks this and names the missing step: joining the group (`sudo usermod
-aG input $USER`), logging out and back in, or restarting a daemon that
started before your login had the group.

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
keyboard focus. If focus moved to another app while words were typing live,
typing stopped and the whole transcript is on your clipboard instead; a
dictation typed when you stop does the same when another app has focus
just before it types, and `voice-keyboard stop` (or `toggle`) says so:
"Focus changed — nothing typed; the transcript is on the clipboard". ASCII
is typed through the uinput key path; anything else (accents, CJK, emoji, smart quotes)
is pasted through the clipboard, which requires `wl-copy` (Wayland) or `xclip`
(X11). If neither tool is installed, non-ASCII characters are skipped with
warnings in the journal.

### Stop Takes A While

Stop-to-text latency depends on the selected provider and your connection.
Check the journal for provider errors if it seems stuck:

```bash
journalctl --user -u voice-keyboard-daemon -n 100 --no-pager
```

### `voice-keyboard tts` Says No Selected Text

On Linux, HyperFurion VK reads the primary selection, not the clipboard
(on macOS and Windows `voice-keyboard tts` reads the clipboard, so copy
first; Windows' `Ctrl+Alt+R` copies the selection for you). Select the
text you want spoken and run `voice-keyboard tts` while it remains selected.

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
input device name. List them (the system default is marked `*`):

```bash
voice-keyboard devices
```

### Provider Authentication Or Quota Errors

Check that the active provider in `[stt]` and `[tts]` has a real API key in the
matching `[providers.<name>]` section (the `openai` provider at a local
address needs none). Placeholder keys intentionally fail validation.

If STT and TTS use different providers, configure both keys.

### Can I Use This Offline?

Yes, with your own local servers. By default speech goes to a cloud provider
(xAI, with your key), but the `openai` provider accepts a `base_url`: point
`[stt]` and `[tts]` at a local OpenAI-compatible speech server (Whisper,
Parakeet, Voxtral, Kokoro; e.g. via
[Speaches](https://github.com/speaches-ai/speaches)) and `[llm]` at a local
model, and everything stays on your machine — no API key and no network
required. See
[Fully Offline (Local Models)](#fully-offline-local-models). Capture, hotkeys,
status UI, IPC, playback, and keyboard injection are already local.

## Security And Privacy

- Your speech-to-text provider receives the audio you dictate, and what you
  say to Kai.
- Your text-to-speech provider receives the text you ask it to read aloud, and
  Kai's spoken answers.
- Your `[llm]` language model (xAI's Grok with your xAI key, by default)
  receives the text of a "VK, …" rewrite (or the selection it rewrites), a
  `[polish.map]` restyle, a "VK, run …" or `voice-keyboard intent` request, a
  `voice-keyboard ask` question with your selection, a
  dictation with a correction cue when `[flow] corrections = "llm"`, Kai's
  questions with your current selection (and, when dictation history is on,
  the ledger entries related to the question), and, with
  `[flow] pause_review = "auto"` (the default), the two words around a pause
  its rules can't settle. Point `[llm]` at a local model to keep all of that on
  your machine; `pause_review = "rules"` alone stops the pause words.
- Recorded audio is held in memory while it is being transcribed; it is not
  written to disk.
- TTS audio is written to a temporary MP3 file for playback, then deleted.
- Kai is on by default and keeps your questions and its answers on your
  computer (`~/.local/state/voice-keyboard/assistant-memory.sqlite3`, mode
  600; `%LOCALAPPDATA%\voice-keyboard\` on Windows) until you set
  `[assistant] memory_enabled = false`. The dictation ledger
  (`[flow] history`) is off by default. A word you spell or correct is saved
  as a suggestion in `dictionary.json` (mode 600) and applied only if you
  accept it.
- API keys live in `~/.config/voice-keyboard/config.toml` (on Windows,
  `%APPDATA%\voice-keyboard\config.toml`); keep it mode `600`.
- The daemon socket is created with mode `600` under your config directory by
  default. Windows uses loopback TCP with a per-session token instead (see
  [Windows](#windows)).
- Where a password field can be recognized — on Linux through AT-SPI, and in
  classic Win32 edit boxes on Windows — it forces the `verbatim` register and
  is never written to the history, offered to the dictionary or sent for pause
  review. A browser's password box on Windows, and any field on macOS, is
  treated like any other field.
- Anyone who can run the daemon as your user or connect to its socket can type
  into your focused app through uinput.
- Membership in the `input` group is broad desktop input access. This is needed
  for global hotkey capture.
- Default logging records operational state and transcript lengths, not what
  you dictated, with two exceptions: Kai's log line quotes the first 60
  characters of each question, and characters the Linux keyboard can't type
  (no clipboard tool installed) are quoted in the warning that drops them. If
  you raise logging to `DEBUG`, interim/final transcript details may appear in
  the log.

## Limitations

- Linux has the longest daily-driving record; Windows is first-class and
  tested in CI on real Windows; macOS is beta and installs from a checkout.
  iOS is not possible as a system-wide keyboard (see
  [iOS — Why Not](#ios--why-not-yet)).
- No speech model ships in the box: bring a key for a speech provider (xAI by
  default) or run a local OpenAI-compatible server (see
  [Fully Offline](#fully-offline-local-models)). The hosted subscription isn't
  on sale right now.
- Words land while you speak only with a streaming provider (xAI, or the
  hosted subscription) or a local server (re-transcribed every 2.5 seconds); with
  OpenAI's cloud, Groq, Deepgram or AssemblyAI they land when you stop
  talking.
- uinput injection on Linux types ASCII directly, as the keys of a US layout,
  and everything else via a clipboard paste (needs `wl-copy`/`xclip`; the
  macOS and Windows backends type full Unicode natively). With another
  keyboard layout active on Linux (German, French, …), characters that sit
  elsewhere on it come out wrong.
- Live molten injection assumes the field is not edited by hand mid-dictation
  (see [Flow limitations](#flow-limitations-honest-ones)). If focus moves to
  another app while words type live, typing stops and the whole transcript
  goes to your clipboard, replacing what was on it (a few words can land in
  the other app first); a dictation typed when you stop types nothing into
  another app either: the transcript goes to the clipboard.
- A spoken line break goes by the app and field it can see, and can guess
  wrong: a chat app it doesn't know gets Enter, which sends; a terminal it
  doesn't know (PuTTY, an editor's terminal panel) can run the line; on
  Windows a web form field or an address bar isn't seen as one line, so
  Shift+Enter can submit it; a web editor's own comment or chat box gets
  Enter; and on macOS (beta) only the app's name counts. Map a terminal in
  `[registers.map]`, or name an app or site in `[registers.newline]`, to set
  it yourself (see [Line breaks](#line-breaks-enter-or-shiftenter)).
- Caret commands (`[nav]`) and "VK, run …" (`[intent]`) are off by default. A
  caret command needs a pause before and after it — said mid-sentence it is
  typed as words — and terminals have no selection to extend.
- The built-in global hotkey requires readable Linux input devices (Linux),
  Accessibility permission (macOS), or a keyboard hook (Windows).
- The near-field overlay is GNOME Shell (45–50) on Linux, and built in on
  Windows; Windows can't type into elevated (administrator) windows from a
  normal process.
- Other desktops use notification fallback unless they implement the same D-Bus
  overlay interface.
- Stop-to-text latency depends on the selected provider.
- IPC commands are handled one at a time. A long TTS request or slow provider
  finalization can delay another simultaneous command.

## Architecture

```text
HyperFurion-VK/
|-- voice_keyboard/
|   |-- daemon.py          # Main daemon, recording state, IPC handling, hotkeys
|   |-- client.py          # CLI, selection reading, overlay calls
|   |-- paths.py           # Config/state/log locations per platform
|   |-- flow/
|   |   |-- engine.py      # Molten dictation state machine (pure logic)
|   |   |-- grammar.py     # Spoken commands, punctuation, vocabulary, wake word
|   |   |-- registers.py   # register rendering (+ semantic compilers in code.py)
|   |   |-- numbers.py     # Spoken cardinals -> digits
|   |   |-- nav.py         # Caret commands -> the keys they press
|   |   |-- spelling.py    # "spell that n g i n x"
|   |   |-- pauses.py      # Punctuation where you paused
|   |   |-- vad.py         # RMS levels, VU meter, silence auto-stop
|   |   `-- worker.py      # Injection convergence loop (type/backspace bursts)
|   |-- transcript.py      # Streaming transcript merge heuristics
|   |-- focusprobe.py      # Focused-app probe (AT-SPI / Quartz / Win32)
|   |-- newline.py         # What a spoken line break presses, per app and site
|   |-- clipboard.py       # Clipboard get/set (wl-copy, xclip, pbcopy, ...)
|   |-- llm.py             # OpenAI-compatible chat client: rewrites, intent, pauses
|   |-- assistant/         # Kai: brain, memory, xAI voice agent
|   |-- history.py         # Opt-in dictation ledger
|   |-- audio_capture.py   # PyAudio microphone capture
|   |-- stt.py             # STT provider clients (+ pseudo-streaming adapter)
|   |-- tts.py             # TTS provider clients and playback
|   |-- injector.py        # UInput virtual keyboard + clipboard paste fallback
|   |-- ipc.py             # Unix socket / loopback-TCP server & client
|   |-- hotkey.py          # Hotkey state machine + Linux evdev listener (hot-plug)
|   |-- config.py          # Config loading and validation
|   |-- setup_wizard.py    # voice-keyboard setup
|   |-- doctor.py          # voice-keyboard doctor
|   |-- macos/             # Quartz injector + event-tap hotkeys (beta)
|   `-- windows/
|       |-- app.py         # Tray app hosting the daemon (pythonw -m voice_keyboard.windows)
|       |-- shell.py       # Overlay pill, Kai orb, tray icon (ctypes Win32)
|       |-- render.py      # Overlay/orb/icon pixels (numpy), placement
|       |-- injector.py    # SendInput typing, key chords, no-Enter guard
|       |-- hotkey.py      # Low-level keyboard hook (hold-to-talk, bare keys)
|       |-- clipboard.py   # Native clipboard, snapshot/restore
|       `-- selection.py   # Copy-the-selection for read-aloud and ask
|-- gnome-shell/
|   `-- voice-keyboard-overlay@liam-hennigan/
|       |-- extension.js   # GNOME Shell near-field overlay + live caption
|       `-- metadata.json
|-- tests/
|-- docs/                  # Landing page, with the engine's JavaScript port
|-- relay/                 # The hosted relay (self-hostable)
|-- scripts/               # Corpus and parity-fuzz tools
|-- config.toml.example
|-- install.sh
|-- packaging/install-hyperfurion-vk.sh
|-- packaging/windows/install-hyperfurion-vk.ps1
|-- pyproject.toml
`-- README.md
```

The daemon owns long-lived resources: the virtual keyboard (uinput on Linux),
microphone capture, provider clients, hotkey listener, and the IPC socket. The
CLI is a thin client: most commands read the config, send one IPC command, and
show overlay or notification state; a few work without the daemon (`setup`,
`doctor`, `try`, `commands`, `devices`, `history`, `find`, `learned`, `login`).

## Development

Create a development environment (PyAudio builds against PortAudio:
install `portaudio19-dev` on Debian/Ubuntu first, or run the installer once):

```bash
python3 -m venv .venv
.venv/bin/pip install -U pip
.venv/bin/pip install -e '.[dev]' ruff
```

Run the test suite, the linter, and lightweight syntax checks:

```bash
.venv/bin/python -m pytest -q
.venv/bin/ruff check .
bash -n install.sh
bash -n packaging/install-hyperfurion-vk.sh
node --test docs/js/test/*.test.mjs   # the landing page's engine port
```

The landing page under `docs/` runs a JavaScript port of
`voice_keyboard/flow/` (`docs/js/flow.js`, `nav.js`, `spelling.js`,
`pauses.js`). `scripts/flow_corpus.py` runs nearly 300 dictations — finals,
and timed streams of interims and ticks — through the Python `FlowEngine` and
writes `tests/flow_corpus.json` (`--check` says whether the file is
current); the Node tests replay the file through the port and expect the
same screens after every step, the same actions and the same final text.
Change the engine, rerun the script, and the diff shows what changed — in
both languages.

CI runs the suite on Linux (Python 3.11–3.13) and on Windows — where
`tests/test_windows_live.py` types into a real edit box and drives the
clipboard, keyboard hook, and tray — plus the landing page's port against the
corpus and a differential fuzz of both engines (`scripts/flow_fuzz_parity.py`),
the relay tests, ruff, a build of the download-and-run setup files, and a full
install → run → upgrade → uninstall of the Windows installer. Some tests skip
where the platform can't run them (Unix sockets, evdev, an interactive
desktop).

## Uninstall

Windows: **Settings › Apps › HyperFurion VK › Uninstall** (see
[Windows](#windows)).

Linux:

```bash
curl -fsSL https://github.com/liamghennigan/HyperFurion-VK/releases/latest/download/install-hyperfurion-vk.sh | bash -s -- --uninstall
# or, from a checkout:
./install.sh --uninstall
```

That stops and removes the user service, the venv, the `voice-keyboard`
commands, and the GNOME overlay, and keeps your config. Add `--purge` to also
delete `~/.config/voice-keyboard` and `~/.local/state/voice-keyboard` (the
dictation history, your dictionary and Kai's memory).

The installer also may have added system packages (PortAudio, libsndfile,
wl-clipboard, xclip, …), a uinput module-load file
(`/etc/modules-load.d/uinput.conf`), a udev rule
(`/etc/udev/rules.d/99-uinput.rules`), and your user to the `input` group.
Those are system-level changes and may be shared with other tools, so remove
them only if you are sure nothing else needs them.

macOS (beta): `launchctl bootout gui/$(id -u)/com.hyperfurion.voice-keyboard`,
delete `~/Library/LaunchAgents/com.hyperfurion.voice-keyboard.plist`, then
`python3 -m pip uninstall voice-keyboard`.
