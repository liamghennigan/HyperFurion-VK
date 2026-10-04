"""Low-level keyboard hook hotkey listener (Windows).

Same shape as the macOS backend: the combo state machine is inherited
unchanged from voice_keyboard.hotkey; only the event source differs — a
WH_KEYBOARD_LL hook pumped on its own thread feeds virtual-key codes
into _handle_key_event(). Injected events (our own SendInput typing)
are ignored so dictation can never re-trigger the hotkey.

Unlike evdev, a hook can CONSUME a key: the trigger key of a chord is
swallowed while its modifiers are held, so Ctrl+Alt+V never also reaches
the focused app (where it is Paste Special in Office). Bare-modifier
bindings (the assistant's Right Ctrl) are never swallowed — a modifier
alone does nothing in an app, which is what makes them terminal-safe.

Two Windows quirks shape the key tracking. AltGr arrives as a synthetic
Left Ctrl plus Right Alt; that fake Ctrl is ignored, or AltGr+V ('@' on
many layouts) would be eaten as Ctrl+Alt+V. And key releases that happen
on another desktop (Ctrl+Alt+Del, the lock screen, UAC) or while an
elevated window has focus never reach the hook; on each fresh trigger
press the held modifiers are re-checked against the real key state, so a
missed release can't leave Ctrl+Alt "stuck" and turn plain V into the
hotkey.

RegisterHotKey was deliberately not used: it reports presses only, and
hold-to-talk needs releases.
"""

import ctypes
import logging
import threading
import time
from typing import Optional

from voice_keyboard.hotkey import HotkeyListener

logger = logging.getLogger(__name__)

# Virtual-key codes (winuser.h). LL hooks report the sided codes
# (VK_LCONTROL...), the generic ones are kept for completeness.
VK_MODIFIER_ALIASES = {
    "control": frozenset({0x11, 0xA2, 0xA3}),
    "ctrl": frozenset({0x11, 0xA2, 0xA3}),
    "shift": frozenset({0x10, 0xA0, 0xA1}),
    "alt": frozenset({0x12, 0xA4, 0xA5}),
    "super": frozenset({0x5B, 0x5C}),
    "meta": frozenset({0x5B, 0x5C}),
    "win": frozenset({0x5B, 0x5C}),
}
MODIFIER_VKS = frozenset().union(*VK_MODIFIER_ALIASES.values())
MENU_VKS = frozenset({0x12, 0xA4, 0xA5, 0x5B, 0x5C})

# Trigger keys by name — the evdev KEY_* vocabulary the Linux backend
# accepts (rightctrl, f9, period, ...) mapped to virtual keys, so a config
# written on Linux binds the same physical key here.
VK_KEY_ALIASES = {
    "space": 0x20, "spacebar": 0x20,
    "enter": 0x0D, "return": 0x0D,
    "tab": 0x09,
    "esc": 0x1B, "escape": 0x1B,
    "backspace": 0x08,
    "insert": 0x2D, "delete": 0x2E,
    "home": 0x24, "end": 0x23, "pageup": 0x21, "pagedown": 0x22,
    "left": 0x25, "up": 0x26, "right": 0x27, "down": 0x28,
    "capslock": 0x14, "scrolllock": 0x91, "pause": 0x13,
    "sysrq": 0x2C, "printscreen": 0x2C, "print": 0x2C,
    "menu": 0x5D, "compose": 0x5D, "apps": 0x5D,
    # Bare-key bindings (the assistant's terminal-safe summon).
    "rightctrl": 0xA3, "leftctrl": 0xA2,
    "rightalt": 0xA5, "leftalt": 0xA4, "altgr": 0xA5,
    "rightshift": 0xA1, "leftshift": 0xA0,
    "rightmeta": 0x5C, "leftmeta": 0x5B,
    "rightsuper": 0x5C, "leftsuper": 0x5B,
    "rightwin": 0x5C, "leftwin": 0x5B,
    # Punctuation (US positions — OEM keys are named by position).
    ".": 0xBE, "period": 0xBE, "dot": 0xBE,
    ",": 0xBC, "comma": 0xBC,
    "/": 0xBF, "slash": 0xBF,
    ";": 0xBA, "semicolon": 0xBA,
    "'": 0xDE, "apostrophe": 0xDE,
    "-": 0xBD, "minus": 0xBD,
    "=": 0xBB, "equal": 0xBB,
    "[": 0xDB, "leftbrace": 0xDB,
    "]": 0xDD, "rightbrace": 0xDD,
    "\\": 0xDC, "backslash": 0xDC,
    "`": 0xC0, "grave": 0xC0,
    **{f"f{n}": 0x6F + n for n in range(1, 25)},
    **{f"kp{n}": 0x60 + n for n in range(10)},
}

