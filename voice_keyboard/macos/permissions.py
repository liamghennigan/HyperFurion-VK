"""macOS privacy permissions: which ones HyperFurion VK has, and the fix.

Three switches in System Settings → Privacy & Security matter:

    Accessibility     typing (posting keystrokes), reading the focused
                      field, and a hotkey tap that can swallow its key
    Input Monitoring  a listen-only hotkey tap (Accessibility covers it)
    Microphone        recording

macOS grants them to the RESPONSIBLE app, not to Python: started from a
terminal, that is the terminal app (Terminal, iTerm2, VS Code, ...);
started by launchd at login, it is the Python interpreter itself. So the
same checkout can work in a terminal and fail as a login agent, and the
fix names whichever app applies.

Every check is best-effort and returns None for "can't tell" (not macOS,
pyobjc missing). Nothing here prompts unless asked: `python -m
voice_keyboard.macos.permissions --request` shows the system prompts
(the dev setup script runs it once).
"""

import ctypes
import logging
import os
import re
import subprocess
import sys
from dataclasses import dataclass
from typing import Callable, Optional

logger = logging.getLogger(__name__)

SETTINGS_URLS = {
    "accessibility": "x-apple.systempreferences:com.apple.preference.security?Privacy_Accessibility",
    "input": "x-apple.systempreferences:com.apple.preference.security?Privacy_ListenEvent",
    "microphone": "x-apple.systempreferences:com.apple.preference.security?Privacy_Microphone",
}
SETTINGS_PATHS = {
    "accessibility": "System Settings → Privacy & Security → Accessibility",
    "input": "System Settings → Privacy & Security → Input Monitoring",
    "microphone": "System Settings → Privacy & Security → Microphone",
}

# TERM_PROGRAM values (and the env vars terminals that don't set it leave
# behind) -> the app name as System Settings lists it.
_TERM_PROGRAMS = {
    "apple_terminal": "Terminal",
    "iterm.app": "iTerm",
    "vscode": "Visual Studio Code (or Cursor/Windsurf: the editor you ran it in)",
    "wezterm": "WezTerm",
    "ghostty": "Ghostty",
    "warpterminal": "Warp",
    "tabby": "Tabby",
    "hyper": "Hyper",
    "rio": "Rio",
}
_TERMINAL_ENV = (("KITTY_WINDOW_ID", "kitty"), ("ALACRITTY_SOCKET", "Alacritty"),
                 ("ALACRITTY_LOG", "Alacritty"), ("WEZTERM_PANE", "WezTerm"),
                 ("GHOSTTY_RESOURCES_DIR", "Ghostty"))

_AV_STATUS = {0: "not determined", 1: "restricted", 2: "denied", 3: "authorized"}

_APPLICATION_SERVICES = "/System/Library/Frameworks/ApplicationServices.framework/ApplicationServices"
_CORE_GRAPHICS = "/System/Library/Frameworks/CoreGraphics.framework/CoreGraphics"


def _is_mac() -> bool:
    return sys.platform == "darwin"


def _ctypes_bool(library: str, function: str) -> Optional[bool]:
    """Call a no-argument C function returning a Boolean/bool."""
    try:
        lib = ctypes.cdll.LoadLibrary(library)
        func = getattr(lib, function)
    except (OSError, AttributeError):
        return None
    func.restype = ctypes.c_bool
    func.argtypes = []
    try:
        return bool(func())
    except Exception:
        return None


def accessibility_granted() -> Optional[bool]:
    """AXIsProcessTrusted: may this process type and read other apps?"""
    if not _is_mac():
        return None
    try:
        import ApplicationServices

        return bool(ApplicationServices.AXIsProcessTrusted())
    except Exception:
        return _ctypes_bool(_APPLICATION_SERVICES, "AXIsProcessTrusted")


def input_monitoring_granted() -> Optional[bool]:
    """CGPreflightListenEventAccess (macOS 10.15+): may a listen-only tap
    see the keyboard?"""
    if not _is_mac():
        return None
    try:
        import Quartz

        return bool(Quartz.CGPreflightListenEventAccess())
    except Exception:
        return _ctypes_bool(_CORE_GRAPHICS, "CGPreflightListenEventAccess")


def post_event_granted() -> Optional[bool]:
    """CGPreflightPostEventAccess (macOS 10.15+): may we post keystrokes?
    Shown under Accessibility in System Settings."""
    if not _is_mac():
        return None
    try:
        import Quartz

        return bool(Quartz.CGPreflightPostEventAccess())
    except Exception:
        return _ctypes_bool(_CORE_GRAPHICS, "CGPreflightPostEventAccess")


