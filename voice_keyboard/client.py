import argparse
import json
import logging
import os
from pathlib import Path
import subprocess
import sys
import tempfile

from voice_keyboard.config import load_config
from voice_keyboard.ipc import IPCClient

logger = logging.getLogger(__name__)


def _runtime_path(name: str) -> Path:
    runtime_dir = os.environ.get("XDG_RUNTIME_DIR")
    base_dir = Path(runtime_dir) if runtime_dir else Path(tempfile.gettempdir())
    return base_dir / name


def _notification_id_path() -> Path:
    return _runtime_path("voice-keyboard-notification-id")


def _read_notification_id() -> str:
    try:
        value = _notification_id_path().read_text(encoding="utf-8").strip()
    except OSError:
        return ""
    return value if value.isdigit() else ""


def _write_notification_id(value: str) -> None:
    if not value.isdigit():
        return
    try:
        _notification_id_path().write_text(value, encoding="utf-8")
    except OSError:
        pass


def _notify(
    summary: str,
    body: str = "",
    *,
    urgency: str = "normal",
    timeout_ms: int = 4000,
) -> None:
    if sys.platform == "darwin":
        # ensure_ascii=False keeps non-ASCII text literal (json.dumps still
        # escapes the quotes/backslashes AppleScript needs); with the default
        # ensure_ascii=True, 'café' becomes "café" and osascript prints
        # the literal escape.
        script = (
            f"display notification {json.dumps(body or summary, ensure_ascii=False)}"
            f" with title {json.dumps(summary, ensure_ascii=False)}"
        )
        try:
            subprocess.run(["osascript", "-e", script], timeout=3, check=False)
        except (FileNotFoundError, subprocess.TimeoutExpired):
            pass
        return
    if sys.platform == "win32":
        _notify_windows_toast(summary, body)
        return
    command = [
        "notify-send",
        "-a",
        "Voice Keyboard",
        "-i",
        "audio-input-microphone-symbolic",
        "-u",
        urgency,
        "-t",
        str(timeout_ms),
        "-p",
        "-h",
        "string:x-canonical-private-synchronous:voice-keyboard",
    ]
    replace_id = _read_notification_id()
    if replace_id:
        command.extend(["-r", replace_id])
    command.append(summary)
    if body:
        command.append(body)

    try:
        result = subprocess.run(
            command,
            capture_output=True,
            text=True,
            timeout=1.5,
            check=False,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
        return
    if result.returncode == 0:
        notification_id = result.stdout.strip()
        if notification_id:
            _write_notification_id(notification_id)


def _focused_anchor() -> tuple[int, int]:
    from voice_keyboard.focusprobe import probe_focus

    info = probe_focus()
    if info is None:
        return (-1, -1)
    return (info.x, info.y)


def _call_shell_overlay(
    method: str,
    *args: str,
    timeout: float = 0.8,
) -> bool:
    command = [
        "gdbus",
        "call",
        "--session",
        "--dest",
        "org.voicekeyboard.Overlay",
        "--object-path",
        "/org/voicekeyboard/Overlay",
        "--method",
        f"org.voicekeyboard.Overlay.{method}",
    ]
    if args:
        command.append("--")
        command.extend(args)
    try:
        result = subprocess.run(
            command,
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
        return False
    return result.returncode == 0


def _stop_overlay() -> None:
    _call_shell_overlay("Hide", timeout=0.4)


def _set_overlay_button(visible: bool) -> None:
    """Show/hide the always-on Kai orb the overlay extension draws."""
    _call_shell_overlay("SetButton", "true" if visible else "false", timeout=0.5)


def _notify_windows_toast(summary: str, body: str) -> None:
    """Notification-center toast via WinRT from PowerShell — no extra deps.

    The script is passed -EncodedCommand (UTF-16LE base64) so message text
    never meets shell quoting.
    """
    import base64

    def ps_quote(text: str) -> str:
        return "'" + text.replace("'", "''") + "'"

    script = (
        "$ErrorActionPreference='SilentlyContinue';"
        "[Windows.UI.Notifications.ToastNotificationManager, Windows.UI.Notifications,"
        " ContentType=WindowsRuntime] > $null;"
        "$t=[Windows.UI.Notifications.ToastNotificationManager]::GetTemplateContent("
        "[Windows.UI.Notifications.ToastTemplateType]::ToastText02);"
        "$x=$t.GetElementsByTagName('text');"
        f"$x.Item(0).AppendChild($t.CreateTextNode({ps_quote(summary)})) > $null;"
        f"$x.Item(1).AppendChild($t.CreateTextNode({ps_quote(body)})) > $null;"
        "$n=[Windows.UI.Notifications.ToastNotification]::new($t);"
        "[Windows.UI.Notifications.ToastNotificationManager]::CreateToastNotifier("
        "'HyperFurion VK').Show($n);"
    )
    encoded = base64.b64encode(script.encode("utf-16-le")).decode()
    try:
        subprocess.run(
            ["powershell", "-NoProfile", "-EncodedCommand", encoded],
            timeout=5,
            check=False,
            capture_output=True,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired):
        pass


def _show_overlay(
    state: str,
    *,
    detail: str = "",
    timeout_ms: int = 0,
    anchor: tuple[int, int] | None = None,
) -> None:
    # A caller with a cached anchor (the daemon's live caption at ~4 Hz)
    # passes it in; re-probing AT-SPI on every update would be too heavy.
    x, y = anchor if anchor is not None else _focused_anchor()
    if not _call_shell_overlay("Show", state, str(x), str(y), detail, str(timeout_ms)):
        _notify("Voice Keyboard", detail or state.replace("_", " ").title())


def _get_clipboard_text() -> str:
    # No primary selection exists off Linux; the clipboard is the selection.
    if sys.platform == "darwin":
        try:
            result = subprocess.run(["pbpaste"], capture_output=True, text=True, timeout=2)
            return result.stdout.strip() if result.returncode == 0 else ""
        except (FileNotFoundError, subprocess.TimeoutExpired):
            return ""
    if sys.platform == "win32":
        try:
            result = subprocess.run(
                ["powershell", "-NoProfile", "-Command", "Get-Clipboard"],
                capture_output=True,
                text=True,
                timeout=3,
            )
            return result.stdout.strip() if result.returncode == 0 else ""
        except (FileNotFoundError, subprocess.TimeoutExpired):
            return ""
    try:
        result = subprocess.run(
            ["wl-paste", "--primary"],
            capture_output=True,
            text=True,
            timeout=2,
        )
        if result.returncode == 0 and result.stdout.strip():
            return result.stdout.strip()
    except (FileNotFoundError, subprocess.TimeoutExpired):
        pass

    try:
        result = subprocess.run(
            ["xclip", "-selection", "primary", "-o"],
            capture_output=True,
            text=True,
            timeout=2,
        )
        if result.returncode == 0 and result.stdout.strip():
            return result.stdout.strip()
    except (FileNotFoundError, subprocess.TimeoutExpired):
        pass

    return ""


def _stop_timeout_for_config(config: dict) -> float:
    provider = str(config.get("stt", {}).get("provider", "xai")).lower()
    if provider == "assemblyai":
        return 270.0
    if provider in {"openai", "groq", "deepgram"}:
        return 90.0
    return 20.0


def _print_status_details(response: dict) -> None:
    details = []
    if response.get("stt_provider"):
        details.append(f"stt: {response['stt_provider']}")
    if response.get("tts_provider"):
        details.append(f"tts: {response['tts_provider']}")
    if response.get("register"):
        details.append(f"register: {response['register']}")
    if "flow_enabled" in response:
        flow = "off"
        if response.get("flow_enabled"):
            flow = "live" if response.get("flow_live") else "on"
        details.append(f"flow: {flow}")
    if response.get("focused_app"):
        details.append(f"app: {response['focused_app']}")
    if response.get("last_text_len"):
        details.append(f"last: {response['last_text_len']} chars")
    if response.get("uptime_s") is not None:
        details.append(f"up: {int(response['uptime_s'])}s")
    if details:
        print("  " + " | ".join(details))
    if response.get("last_error"):
        print(f"  last error: {response['last_error']}")


def _format_history_time(ts: float) -> str:
    import datetime

    try:
        return datetime.datetime.fromtimestamp(ts).strftime("%Y-%m-%d %H:%M")
    except (OverflowError, OSError, ValueError):
        return "?"


def _run_history(extra_args: list[str]) -> None:
    from voice_keyboard.history import history_path, last_entries

    count = 10
    if extra_args:
        try:
            count = max(1, int(extra_args[0]))
        except ValueError:
            print(f"history: not a count: {extra_args[0]!r}", file=sys.stderr)
            sys.exit(2)
    entries = last_entries(count)
    if not entries:
        print(
            "No dictation history. Enable it with `history = true` under"
            f" [flow] in the config; entries land in {history_path()}."
        )
        return
    total = len(entries)
    for index, entry in enumerate(entries):
        n_back = total - index  # `recall N` counts back from the latest
        stamp = _format_history_time(float(entry.get("ts", 0)))
        app = entry.get("app") or "?"
        print(f"{n_back:3d}. [{stamp}] ({app}) {entry.get('text', '')}")


def _run_recall(client: "IPCClient", extra_args: list[str]) -> None:
    from voice_keyboard.history import last_entries

    n_back = 1
    if extra_args:
        try:
            n_back = max(1, int(extra_args[0]))
        except ValueError:
            print(f"recall: not an index: {extra_args[0]!r}", file=sys.stderr)
            sys.exit(2)
    entries = last_entries(max(n_back, 10))
    if len(entries) < n_back:
        print("recall: no such history entry (is [flow] history enabled?)", file=sys.stderr)
        sys.exit(1)
    text = str(entries[-n_back].get("text", ""))
    try:
        response = client.send_command(
            "type", {"text": text}, timeout=max(20.0, len(text) * 0.02)
        )
    except Exception as e:
        print(f"Failed to connect to daemon: {e}", file=sys.stderr)
        sys.exit(1)
    if response.get("status") == "ok":
        print(f"Re-typed {len(text)} characters")
    else:
        print(f"Error: {response.get('message')}", file=sys.stderr)
        sys.exit(1)


def _run_transform(client: "IPCClient", extra_args: list[str]) -> None:
    instruction = " ".join(extra_args).strip()
    if not instruction:
        print('usage: voice-keyboard transform "make that more formal"', file=sys.stderr)
        sys.exit(2)
    _show_overlay("processing", detail=f"⌁ {instruction}")
    try:
        response = client.send_command(
            "transform", {"instruction": instruction}, timeout=50.0
        )
    except Exception as e:
        _show_overlay("error", detail=str(e), timeout_ms=3000)
        print(f"Failed to connect to daemon: {e}", file=sys.stderr)
        sys.exit(1)
    if response.get("status") == "ok":
        text = response.get("text", "")
        _show_overlay("inserted", detail="Rewrote in place", timeout_ms=1800)
        print(f"Transformed: {text}")
    else:
        message = response.get("message", "transform failed")
        _show_overlay("error", detail=message, timeout_ms=3000)
        print(f"Error: {message}", file=sys.stderr)
        sys.exit(1)


def _run_learned(extra_args: list[str]) -> None:
    """Review corrections mined from the ledger. Nothing applies until it
    is accepted here; accepted entries merge into the grammar vocabulary
    at the next recording start."""
    from voice_keyboard import dictionary
    from voice_keyboard.history import last_entries

    entries = last_entries(500)
    data = dictionary.load_dictionary()
    candidates = dictionary.open_candidates(entries)
    hotword_candidates = dictionary.open_hotword_candidates(entries)
    macro_candidates = dictionary.open_macro_candidates(entries)

    if not extra_args:
        if data["overrides"]:
            print("accepted overrides:")
            for spoken, replacement in sorted(data["overrides"].items()):
                print(f'  "{spoken}" -> "{replacement}"')
        if data["hotwords"]:
            print("hotwords: " + ", ".join(sorted(data["hotwords"])))
        if data["macros"]:
            print("macros (say \"furion, <name>\"):")
            for name, body in sorted(data["macros"].items()):
                preview = body if len(body) <= 50 else body[:47] + "…"
                print(f'  "{name}" -> "{preview}"')
        if candidates:
            print("correction candidates (learned accept N / reject N):")
            for n, (heard, meant, count) in enumerate(candidates, 1):
                print(f'  {n:3d}. "{heard}" -> "{meant}"  (seen {count}x)')
        if hotword_candidates:
            print("hotword candidates (learned hotword N / reject-hotword N):")
            for n, (token, count) in enumerate(hotword_candidates, 1):
                print(f"  {n:3d}. {token}  (seen {count}x)")
        if macro_candidates:
            print("macro candidates (learned macro N <name> / reject-macro N):")
            for n, (text, count) in enumerate(macro_candidates, 1):
                preview = text if len(text) <= 50 else text[:47] + "…"
                print(f'  {n:3d}. "{preview}"  (dictated {count}x)')
        if not (
            data["overrides"] or data["hotwords"] or data["macros"]
            or candidates or hotword_candidates or macro_candidates
        ):
            if entries:
                print("No candidates yet — they appear after you re-dictate a line.")
            else:
                print(
                    "Nothing to mine. Enable the ledger with `history = true`"
                    " under [flow]; corrections are mined from it, locally."
                )
        return

    def _pick(items: list, index_arg: str, what: str):
        try:
            index = int(index_arg)
        except ValueError:
            print(f"learned: not an index: {index_arg!r}", file=sys.stderr)
            sys.exit(2)
        if not 1 <= index <= len(items):
            print(f"learned: no such {what} candidate: {index}", file=sys.stderr)
            sys.exit(1)
        return items[index - 1]

    action = extra_args[0].lower()
    if action == "accept" and len(extra_args) > 1:
        heard, meant, _count = _pick(candidates, extra_args[1], "correction")
        data["overrides"][heard] = meant
        dictionary.save_dictionary(data)
        print(f'accepted: "{heard}" -> "{meant}"')
    elif action == "reject" and len(extra_args) > 1:
        heard, meant, _count = _pick(candidates, extra_args[1], "correction")
        data["rejected"].append(dictionary.candidate_key(heard, meant))
        dictionary.save_dictionary(data)
        print(f'rejected: "{heard}" -> "{meant}"')
    elif action == "hotword" and len(extra_args) > 1:
        token, _count = _pick(hotword_candidates, extra_args[1], "hotword")
        data["hotwords"].append(token)
        dictionary.save_dictionary(data)
        print(f"hotword added: {token}")
    elif action == "reject-hotword" and len(extra_args) > 1:
        token, _count = _pick(hotword_candidates, extra_args[1], "hotword")
        data["rejected"].append(dictionary.candidate_key("hotword", token))
        dictionary.save_dictionary(data)
        print(f"hotword rejected: {token}")
    elif action == "macro" and len(extra_args) > 2:
        text, _count = _pick(macro_candidates, extra_args[1], "macro")
        name = " ".join(extra_args[2:]).strip().casefold()
        if not name:
            print("learned macro N <name>: a spoken name is required", file=sys.stderr)
            sys.exit(2)
        data["macros"][name] = text
        dictionary.save_dictionary(data)
        print(f'macro saved — say "furion, {name}" to type it')
    elif action == "reject-macro" and len(extra_args) > 1:
        text, _count = _pick(macro_candidates, extra_args[1], "macro")
        data["rejected"].append(dictionary.candidate_key("macro", text))
        dictionary.save_dictionary(data)
        print("macro candidate rejected")
    elif action == "forget" and len(extra_args) > 1:
        spoken = " ".join(extra_args[1:]).casefold()
        removed = [k for k in data["overrides"] if k.casefold() == spoken]
        for key in removed:
            del data["overrides"][key]
        data["hotwords"] = [w for w in data["hotwords"] if w.casefold() != spoken]
        had_macro = data["macros"].pop(spoken, None) is not None
        dictionary.save_dictionary(data)
        found = bool(removed) or had_macro
        print(f"forgot: {extra_args[1]}" if found else f"not found: {extra_args[1]}")
    else:
        print(
            "usage: voice-keyboard learned"
            " [accept N | reject N | hotword N | reject-hotword N |"
            " macro N <name> | reject-macro N | forget <spoken>]",
            file=sys.stderr,
        )
        sys.exit(2)


def _run_ask_cli(client: "IPCClient", extra_args: list[str]) -> None:
    """Talk to any app: answer a question about the current selection."""
    question = " ".join(extra_args).strip()
    if not question:
        print('usage: voice-keyboard ask "why does this stack trace fire"', file=sys.stderr)
        sys.exit(2)
    _show_overlay("processing", detail=f"⌁ {question[:40]}")
    try:
        response = client.send_command("ask", {"instruction": question}, timeout=95.0)
    except Exception as e:
        _show_overlay("error", detail=str(e), timeout_ms=3000)
        print(f"Failed to connect to daemon: {e}", file=sys.stderr)
        sys.exit(1)
    if response.get("status") == "ok":
        print(response.get("text", ""))
    else:
        message = response.get("message", "ask failed")
        _show_overlay("error", detail=message, timeout_ms=3000)
        print(f"Error: {message}", file=sys.stderr)
        sys.exit(1)


def _run_find(extra_args: list[str]) -> None:
    """Total recall: search the dictation ledger, semantically when
    [recall] points at an embeddings endpoint, by keyword otherwise."""
    from voice_keyboard import recall as recall_mod
    from voice_keyboard.config import load_config
    from voice_keyboard.history import last_entries

    query = " ".join(extra_args).strip()
    if not query:
        print('usage: voice-keyboard find "the relay caps"', file=sys.stderr)
        sys.exit(2)
    entries = last_entries(500)
    if not entries:
        print("The ledger is empty — enable it with `history = true` under [flow].")
        return
    embedder = recall_mod.create_embedder(load_config())
    hits = recall_mod.search(entries, query, embedder=embedder, limit=5)
    if not hits:
        print("Nothing recalled for that.")
        return
    for hit in hits:
        stamp = _format_history_time(float(hit.get("ts", 0)))
        app = hit.get("app") or "?"
        print(f"[{stamp}] ({app}, {hit.get('score', 0):.2f}) {hit.get('text', '')}")


def _run_intent_cli(client: "IPCClient", extra_args: list[str]) -> None:
    """Type-don't-execute: the daemon types ONE command line at the caret
    and never presses Enter — that keypress stays with the human."""
    request = " ".join(extra_args).strip()
    if not request:
        print('usage: voice-keyboard intent "find every TODO in this repo"', file=sys.stderr)
        sys.exit(2)
    _show_overlay("processing", detail=f"⌁ {request}")
    try:
        response = client.send_command("intent", {"instruction": request}, timeout=50.0)
    except Exception as e:
        _show_overlay("error", detail=str(e), timeout_ms=3000)
        print(f"Failed to connect to daemon: {e}", file=sys.stderr)
        sys.exit(1)
    if response.get("status") == "ok":
        text = response.get("text", "")
        _show_overlay("inserted", detail="⌁ typed — Enter is yours", timeout_ms=2200)
        print(f"Typed (Enter is yours): {text}")
    else:
        message = response.get("message", "intent failed")
        _show_overlay("error", detail=message, timeout_ms=3000)
        print(f"Error: {message}", file=sys.stderr)
        sys.exit(1)


def main() -> None:
    parser = argparse.ArgumentParser(
        prog="voice-keyboard",
        description="Universal Linux voice keyboard with selectable speech providers",
    )
    parser.add_argument(
        "command",
        nargs="?",
        default="toggle",
        choices=[
            "start", "stop", "toggle", "tts", "status",
            "history", "recall", "transform", "intent", "learned",
            "keep", "discard", "ask", "find", "converse", "summon",
        ],
        help="Command to send to daemon (default: toggle)",
    )
    parser.add_argument(
        "args",
        nargs="*",
        help=(
            "history [count] | recall [n-back] | transform <instruction...>"
            " | intent <request...> | ask <question...> | find <query...>"
            " | learned [accept N | reject N | hotword N | macro N <name> |"
            " forget <spoken>]"
        ),
    )
    parser.add_argument(
        "--socket",
        default=None,
        help="Unix socket path (default: ~/.config/voice-keyboard/socket)",
    )
    args = parser.parse_args()

    config = load_config()
    socket_path = args.socket or config["daemon"]["socket_path"]
    client = IPCClient(socket_path)

    if args.command == "history":
        _run_history(args.args)
        return

    if args.command == "recall":
        _run_recall(client, args.args)
        return

    if args.command == "transform":
        _run_transform(client, args.args)
        return

    if args.command == "intent":
        _run_intent_cli(client, args.args)
        return

    if args.command == "learned":
        _run_learned(args.args)
        return

    if args.command == "ask":
        _run_ask_cli(client, args.args)
        return

    if args.command == "find":
        _run_find(args.args)
        return

    if args.command in {"converse", "summon"}:
        # Summon Kai (or end/cancel a live turn) — the same toggle the second
        # hotkey and the on-screen orb fire. Fire-and-forget: the turn owns
        # its own overlay, so don't wait on it.
        try:
            client.send_command("converse", timeout=5.0)
        except Exception as e:
            print(f"Failed to connect to daemon: {e}", file=sys.stderr)
            sys.exit(1)
        return

    if args.command in {"keep", "discard"}:
        try:
            response = client.send_command(args.command, timeout=35.0)
        except Exception as e:
            _show_overlay("error", detail=str(e), timeout_ms=3000)
            print(f"Failed to connect to daemon: {e}", file=sys.stderr)
            sys.exit(1)
        if response.get("status") == "ok":
            if args.command == "keep":
                _show_overlay("inserted", detail="⌁ kept", timeout_ms=1500)
                print(f"Kept: {response.get('text', '')}")
            else:
                print("Discarded the pending rewrite")
        else:
            message = response.get("message", f"{args.command} failed")
            _show_overlay("error", detail=message, timeout_ms=3000)
            print(f"Error: {message}", file=sys.stderr)
            sys.exit(1)
        return

    if args.command == "tts":
        text = _get_clipboard_text()
        if not text:
            _show_overlay("empty", detail="No selected text", timeout_ms=2200)
            _notify("Voice Keyboard", "No selected text", urgency="normal")
            print("No text found in primary selection", file=sys.stderr)
            sys.exit(1)
        _show_overlay("processing", detail="Reading selected text")
        _notify("Voice Keyboard", "Reading selected text...", timeout_ms=5000)
        try:
            response = client.send_command("tts", {"text": text})
            if response.get("status") == "ok":
                _show_overlay("inserted", detail="Finished reading selection", timeout_ms=1800)
                _notify("Voice Keyboard", "Finished reading selection")
                print("TTS played successfully")
            else:
                _show_overlay(
                    "error",
                    detail=response.get("message", "TTS failed"),
                    timeout_ms=3000,
                )
                _notify(
                    "Voice Keyboard",
                    response.get("message", "TTS failed"),
                    urgency="critical",
                    timeout_ms=4000,
                )
                print(f"Error: {response.get('message')}", file=sys.stderr)
                sys.exit(1)
        except Exception as e:
            _show_overlay("error", detail=str(e), timeout_ms=3000)
            _notify("Voice Keyboard", str(e), urgency="critical", timeout_ms=4000)
            print(f"Failed to connect to daemon: {e}", file=sys.stderr)
            sys.exit(1)
        return

    if args.command == "status":
        try:
            response = client.send_command("status", timeout=5.0)
            if response.get("recording"):
                print("recording")
            else:
                print("idle")
            _print_status_details(response)
        except Exception as e:
            print(f"Failed to connect to daemon: {e}", file=sys.stderr)
            sys.exit(1)
        return

    if args.command == "toggle":
        try:
            response = client.send_command("status", timeout=5.0)
            if response.get("recording"):
                command = "stop"
            else:
                command = "start"
        except (ConnectionRefusedError, FileNotFoundError):
            command = "start"
    else:
        command = args.command

    try:
        # `start` must complete STT connect within the client window; `stop`
        # may wait on a provider-backed transcription result.
        send_timeout = 15.0 if command == "start" else _stop_timeout_for_config(config)
        if command == "start":
            _show_overlay("starting")
        elif command == "stop":
            _show_overlay("processing")
            _notify("Voice Keyboard", "Processing speech...", timeout_ms=5000)
        response = client.send_command(command, timeout=send_timeout)
        if response.get("status") == "ok":
            if command == "start":
                _show_overlay("listening")
                _notify(
                    "Voice Keyboard",
                    "Listening... press Ctrl+Alt+V again to stop",
                    timeout_ms=5000,
                )
                print("Recording started...")
            elif command == "stop":
                text = response.get("text", "")
                if text:
                    _show_overlay(
                        "inserted",
                        detail=f"Inserted {len(text)} characters",
                        timeout_ms=1800,
                    )
                    _notify(
                        "Voice Keyboard",
                        f"Inserted {len(text)} characters",
                        timeout_ms=4000,
                    )
                    print(f"Transcribed: {text}")
                else:
                    _show_overlay("empty", timeout_ms=2200)
                    _notify("Voice Keyboard", "No speech detected", timeout_ms=4000)
                    print("Recording stopped (no transcript)")
        else:
            _show_overlay(
                "error",
                detail=response.get("message", f"{command} failed"),
                timeout_ms=3000,
            )
            _notify(
                "Voice Keyboard",
                response.get("message", f"{command} failed"),
                urgency="critical",
                timeout_ms=4000,
            )
            print(f"Error: {response.get('message')}", file=sys.stderr)
            sys.exit(1)
    except Exception as e:
        _show_overlay("error", detail=str(e), timeout_ms=3000)
        _notify("Voice Keyboard", str(e), urgency="critical", timeout_ms=4000)
        print(f"Failed to connect to daemon: {e}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