WM_KEYDOWN = 0x0100
WM_KEYUP = 0x0101
WM_SYSKEYDOWN = 0x0104
WM_SYSKEYUP = 0x0105
WM_TIMER = 0x0113
WM_QUIT = 0x0012
LLKHF_INJECTED = 0x00000010
WH_KEYBOARD_LL = 13
VK_LCONTROL = 0xA2
# AltGr's synthetic Left Ctrl carries this marker bit in its scan code
# (0x21D); a physical Left Ctrl is plain 0x1D.
ALTGR_FAKE_CTRL_SCAN_BIT = 0x200

# Windows silently unhooks a low-level hook that ever overruns
# LowLevelHooksTimeout (a GIL stall can do it). Re-arming periodically
# bounds how long a dropped hook can leave the hotkey dead.
REHOOK_INTERVAL_MS = 120_000


def vk_for_key(name: str) -> int:
    name = name.strip().lower()
    if name in VK_KEY_ALIASES:
        return VK_KEY_ALIASES[name]
    if len(name) == 1 and name.isascii() and name.isalnum():
        return ord(name.upper())
    raise ValueError(f"unsupported hotkey key: {name}")


class WinHotkeySpec:
    """Same contract as HotkeySpec, expressed in Windows virtual keys."""

    def __init__(self, key: str, *, allow_bare: bool = False):
        parts = [part.strip().lower() for part in key.split("+") if part.strip()]
        if not parts:
            raise ValueError("hotkey.key is empty")
        if len(parts) < 2 and not allow_bare:
            raise ValueError("hotkey.key must include at least one modifier and one key")
        self.modifier_groups = []
        for part in parts[:-1]:
            if part not in VK_MODIFIER_ALIASES:
                raise ValueError(f"unsupported hotkey modifier: {part}")
            self.modifier_groups.append(VK_MODIFIER_ALIASES[part])
        self.trigger_code = vk_for_key(parts[-1])
        self.key = key

    @property
    def is_bare(self) -> bool:
        return not self.modifier_groups

    @property
    def codes(self) -> set[int]:
        codes = {self.trigger_code}
        for group in self.modifier_groups:
            codes.update(group)
        return codes

    def is_pressed(self, pressed: set[int]) -> bool:
        return (
            self.trigger_code in pressed
            and all(group & pressed for group in self.modifier_groups)
        )


# When the user last pressed a key (a real, fresh press: not our injected
# input, not auto-repeat) — monotonic seconds, from whichever hotkey hook
# saw it. The selection copy uses it to tell a copy the user made from an
# app's late one.
_last_user_keydown = 0.0


def last_user_keydown() -> float:
    return _last_user_keydown


def _send_menu_mask() -> None:  # pragma: no cover - requires Windows
    """Tap the inert mask key so the user's coming Alt/Win release (after a
    swallowed chord) can't open the menu bar or the Start menu."""
    try:
        from voice_keyboard.windows.injector import KEYEVENTF_KEYUP, VK_MASK, WinTextInjector

        injector = WinTextInjector()
        injector.start()
        injector._send([(VK_MASK, 0, 0), (VK_MASK, 0, KEYEVENTF_KEYUP)])
    except Exception:
        logger.debug("menu mask send failed", exc_info=True)


