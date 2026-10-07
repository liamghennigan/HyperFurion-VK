"""`voice-keyboard doctor`: what stands between you and dictation, and the fix.

Each check is independent and best-effort: a check that can't run says so
instead of failing the rest. Nothing here sends audio or text anywhere;
the only connection made is to the local daemon's control socket.
"""

import glob
import os
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Optional

OK, WARN, FAIL = "ok", "warn", "fail"
_MARK = {OK: "✓", WARN: "!", FAIL: "✗"}


@dataclass
class Finding:
    status: str
    label: str
    detail: str = ""
    fix: str = ""


def check_config(path: Path) -> Finding:
    from voice_keyboard.config import load_config, validate_config

    if not path.exists():
        return Finding(FAIL, "config", f"no {path}", "run `voice-keyboard setup`")
    try:
        validate_config(load_config(path))
    except Exception as exc:
        return Finding(FAIL, "config", str(exc), "run `voice-keyboard setup`, or fix the file")
    return Finding(OK, "config", str(path))


def check_audio(config: dict) -> Finding:
    try:
        from voice_keyboard.client import _list_input_devices

        devices = _list_input_devices()
    except ModuleNotFoundError:
        return Finding(FAIL, "microphone", "PyAudio is not installed",
                       "reinstall: pip install --force-reinstall voice-keyboard (needs libportaudio2)")
    except Exception as exc:
        return Finding(FAIL, "microphone", f"can't list audio inputs ({exc})",
                       "install PortAudio (libportaudio2) and check your sound settings")
    if not devices:
        return Finding(FAIL, "microphone", "no audio input devices", "plug in or enable a microphone")
    wanted = str(config.get("audio", {}).get("device_name", "") or "").strip()
    if wanted and wanted.lower() != "default":
        matches = [d for d in devices if wanted.lower() in d["name"].lower()]
        if len(matches) != 1:
            what = "matches nothing" if not matches else f"matches {len(matches)} devices"
            return Finding(FAIL, "microphone", f'[audio] device_name "{wanted}" {what}',
                           "pick one from `voice-keyboard devices`")
        return Finding(OK, "microphone", matches[0]["name"])
    default = next((d for d in devices if d["default"]), None)
    if default is None:
        return Finding(WARN, "microphone", "no system default input",
                       'set [audio] device_name (see `voice-keyboard devices`)')
    return Finding(OK, "microphone", default["name"])


def check_daemon(config: dict, seen: Optional[dict] = None) -> Finding:
    """Is the daemon up? Its status answer goes into `seen` for the
    checks that ask what the running daemon itself can do."""
    try:
        from voice_keyboard.ipc import IPCClient

        response = IPCClient(config["daemon"]["socket_path"]).send_command("status", timeout=2.0)
    except Exception as exc:
        from voice_keyboard.client import _daemon_start_hint

        return Finding(FAIL, "daemon", f"not reachable ({exc.__class__.__name__})", _daemon_start_hint())
    if response.get("status", "ok") != "ok":
        return Finding(FAIL, "daemon", str(response.get("message", "refused")), "restart the daemon")
    if seen is not None:
        seen.update(response)
    stt = response.get("stt_provider", "")
    return Finding(OK, "daemon", "running" + (f", speech: {stt}" if stt else ""))


def check_typing() -> Finding:
    if sys.platform == "win32":
        return Finding(OK, "typing", "SendInput (no setup needed)")
    if sys.platform == "darwin":
        return Finding(WARN, "typing", "needs Accessibility permission",
                       "System Settings → Privacy & Security → Accessibility: allow your terminal or the app")
    if not os.path.exists("/dev/uinput"):
        return Finding(FAIL, "typing", "/dev/uinput is missing", "sudo modprobe uinput")
    if not os.access("/dev/uinput", os.W_OK):
        return Finding(FAIL, "typing", "can't write /dev/uinput", _uinput_fix())
    return Finding(OK, "typing", "/dev/uinput is writable")


def _input_group_fix(device_gid: Optional[int]) -> str:
    """When a device belongs to the input group: the step that is missing
    to use it — joining the group, or a fresh login so this session has
    it. "" when the group is not the problem (the device isn't the
    group's, or this session has the group already)."""
    try:
        import grp

        group = grp.getgrnam("input")
    except (ImportError, KeyError):
        return ""
    if device_gid != group.gr_gid or group.gr_gid in os.getgroups():
        return ""  # joining would not help, or it is joined and applied
    user = os.environ.get("USER", "")
    if user and user not in group.gr_mem:
        return "sudo usermod -aG input $USER, then log out and back in"
    return "log out and back in (your session predates joining the input group)"


