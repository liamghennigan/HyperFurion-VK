# Changelog

All notable changes to HyperFurion VK. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and the project uses
[semantic versioning](https://semver.org/). A version bump in `pyproject.toml`
on `main` cuts the GitHub release automatically.

## [2.2.0] — 2026-10

Windows becomes a first-class platform, and the whole repo gets a polish pass.

### Added — Windows

- **A tray app** (`pythonw -m voice_keyboard.windows`): no console window, one
  instance per user, logs in `%LOCALAPPDATA%\voice-keyboard\logs`. Its icon
  tracks the daemon (cyan idle, red recording, amber setup needed); the menu
  starts/stops dictation, asks Kai, reads the clipboard aloud, toggles the orb
  and start-with-Windows, opens settings and logs, restarts, and quits.
- **The overlay pill**, drawn natively as the same instrument as the GNOME
  overlay — STARTING, LISTENING with the live caption and level meter,
  PROCESSING, INSERTED, NO SIGNAL, ERROR — anchored at the text cursor, and
  never taking focus.
- **The Kai orb**: click to summon, drag to move (remembered); never steals
  focus, so Kai still knows which app you were in.
- **A one-line installer** (`irm … | iex`): installs Python 3.12 if needed,
  sets up a per-user venv, the `voice-keyboard` command, a Start menu entry,
  start-with-Windows, and a Settings › Apps entry with an uninstaller; walks
  you through sign-in or provider keys. Upgrades in place, never touches your
  settings.
- **Setup mode**: with no usable config the app waits (amber icon, sign-in in
  the menu) and starts dictation by itself once the config is valid.
- **Read the selection aloud** with `[tts] hotkey` (Windows default
  Ctrl+Alt+R): copies the highlighted text with Ctrl+Insert and puts your
  clipboard back exactly (pictures included). Terminals and password fields
  are never sent a key, a clipboard that can't be saved whole is left
  untouched, and text a password manager marks private is never read.
- Native clipboard (no PowerShell), caret-anchored overlay, password-field
  detection, per-monitor DPI.

### Added — everywhere

- `[tts] hotkey`: a daemon-owned read-aloud hotkey (off by default on Linux).
- `voice-keyboard quit`, `voice-keyboard devices` (lists input devices for
  `[audio] device_name`), and `voice-keyboard --version`.
- When the daemon isn't running, the CLI says how to start it on your platform.
- NO SIGNAL explains a mic that delivered pure digital silence (on Windows: the
  microphone privacy setting).
- CI: tests on Linux (Python 3.11–3.13) and real Windows (including live Win32
  typing, clipboard, hook, and tray tests), the relay suite, ruff, and a full
  install → run → upgrade → uninstall of the Windows installer. Dependabot.
- Linux: `install.sh --uninstall [--purge]`, also through the curl bootstrap.

### Fixed

- **Windows could not start**: the installer wrote the config to `%APPDATA%`
  while the daemon read `~\.config`. Config now lives in `%APPDATA%`, state and
  logs in `%LOCALAPPDATA%` (an old `~\.config` config is still honored).
- **"Enter is always yours" on Windows and macOS**: the intent channel / Kai
  terminal route guard was only enforced on Linux; drafted commands could carry
  a newline (Return) on the other platforms.
- Windows: the Kai hotkey (bare Right Ctrl) could not bind; Ctrl+Alt+V also
  reached the focused app (Paste Special in Office); every overlay update
  spawned a PowerShell toast; newlines typed as raw characters; Ctrl+Backspace
  word-deletes during hold-to-talk molten repairs; a second daemon could bind
  the IPC port; the hook could be silently dropped by Windows.
- Linux: keyboards plugged in after the daemon started (or re-enumerated after
  resume) never triggered the hotkey, and a vanished device ended the listener.
- The GNOME overlay now loads on GNOME 45–49 (it declared GNOME 50 only, and
  used a property that only exists on 48+).
- The remote mic no longer needs `openssl` on PATH (Git for Windows' copy or
  the `cryptography` package work too).
- The test suite's evdev stub accepted any key name, so the invalid-hotkey
  validation test could never pass.
- The landing page overflowed horizontally on phones.

## [2.1.3] — 2026-07-04

- Phosphor-instrument overlay aesthetic, matching the landing page.
- Landing page copy and flow pass; Talon comparison page; AUR package.
- The keyboard answers to "VK" (the old command word is retired).
- `key` IPC command: press key chords for integrators.
- Hosted sign-in: `voice-keyboard login <email>`.

## [2.1.2] — 2026-07-04

- Kai routes commands correctly when Wayland hides the focused app.

## [2.1.1] — 2026-07-04

- Terminal-safe Kai summon (bare Right Ctrl), turns that can never get stuck,
  and raw-PCM answers.

## [2.0.0] — 2026-07-04

- **Kai**, the voice assistant in the keyboard: hold-to-talk summon, the
  clickable orb, an opt-in wake word, terminal commands drafted at the prompt
  (never run), answers spoken back.
- The roadmap build: widget probe and hotword bias, the correction-mining
  personal dictionary, `python`/`shell` registers, held rewrites
  (`keep`/`discard`), the intent channel, ambient containment, ask, recall,
  remote mic, macros.

## [1.2.0] — 2026-07-04

- **Flow — molten dictation**: words land while you speak and repair in place;
  the spoken grammar, context registers, numbers, silence auto-stop.

## [1.1.0] — 2026-07-02

- Stop button, mobile pass, automatic releases, macOS and Windows betas.

[2.2.0]: https://github.com/liamghennigan/HyperFurion-VK/compare/v2.1.3...v2.2.0
[2.1.3]: https://github.com/liamghennigan/HyperFurion-VK/releases/tag/v2.1.3
[2.1.2]: https://github.com/liamghennigan/HyperFurion-VK/releases/tag/v2.1.2
[2.1.1]: https://github.com/liamghennigan/HyperFurion-VK/releases/tag/v2.1.1
[2.0.0]: https://github.com/liamghennigan/HyperFurion-VK/releases/tag/v2.0.0
[1.2.0]: https://github.com/liamghennigan/HyperFurion-VK/releases/tag/v1.2.0
[1.1.0]: https://github.com/liamghennigan/HyperFurion-VK/releases/tag/v1.1.0
