# Changelog

All notable changes to HyperFurion VK. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and the project uses
[semantic versioning](https://semver.org/). A version bump in `pyproject.toml`
on `main` cuts the GitHub release automatically.

## [Unreleased]

### Added

- **"Cap that", "uppercase that", "lowercase that".** Said on their own,
  they recase the last utterance in place — no model, no selection.
  Mid-sentence ("let's cap that at ten") they stay words.
- **"Select that".** Selects what you just said — one `shift+left` per
  character of the last utterance — so the next words replace it, or a
  rewrite ("VK, make that formal") works on it. Refused when nothing was
  said yet, past 400 characters, across emoji, and in a terminal.
- **"Undo that", "redo that", "paste that".** Caret commands, said on their
  own like the others: `ctrl+z`, `ctrl+shift+z` (`ctrl+y` on Windows),
  `ctrl+v`, and `cmd` on a Mac; "undo that twice" repeats. Refused in a
  terminal, where a paste can carry a line break that runs the line.
- **Spoken emoji.** "Ship it emoji rocket" types `Ship it 🚀`: 27 built in
  (thumbs up, fire, party, check mark, laughing, eyes, …), each one code
  point so a Backspace removes exactly one. `[flow.vocabulary]` remaps or
  adds more; "emoji" alone and the bare names stay words.
- **A lowercase recognizer's "i" is a capital in prose.** Streaming models
  that write everything lowercase typed "i think i'm"; prose now types
  "I think I'm" (also I'll, I'd, I've). Spelled words are left alone.
  Days and months are capitals too ("Monday", "Friday's", "October"),
  though never "may" or "march", which are verbs.
  So are common initialisms ("ok" → OK, PDF, ASAP, FYI, URL, API, …),
  never ones that are also words ("us", "it").
- **Amounts and times in prose.** Prose still keeps spoken numbers as words
  ("no one knows"), but a unit right after one makes the reading certain:
  "twenty five percent" → `25%`, "five dollars" → `$5`, "three thirty pm"
  → `3:30 PM`, "five dollars and fifty cents" → `$5.50`, and a recognizer's
  "25 percent" → `25%`. Dates too: "october sixth" → `October 6`, "june twenty
  first" → `June 21` (an ordinal day only, so "in june twenty people came"
  stays words; never "may" or "march"). Phone numbers: seven or more digits read one by one
  ("five five five one two three four") type `555-1234`, ten type
  `415-555-1212`; a short count ("one two three go") stays words. "Which one am I"
  stays words; a run already typed as words is never rewritten.
- **Spoken numbered lists.** "Steps colon new number build new number test"
  types `Steps:` / `1. Build` / `2. Test`. The count survives a scratch
  (scratching item 2 gives its number back) and restarts at a new paragraph.
- **`voice-keyboard stats`**: per-dictation latency, from speech onset to
  first transcript, first keystroke and settled screen. It reports p50/p95
  over the last 200 dictations. `[flow] latency_log` also writes a
  numbers-only `latency.jsonl`. A benchmark in CI drives the real daemon on
  Linux and Windows, guards VK's own pipeline overhead, and posts the table
  to the job summary.
- **Spell to fix**: `spell that n g i n x` replaces the last word, and
  `spell k eight s` types one. It accepts letters, hyphenated runs, NATO
  words, `capital x` and spoken digits. Each fix is offered as a
  `voice-keyboard learned` candidate; none is applied on its own.
- **Hands-free navigation** (`[nav] enabled`, off by default):
  - Commands: `go left three words`, `select previous word`,
    `go to end of line`, `delete next word`, `press tab`.
  - Keys: editors use the same chords on Linux and Windows. Terminals get
    readline keys on Linux and Windows Terminal/PSReadLine keys on Windows.
  - Safety: a command fires only as an utterance of its own and never
    presses Enter. With hold-to-talk it waits until the hotkey is released.

## [Unreleased]

The landing page becomes a real text field, and the engine it runs is
proven to be the daemon's.

### Fixed

- **Safety: "new line" in a terminal no longer presses Enter.** The
  grammar rendered a spoken line break as `\n` in every register, and in
  a terminal the injector types `\n` as the Enter key — so "rm dash rf
  build new line" ran the command. Terminal and shell registers now
  render a spoken line break as nothing, and the daemon arms the
  injector's Enter refusal for the whole of a terminal session (every
  path: keycodes, newlines, clipboard paste), released at teardown; the
  intent and ask paths restore that state instead of clearing it. The
  paths that type outside a recording — `voice-keyboard type` (and so
  `recall`) and `voice-keyboard transform` — probe the focused app and
  refuse Enter there too when it is a terminal; an integrator that means
  Enter presses it with `voice-keyboard key`. Only a hand sends, as the
  landing page always said.