def _capture_device_class():
    """AVCaptureDevice without the AVFoundation wrapper (pyobjc-core can
    load the framework itself)."""
    try:
        from AVFoundation import AVCaptureDevice  # pyobjc-framework-AVFoundation, if present

        return AVCaptureDevice
    except Exception:
        pass
    import objc  # pyobjc-core: a dependency of pyobjc-framework-Quartz

    objc.loadBundle(
        "AVFoundation", {}, bundle_path="/System/Library/Frameworks/AVFoundation.framework"
    )
    return objc.lookUpClass("AVCaptureDevice")


def microphone_status() -> Optional[str]:
    """"authorized", "denied", "restricted" or "not determined" (macOS asks
    on the first recording); None when unknown."""
    if not _is_mac():
        return None
    try:
        device = _capture_device_class()
        code = int(device.authorizationStatusForMediaType_("soun"))  # AVMediaTypeAudio
    except Exception:
        logger.debug("microphone permission check failed", exc_info=True)
        return None
    return _AV_STATUS.get(code)


def responsible_app(env: Optional[dict] = None, parent_pid: Optional[int] = None,
                    executable: Optional[str] = None) -> str:
    """Which app macOS asks about: the terminal this runs in, else (under
    launchd, parent pid 1) the Python interpreter itself."""
    env = os.environ if env is None else env
    program = str(env.get("TERM_PROGRAM", "")).strip().lower()
    if program in _TERM_PROGRAMS:
        return _TERM_PROGRAMS[program]
    for variable, name in _TERMINAL_ENV:
        if env.get(variable):
            return name
    parent = os.getppid() if parent_pid is None else parent_pid
    if parent == 1:
        path = os.path.realpath(executable or sys.executable)
        return f"Python ({path})"
    if program:
        return env.get("TERM_PROGRAM", program)
    return "the app you started HyperFurion VK from"


_SECURE_PID = re.compile(r'"kCGSSessionSecureInputPID"\s*=\s*(\d+)')


def secure_input_owner(run: Optional[Callable] = None) -> Optional[tuple[int, str]]:
    """(pid, process name) of the app holding Secure Keyboard Entry — while
    it is on, no event tap sees any key, so hotkeys go dead. None when off
    or unknown. Terminal and iTerm2 have a menu item that turns it on for
    good; password fields turn it on while focused."""
    if run is None:
        if not _is_mac():
            return None
        run = subprocess.run
    try:
        result = run(["ioreg", "-l", "-w", "0", "-d", "1"],
                     capture_output=True, text=True, timeout=3, check=False)
    except (OSError, subprocess.TimeoutExpired):
        return None
    match = _SECURE_PID.search(result.stdout or "")
    if not match:
        return None
    pid = int(match.group(1))
    if pid <= 0:
        return None
    name = ""
    try:
        ps = run(["ps", "-p", str(pid), "-o", "comm="], capture_output=True, text=True,
                 timeout=3, check=False)
        name = os.path.basename((ps.stdout or "").strip())
    except (OSError, subprocess.TimeoutExpired):
        pass
    return pid, name


@dataclass
class Check:
    name: str               # "accessibility", "input", "microphone", "secure input"
    ok: Optional[bool]      # None: can't tell
    detail: str
    fix: str = ""


def checks(*, accessibility=accessibility_granted, input_monitoring=input_monitoring_granted,
           microphone=microphone_status, secure_input=secure_input_owner,
           who: Optional[str] = None) -> list[Check]:
    """Every permission, in plain words, with the exact switch to flip."""
    app = who or responsible_app()
    found: list[Check] = []

    granted = accessibility()
    if granted is None:
        found.append(Check("accessibility", None, "can't tell (pyobjc missing?)"))
    elif granted:
        found.append(Check("accessibility", True, f"granted to {app}"))
    else:
        found.append(Check(
            "accessibility", False,
            "not granted: macOS drops every keystroke, and the hotkey can't see the keyboard",
            f"{SETTINGS_PATHS['accessibility']} → turn on {app} (add it with + if it isn't listed),"
            " then restart HyperFurion VK",
        ))

    listening = input_monitoring()
    if listening is None:
        found.append(Check("input monitoring", None, "can't tell"))
    elif listening or granted:
        found.append(Check("input monitoring", True,
                           "granted" if listening else "covered by Accessibility"))
    else:
        found.append(Check(
            "input monitoring", False, "not granted: the hotkey can't see the keyboard",
            f"{SETTINGS_PATHS['input']} → turn on {app}, then restart HyperFurion VK",
        ))

    status = microphone()
    if status is None:
        found.append(Check("microphone", None, "can't tell"))
    elif status == "authorized":
        found.append(Check("microphone", True, f"granted to {app}"))
    elif status == "not determined":
        found.append(Check(
            "microphone", None, "not asked yet: macOS asks on the first recording",
            f"say yes when it asks, or {SETTINGS_PATHS['microphone']} → turn on {app}",
        ))
    else:
        found.append(Check(
            "microphone", False, f"{status}: every recording would be silence (NO SIGNAL)",
            f"{SETTINGS_PATHS['microphone']} → turn on {app}, then restart HyperFurion VK",
        ))

    owner = secure_input()
    if owner is not None:
        pid, name = owner
        found.append(Check(
            "secure input", False,
            f"Secure Keyboard Entry is on ({name or 'pid'} {pid}): no hotkey can see keys while it is",
            "turn off Terminal/iTerm2 → Secure Keyboard Entry, or leave the password field",
        ))
    return found


