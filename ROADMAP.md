# Roadmap

Where HyperFurion VK goes from here. This is direction, not a schedule —
items land when they are ready, and each one is shippable on its own.

> **Status 2026-07-04:** first implementations of all seven landed,
> config-gated and off by default where behavior could change: 1 (widget
> probe + `[stt] hotword_bias`), 2 (`voice-keyboard learned` +
> `[flow] personal_dictionary`), 3 (`python`/`shell` registers),
> 4 (`[flow] rewrite_pending` + `keep`/`discard`), 5 (`[intent]` +
> injector Enter-refusal), 6 (`[ambient]` containment layer,
> experimental), 7 (preedit mapper + SPOKEN-INPUT-PROTOCOL.md draft;
> IM host deliberately unwired). LoRA fine-tuning (2) and the
> freedesktop submission (7) remain future work.

## The thesis

A keyboard is the **universal actuator**: every app on every OS already
accepts keystrokes. Voice is the **universal intent channel**. Today the
daemon converts voice into text. The next level is converting intent into
action while keeping the physics of a keyboard: **it can only type — a
human finger still owns Enter.**

The deeper claim: molten dictation is not a rendering trick. The stability
window is a **consent boundary between probabilistic generation and
irreversible action** — nothing becomes real until it freezes. Every item
below is that same boundary applied to something bigger: words, then
edits, then commands, then always-on speech, then a desktop-wide protocol.

Tiers: **now** (buildable next), **medium** (needs the now-tier underneath
it), **horizon** (the endgame).

## 1. See the field, not the window — *now*

The focus probe currently reads the focused *app* to pick a register
(`[registers.map]`). Extend it to read the focused *widget* through the
accessibility tree (AT-SPI2 on Linux, UIA on Windows, AX on macOS):

- Password fields → refuse or mask. Terminal widgets inside IDEs → the
  terminal register, even though the app is an editor. Spreadsheet cell
  vs. formula bar. URL bar → verbatim.
- Bias the STT toward *curated* personal vocabulary (accepted hotwords
  from the correction miner). Generic STT fails precisely on personal
  vocabulary — this collapses that error class. (An earlier draft
  proposed harvesting text near the caret as biasing context; rejected —
  dictation is new thought, not a continuation of screen text, and fake
  "prior transcript" skews the decoder. Only vocabulary the user
  accepted earns a place in the prior.)

Almost nothing on Linux exploits AT-SPI2 well. This is the Linux-first
moat, rung one.

## 2. Learn from every correction — *now*

Every "scratch that" followed by a re-dictation, every wake-word repair,
every immediate manual fix is a **labeled training pair**: what the engine
heard vs. what you meant. The opt-in ledger (`[flow] history`,
`~/.local/state/voice-keyboard/history.jsonl`, mode 600) already captures
the raw material.

- Mine correction pairs into an auto-grown personal dictionary: per-user
  word overrides plus STT biasing hotwords, with a review step ("furion
  learned 3 words this week — keep them?").
- Later: periodic local fine-tuning (LoRA-class) of a local STT model on
  your own corrections.

After a month the daemon is unswappable — not lock-in by hostage data,
but a personal model that lives on your disk and is yours. A hosted
competitor structurally cannot match this without shipping your voice to
a server. This one never leaves the machine.

## 3. Compile speech, don't transcribe it — *medium*

Registers today are formatting rules. Make them **compilation targets**:

- "for i in range ten colon" → `for i in range(10):`
- "pipe grep dash i error" → `| grep -i error`
- Spoken regex, spoken LaTeX.

Deterministic grammar first — fast, offline, predictable — with the
configured `[llm]` handling only the tail the grammar can't reach. Voice
becomes a programming surface. The RSI and accessibility developer
community has wanted exactly this for years, and nobody offers it with a
molten, self-repairing UI.

## 4. Molten diffs — the document as a conversation — *medium*

The wake-word rewrite channel ("… furion, make that formal") already
routes just-typed text through an LLM and repairs it on screen. Generalize
it: "furion, tighten that paragraph" renders the rewrite **in place as a
pending molten diff** — deletions and insertions held amber until you say
"keep it" or let the stability window settle, "scratch that" to discard.

Every AI editor today applies changes and asks forgiveness. Molten
inverts it: no edit is real until it freezes. The stability window
generalizes from words to edits — a new interaction primitive.

## 5. Type actions, never take them — *now*

An intent register: "furion, find every TODO in this repo" types
`grep -rn "TODO" .` at your prompt — **and stops**. Pressing Enter is the
consent, and it is yours.

The guarantee lives in the injector, not in a prompt: in the intent
register the keystroke injector refuses to emit Return. The keyboard
constitutionally cannot execute — form factor, not policy. While the
industry races toward agents that act autonomously, an agent that drafts
keystrokes but cannot press Enter is both the safety story and the better
UX: the confirmation dialog disappears because the confirmation is the
keypress you were already going to make.

## 6. Ambient mode, contained by molten — *horizon*

Push-to-talk exists because always-on transcription can't be trusted — a
mishearing keyboard types garbage into reality. But text that never
freezes without stability, with a visible pending state that can simply
evaporate, is exactly the containment always-on input needs.

- Local-only wake-word and voice activity detection; nothing streams
  anywhere while idle. **Shipped (2.1):** the opt-in `[wake]` word "Kai"
  runs a local openWakeWord detector — no transcription, nothing off-box —
  and summons Kai hands-free; the hotkey stays the hard mute.
- Addressing segmentation: machine-directed vs. room-directed speech, by
  wake word, prosody, and pause structure.
- Anything uncertain stays molten and dies unfrozen. Hard-mute is a
  first-class control.

Nobody ships ambient dictation because nobody has the pending-state
machinery. HyperFurion already does. The wake-word *summon* is done; the
remaining horizon work is always-on *dictation* contained by molten, where
the false-positive cost still bites.

## 7. Become the layer, not the app — *horizon*

Input-method frameworks (ibus, fcitx, the Wayland input-method protocol)
already have **preedit/commit semantics** — the underlined composition
string CJK users see before text commits. *Preedit is molten.* The
standard has been waiting for this.

- Implement HyperFurion as a Wayland input method: preedit = molten,
  commit = freeze. Every app that supports an IM gets first-class molten
  dictation with no injection tricks.
- Then propose a spoken-input protocol through freedesktop, with the
  pending state in the spec, and ship HyperFurion as the reference
  implementation — what ibus/fcitx did for CJK, done for voice.

The endgame is infrastructure: the question stops being "which voice
keyboard?" and becomes "which implementation of the spoken-input
protocol?"

## One doctrine

Human-gated agency, enforced by the substrate: words gate on stability,
edits gate on approval, commands gate on Enter, ambient speech gates on
address, the protocol gates on commit. The intelligence grows; the gate
never moves.

Discussion and dibs: open an issue at
<https://github.com/liamghennigan/HyperFurion-VK/issues>.