- **Python register: calls read like Python.** An opening paren or
  bracket right after a name glues to it — "def get user open paren"
  types `def get_user(`, "items open bracket zero close bracket" types
  `items[0]` — while keywords and operators keep their space (`x = (a +
  b)`, `if (x)`). "print open paren x close paren" no longer types
  `print((x)`: a spoken open paren right after a callable that already
  opened one is absorbed. Spoken calls nest, and a colon closes every
  open one: "for i in range len xs colon" types `for i in range(len(xs)):`.
- **Flow: words committed one at a time never merge later.** "open" let
  go by the holdback timer, then "quote" committed on its own, read back
  as the phrase `"` on the next reparse — the fence only guarded the
  latest commit. The grammar now parses everything behind the fence item
  by item (the engine hands it each committed item's end), so no phrase,
  number run, or spelled run can cross a commit boundary; the two fixes
  below are special cases of this rule.
- **Flow: a word duplicated at stop.** When a dictation contained a spoken
  number that was folded to digits ("twenty three" → `23`) or a `literal`
  phrase, finalizing re-committed the last word: a terminal got
  `23 failed tests tests`. The grammar re-read the committed tokens
  differently once they sat behind the fence (number runs could not start
  there; a bare `literal` read back as two words). Both now reproduce the
  committed items exactly, and the engine's "committed items changed under
  reparse" warning is a test failure in the new parity corpus.
- **Flow: a verb that closed a segment stays a word.** "select" said alone,
  then "previous word" after a pause, could be re-read as one navigation
  command at stop. A command is now decided against the segment it started
  in, so a split command is typed as the words it was.

### Added

- **Spoken bullets.** "Shopping list colon new bullet milk new bullet
  eggs" types a list: `Shopping list:` then `- Milk` and `- Eggs` on lines
  of their own. Right after a line break no extra one is added; in a
  terminal or a code register it types nothing. ("Bullet point" stays a
  noun phrase: "the first bullet point is about cost" is prose.)
- **Spoken case formatters (`[flow] formatters = "code"`).** In code and
  terminal registers — where "no space" or "all caps" is never prose;
  `"everywhere"` opts prose in. "snake case user id"
  types `user_id`, "camel case get user name" `getUserName`, "pascal case
  http client" `HttpClient`; kebab, constant, dot and title case, "all
  caps" and "no space" too. A formatter takes the words after it up to a
  pause, punctuation or another command; in the python and shell
  registers an operator word, a keyword or a spoken callable ends it
  too, so "for snake case row count in range ten colon" compiles to `for
  row_count in range(10):`. Title case keeps "of", "the" and friends
  lowercase. Both engines, ten corpus cases.
- **Say an email or a web address (`[flow] addresses`).** "liam at
  example dot com" types `liam@example.com`; "docs dot python dot org"
  types `docs.python.org`; "liam dot hennigan at gmail dot com" and
  "example dot co dot uk" work too. Only a run that ends in a known
  top-level domain becomes an address, so "meet at the office" and "the
  dot product" stay prose; an address is never auto-capitalized at a
  sentence start. While a run could still grow it waits at the live
  tail; a lone word never does. Prose and terminal registers, both
  engines, nine corpus cases (one streamed). On by default.
- **"Scratch that" reaches back.** Stop, notice the mistake, press the
  hotkey and say "scratch that": the previous dictation is taken back.
  Only when the caret is surely still right after it — the same app and
  register, within 30 seconds, no focus change, nothing complex to
  backspace over; otherwise the overlay says why and nothing is touched.
  The recognizer's "scratched that" works too. (The landing page's "undo
  that" and "strike that" are gone: "I can't undo that decision" is a
  sentence, not a command.) A scratch needs to know which app has focus,
  and takes the live preview back before counting.
- **`[snippets]` — text you type by name.** `"my email" =
  "you@example.com"` in config, then "VK, my email" types it — alone, or
  at the end of a dictation ("send the invoice to VK, my email"), where
  it now lands after your words; before, a name there was sent to
  `[llm]` as a rewrite of the sentence. Learned macros work the same
  way. Names match without case or trailing punctuation; newlines in the
  text are kept.
