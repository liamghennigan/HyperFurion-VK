# Spoken input as an input method — protocol draft

Status: **draft v0** — implementation notes and a proposal skeleton.
Nothing here has been submitted anywhere; submission to freedesktop /
wayland-protocols is a deliberate, separate act by the maintainer.

## The observation

Input-method frameworks (ibus, fcitx, the Wayland input-method and
text-input protocols) have carried spoken input's missing primitive for
decades without knowing it:

- **preedit** — composition text rendered at the caret: visible, styled,
  pending, replaceable. CJK users watch romaji become kanji through it.
- **commit** — the moment composition text becomes real input to the app.

Molten dictation maps onto this exactly:

| Flow concept            | IM primitive                          |
|-------------------------|---------------------------------------|
| molten words            | preedit string                        |
| in-place repair         | preedit replacement                   |
| stability-window freeze | commit                                |
| "scratch that" rewind   | `delete_surrounding_text` + commit    |
| session end, unfrozen   | preedit cleared — evaporates          |

The consequence: a voice keyboard implemented as an input method needs
**no uinput, no synthetic keystrokes, no clipboard tricks** — and every
app that supports an IM (all of them, effectively) renders molten text
natively, styled by the toolkit, with the pending state *in the
protocol* instead of simulated by backspace storms.

## What exists in this repo

`voice_keyboard/imethod.py` — `PreeditMapper`, the tested translation
layer from flow-engine state `(committed, molten)` to the minimal IM
operation stream (`commit` / `delete` / `preedit`). Any host loops:

```
for op, value in mapper.update(engine_committed, engine_molten):
    apply op via the IM framework
```

Host integration is deliberately not wired into the daemon yet:
registering an input method touches the whole desktop's typing stack and
must be an explicit opt-in, never a side effect of an upgrade.

## Staged plan

1. **ibus engine (works on GNOME today).** A small `IBus.Engine` process
   registers as `voice-keyboard`, connects to the daemon's IPC, and
   applies `PreeditMapper` ops via `update_preedit_text` /
   `commit_text` / `delete_surrounding_text`. GNOME only admits IM
   engines through ibus, so this is the pragmatic first host.
2. **zwp_input_method_v2 host (wlroots/KDE).** The same mapper against
   the Wayland input-method protocol directly; compositors that expose
   the protocol get a framework-free host.
3. **Protocol proposal.** With implementation experience from 1–2,
   propose spoken-input additions where the existing protocols fall
   short (below) — as a wayland-protocols / freedesktop discussion,
   reference implementation in hand.

## Where existing protocols fall short (the proposal seed)

- **Pending-state semantics.** Preedit styling is presentational; there
  is no way to say "this text is *provisional recognition* — do not
  spell-check it, do not fire input events downstream as if final."
  Proposal: a preedit-style hint (`provisional`) on text-input.
- **Register/context hints.** `text-input-v3` has `content_purpose`
  (terminal, password, url…) — exactly what the focus probe reconstructs
  today from AT-SPI. IM hosts get it for free; spoken input should
  consume it as the register signal. Gap: no purpose value for
  "code editor buffer" vs prose; proposal: extend `content_purpose` or
  add a `content_language`-style hint.
- **Addressing state.** Ambient mode needs a way for the IM to signal
  "listening but contained" vs "composing" so compositors can render an
  honest indicator. Proposal: an input-method state enum surfaced to the
  compositor's UI, not app-visible.
- **Consent boundary.** Commit is today the IM's unilateral act. A
  spoken-input profile should document the invariant this project builds
  everything on: *nothing commits without stability or explicit human
  action* — words gate on the stability window, commands gate on a human
  keypress (the daemon's intent channel refuses Enter in the injector),
  pending rewrites gate on approval.

## Security posture

An IM host inherits the daemon's rules: password/secret inputs
(`content_purpose = password`) are never biased, never remembered, never
sent through rewrite channels; the intent channel cannot emit activation
(Enter/Return) regardless of model output; ambient containment drops
unaddressed speech before it reaches composition.

---

Maintainer gate: any external submission (mailing list, MR, issue) of
this draft is Liam's call, explicitly not automated.