def _uinput_fix() -> str:
    """The step that is actually missing: the group, a fresh login, or the rule."""
    try:
        device_gid = os.stat("/dev/uinput").st_gid
    except OSError:
        device_gid = None
    return _input_group_fix(device_gid) or (
        "echo 'KERNEL==\"uinput\", GROUP=\"input\", MODE=\"0660\"' | "
        "sudo tee /etc/udev/rules.d/99-uinput.rules && sudo udevadm trigger"
    )


def _input_nodes() -> list[str]:
    return sorted(glob.glob("/dev/input/event*"))


def _readable_keyboards(hotkey: dict) -> Optional[int]:
    """How many keyboards with the hotkey's keys this session can read,
    found the way the daemon's listener finds them; None without evdev."""
    from voice_keyboard.hotkey import HotkeyListener, list_devices

    if list_devices is None:
        return None
    listener = HotkeyListener(
        hotkey, on_toggle=lambda: None, on_hold_start=lambda: None, on_hold_stop=lambda: None
    )
    devices = listener._open_devices()
    for device in devices:
        try:
            device.close()
        except OSError:
            pass
    return len(devices)


def check_hotkey(config: dict, daemon_status: Optional[dict] = None) -> Finding:
    """Linux: the built-in hotkey reads the keyboard at /dev/input/event*,
    which takes the input group — joined, and applied to your login.
    `daemon_status`: what the running daemon says it can read (it may
    have started before your login had the group)."""
    from voice_keyboard.hotkey import pretty_binding

    hotkey = dict(config.get("hotkey", {}) or {})
    label = pretty_binding(str(hotkey.get("key", "control+alt+v")))
    if not hotkey.get("enabled", True) or str(hotkey.get("mode", "auto")).lower() == "disabled":
        return Finding(OK, "hotkey", "off ([hotkey] enabled = false)")
    nodes = _input_nodes()
    if not nodes:
        return Finding(WARN, "hotkey", f"no keyboards under /dev/input: {label} has nothing to listen to",
                       "start and stop dictation with `voice-keyboard toggle` (a desktop shortcut can run it)")
    keyboards = _readable_keyboards(hotkey)
    if keyboards is None:
        return Finding(WARN, "hotkey", "python-evdev is missing: the hotkey can't read the keyboard",
                       "reinstall: pip install --force-reinstall voice-keyboard")
    if keyboards == 0:
        gids = set()
        for node in nodes:
            try:
                gids.add(os.stat(node).st_gid)
            except OSError:
                pass
        fix = next((f for f in (_input_group_fix(gid) for gid in sorted(gids)) if f), "")
        if fix:
            return Finding(FAIL, "hotkey", f"can't read your keyboard, so {label} won't start dictation", fix)
        if not any(os.access(node, os.R_OK) for node in nodes):
            return Finding(FAIL, "hotkey", f"can't read your keyboard, so {label} won't start dictation",
                           "your keyboard's /dev/input/event* files aren't readable by the input group:"
                           " check them with `ls -l /dev/input/`")
        return Finding(WARN, "hotkey", f"no keyboard it can read has the keys of {label}",
                       "pick another [hotkey] key, or use `voice-keyboard toggle`")
    if daemon_status and daemon_status.get("hotkey_keyboards") == 0:
        return Finding(FAIL, "hotkey",
                       f"you can read your keyboard, but the running daemon can't: {label} won't start dictation",
                       "the daemon started before you joined the input group: log out and back in"
                       " (or restart the computer)")
    return Finding(OK, "hotkey", f"{label} (reads {keyboards} keyboard{'s' if keyboards != 1 else ''})")


def check_clipboard() -> Finding:
    """Non-ASCII text (é, emoji) is pasted, so a clipboard tool matters."""
    if sys.platform in ("win32", "darwin"):
        return Finding(OK, "clipboard", "built in")
    wayland = os.environ.get("XDG_SESSION_TYPE") == "wayland" or bool(os.environ.get("WAYLAND_DISPLAY"))
    tool = "wl-copy" if wayland else "xclip"
    if shutil.which(tool):
        return Finding(OK, "clipboard", f"{tool} ({'Wayland' if wayland else 'X11'})")
    package = "wl-clipboard" if wayland else "xclip"
    return Finding(WARN, "clipboard", f"no {tool}: accents and emoji can't be typed",
                   f"install {package}")


