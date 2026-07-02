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


FOCUS_ANCHOR_SCRIPT = r"""
import json

try:
    import gi
    gi.require_version("Atspi", "2.0")
    from gi.repository import Atspi
except Exception:
    raise SystemExit(1)

CoordType = Atspi.CoordType.SCREEN
Focused = Atspi.StateType.FOCUSED


def state_contains(accessible, state):
    try:
        return accessible.get_state_set().contains(state)
    except Exception:
        return False


def accessible_name(accessible):
    try:
        return accessible.get_name() or ""
    except Exception:
        return ""


def accessible_role(accessible):
    try:
        return accessible.get_role_name() or ""
    except Exception:
        return ""


def application_name(accessible):
    try:
        app = accessible.get_application()
        return app.get_name() or ""
    except Exception:
        return ""


def is_shell_chrome(accessible):
    return (
        application_name(accessible) == "gnome-shell"
        and accessible_name(accessible) == "Main stage"
        and accessible_role(accessible) == "window"
    )


def rect_tuple(rect):
    return int(rect.x), int(rect.y), int(rect.width), int(rect.height)


def usable_rect(rect):
    x, y, width, height = rect_tuple(rect)
    return width > 0 and height > 0 and x > -30000 and y > -30000


def find_focused(accessible, depth=0, max_depth=14, seen=None):
    if seen is None:
        seen = set()
    if depth > max_depth:
        return None
    ident = id(accessible)
    if ident in seen:
        return None
    seen.add(ident)

    best = (
        accessible
        if state_contains(accessible, Focused) and not is_shell_chrome(accessible)
        else None
    )
    try:
        child_count = accessible.get_child_count()
    except Exception:
        return best

    for index in range(child_count):
        try:
            child = accessible.get_child_at_index(index)
        except Exception:
            continue
        found = find_focused(child, depth + 1, max_depth, seen)
        if found is not None:
            best = found
    return best


def caret_anchor(accessible):
    try:
        offset = Atspi.Text.get_caret_offset(accessible)
    except Exception:
        return None
    for candidate in [offset, offset - 1, 0]:
        if candidate < 0:
            continue
        try:
            rect = Atspi.Text.get_character_extents(accessible, candidate, CoordType)
        except Exception:
            continue
        if usable_rect(rect):
            x, y, width, height = rect_tuple(rect)
            anchor_x = x if candidate == offset else x + width
            return {"x": anchor_x, "y": y}
    return None


def component_anchor(accessible):
    try:
        rect = Atspi.Component.get_extents(accessible, CoordType)
    except Exception:
        return None
    if not usable_rect(rect):
        return None
    x, y, width, height = rect_tuple(rect)
    return {"x": x + max(width // 2, 1), "y": y}


focused = find_focused(Atspi.get_desktop(0))
if focused is None:
    raise SystemExit(1)

anchor = caret_anchor(focused) or component_anchor(focused)
if anchor is None:
    raise SystemExit(1)

print(json.dumps(anchor))
"""


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
        script = (
            f"display notification {json.dumps(body or summary)}"
            f" with title {json.dumps(summary)}"
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
    try:
        result = subprocess.run(
            ["/usr/bin/python3", "-c", FOCUS_ANCHOR_SCRIPT],
            capture_output=True,
            text=True,
            timeout=1.2,
            check=False,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
        return (-1, -1)
    if result.returncode != 0:
        return (-1, -1)
    try:
        payload = json.loads(result.stdout)
        return (int(payload["x"]), int(payload["y"]))
    except (KeyError, TypeError, ValueError):
        return (-1, -1)


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
) -> None:
    x, y = _focused_anchor()
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


def main() -> None:
    parser = argparse.ArgumentParser(
        prog="voice-keyboard",
        description="Universal Linux voice keyboard with selectable speech providers",
    )
    parser.add_argument(
        "command",
        nargs="?",
        default="toggle",
        choices=["start", "stop", "toggle", "tts", "status"],
        help="Command to send to daemon (default: toggle)",
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