class WinHotkeyListener(HotkeyListener):
    def __init__(
        self,
        config: dict,
        *,
        on_toggle,
        on_hold_start,
        on_hold_stop,
        on_hold_cancel=None,
    ):
        # Base initializes the shared state machine; _make_spec swaps in the
        # Windows virtual-key spec.
        super().__init__(
            config,
            on_toggle=on_toggle,
            on_hold_start=on_hold_start,
            on_hold_stop=on_hold_stop,
            on_hold_cancel=on_hold_cancel,
        )
        self._thread_id = None
        # True between a swallowed trigger key-down and its key-up, so the
        # auto-repeats and the release are swallowed with it.
        self._swallowing = False
        self._send_mask = _send_menu_mask
        # vk -> is it down right now (GetAsyncKeyState); set by the hook
        # thread. None (tests, or before the hook runs) skips the resync.
        self._key_is_down = None
        # Modifiers our own injector released while still physically held
        # (hold-to-talk): logically up, but NOT released by the user.
        self._injected_up: set[int] = set()
        # Why SetWindowsHookExW failed (Win32 error code), if it did.
        self.hook_error: Optional[int] = None

    def _make_spec(self, key: str):
        return WinHotkeySpec(key, allow_bare=self._allow_bare)

    def start(self) -> None:
        if not self._enabled or self._mode == "disabled":
            logger.info("Hotkey listener disabled")
            return
        self._stop_event.clear()
        self._thread = threading.Thread(
            target=self._run_hook, name="voice-keyboard-hotkey", daemon=True
        )
        self._thread.start()
        logger.info(
            "Hotkey listener started (keyboard hook): %s (%s)", self._spec.key, self._mode
        )

    def stop(self) -> None:
        self._stop_event.set()
        self._cancel_auto_hold_timer()
        if self._thread_id is not None:
            try:
                ctypes.WinDLL("user32").PostThreadMessageW(  # type: ignore[attr-defined]
                    self._thread_id, WM_QUIT, 0, 0
                )
            except Exception:  # pragma: no cover - teardown best effort
                pass
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=1.0)
        self._thread = None
        self._thread_id = None
        logger.info("Hotkey listener stopped")

    def _should_swallow(self, vk_code: int, down: bool) -> bool:
        """Consume the chord's trigger key so the focused app never sees it.
        Called on the hook thread BEFORE the state machine updates."""
        if vk_code != self._spec.trigger_code or vk_code in MODIFIER_VKS:
            return False
        if not down:
            swallowed, self._swallowing = self._swallowing, False
            return swallowed
        if self._swallowing:
            return True  # auto-repeat of a swallowed press
        self._swallowing = all(
            group & self._pressed for group in self._spec.modifier_groups
        )
        if self._swallowing and MENU_VKS & self._pressed:
            threading.Thread(target=self._send_mask, daemon=True).start()
        return self._swallowing

    def _resync_before_press(self, vk_code: int) -> None:
        """A key is going down: forget held keys whose release this hook
        never saw, so they can't complete the chord. The trigger is checked
        on every press (a stale V plus a fresh Ctrl+Alt would fire); the
        modifiers when the trigger itself goes down. Our own injected
        releases are exempt — hold-to-talk keeps the hotkey physically held
        while molten typing logically releases it."""
        is_down = self._key_is_down
        if is_down is None:
            return
        trigger = self._spec.trigger_code
        try:
            if (
                trigger in self._pressed
                and not self._swallowing
                and trigger not in self._injected_up
                and not is_down(trigger)
            ):
                # Recorded as held, but Windows says it's up: its release was
                # missed. (A swallowed trigger never updates Windows' key
                # state, so this is skipped while swallowing.)
                self._handle_key_event(trigger, 0)
            if vk_code != trigger:
                return
            for vk in [v for v in self._pressed if v in MODIFIER_VKS and v != trigger]:
                if vk not in self._injected_up and not is_down(vk):
                    self._handle_key_event(vk, 0)
        except Exception:
            logger.debug("hotkey key-state resync failed", exc_info=True)

    def _resync_left_ctrl(self) -> None:
        """At AltGr's fake Left Ctrl: once it passes, Windows reports Left
        Ctrl down for as long as AltGr is held, hiding a recorded Left Ctrl
        whose release was missed. Inside the hook Windows still reports the
        state from before this event — the last chance to tell."""
        is_down = self._key_is_down
        if (
            is_down is not None
            and VK_LCONTROL in self._pressed
            and VK_LCONTROL not in self._injected_up
        ):
            try:
                if not is_down(VK_LCONTROL):
                    self._handle_key_event(VK_LCONTROL, 0)
            except Exception:
                logger.debug("AltGr key-state resync failed", exc_info=True)

    def _on_hook_event(self, w_param: int, vk_code: int, flags: int, scan_code: int = 0) -> bool:
        """Feed one hook event to the state machine. True = swallow it."""
        global _last_user_keydown
        down = w_param in (WM_KEYDOWN, WM_SYSKEYDOWN)
        up = w_param in (WM_KEYUP, WM_SYSKEYUP)
        if flags & LLKHF_INJECTED:
            # Our own SendInput typing must never trigger the hotkey. Note
            # modifier releases the injector makes while the user still
            # holds the key, so the resync doesn't take them as real.
            if up and vk_code in self._pressed:
                self._injected_up.add(vk_code)
            return False
        if vk_code == VK_LCONTROL and scan_code & ALTGR_FAKE_CTRL_SCAN_BIT:
            if down:
                self._resync_left_ctrl()
            return False  # AltGr's synthetic Ctrl: AltGr is not Ctrl+Alt
        if not (down or up):
            return False
        if down:
            if vk_code not in self._pressed:
                _last_user_keydown = time.monotonic()
            self._resync_before_press(vk_code)
            if vk_code not in MODIFIER_VKS and vk_code != self._spec.trigger_code:
                # Typing other keys: a chord's modifiers our injector
                # released are no longer presumed held (their real release
                # may have been missed meanwhile). Only after this press's
                # own resync, and never a held bare trigger: that one ends
                # with its own release (or a cancel, not a send).
                self._injected_up &= {self._spec.trigger_code}
        # Only now: the resync above must still see this key's exemption
        # (a held bare-modifier trigger auto-repeats after our injected
        # release, and Windows calls it up until this event passes).
        self._injected_up.discard(vk_code)
        swallow = self._should_swallow(vk_code, down)
        # LL hooks repeat key-down while held; the state machine treats
        # re-adding a pressed code as a no-op, so this is naturally safe.
        self._handle_key_event(vk_code, 1 if down else 0)
        return swallow

    def _run_hook(self) -> None:  # pragma: no cover - requires Windows
        from ctypes import wintypes

        user32 = ctypes.WinDLL("user32", use_last_error=True)  # type: ignore[attr-defined]
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)  # type: ignore[attr-defined]
        self._thread_id = kernel32.GetCurrentThreadId()

        LRESULT = ctypes.c_ssize_t  # LONG_PTR — pointer-width, not c_int

        class KBDLLHOOKSTRUCT(ctypes.Structure):
            _fields_ = [
                ("vkCode", wintypes.DWORD),
                ("scanCode", wintypes.DWORD),
                ("flags", wintypes.DWORD),
                ("time", wintypes.DWORD),
                ("dwExtraInfo", ctypes.c_size_t),
            ]

        HOOKPROC = ctypes.WINFUNCTYPE(
            LRESULT, ctypes.c_int, wintypes.WPARAM, wintypes.LPARAM
        )

        # Prototype every user32 call so ctypes does not truncate 64-bit
        # pointers/handles to c_int. Untyped, CallNextHookEx would mangle the
        # KBDLLHOOKSTRUCT pointer it forwards down the hook chain, and
        # SetWindowsHookExW would return a truncated (invalid) HHOOK.
        user32.SetWindowsHookExW.argtypes = [
            ctypes.c_int, HOOKPROC, wintypes.HINSTANCE, wintypes.DWORD
        ]
        user32.SetWindowsHookExW.restype = wintypes.HHOOK
        user32.CallNextHookEx.argtypes = [
            wintypes.HHOOK, ctypes.c_int, wintypes.WPARAM, wintypes.LPARAM
        ]
        user32.CallNextHookEx.restype = LRESULT
        user32.UnhookWindowsHookEx.argtypes = [wintypes.HHOOK]
        user32.UnhookWindowsHookEx.restype = wintypes.BOOL
        user32.GetMessageW.argtypes = [
            ctypes.POINTER(wintypes.MSG), wintypes.HWND, ctypes.c_uint, ctypes.c_uint
        ]
        user32.GetMessageW.restype = ctypes.c_int
        user32.SetTimer.argtypes = [
            wintypes.HWND, ctypes.c_size_t, ctypes.c_uint, ctypes.c_void_p
        ]
        user32.SetTimer.restype = ctypes.c_size_t
        user32.KillTimer.argtypes = [wintypes.HWND, ctypes.c_size_t]
        user32.GetAsyncKeyState.argtypes = [ctypes.c_int]
        user32.GetAsyncKeyState.restype = ctypes.c_short
        user32.PostThreadMessageW.argtypes = [
            wintypes.DWORD, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM
        ]
        self._key_is_down = lambda vk: bool(user32.GetAsyncKeyState(vk) & 0x8000)

        def hook(n_code, w_param, l_param):
            if n_code >= 0:
                try:
                    data = ctypes.cast(l_param, ctypes.POINTER(KBDLLHOOKSTRUCT)).contents
                    if self._on_hook_event(
                        int(w_param), int(data.vkCode), int(data.flags), int(data.scanCode)
                    ):
                        return 1
                except Exception:
                    logger.exception("hotkey hook handling failed")
            return user32.CallNextHookEx(None, n_code, w_param, l_param)

        hook_proc = HOOKPROC(hook)
        handle = user32.SetWindowsHookExW(WH_KEYBOARD_LL, hook_proc, None, 0)
        if not handle:
            self.hook_error = ctypes.get_last_error()
            logger.warning("Could not install the keyboard hook (error %d)", self.hook_error)
            return
        timer = user32.SetTimer(None, 0, REHOOK_INTERVAL_MS, None)
        try:
            msg = wintypes.MSG()
            while user32.GetMessageW(ctypes.byref(msg), None, 0, 0) > 0:
                if self._stop_event.is_set():
                    break
                if msg.message == WM_TIMER and msg.hWnd is None:
                    fresh = user32.SetWindowsHookExW(WH_KEYBOARD_LL, hook_proc, None, 0)
                    if fresh:
                        user32.UnhookWindowsHookEx(handle)
                        handle = fresh
                    continue
                user32.TranslateMessage(ctypes.byref(msg))
                user32.DispatchMessageW(ctypes.byref(msg))
        finally:
            if timer:
                user32.KillTimer(None, timer)
            user32.UnhookWindowsHookEx(handle)
