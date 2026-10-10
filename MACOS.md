# HyperFurion VK on macOS

Where the Mac version stands, how to dogfood it today, and the plan to bring
it level with Windows, then past it. Written for an Apple Silicon Mac on a
current macOS.

**Status:** beta, run from a checkout. The daemon, dictation engine, speech
providers, typing, hotkeys and the focus probe all run on macOS. There is no
overlay, menu-bar app or Kai orb yet. The platform layer is unit-tested
through fakes in CI on Linux and on a real macOS runner (where
`tests/test_macos_live.py` drives the real Quartz, Accessibility, Carbon and
AVFoundation calls without typing anything). It has **not** yet been used on
real hardware: see [Untested on real hardware](#untested-on-real-hardware).

---

## Tonight: from opening the laptop to first dictated words

About 15 minutes, most of it Homebrew downloads.

1. **Install the basics.** Open Terminal.
   - Command Line Tools (git): `xcode-select --install`, then click Install.
   - Homebrew, from <https://brew.sh>:
     `/bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"`,
     then run the two `eval` lines it prints.
2. **Get the code and run the setup.**
   ```bash
   git clone https://github.com/liamghennigan/HyperFurion-VK
   cd HyperFurion-VK
   ./scripts/macos-dev-setup.sh
   ```
   It installs PortAudio and Python 3.12, makes `.venv` with an editable
   install, runs the settings walkthrough (pick your speech provider and paste
   the key), sets Mac hotkeys (Kai on Right Command, read-aloud on
   Control+Option+R), and asks macOS for the permissions.
3. **Grant the permissions** to **Terminal** (the app the daemon runs in):
   click *Open System Settings* on each prompt and turn the switch on. The
   list is in [Permissions](#permissions). Then **quit and reopen Terminal**
   (grants apply to new processes).
4. **Start the daemon** in that new Terminal window and leave it running:
   ```bash
   cd HyperFurion-VK && ./scripts/macos-dev-setup.sh --run-only
   ```
5. **Check.** In a second tab: `source .venv/bin/activate && voice-keyboard doctor`.
   Every line should be ✓, except perhaps *mic access: not asked yet*.
6. **Dictate.** Open TextEdit, click into the document, tap
   **Control+Option+V**, say "hello from my new Mac, period", and tap it
   again. A rising tone marks the start, a falling one the end. The first
   recording triggers the Microphone prompt: allow it, then dictate again.
7. Work through the [dogfood checklist](#dogfooding). When something breaks:
   `./scripts/macos-dev-setup.sh --bug-report` (it writes a file to the Desktop).

Want it fully offline? `./scripts/macos-dev-setup.sh --local-whisper`, then
restart the daemon. See [Fully offline on Apple Silicon](#fully-offline-on-apple-silicon).

---

## Permissions

All three are in **System Settings → Privacy & Security**.

| Switch | What needs it | Without it |
| --- | --- | --- |
| **Accessibility** | typing (posting keystrokes), reading the focused field (password fields, terminals, the caret, the window title, selections), a hotkey that swallows its key | every keystroke is silently dropped; hotkey falls back to listen-only or dies |
| **Input Monitoring** | a listen-only hotkey tap (Accessibility covers it) | no hotkey (CLI `voice-keyboard toggle` still works) |
| **Microphone** | recording; macOS asks on the first recording | every recording is silence: the result is *NO SIGNAL* and a banner names this switch |

Open each pane directly:

```bash
open "x-apple.systempreferences:com.apple.preference.security?Privacy_Accessibility"
open "x-apple.systempreferences:com.apple.preference.security?Privacy_ListenEvent"
open "x-apple.systempreferences:com.apple.preference.security?Privacy_Microphone"
```

**Who gets the switch.** macOS grants permissions to the *responsible app*,
not to Python ([TCC attribution][tcc-responsible]). Started from a terminal,
that is the terminal app (Terminal, iTerm, Ghostty, VS Code, ...). Started by
the login agent, it is the Python interpreter itself, which needs its own
three switches: the setup script and `voice-keyboard doctor` print its path.
Since Catalina, listen-only event taps need Input Monitoring, and posting
events (and taps that change events) need Accessibility
([Apple DTS][dts-input]).

**How the app detects and explains missing ones.**

- `voice-keyboard doctor` checks all three for the app it runs in, names
  that app and the exact switch, warns when Secure Keyboard Entry is on (no
  event tap sees keys then: Terminal and iTerm2 have a menu item for it, and
  password fields turn it on while focused), and warns that a login agent
  needs its own grants.
- `python -m voice_keyboard.macos.permissions` prints the same; `--request`
  shows the system prompts; `--open` opens the three panes.
- The daemon logs a plain-words warning at start when Accessibility is
  missing, shows macOS's own prompt once (which adds the app to the list),
  and posts one notification. A tap it cannot create is explained the same
  way. NO SIGNAL names the Microphone switch.
- `voice-keyboard setup` ends with the checklist.

---

## Dogfooding

Run the daemon in a terminal (`--run-only`) so the log scrolls next to you.
For each line: what to do, and what good looks like.

**Typing**
- [ ] TextEdit: dictate two sentences. Words land while you speak and repair
      themselves; capitals and punctuation are right.
- [ ] Notes, Mail, Messages, Safari (a text field on any page), Chrome (a
      Google Doc), Slack or Discord: same. In Electron apps (Slack, Discord,
      VS Code) check nothing is dropped or doubled.
- [ ] Accents and emoji: "café", "naïve", and say "emoji thumbs up". They
      type as themselves (no clipboard involved on macOS).
- [ ] Hold-to-talk: hold Control+Option+V, speak, let go. While you hold,
      typed words must not turn into shortcuts (no menus opening, no words
      deleted by Option+Delete).

**Line breaks and the Enter guard**
- [ ] TextEdit: "new line" makes a line break; "new paragraph" two.
- [ ] Slack/Discord/Messages: "new line" never sends: Shift+Return there.
- [ ] Terminal, iTerm2, Ghostty, Warp: dictate `git status`; **nothing runs**.
      "new line" types nothing. Enter is always yours.
- [ ] VS Code: in the editor "new line" breaks the line; in the integrated
      terminal it does not (the terminal pane is detected as a terminal).
      Run `voice-keyboard status` while focused there if unsure.
- [ ] A password field (Safari login, System Settings): dictation types it
      verbatim and `voice-keyboard history` does not record it.

**Caret commands** (`[nav] enabled = true`)
- [ ] TextEdit: "go left three words", "select previous word", "delete line",
      "undo that". Option+arrows, Command+arrows and Command+Z happen.
- [ ] Terminal: "go to start of line" (Control+A), "delete previous word"
      (Control+W), "go left two words" (Esc b). Selection commands refuse.
- [ ] A non-US layout, if you use one: "select all" must select, not quit
      (letters are resolved on the current layout).

**Hotkeys**
- [ ] Control+Option+V in Terminal does not leave a stray `^V` or quoted
      character behind (the trigger key is swallowed).
- [ ] Kai: hold Right Command, ask something, let go. Command+C with the right
      hand still copies (the gesture cancels).
- [ ] Read aloud: select text in Safari, press Control+Option+R; press again
      to stop. Nothing selected: it reads the clipboard.
- [ ] "VK, make this shorter" with a sentence selected in TextEdit rewrites it.
- [ ] After a password prompt or sleep, the hotkey still works (the tap
      re-enables itself; the log says "Hotkey event tap re-enabled").

**Feedback and focus**
- [ ] No notification banner during a normal dictation; one banner when
      something fails (e.g. a wrong API key).
- [ ] Switch apps mid-dictation: typing freezes and the text lands on the
      clipboard instead.

**Login agent** (optional tonight): `./scripts/macos-dev-setup.sh --agent`,
grant the printed Python its three switches, log out and in, dictate. If it
types but every recording is NO SIGNAL, that is the known launchd microphone
risk below; remove it with `--remove-agent` and keep the terminal run.

**Bug reports.** `./scripts/macos-dev-setup.sh --bug-report` writes
`~/Desktop/hfvk-bug-report-<time>.txt`: macOS version, the checkout, package
versions, `voice-keyboard doctor`, permissions, the login agent's state and
the last 400 log lines (`~/Library/Logs/voice-keyboard-daemon.log`), with API
keys masked. The log can contain what you dictated: read it before sharing.
Add what you did, in which app, what you expected and what happened.

---

## Gap analysis: macOS against Windows

"Now" is this branch. *Untested* means written and unit-tested through fakes
but not yet run on a Mac.

| Area | Windows | macOS now | Left to do |
| --- | --- | --- | --- |
| Hotkey: tap, hold, auto | low-level hook; swallows the trigger; bare Right Ctrl; resyncs missed releases | event tap that swallows the trigger (listen-only fallback); bare Right Cmd/Option/Ctrl and fn; re-enabled when macOS disables it; ignores our own events; resyncs missed releases (*untested*) | hardware check; fn conflicts with the Globe key's own action unless *Keyboard → Press 🌐 key to* is *Do Nothing* ([Apple][fn-key]) |
| Typing, Unicode, emoji | SendInput Unicode; Enter and Tab are real keys | CGEvent Unicode in ≤18-unit chunks; Return and Tab real keys; explicit modifier flags (*untested*) | check Electron, Office, Terminal; apps that read keycodes (games, remote desktop) may need a per-app paste fallback |
| Enter guard | Enter refused while guarded | Return/keypad Enter refused at the key level; line breaks become spaces | — |
| Chat apps (Shift+Enter) | yes | yes (Shift+Return) | *Microsoft Teams* and *Messages* are not in the chat list under their macOS names (see the newline policy notes) |
| Caret commands | Ctrl chords; terminal tables | Option/Command chords, Esc b/f in terminals (in `flow/nav.py`); letters on the current layout (*untested*) | hardware check in Terminal/iTerm2 |
| Focus probe: app | exe name | localized name + bundle id via the Accessibility API; window-list fallback (*untested*) | — |
| Password fields | classic Edit `ES_PASSWORD` only | `AXSecureTextField` role/subrole, which covers web password fields too (*untested*) | — |
| Caret position | GUITHREADINFO caret | `AXBoundsForRange` at the caret, else the field (*untested*) | used once there is an overlay |
| Window title | (being added) | the focused window's `AXTitle` (Accessibility only; never `kCGWindowName`, which is blank without Screen Recording) (*untested*) | the newline policy |
| Single vs multi-line | — | from the AX role (`AXTextField` vs `AXTextArea`) (*untested*) | the newline policy |
| Terminal detection | exe names | bundle ids for Terminal, iTerm2, Warp, Ghostty, kitty, Alacritty, WezTerm, Hyper, Tabby, Rio; the xterm.js terminal in VS Code, Cursor, Windsurf (or any browser) by its widget (*untested*) | JetBrains terminals; Zed |
| Rewrite the selection | copy + restore the clipboard | reads `AXSelectedText` (no copy, no keys) (*untested*) | — |
| Read aloud | Ctrl+Alt+R, copies the selection | selection via Accessibility, else clipboard; Control+Option+R set by the dev setup | a platform default (needs test updates) |
| Overlay | native window by the caret | rising/falling tones, a banner only on failure (before: one banner per caption update) | **native overlay** (P1) |
| Tray / menu bar | tray icon and menu | none | **menu-bar app** (P1) |
| Kai orb | yes | none; Kai works by hotkey and voice | P2, after the overlay |
| Kai summon key | Right Ctrl | Right Ctrl parses now; the dev setup maps it to Right Command (MacBooks have no Right Ctrl) | a platform default (needs test updates) |
| Audio capture | PortAudio | PortAudio via Homebrew | switch macOS capture to `sounddevice` (its wheel bundles PortAudio): no Homebrew at all |
| Speech, Flow, LLM, relay | yes | yes (platform-independent) | — |
| Autostart | Startup folder | launchd agent (installer, `--agent`) | microphone under launchd is uncertain; an app bundle + `SMAppService` login item |
| Setup | PowerShell installer + walkthrough | dev script, venv installer, walkthrough with a permissions checklist | a first-run window inside the app |
| Permissions | — | detected and explained (doctor, daemon, prompts) | doctor should also show the *daemon's* grants (status over IPC) |
| Install / uninstall | one line, Start menu, Settings › Apps | from a checkout; no uninstaller | DMG + notarized app (P2), Homebrew cask |
| CI | real Windows job with live tests | macOS job with live API checks | a live typing test needs a GUI session + permissions: not on a hosted runner |

### What macOS can do better than Windows

- **Password fields everywhere.** `AXSecureTextField` covers web and native
  fields; Windows only sees classic Edit controls.
- **Selections without touching the clipboard.** Rewrite and read-aloud read
  the selection directly; Windows has to copy and restore.
- **On-device speech from the OS.** macOS 26 ships `SpeechAnalyzer`, an
  on-device transcription API that Apple reports is much faster than
  Whisper large-v3 ([WWDC25 session][speechanalyzer], [benchmark][speechanalyzer-bench]).
  A small Swift helper serving it as an OpenAI-compatible endpoint would give
  offline dictation with nothing to download. Worth a spike.
- **Apple Silicon local models** (below): whisper.cpp on Metal, MLX.

---

## The plan, ranked

**P0 — tonight**
1. Run the setup, grant permissions, dogfood with the checklist above.
2. Watch the first CI run of the `macos` job: `tests/test_macos_live.py`
   checks the real pyobjc call shapes; any failure there is a wrong API call
   to fix before trusting the hardware results.
3. Fix what breaks. Likely spots: the event tap (active vs listen-only),
   Unicode typing in Electron apps, the Accessibility probe in Chrome and
   Electron apps.

**P1 — this week**
1. **Menu-bar app with a native overlay**, one AppKit helper process: an
   `NSStatusItem` (state icon; Start/Stop, Ask Kai, Read aloud, Open
   settings, Open logs, Restart, Quit) and a non-activating, click-through
   `NSPanel` near the caret (STARTING, LISTENING with the caption and level
   meter, PROCESSING, INSERTED, NO SIGNAL, ERROR), fed over a pipe. The seam
   exists: `voice_keyboard/macos/overlay.py` (`show`/`hide`) is all
   `client.py` calls on macOS; the caret anchor is already in `FocusInfo`.
   A separate process keeps the AppKit main thread away from the daemon's
   asyncio loop.
2. **An app bundle** with a stable identity (bundle id, signed with an Apple
   Development or Developer ID certificate) and `NSMicrophoneUsageDescription`,
   so permissions attach to *HyperFurion VK* instead of Terminal or Python
   and survive rebuilds (an ad-hoc signature is pinned to its exact code hash,
   so every rebuild drops the grants ([example][adhoc-tcc])). Start it at
   login with `SMAppService` (or the launchd agent pointing inside the
   bundle). This removes the launchd microphone risk.
3. **The smart newline policy on macOS**, using the window title and the
   single/multi-line flag the probe already returns.
4. `voice-keyboard doctor` reports the daemon's own permissions (a field in
   the IPC `status` reply), so a login agent's missing grant is visible from
   a terminal. An uninstall path (`--uninstall` in the installer).

**P2 — later**
1. Kai orb (the same helper process).
2. Release pipeline: a macOS job in `release.yml` that builds, signs with
   Developer ID, notarizes (`xcrun notarytool submit --wait`, then
   `xcrun stapler staple`) and attaches a DMG; then a Homebrew cask.
3. Capture with `sounddevice` on macOS, dropping the Homebrew PortAudio need.
4. The setup walkthrough detects a local speech server (whisper-server,
   mlx-audio) the way it detects llama.cpp.
5. A `SpeechAnalyzer` provider spike (macOS 26).
6. Platform defaults for Kai (Right Command) and read-aloud
   (Control+Option+R); today the dev setup writes them into the config,
   because changing the defaults means updating tests that assume Right Ctrl.

### Packaging, signing and notarization options

- **Briefcase** (BeeWare): builds the `.app`, and `briefcase package macOS`
  signs and notarizes by default and produces a DMG, zip or pkg
  ([docs][briefcase]). The most automated path.
- **py2app**: the PyObjC-native bundler, fine control over `Info.plist`; you
  run `codesign`/`notarytool` yourself.
- **PyInstaller** `--windowed`: works; same manual signing.
- **A small Swift launcher** embedding the Python environment: the most
  control (permission prompts, `SMAppService`, menu bar in Swift), the most
  work.

Whichever: Developer ID Application certificate (Apple Developer Program),
hardened runtime (required for notarization), the
`com.apple.security.device.audio-input` entitlement for the microphone under
the hardened runtime ([Apple][hardened-runtime]), and likely
`com.apple.security.cs.allow-unsigned-executable-memory` for ctypes/libffi.
Inside a bundle, post notifications with `UNUserNotificationCenter` instead
of `osascript`. Recommendation: Briefcase for the first signed DMG; revisit a
Swift launcher only if permissions or login items fight us.

---

## Fully offline on Apple Silicon

The `openai` provider takes any OpenAI-compatible base URL, and a local one
also turns on live (molten) dictation over REST by itself.

**Recommended: whisper.cpp with Metal.** One command:
`./scripts/macos-dev-setup.sh --local-whisper`. It installs Homebrew's
`whisper.cpp` (bottles for Apple Silicon; ships `whisper-server`)
([formula][brew-whisper]), downloads `ggml-large-v3-turbo-q5_0.bin` (574 MB)
([models][whisper-models]), runs `whisper-server` as a login agent on
`127.0.0.1:2022` with `--inference-path /v1/audio/transcriptions` (its
default path is `/inference`) ([server options][whisper-server]), and points
the config at it. On Apple Silicon inference runs on the GPU through Metal
([whisper.cpp][whisper-cpp]). The daemon sends 16 kHz mono WAV, which
whisper.cpp reads natively (no `--convert`/ffmpeg). By hand:

```bash
brew install whisper.cpp
whisper-server --host 127.0.0.1 --port 2022 \
  --model ~/Library/Application\ Support/voice-keyboard/models/ggml-large-v3-turbo-q5_0.bin \
  --inference-path /v1/audio/transcriptions --language auto
```
```toml
[providers.openai]
base_url = "http://127.0.0.1:2022/v1"

[stt]
provider = "openai"
```

Faster but English-only: `ggml-base.en.bin` (148 MB). Core ML can run the
encoder on the Neural Engine for a further speed-up, but needs a custom build
(`-DWHISPER_COREML=1`) and a converted encoder; not worth it before measuring.

**Alternative: MLX (mlx-audio), STT and TTS in one server.**
`pip install mlx-audio`, then `mlx_audio.server --port 8000`. It serves
`/v1/audio/transcriptions` (Whisper, Parakeet, Voxtral, ...; the model is
chosen per request) and `/v1/audio/speech` (Kokoro) ([mlx-audio][mlx-audio]);
`response_format=json` returns `{"text": ...}`, which is what the daemon
reads ([server source][mlx-audio-server]). Config:

```toml
[providers.openai]
base_url = "http://127.0.0.1:8000/v1"

[stt]
provider = "openai"
model = "mlx-community/parakeet-tdt-0.6b-v3"   # or mlx-community/whisper-large-v3-turbo-asr-fp16

[tts]
provider = "openai"
model = "mlx-community/Kokoro-82M-bf16"
voice_id = "af_heart"
```

Not verified against the daemon yet (no Mac here): check with
`curl -s localhost:8000/v1/audio/transcriptions -F file=@a.wav -F model=... -F response_format=json`,
and that `/v1/audio/speech` answers `response_format: "mp3"` (what the
daemon asks for).
Speaches (the Linux recommendation) runs faster-whisper on the CPU on a Mac,
so prefer the two above.

---

## Untested on real hardware

Everything below is unit-tested through fakes; the call shapes are checked
by `tests/test_macos_live.py` on the CI macOS runner; none of it has typed
into a real app yet.

- The active event tap: swallowing the trigger, the listen-only fallback,
  re-enabling after `kCGEventTapDisabledBy*`, device-dependent flag bits for
  Right Command/Option, fn, the HID key-state resync.
- Explicit flags on posted events actually keeping held modifiers out of
  typed text; Return/Tab as real keys in every app; the injected-event tag
  reaching our own tap.
- Layout-aware chords via `UCKeyTranslate` (and its main-thread rule). The
  map is built when the daemon starts, on its main thread; switching
  keyboard layouts later is picked up at the next daemon restart.
- Secure Keyboard Entry detection (`ioreg`'s `kCGSSessionSecureInputPID`).
- The Accessibility probe in real apps: focused element in Chrome, Safari,
  Electron (`AXManualAccessibility`), VS Code's terminal widget
  (`AXDOMClassList` / its label), caret bounds, window titles; the selection
  reads.
- Permission checks under launchd vs a terminal; the microphone under the
  login agent; the notification and prompt at start.
- `scripts/macos-dev-setup.sh` end to end (it was syntax-checked, its
  plists and key masking are tested; it has not run on a Mac).
- The mlx-audio recipe.

Known risk: under the login agent the daemon is Python, and whether macOS
lets a Homebrew Python record without an app bundle around it is not
certain. The terminal run avoids the question; the app bundle (P1) settles
it.

---

## For the smart-newline policy and the landing page

**What the newline policy gets from macOS** (`voice_keyboard/macos/focus.py`):
`MacFocus` carries `window_title` (the focused window's `AXTitle`; never
`kCGWindowName`, which needs Screen Recording), `multiline` (`True` for
`AXTextArea`, `False` for `AXTextField`/`AXComboBox`/`AXSearchField`/secure
fields, `None` when unknown), `bundle_id` and `terminal`. `to_focus_info()`
copies them into `FocusInfo` by field name, so once `FocusInfo` grows `title`
(or `window_title`), `multiline` (or `multi_line`/`single_line`),
`bundle_id` or `terminal`, macOS fills them with no further change. Caveats:
chat composers (Slack, Discord, Messages) are multi-line `AXTextArea`s where
Return sends, so "multi-line" alone must not mean "Return is a line break";
in browsers, the title is the page title; the macOS app names to know are
"Microsoft Teams" and "Messages" (not in `CHAT_APPS` today), "Code" for VS
Code, "Cursor", and bundle ids are steadier than names. On macOS a line break
is typed by `MacTextInjector._press_newline()` (Return, or Shift+Return with
`shift_newline`); a third mode ("nothing") would go there too.

**Landing page:** macOS is beta and from a checkout. Fair claims now:
full Unicode typing, terminal-safe (Enter never pressed, terminals detected
by bundle id and VS Code's terminal widget), password fields recognized,
offline with whisper.cpp on Metal. Not yet: an overlay, a menu-bar app, a
download.

---

## Where things live

| Path | What |
| --- | --- |
| `voice_keyboard/macos/injector.py` | Quartz typing, chords, Delete |
| `voice_keyboard/macos/hotkey.py` | the event tap, macOS keycodes, flags |
| `voice_keyboard/macos/keylayout.py` | char → keycode on the current layout |
| `voice_keyboard/macos/ax.py`, `focus.py` | the Accessibility API adapter and the focus/selection probe |
| `voice_keyboard/macos/permissions.py` | checks, explanations, prompts (`python -m ...`) |
| `voice_keyboard/macos/overlay.py` | tones and failure banners; the overlay seam |
| `voice_keyboard/macos/devsetup.py` | Mac hotkeys and local speech in a config |
| `scripts/macos-dev-setup.sh` | dev setup, run, login agent, local whisper, bug report |
| `packaging/macos/` | the end-user installer (venv + login agent) |
| `tests/test_macos*.py` | fakes-driven tests; `test_macos_live.py` runs on a Mac |

[tcc-responsible]: https://eclecticlight.co/2023/03/11/whos-managing-my-apps/
[dts-input]: https://developer.apple.com/forums/thread/707680
[fn-key]: https://support.apple.com/en-kg/guide/mac-help/mh40584
[speechanalyzer]: https://developer.apple.com/videos/play/wwdc2025/277/
[speechanalyzer-bench]: https://www.gigazine.net/gsc_news/en/20250619-apple-speech-analyzer
[adhoc-tcc]: https://git.bdeshi.space/bdeshi/shannoncoat/src/tag/0.0.8
[briefcase]: https://briefcase.beeware.org/en/v0.3.24/reference/platforms/macOS/index.html
[hardened-runtime]: https://developer.apple.com/documentation/security/hardened-runtime
[brew-whisper]: https://formulae.brew.sh/formula/whisper.cpp
[whisper-models]: https://huggingface.co/ggerganov/whisper.cpp/tree/main
[whisper-server]: https://github.com/ggml-org/whisper.cpp/tree/master/examples/server
[whisper-cpp]: https://github.com/ggml-org/whisper.cpp
[mlx-audio]: https://github.com/Blaizzy/mlx-audio
[mlx-audio-server]: https://github.com/Blaizzy/mlx-audio/blob/main/mlx_audio/server.py