def check_focus_probe() -> Finding:
    """AT-SPI lets the keyboard see which app has focus (registers, safety)."""
    if sys.platform != "linux":
        return Finding(OK, "focus", "built in")
    python = "/usr/bin/python3"
    if not os.path.exists(python):
        return Finding(WARN, "focus", "no /usr/bin/python3 for the AT-SPI probe",
                       "install python3 and python3-gi (gir1.2-atspi-2.0)")
    try:
        probe = subprocess.run(
            [python, "-c", "import gi; gi.require_version('Atspi', '2.0'); from gi.repository import Atspi"],
            capture_output=True, timeout=5,
        )
    except Exception as exc:
        return Finding(WARN, "focus", f"probe failed to start ({exc})", "")
    if probe.returncode != 0:
        return Finding(WARN, "focus", "AT-SPI bindings missing: apps can't be told apart, so line breaks"
                       " are refused everywhere (any app could be a terminal)",
                       "install python3-gi and gir1.2-atspi-2.0")
    return Finding(OK, "focus", "AT-SPI available")


def check_speech(config: dict) -> Finding:
    from voice_keyboard.assistant.locality import speech_hop

    stt = config.get("stt", {})
    provider = str(stt.get("provider", "xai")).lower()
    # The same test Kai's verdict uses: a local speech server is local; a
    # HyperFurion relay is cloud even on localhost (it forwards to xAI).
    local = speech_hop(config, "stt").local
    where = "local, nothing leaves the machine" if local else "cloud"
    if not local:
        from voice_keyboard.stt import _provider_api_key

        if not _provider_api_key(config, provider):
            fix = ("voice-keyboard login" if provider == "hyperfurion"
                   else f"set [providers.{provider}] api_key, or run `voice-keyboard setup`")
            return Finding(FAIL, "speech", f"{provider} ({where}) has no API key: every dictation would fail", fix)
    return Finding(OK, "speech", f"{provider} ({where})")


def _and(items: list[str]) -> str:
    return items[0] if len(items) == 1 else ", ".join(items[:-1]) + " and " + items[-1]


def _voice_agent(config: dict) -> bool:
    """Kai answers spoken questions through the xAI voice agent, not [llm]."""
    from voice_keyboard.assistant.locality import uses_voice_agent

    return uses_voice_agent(config)


def check_llm(config: dict) -> Finding:
    """Features that use [llm], and whether [llm] can answer. Some you
    switch on, and they fail without it; some are on by default — Kai
    (while it is on), "VK, …" rewrites, pause review — and do less without
    it."""
    from voice_keyboard.llm import llm_ready

    flow = config.get("flow", {}) or {}
    needs = []  # switched on by you: nothing without [llm]
    if (config.get("polish", {}) or {}).get("map"):
        needs.append("[polish.map]")
    if str(flow.get("corrections", "off")).lower() == "llm":
        needs.append("self-corrections")
    for section in ("intent", "ask"):  # recall searches by keyword without it
        if (config.get(section, {}) or {}).get("enabled"):
            needs.append(f"[{section}]")
    review = str(flow.get("pause_review", "auto")).strip().lower()
    if review == "llm":
        needs.append("pause review")
    uses = []  # on by default: they don't work without [llm]
    from voice_keyboard.assistant.locality import kai_state

    assistant = config.get("assistant", {}) or {}
    kai = ""
    if kai_state(config).on:
        name = str(assistant.get("name", "Kai")).strip() or "Kai"
        # A voice agent answers out loud without [llm]; [llm] still turns
        # a request in a terminal into a command (else Kai answers it).
        kai = f"{name}'s terminal commands" if _voice_agent(config) else name
        uses.append(kai)
    wake = str(flow.get("wake_word", "vk")).strip()
    if flow.get("enabled", True) and flow.get("grammar", True) and wake:
        spoken = wake.upper() if len(wake) <= 3 else wake.capitalize()
        uses.append(f'"{spoken}, …" rewrites')
    if llm_ready(config):
        wanted = needs + uses + (["pause review"] if review == "auto" else [])
        return Finding(OK, "llm", "ready for " + ", ".join(wanted) if wanted else "ready (nothing uses it)")
    fix = "set [llm] base_url, model and api_key (or point base_url at a local server)"
    if kai:
        from voice_keyboard.assistant.locality import turn_off_hint

        fix += f"; or turn Kai off: {turn_off_hint()}"
    if needs:
        detail = ", ".join(needs) + " need [llm], which has no model, endpoint or key"
        if uses:
            detail += f" ({_and(uses)} won't work either)"
        return Finding(FAIL, "llm", detail, fix)
    if not uses and review != "auto":
        return Finding(OK, "llm", "not set (nothing needs it)")
    parts = [f"{_and(uses)} won't work"] if uses else []
    if review == "auto":
        parts.append("pause review uses rules only")
    return Finding(WARN, "llm", "not set: " + "; ".join(parts), fix)


