"""Event-tap hotkey listener (macOS).

The combo state machine — tap/hold/auto semantics, hold thresholds,
latching — is inherited unchanged from voice_keyboard.hotkey. Only the
event source differs: a listen-only CGEventTap feeds macOS virtual
keycodes into the same _handle_key_event(), and modifier state is
derived from flagsChanged events (macOS reports modifiers as a bitmask,
not as ordinary key events).

Requires Accessibility permission; without it the tap cannot be created
and the listener logs how to grant it instead of crashing the daemon.
"""

import logging
import threading

from voice_keyboard.hotkey import HotkeyListener

logger = logging.getLogger(__name__)

# ANSI-layout virtual keycodes (Carbon HIToolbox kVK_* values).
MAC_KEYCODES = {
    "a": 0, "s": 1, "d": 2, "f": 3, "h": 4, "g": 5, "z": 6, "x": 7,
    "c": 8, "v": 9, "b": 11, "q": 12, "w": 13, "e": 14, "r": 15,
    "y": 16, "t": 17, "1": 18, "2": 19, "3": 20, "4": 21, "6": 22,
    "5": 23, "=": 24, "9": 25, "7": 26, "-": 27, "8": 28, "0": 29,
    "]": 30, "o": 31, "u": 32, "[": 33, "i": 34, "p": 35, "l": 37,
    "j": 38, "'": 39, "k": 40, ";": 41, "\\": 42, ",": 43, "/": 44,
    "n": 45, "m": 46, ".": 47, "`": 50,
    "return": 36, "enter": 36, "tab": 48, "space": 49, "spacebar": 49,
}

MAC_MODIFIER_ALIASES = {
    "control": frozenset({59, 62}),
    "ctrl": frozenset({59, 62}),
    "shift": frozenset({56, 60}),
    "alt": frozenset({58, 61}),
    "option": frozenset({58, 61}),
    "super": frozenset({55, 54}),
    "meta": frozenset({55, 54}),
    "cmd": frozenset({55, 54}),
    "command": frozenset({55, 54}),
}

# CGEventFlags bits (numeric so the logic is testable without Quartz).
FLAG_MASKS = (
    (frozenset({59, 62}), 0x00040000),  # control
    (frozenset({56, 60}), 0x00020000),  # shift
    (frozenset({58, 61}), 0x00080000),  # option/alt
    (frozenset({55, 54}), 0x00100000),  # command
)


class MacHotkeySpec:
    """Same contract as HotkeySpec, expressed in macOS virtual keycodes."""

    def __init__(self, key: str):
        parts = [part.strip().lower() for part in key.split("+") if part.strip()]
        if len(parts) < 2:
            raise ValueError("hotkey.key must include at least one modifier and one key")
        self.modifier_groups = []
        for part in parts[:-1]:
            if part not in MAC_MODIFIER_ALIASES:
                raise ValueError(f"unsupported hotkey modifier: {part}")
            self.modifier_groups.append(MAC_MODIFIER_ALIASES[part])
        key_part = parts[-1]
        if key_part not in MAC_KEYCODES:
            raise ValueError(f"unsupported hotkey key: {key_part}")
        self.trigger_code = MAC_KEYCODES[key_part]
        self.key = key

    def is_pressed(self, pressed: set[int]) -> bool:
        return (
            self.trigger_code in pressed
            and all(group & pressed for group in self.modifier_groups)
        )


class MacHotkeyListener(HotkeyListener):
    def __init__(self, config: dict, *, on_toggle, on_hold_start, on_hold_stop):
        # The shared tap/hold/auto state machine is initialized by the base;
        # _make_spec swaps in the macOS keycode spec. Only the event-source
        # fields are added here.
        super().__init__(
            config,
            on_toggle=on_toggle,
            on_hold_start=on_hold_start,
            on_hold_stop=on_hold_stop,
        )
        self._mod_down: set[int] = set()
        self._runloop = None

    def _make_spec(self, key: str):
        return MacHotkeySpec(key)

    def start(self) -> None:
        if not self._enabled or self._mode == "disabled":
            logger.info("Hotkey listener disabled")
            return
        self._stop_event.clear()
        self._thread = threading.Thread(
            target=self._run_tap, name="voice-keyboard-hotkey", daemon=True
        )
        self._thread.start()
        logger.info("Hotkey listener started (event tap): %s (%s)", self._spec.key, self._mode)

    def stop(self) -> None:
        self._stop_event.set()
        self._cancel_auto_hold_timer()
        if self._runloop is not None:
            try:
                import Quartz

                Quartz.CFRunLoopStop(self._runloop)
            except Exception:  # pragma: no cover - teardown best effort
                pass
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=1.0)
        self._thread = None
        self._runloop = None
        logger.info("Hotkey listener stopped")

    def _apply_flags(self, flags: int) -> None:
        """Translate a CGEventFlags bitmask into modifier key transitions."""
        for group, mask in FLAG_MASKS:
            canonical = min(group)
            down = bool(flags & mask)
            if down and canonical not in self._mod_down:
                self._mod_down.add(canonical)
                self._handle_key_event(canonical, 1)
            elif not down and canonical in self._mod_down:
                self._mod_down.discard(canonical)
                self._handle_key_event(canonical, 0)

    def _run_tap(self) -> None:  # pragma: no cover - requires macOS
        import Quartz

        def callback(_proxy, event_type, event, _refcon):
            try:
                if event_type in (Quartz.kCGEventKeyDown, Quartz.kCGEventKeyUp):
                    if Quartz.CGEventGetIntegerValueField(
                        event, Quartz.kCGKeyboardEventAutorepeat
                    ):
                        return event
                    code = int(
                        Quartz.CGEventGetIntegerValueField(
                            event, Quartz.kCGKeyboardEventKeycode
                        )
                    )
                    self._handle_key_event(
                        code, 1 if event_type == Quartz.kCGEventKeyDown else 0
                    )
                elif event_type == Quartz.kCGEventFlagsChanged:
                    self._apply_flags(int(Quartz.CGEventGetFlags(event)))
            except Exception:
                logger.exception("hotkey event handling failed")
            return event

        mask = (
            (1 << Quartz.kCGEventKeyDown)
            | (1 << Quartz.kCGEventKeyUp)
            | (1 << Quartz.kCGEventFlagsChanged)
        )
        tap = Quartz.CGEventTapCreate(
            Quartz.kCGSessionEventTap,
            Quartz.kCGHeadInsertEventTap,
            Quartz.kCGEventTapOptionListenOnly,
            mask,
            callback,
            None,
        )
        if tap is None:
            logger.warning(
                "Could not create the keyboard event tap. Grant Accessibility"
                " permission to your Python/terminal in System Settings →"
                " Privacy & Security → Accessibility, then restart the daemon."
            )
            return
        source = Quartz.CFMachPortCreateRunLoopSource(None, tap, 0)
        self._runloop = Quartz.CFRunLoopGetCurrent()
        Quartz.CFRunLoopAddSource(self._runloop, source, Quartz.kCFRunLoopCommonModes)
        Quartz.CGEventTapEnable(tap, True)
        Quartz.CFRunLoopRun()