- **Self-corrections, tidied (`[flow] corrections = "llm"`).** "Send it
  Tuesday, no wait, Wednesday" types "Send it Wednesday." A grammar
  can't know what a correction replaces, so a dictation with a cue ("no
  wait", "I mean", "sorry", "actually", "or rather", a stuttered "the
  the") goes to `[llm]` at stop — under a rule the daemon checks: the
  answer may only DELETE words. Every word it keeps must be one you said,
  in order; an answer that adds, swaps or respells a word, or deletes
  more than 60 % of the dictation, is thrown away and your text stays as
  dictated. The screen repairs itself in place. Dictations without a cue
  are never sent, nor a cue that opens the sentence ("Sorry for the
  delay") or one with line breaks; prose fields only; a recording that
  continues a sentence keeps its lowercase start. Off by default; `voice-keyboard
  setup` asks.
- **Rewrite any selection, in any app.** Highlight text, say "VK, make
  this shorter" (or fix the grammar, translate, make it friendlier) and
  the answer from `[llm]` is typed over the selection; the app's undo
  restores the original. The selection is read where the platform can
  say for sure what typing would replace: on Linux from the focused,
  editable widget through AT-SPI, by a separate probe that runs only when
  you ask (the focus probe at every recording start still never reads
  screen text; never the PRIMARY selection, which can belong to another
  window), on Windows by copying from the focused app with the clipboard
  restored. Never in
  a terminal or a password field; a single-line selection never gains an
  Enter; over 4000 characters is refused out loud rather than rewriting
  something else. A multi-line selection is refused (typing its line
  breaks would press Enter, which sends a chat message); on Windows an
  editor's copy of the whole caret line (VS Code does that when nothing
  is selected) counts as no selection. With nothing selected, an
  instruction alone still rewrites your previous dictation.
- **Hesitations never reach the page (`[flow] fillers`).** Streaming
  recognizers write down "um" and "uh"; they are now dropped from what is
  typed, with the commas that bracket them: "Um, so we should, uh, ship
  it." types "So we should ship it." A sentence end the recognizer hung
  on a filler survives; a pause before "Um, and …" lets the word after
  the hesitation decide. Only sounds are on the list (um, umm, uh, uhh,
  uhm, erm) — never words that carry meaning; `fillers = []` keeps them.
  A word ending in a comma waits at the live tail for one more word, so
  the comma and the hesitation freeze together. Both engines, eleven
  corpus cases.
- **`[flow] rejoin` — stop, think, press the hotkey again.** A recording
  that starts within 30 seconds of the last one, in the same app and the
  same prose register, continues its text: a space before the first word,
  and a capital only if the last one ended a sentence. Until now the
  second recording was glued to the first ("Hello world.Next"). Terminals
  and code registers never get a leading space (a shell may read one as
  "keep out of history"); a focus change or a nav command resets it. On
  by default; `rejoin = false` restores a fresh caret every time. The
  landing page mirrors it.
- **"VK, run …" on the landing page.** The `[intent]` channel is live in
  the demo: a request after the wake word types one command line at the
  caret and the board's Enter key rings instead of going down — the
  consent story, on screen. The page has no model, so it answers the
  daemon's own few-shot examples and a few everyday requests from a
  table, and types anything else as a comment line, saying so. A "try
  saying" chip plays it in the shell tab; the scripted demo includes it.
  The status line now also says what a joined pause did ("project. And"
  → "project and").
- **"Spell that NGINX".** A recognizer that hears spelled letters as one
  word writes it in capitals; after "spell" or "spell that" such a token
  ("NGINX", "K8S") now counts as the letters, spelled. Both engines.
- **The page keeps a period you paused on revisable.** `pauses.py` is
  ported too: with the page's utterance-per-pause recognizer, "I think.
  We should wait" is exactly the case the daemon's pause rules exist for,
  and the page now runs them (`pause_review = "rules"`; the `[llm]`
  review needs the daemon). The parity corpus gains thirteen pause cases,
  two of them streamed on the clock.
- **Hands-free navigation on macOS.** The Quartz injector can press
  chords now (modifier keys down, the key posted with their flags, release
  in reverse), so `[nav]` works there: option+arrows by word, command+
  arrows to the ends of a line or the document, `cmd+a`, `option+
  backspace`; in Terminal.app, iTerm2 and Ghostty the readline word
  motions go as `Esc b` / `Esc f` / `Esc d`, which readline takes as Meta
  whether or not Option is set to send it. The landing page presses the
  visitor's own platform's keys — a Mac sees `option+←`, Windows Terminal
  `ctrl+←`.
- **`voice-keyboard setup` asks about hands-free navigation** (off by
  default), after Kai and the history question, and says what it does and
  that Enter is never pressed.
- **"Try saying" on the landing page.** Five chips under the mic name what
  to say ("scratch that", "spell that n g i n x", "select previous word",
  "delete previous word", "VK, make that formal"); while you dictate, the
  one you just said lights the moment the engine acts on it, and while
  nothing is recording a tap plays that one thing as a short scripted
  session.
- **The landing page reads well to a screen reader**: the text field no
  longer announces every keystroke of a molten repair; each dictation is
  announced once, when it lands. The comparison table gains a row for
  caret commands by voice.
