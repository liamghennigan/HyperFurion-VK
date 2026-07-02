"""Low-level keyboard hook hotkey listener (Windows).

Same shape as the macOS backend: the combo state machine is inherited
unchanged from voice_keyboard.hotkey; only the event source differs — a
WH_KEYBOARD_LL hook pumped on its own thread feeds virtual-key codes
into _handle_key_event(). Injected events (our own SendInput typing)
are ignored so dictation can never re-trigger the hotkey.

RegisterHotKey was deliberately not used: it reports presses only, and
hold-to-talk needs releases.
"""

import logging
import threading

from voice_keyboard.hotkey import HotkeyListener

logger = logging.getLogger(__name__)

# Virtual-key codes (winuser.h).
VK_MODIFIER_ALIASES = {
    "control": frozenset({0x11, 0xA2, 0xA3}),
    "ctrl": frozenset({0x11, 0xA2, 0xA3}),
    "shift": frozenset({0x10, 0xA0, 0xA1}),
    "alt": frozenset({0x12, 0xA4, 0xA5}),
    "super": frozenset({0x5B, 0x5C}),
    "meta": frozenset({0x5B, 0x5C}),
    "win": frozenset({0x5B, 0x5C}),
}

VK_KEY_ALIASES = {
    "space": 0x20,
    "spacebar": 0x20,
    "enter": 0x0D,
    "return": 0x0D,
    "tab": 0x09,
}

WM_KEYDOWN = 0x0100
WM_KEYUP = 0x0101
WM_SYSKEYDOWN = 0x0104
WM_SYSKEYUP = 0x0105
LLKHF_INJECTED = 0x00000010
WH_KEYBOARD_LL = 13
WM_QUIT = 0x0012


def vk_for_key(name: str) -> int:
    if name in VK_KEY_ALIASES:
        return VK_KEY_ALIASES[name]
    if len(name) == 1 and (name.isalpha() or name.isdigit()):
        return ord(name.upper())
    raise ValueError(f"unsupported hotkey key: {name}")


class WinHotkeySpec:
    """Same contract as HotkeySpec, expressed in Windows virtual keys."""

    def __init__(self, key: str):
        parts = [part.strip().lower() for part in key.split("+") if part.strip()]
        if len(parts) < 2:
            raise ValueError("hotkey.key must include at least one modifier and one key")
        self.modifier_groups = []
        for part in parts[:-1]:
            if part not in VK_MODIFIER_ALIASES:
                raise ValueError(f"unsupported hotkey modifier: {part}")
            self.modifier_groups.append(VK_MODIFIER_ALIASES[part])
        self.trigger_code = vk_for_key(parts[-1])
        self.key = key

    def is_pressed(self, pressed: set[int]) -> bool:
        return (
            self.trigger_code in pressed
            and all(group & pressed for group in self.modifier_groups)
        )


class WinHotkeyListener(HotkeyListener):
    def __init__(self, config: dict, *, on_toggle, on_hold_start, on_hold_stop):
        # Mirrors the base initializer, minus evdev devices.
        self._enabled = bool(config.get("enabled", True))
        self._mode = str(config.get("mode", "auto")).lower()
        self._hold_threshold_s = (
            float(config.get("hold_threshold_ms", self.DEFAULT_HOLD_THRESHOLD_MS)) / 1000.0
        )
        self._spec = WinHotkeySpec(str(config.get("key", "control+alt+v")))
        self._on_toggle = on_toggle
        self._on_hold_start = on_hold_start
        self._on_hold_stop = on_hold_stop
        self._pressed: set[int] = set()
        self._combo_latched = False
        self._auto_combo_pending = False
        self._hold_active = False
        self._auto_hold_timer = None
        self._devices: list = []
        self._thread = None
        self._stop_event = threading.Event()
        self._lock = threading.Lock()
        self._thread_id = None

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
                import ctypes

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

    def _on_hook_event(self, w_param: int, vk_code: int, flags: int) -> None:
        if flags & LLKHF_INJECTED:
            return  # our own SendInput typing must never trigger the hotkey
        if w_param in (WM_KEYDOWN, WM_SYSKEYDOWN):
            # LL hooks repeat key-down while held; the state machine treats
            # re-adding a pressed code as a no-op, so this is naturally safe.
            self._handle_key_event(vk_code, 1)
        elif w_param in (WM_KEYUP, WM_SYSKEYUP):
            self._handle_key_event(vk_code, 0)

    def _run_hook(self) -> None:  # pragma: no cover - requires Windows
        import ctypes
        from ctypes import wintypes

        user32 = ctypes.WinDLL("user32", use_last_error=True)  # type: ignore[attr-defined]
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)  # type: ignore[attr-defined]
        self._thread_id = kernel32.GetCurrentThreadId()

        class KBDLLHOOKSTRUCT(ctypes.Structure):
            _fields_ = [
                ("vkCode", wintypes.DWORD),
                ("scanCode", wintypes.DWORD),
                ("flags", wintypes.DWORD),
                ("time", wintypes.DWORD),
                ("dwExtraInfo", ctypes.POINTER(wintypes.ULONG)),
            ]

        HOOKPROC = ctypes.WINFUNCTYPE(
            ctypes.c_longlong, ctypes.c_int, wintypes.WPARAM, wintypes.LPARAM
        )

        def hook(n_code, w_param, l_param):
            if n_code >= 0:
                try:
                    data = ctypes.cast(l_param, ctypes.POINTER(KBDLLHOOKSTRUCT)).contents
                    self._on_hook_event(int(w_param), int(data.vkCode), int(data.flags))
                except Exception:
                    logger.exception("hotkey hook handling failed")
            return user32.CallNextHookEx(None, n_code, w_param, l_param)

        hook_proc = HOOKPROC(hook)
        handle = user32.SetWindowsHookExW(WH_KEYBOARD_LL, hook_proc, None, 0)
        if not handle:
            logger.warning("Could not install the keyboard hook (error %d)",
                           ctypes.get_last_error())
            return
        try:
            msg = wintypes.MSG()
            while user32.GetMessageW(ctypes.byref(msg), None, 0, 0) > 0:
                if self._stop_event.is_set():
                    break
                user32.TranslateMessage(ctypes.byref(msg))
                user32.DispatchMessageW(ctypes.byref(msg))
        finally:
            user32.UnhookWindowsHookEx(handle)
