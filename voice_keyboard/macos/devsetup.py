"""Mac-friendly bindings for a config (scripts/macos-dev-setup.sh runs it).

Two defaults suit a MacBook better than the cross-platform ones:

    [assistant] hotkey = "rightcmd"       MacBook keyboards have no Right
                                          Control, Kai's default summon key
    [tts] hotkey = "control+alt+r"        read the selection aloud, as on
                                          Windows (no desktop shortcut system
                                          to bind `voice-keyboard tts` to)

A key the user set is left alone, except Kai's "rightctrl", which is the
cross-platform default the starter config spells out (and unreachable on a
MacBook). A binding that would clash with one of the user's is skipped.
Only those lines change; comments and everything else stay as they are.

    python -m voice_keyboard.macos.devsetup [path/to/config.toml]
    python -m voice_keyboard.macos.devsetup --local-speech URL [config.toml]

The second form points speech-to-text at a local OpenAI-compatible server
(see "Fully offline on Apple Silicon" in MACOS.md).
"""

import os
import sys
import tempfile
import tomllib
from pathlib import Path
from typing import Optional

MAC_BINDINGS = (
    ("assistant", "hotkey", "rightcmd",
     'Kai: hold Right Command to talk ([assistant] hotkey = "rightcmd")'),
    ("tts", "hotkey", "control+alt+r",
     'Read aloud: select text, press Control+Option+R ([tts] hotkey = "control+alt+r")'),
)
# Values a starter config spells out that are fine to replace on a Mac.
_REPLACEABLE = {("assistant", "hotkey"): {"rightctrl"}}
_ALL_BINDINGS = (("hotkey", "key"), ("assistant", "hotkey"), ("tts", "hotkey"))


def _bindings(data: dict) -> dict:
    found = {}
    for table, key in _ALL_BINDINGS:
        section = data.get(table)
        if isinstance(section, dict) and isinstance(section.get(key), str) and section[key].strip():
            found[(table, key)] = section[key].strip()
    return found


def plan(text: str) -> list[tuple[str, str, str, str]]:
    """The (table, key, value, label) edits a config needs."""
    from voice_keyboard.hotkey import bindings_clash

    data = tomllib.loads(text) if text.strip() else {}
    current = _bindings(data)
    if ("hotkey", "key") not in current:
        current[("hotkey", "key")] = "control+alt+v"
    edits = []
    for table, key, value, label in MAC_BINDINGS:
        section = data.get(table)
        written = isinstance(section, dict) and key in section
        if written and str(section[key]).strip() not in _REPLACEABLE.get((table, key), set()):
            continue  # theirs
        others = [v for k, v in current.items() if k != (table, key)]
        if any(bindings_clash(value, other) for other in others):
            continue
        edits.append((table, key, value, label))
        current[(table, key)] = value
    return edits


def apply(path: Path) -> list[str]:
    """Write the Mac bindings into `path`; the labels of what changed."""
    from voice_keyboard.client import _set_toml_value

    text = path.read_text(encoding="utf-8") if path.exists() else ""
    edits = plan(text)
    if not edits:
        return []
    for table, key, value, _label in edits:
        text = _set_toml_value(text, table, key, value)
    _write(path, text)
    return [label for _table, _key, _value, label in edits]


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=str(path.parent), prefix=".config-", suffix=".toml")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(text)
        os.chmod(tmp, 0o600)
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def use_local_speech(path: Path, base_url: str) -> None:
    """Point speech-to-text at a local OpenAI-compatible server (whisper.cpp's
    whisper-server, mlx-audio): nothing leaves the machine, no key needed.
    Text-to-speech is left as it is."""
    from voice_keyboard.client import _set_toml_value

    text = path.read_text(encoding="utf-8") if path.exists() else ""
    tomllib.loads(text)  # refuse to edit a file that doesn't parse
    text = _set_toml_value(text, "providers.openai", "base_url", base_url)
    text = _set_toml_value(text, "stt", "provider", "openai")
    _write(path, text)


def main(argv: Optional[list[str]] = None) -> int:
    from voice_keyboard import paths

    argv = sys.argv[1:] if argv is None else argv
    local = None
    if argv[:1] == ["--local-speech"]:
        if len(argv) < 2:
            print("usage: --local-speech BASE_URL [config.toml]", file=sys.stderr)
            return 2
        local, argv = argv[1], argv[2:]
    path = Path(argv[0]) if argv else paths.config_dir() / "config.toml"
    try:
        if local is not None:
            use_local_speech(path, local)
            print(f"  speech-to-text: the local server at {local}")
            return 0
        changed = apply(path)
    except (OSError, tomllib.TOMLDecodeError) as exc:
        print(f"Could not update {path}: {exc}", file=sys.stderr)
        return 1
    for label in changed:
        print(f"  set {label}")
    if not changed:
        print("  hotkeys: keeping yours")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