- **The landing page types into a real text field.** The focused window is
  a document with a caret and a selection (per register: the editor's and
  the shell's), and every keystroke the 3D board presses lands in it — so
  "spell that n g i n x" backspaces over the misheard word, and a caret
  command said on its own ("select previous word", "go to start of line",
  "delete previous word") waits for the text to land, presses the real
  chord on the board (`shift+ctrl+←` in the editor, `ctrl+w` in the
  shell), moves the caret, and the next words type over the selection —
  in the daemon's order. The board grew to a 75% layout (an F-row, home /
  end / page keys, an arrow cluster) so it has the keys to press. Enter
  still cannot go down.
- **One engine per recording on the page.** Each utterance the recognizer
  closes is a final segment of the same engine, as in the daemon: "scratch
  that" after a pause rewinds the previous utterance, and the stability
  window, the fence, and navigation barriers behave identically. The
  scripted demo is now one continuous recording per register.
- **A parity corpus** (`scripts/flow_corpus.py` → `tests/flow_corpus.json`):
  67 dictations run through the Python `FlowEngine`, recorded, and
  replayed through the page's port under `node --test docs/js/test` — same
  screens after every segment, same navigation actions, same final text,
  corrections and scratch counts. Ten of them stream: timed interims,
  finals and ticks on a clock, so the stability window, the adaptive
  horizon (now ported too), the holdback expiry and the non-ASCII eager
  commit agree over time, not just at stop. A new `landing` CI job runs
  it; `tests/test_flow_corpus.py` keeps the file current on the Python
  side and fails on any "committed items changed under reparse".

## [2.3.0] — 2026-10

Download it, run it, and it walks you through the settings.

### Added

- **Download-and-run setup files** on every release:
  `HyperFurion-VK-Setup.run` (Linux; a self-extracting installer that reopens
  itself in a terminal when double-clicked) and `HyperFurion-VK-Setup.cmd`
  (Windows; double-click, no PowerShell step). Both install for the user who
  runs them; the Linux one refuses to run as root, as does `install.sh`.
- **`voice-keyboard setup`**, a walkthrough of the settings — speech provider
  and keys (or the hosted sign-in, or a local server), the language model,
  the dictation hotkey and its mode, the language, Kai, dictation history.
  It edits `config.toml` in place (comments and everything else kept), shows
  a summary before saving, and saves nothing on Ctrl+C. Both installers end
  in it; an upgrade that already works only offers it.
- **llama.cpp detection**: setup first looks for a `llama-server` running on
  this computer (ports 8080, 8081, 8000, 8012, `$LLAMA_ARG_PORT`, or
  `HFVK_LLAMA_URL`) and offers its model as the default `[llm]`. A server
  with `--api-key` is recognized and asks for the key; one serving several
  models lets you pick.

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
  settings; replaces the early beta cleanly (stops it, removes its startup
  launcher, brings its settings and history along). Works for non-ASCII
  profile folders and on ARM64 PCs (x64 Python under emulation).
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

- **Punctuation where you paused** (`[flow] pause_review`). xAI's
  grok-voice-transcribe-2.0, the server default since 2026-09-18, punctuates
  every pause-delimited chunk as a sentence of its own, so thinking out loud
  came out as "I think. We should wait." The period at a pause now stays
  revisable until the next words decide it: clear cases by rule ("… the.
  Project", "… X. And Y", "… X. But Y"), the rest reviewed by `[llm]` when it
  is usable (only a few words around the pause are sent), and repaired on
  screen like any molten word. `rules` sends nothing; `off` keeps the
  recognizer's periods.
- `[stt] model` now reaches xAI and the HyperFurion relay (pin
  `grok-voice-transcribe-1.0` while xAI still serves it).
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

- Streaming dictation follows xAI's chunk/utterance protocol: chunks are
  appended as they come instead of being overlap-merged, which could drop a
  phrase you repeated (or merge a word that ended one chunk and began the
  next) until the utterance ended — after it was already typed.
- **Windows could not start**: the installer wrote the config to `%APPDATA%`
  while the daemon read `~\.config`. Config now lives in `%APPDATA%`, state and
  logs in `%LOCALAPPDATA%` (an old `~\.config` config is still honored).
- **"Enter is always yours" on Windows and macOS**: the intent channel / Kai
  terminal route guard was only enforced on Linux; drafted commands could carry
  a newline (Return) on the other platforms.
- Windows: the Kai hotkey (bare Right Ctrl) could not bind; Ctrl+Alt+V also
  reached the focused app (Paste Special in Office); every overlay update
  spawned a PowerShell toast; newlines typed as raw characters; Ctrl+Backspace
  word-deletes during hold-to-talk molten repairs; the IPC port was one fixed
  port for the whole machine, so a second signed-in user could never start
  (each daemon now binds a free port of its own); the hook could be silently
  dropped by Windows.
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