_PROXY_VARIABLES = ("HTTPS_PROXY", "HTTP_PROXY", "ALL_PROXY", "https_proxy", "http_proxy", "all_proxy")


def check_kai(config: dict, daemon_status: Optional[dict] = None) -> Finding:
    """Kai, the voice assistant: on or off, and why. Under the default
    ([assistant] enabled = "auto") it is on only when everything it uses
    runs on this computer. `daemon_status`: what the running daemon decided
    (it keeps [stt]/[tts] until a restart)."""
    from voice_keyboard.assistant.locality import kai_state, turn_on_hint, turn_off_hint
    from voice_keyboard.assistant.memory import memory_db_path

    state = kai_state(config)
    name = state.name
    detail = state.why()
    if state.from_example:
        detail += (
            " (the enabled = true in your settings was copied from an older example"
            ' config, where it was the default, so it counts as "auto")'
        )
    if not state.on and state.setting == "auto" and state.can_answer:
        detail += f"; to turn it on: {turn_on_hint()}"
    recall = config.get("recall", {}) or {}
    if (
        state.on and state.setting == "auto" and not state.uses_embeddings
        and str(recall.get("base_url", "") or "").strip() and str(recall.get("model", "") or "").strip()
        and (config.get("assistant", {}) or {}).get("memory_enabled", True) is not False
    ):
        detail += "; its history search uses keywords: [recall] is online"
    if not state.on:
        try:
            memory = memory_db_path()
            if memory.exists():
                detail += f"; its memory is kept at {memory}"
        except Exception:
            pass
    wake = (config.get("wake", {}) or {}).get("enabled", False) is True
    if wake and not state.on:
        return Finding(WARN, "kai", f"the wake word is on, but {name} is off, so it isn't listening"
                       f" ({detail})", f"{turn_on_hint()}, or [wake] enabled = false")
    if daemon_status and "assistant" in daemon_status:
        running = bool(daemon_status.get("assistant"))
        if running != state.on:
            from voice_keyboard.client import _daemon_restart_hint

            now = "on" if running else "off"
            why = daemon_status.get("assistant_why", "")
            return Finding(
                WARN, "kai",
                f"the running daemon has {name} {now}" + (f" ({why})" if why else "")
                + f", but your settings say {detail}",
                f"it follows config.toml within seconds ({turn_on_hint() if state.on else turn_off_hint()}"
                f" applies it at once); [stt] and [tts] changes need a restart: {_daemon_restart_hint()}",
            )
    if state.on and state.local:
        proxy = next((v for v in _PROXY_VARIABLES if os.environ.get(v)), "")
        if proxy:
            return Finding(
                WARN, "kai", f"{detail}; {proxy} is set, but {name} reaches its local servers"
                " directly, never through the proxy",
                "nothing to do while they answer: a local address is never sent through a"
                " proxy, so a server reachable only through one won't answer Kai",
            )
    return Finding(OK, "kai", detail)


def run(config_path: Path, config: Optional[dict]) -> list[Finding]:
    findings = [check_config(config_path)]
    checks: list[Callable[[], Finding]] = [check_typing, check_clipboard, check_focus_probe]
    if config is not None:
        status: dict = {}  # the daemon's answer, once check_daemon has run
        checks = [lambda: check_speech(config), lambda: check_audio(config),
                  lambda: check_daemon(config, status), lambda: check_llm(config),
                  lambda: check_kai(config, status)] + checks
        if sys.platform == "linux":
            checks.append(lambda: check_hotkey(config, status))
    for check in checks:
        try:
            findings.append(check())
        except Exception as exc:  # a broken check never hides the others
            findings.append(Finding(WARN, getattr(check, "__name__", "check"), f"could not run ({exc})"))
    return findings


def render(findings: list[Finding]) -> str:
    width = max(len(f.label) for f in findings)
    lines = []
    for f in findings:
        line = f"{_MARK[f.status]} {f.label:<{width}}  {f.detail}"
        if f.fix and f.status != OK:
            line += f"\n  {'':<{width}}  → {f.fix}"
        lines.append(line)
    failed = sum(f.status == FAIL for f in findings)
    warned = sum(f.status == WARN for f in findings)
    if failed:
        lines.append(f"\n{failed} problem(s) to fix" + (f", {warned} warning(s)." if warned else "."))
    elif warned:
        lines.append(f"\nReady, with {warned} warning(s).")
    else:
        lines.append("\nEverything checks out.")
    return "\n".join(lines) + "\n"
