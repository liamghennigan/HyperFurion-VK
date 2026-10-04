# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Commands

```bash
# Dev environment
python3 -m venv .venv
.venv/bin/pip install -e '.[dev]'
.venv/bin/pip install -e 'relay[dev]'        # only if touching relay/

# Tests (repo root runs both tests/ and relay/tests/)
python -m pytest -q
python -m pytest -q tests/test_flow_engine.py
python -m pytest -q tests/test_flow_engine.py::test_name
python -m pytest -q -k "pending_rewrite"

# Lightweight checks the README asks for
python -m compileall -q voice_keyboard tests
bash -n install.sh
bash -n packaging/install-hyperfurion-vk.sh
```

There is no linter/formatter configured. Some tests skip when Unix sockets or
uinput/input access are unavailable; `relay/tests` skip entirely unless
`aiohttp` is installed. `tests/conftest.py` installs an `evdev` stub (unknown
`KEY_*` names get synthetic codes), so tests run on any OS.

## Architecture

Two Python packages in one repo:

- **`voice_keyboard/`** — the desktop app. Entry points: `voice-keyboard`
  (`client.py:main`) and `voice-keyboard-daemon` (`daemon.py:main`).
- **`relay/`** (`hyperfurion_relay`, aiohttp) — the hosted subscription relay
  behind the `hyperfurion` provider. It speaks the **xAI wire protocol**, so the
  daemon treats it as xAI with a different URL/key; no client special cases.
  Tier quotas in `relay/hyperfurion_relay/tiers.py` are derived from xAI pricing.

Also: `gnome-shell/` (GNOME 50 overlay extension, talks to the daemon),
`docs/` (static landing page deployed to GitHub Pages on push to main),
`packaging/` (AUR, macOS, Windows, bootstrap installer).

### Daemon / client split

The daemon owns all long-lived resources (uinput injector, mic capture,
provider clients, hotkey listeners, IPC socket). The CLI is thin: load config,
send one JSON IPC command (`{"command", "payload", "token"}`), print/overlay
the result. `Daemon._ipc_loop` runs in a **thread** and dispatches each command
onto the asyncio loop with `asyncio.run_coroutine_threadsafe(...)` plus a
per-command timeout — new commands follow the same `elif command == ...`
pattern there. IPC is a Unix socket (`~/.config/voice-keyboard/socket`) or
loopback TCP on Windows (`tcp:127.0.0.1:48765`), guarded by `ipc-token`.

### Flow (molten dictation) — `voice_keyboard/flow/`

Pipeline: STT stream → `transcript.py` (merge interim snapshots) →
`flow/grammar.py` (spoken commands, punctuation, numbers, wake-word
instructions) → `flow/engine.py` → `flow/worker.py` → injector.

- `FlowEngine` is **pure logic** (no IO, no clocks). The daemon feeds it
  transcripts and tick timestamps; it exposes `desired_text()`. Text is
  committed (frozen, never repaired across) or molten (revisable). Commits are
  monotonic except for "scratch that".
- `InjectionWorker` converges the screen toward `desired_text()` with
  type/backspace bursts.
- `registers.py` / `code.py` choose rendering per focused app
  (`prose`/`terminal`/`verbatim`/`python`/`shell`), picked via `focusprobe.py`.
- `imethod.py` `PreeditMapper` maps the same engine state to IM
  preedit/commit ops; it is intentionally **not wired** into the daemon
  (see `SPOKEN-INPUT-PROTOCOL.md`).

### Providers

`stt.py` / `tts.py` hold `SUPPORTED_*_PROVIDERS`, default models, and the
`create_stt_client` / `create_tts_client` factories. Non-streaming REST
providers are wrapped by `ChunkedRESTAdapter` to pseudo-stream. Setting
`providers.openai.base_url` to a local endpoint makes everything offline;
`config.py` skips API-key validation for local endpoints.

### Platform backends

`injector.create_injector()` and `hotkey.py` pick by `sys.platform`: uinput +
evdev on Linux (default), `macos/` (Quartz) and `windows/` (SendInput) are
beta. Keep new injection/hotkey features working through these factories.

### Config

`config.py` `DEFAULT_CONFIG` is deep-merged with
`~/.config/voice-keyboard/config.toml`, then validated by per-section
`_validate_*` functions. When adding a config key, update `DEFAULT_CONFIG`,
its validator, and `config.toml.example` (the documented reference). Flow
config is hot-reloaded on config file mtime change
(`_maybe_reload_flow_config`). New behavior that could change what gets typed
ships config-gated and off by default (see `ROADMAP.md` status).

### Kai assistant — `voice_keyboard/assistant/`

Voice assistant routed by focus: in a terminal it drafts a command, elsewhere
it answers/searches and speaks back. `llm.py` is the OpenAI-compatible chat
client shared with transform/intent/rewrite features. `wake.py` (optional
`[wake]` extra, openWakeWord) is opt-in.

## Invariants

- **Never press Enter on the user's behalf for generated commands.** The
  injector's `suppress_enter` mode refuses `KEY_ENTER` and collapses newlines
  on every path (keycode and clipboard paste). Intent/assistant command
  delivery must go through `_deliver_command` → `_type_no_enter`. Explicit
  `key` IPC combos are deliberately exempt.
- Post-hoc typing (rewrites, commands, macros) checks
  `_focus_changed_since_session()` first; if focus moved, the text goes to
  the clipboard instead of being typed into the wrong app.

## Releases

Bumping `version` in `pyproject.toml` on `main` automatically creates a GitHub
release (`.github/workflows/release.yml`), which stamps and attaches
`packaging/install-hyperfurion-vk.sh`. Only bump the version intentionally.