_notified: set[str] = set()


def notify_missing(component: str) -> None:
    """One notification per component and run when a permission is missing
    (the daemon has no other visible channel at login). Best-effort."""
    if component in _notified or not _is_mac():
        return
    _notified.add(component)
    what = {"typing": "type", "hotkey": "see the hotkey"}.get(component, component)
    body = (f"HyperFurion VK can't {what}: turn on {responsible_app()} in "
            "System Settings → Privacy & Security → Accessibility, then restart it.")
    script = ("display notification " + _applescript_string(body)
              + " with title " + _applescript_string("HyperFurion VK needs a permission"))
    try:
        subprocess.run(["osascript", "-e", script], timeout=3, check=False, capture_output=True)
    except (OSError, subprocess.TimeoutExpired):
        pass


def _applescript_string(text: str) -> str:
    return '"' + text.replace("\\", "\\\\").replace('"', '\\"') + '"'


def checklist_lines() -> list[str]:
    """The static permissions checklist (the setup walkthrough prints it)."""
    app = responsible_app()
    return [
        "macOS needs three switches in System Settings → Privacy & Security,",
        f"each turned on for {app}:",
        "  • Accessibility — typing, reading the focused field, the hotkey",
        "  • Input Monitoring — the hotkey (if Accessibility alone isn't enough)",
        "  • Microphone — recording (macOS asks on the first recording)",
        "Then restart HyperFurion VK. `voice-keyboard doctor` checks all three;",
        "`python -m voice_keyboard.macos.permissions --request` shows the prompts.",
    ]


def prompt_accessibility() -> None:
    """Show macOS's own Accessibility prompt ("... would like to control this
    computer"), which also adds this process's responsible app to the list,
    so turning it on is one switch. Best-effort; never raises."""
    if not _is_mac():
        return
    try:
        import ApplicationServices

        ApplicationServices.AXIsProcessTrustedWithOptions(
            {ApplicationServices.kAXTrustedCheckOptionPrompt: True}
        )
    except Exception:
        logger.debug("Accessibility prompt failed", exc_info=True)


def request_all() -> None:  # pragma: no cover - shows macOS prompts
    """Show the system prompts for every missing permission (each adds the
    responsible app to its list, so turning it on is one click)."""
    prompt_accessibility()
    try:
        import Quartz

        if not Quartz.CGPreflightListenEventAccess():
            Quartz.CGRequestListenEventAccess()
        if not Quartz.CGPreflightPostEventAccess():
            Quartz.CGRequestPostEventAccess()
    except Exception:
        logger.debug("Input Monitoring prompt failed", exc_info=True)
    if microphone_status() == "not determined":
        try:
            import threading

            done = threading.Event()
            _capture_device_class().requestAccessForMediaType_completionHandler_(
                "soun", lambda granted: done.set()
            )
            done.wait(60)
        except Exception:
            logger.debug("Microphone prompt failed", exc_info=True)


def main(argv: Optional[list[str]] = None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    if not _is_mac():
        print("This checks macOS permissions; run it on a Mac.")
        return 1
    if "--request" in argv:
        request_all()
    if "--open" in argv:
        for url in SETTINGS_URLS.values():
            subprocess.run(["open", url], check=False)
    missing = 0
    for check in checks():
        mark = "✓" if check.ok else ("?" if check.ok is None else "✗")
        print(f"{mark} {check.name:<17} {check.detail}")
        if check.fix and not check.ok:
            print(f"  {'':<17} → {check.fix}")
        missing += check.ok is False
    return 1 if missing else 0


if __name__ == "__main__":
    raise SystemExit(main())
