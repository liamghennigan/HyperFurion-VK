"""Event-tap hotkey listener (macOS).

The combo state machine — tap/hold/auto semantics, hold thresholds,
latching, the bare-key abort — is inherited unchanged from
voice_keyboard.hotkey. Only the event source differs: a CGEventTap feeds
macOS virtual keycodes into the same _handle_key_event(), and modifier
state is derived from flagsChanged events (macOS reports modifiers as a
bitmask, not as ordinary key events). The device-dependent bits of that
mask tell left from right, so a bare Right Command or Right Option can be
a hold-to-talk key, as Right Ctrl is on Linux and Windows. The fn (Globe)
key works too.

Like the Windows hook, the tap CONSUMES the trigger key of a chord while
its modifiers are held, so Control+Option+V never also reaches the
focused app (in a terminal, ^V would quote the next key). That needs an
active tap, which needs Accessibility — the permission typing needs
anyway. Without it the listener falls back to a listen-only tap (Input
Monitoring), where the chord also reaches the app, as it does on Linux.
Bare-modifier bindings are never swallowed.

Our own injected keystrokes carry a tag (see INJECTED_EVENT_TAG) and are
ignored, so dictation can never re-trigger a hotkey. macOS switches a tap
off when a callback is slow or when the user changes input focus to a
secure field; the callback switches it straight back on.
"""

import logging
import threading
from typing import Optional

from voice_keyboard.hotkey import HotkeyListener

logger = logging.getLogger(__name__)

# ANSI-layout virtual keycodes (Carbon HIToolbox kVK_* values). These are
# key POSITIONS: on a non-US layout the key labelled V may sit elsewhere.
MAC_KEYCODES = {
    "a": 0, "s": 1, "d": 2, "f": 3, "h": 4, "g": 5, "z": 6, "x": 7,
    "c": 8, "v": 9, "b": 11, "q": 12, "w": 13, "e": 14, "r": 15,
    "y": 16, "t": 17, "1": 18, "2": 19, "3": 20, "4": 21, "6": 22,
    "5": 23, "=": 24, "9": 25, "7": 26, "-": 27, "8": 28, "0": 29,
    "]": 30, "o": 31, "u": 32, "[": 33, "i": 34, "p": 35, "l": 37,
    "j": 38, "'": 39, "k": 40, ";": 41, "\\": 42, ",": 43, "/": 44,
    "n": 45, "m": 46, ".": 47, "`": 50,
    "return": 36, "enter": 36, "tab": 48, "space": 49, "spacebar": 49,
    # The evdev-style names the Linux and Windows tables accept, so one
    # config.toml binds the same keys everywhere.
    "period": 47, "dot": 47, "comma": 43, "slash": 44, "minus": 27,
    "equal": 24, "semicolon": 41, "apostrophe": 39, "grave": 50,
    "backslash": 42, "leftbrace": 33, "rightbrace": 30,
    "esc": 53, "escape": 53,
    "f1": 122, "f2": 120, "f3": 99, "f4": 118, "f5": 96, "f6": 97,
    "f7": 98, "f8": 100, "f9": 101, "f10": 109, "f11": 103, "f12": 111,
    "f13": 105, "f14": 107, "f15": 113, "f16": 106, "f17": 64,
    "f18": 79, "f19": 80, "f20": 90,
}

# Modifier keys by side (kVK_Command, kVK_RightCommand, ...).
KVK_LEFT_COMMAND, KVK_RIGHT_COMMAND = 55, 54
KVK_LEFT_SHIFT, KVK_RIGHT_SHIFT = 56, 60
KVK_LEFT_OPTION, KVK_RIGHT_OPTION = 58, 61
KVK_LEFT_CONTROL, KVK_RIGHT_CONTROL = 59, 62
KVK_FUNCTION = 63

# Single modifier keys, usable as a bare hold-to-talk binding (allow_bare)
# or as a chord's trigger. MacBook keyboards have no Right Control; Right
# Command and Right Option sit under the right thumb.
MAC_SIDE_KEYS = {
    "rightcmd": KVK_RIGHT_COMMAND, "rightcommand": KVK_RIGHT_COMMAND,
    "rightsuper": KVK_RIGHT_COMMAND, "rightmeta": KVK_RIGHT_COMMAND,
    "leftcmd": KVK_LEFT_COMMAND, "leftcommand": KVK_LEFT_COMMAND,
    "leftsuper": KVK_LEFT_COMMAND, "leftmeta": KVK_LEFT_COMMAND,
    "rightoption": KVK_RIGHT_OPTION, "rightalt": KVK_RIGHT_OPTION, "altgr": KVK_RIGHT_OPTION,
    "leftoption": KVK_LEFT_OPTION, "leftalt": KVK_LEFT_OPTION,
    "rightctrl": KVK_RIGHT_CONTROL, "rightcontrol": KVK_RIGHT_CONTROL,
    "leftctrl": KVK_LEFT_CONTROL, "leftcontrol": KVK_LEFT_CONTROL,
    "rightshift": KVK_RIGHT_SHIFT, "leftshift": KVK_LEFT_SHIFT,
    "fn": KVK_FUNCTION, "globe": KVK_FUNCTION,
}

MAC_MODIFIER_ALIASES = {
    "control": frozenset({KVK_LEFT_CONTROL, KVK_RIGHT_CONTROL}),
    "ctrl": frozenset({KVK_LEFT_CONTROL, KVK_RIGHT_CONTROL}),
    "shift": frozenset({KVK_LEFT_SHIFT, KVK_RIGHT_SHIFT}),
    "alt": frozenset({KVK_LEFT_OPTION, KVK_RIGHT_OPTION}),
    "option": frozenset({KVK_LEFT_OPTION, KVK_RIGHT_OPTION}),
    "opt": frozenset({KVK_LEFT_OPTION, KVK_RIGHT_OPTION}),
    "super": frozenset({KVK_LEFT_COMMAND, KVK_RIGHT_COMMAND}),
    "meta": frozenset({KVK_LEFT_COMMAND, KVK_RIGHT_COMMAND}),
    "win": frozenset({KVK_LEFT_COMMAND, KVK_RIGHT_COMMAND}),
    "cmd": frozenset({KVK_LEFT_COMMAND, KVK_RIGHT_COMMAND}),
    "command": frozenset({KVK_LEFT_COMMAND, KVK_RIGHT_COMMAND}),
}

# CGEventFlags: the device-independent bit per modifier family, and the
# device-dependent bits (IOKit NX_DEVICE*KEYMASK) that tell left from
# right. Numeric so the logic is testable without Quartz. fn has no
# per-side bit (kCGEventFlagMaskSecondaryFn only).
FLAG_MASKS = (
    (frozenset({KVK_LEFT_CONTROL, KVK_RIGHT_CONTROL}), 0x00040000),  # control
    (frozenset({KVK_LEFT_SHIFT, KVK_RIGHT_SHIFT}), 0x00020000),      # shift
    (frozenset({KVK_LEFT_OPTION, KVK_RIGHT_OPTION}), 0x00080000),    # option/alt
    (frozenset({KVK_LEFT_COMMAND, KVK_RIGHT_COMMAND}), 0x00100000),  # command
    (frozenset({KVK_FUNCTION}), 0x00800000),                         # fn / Globe
)
DEVICE_FLAG_MASKS = {
    KVK_LEFT_CONTROL: 0x0001, KVK_LEFT_SHIFT: 0x0002, KVK_RIGHT_SHIFT: 0x0004,
    KVK_LEFT_COMMAND: 0x0008, KVK_RIGHT_COMMAND: 0x0010,
    KVK_LEFT_OPTION: 0x0020, KVK_RIGHT_OPTION: 0x0040,
    KVK_RIGHT_CONTROL: 0x2000,
}
MODIFIER_CODES = frozenset().union(*(group for group, _ in FLAG_MASKS))

# kCGEventSourceUserData value stamped on every event our injector posts
# ("HFVK"): the tap ignores them, as the Windows hook ignores injected input.
INJECTED_EVENT_TAG = 0x4846564B
# Quartz event-field and event-type numbers, for the callback's fallbacks.
_FIELD_SOURCE_USER_DATA = 42  # kCGEventSourceUserData
_TAP_DISABLED_BY_TIMEOUT = 0xFFFFFFFE
_TAP_DISABLED_BY_USER_INPUT = 0xFFFFFFFF

PERMISSION_HINT = (
    "Hotkeys need permission to see the keyboard: System Settings → Privacy &"
    " Security → Accessibility (and Input Monitoring) → turn on the app that runs"
    " HyperFurion VK (your terminal app when you start it from a terminal, else"
    " Python), then restart it. `voice-keyboard doctor` checks this."
)


def mac_key_code(name: str, layout=None) -> int:
    """A trigger key name as a macOS virtual keycode. A single character
    resolves on `layout` (a keylayout.LayoutKeymap) first, so control+alt+v
    is the key labelled V on Dvorak or AZERTY too."""
    key = str(name).strip().lower()
    if key in MAC_SIDE_KEYS:
        return MAC_SIDE_KEYS[key]
    if len(key) == 1 and layout is not None:
        code = layout.code_for(key)
        if code is not None:
            return code
    if key in MAC_KEYCODES:
        return MAC_KEYCODES[key]
    raise ValueError(f"unsupported hotkey key: {name}")


class MacHotkeySpec:
    """Same contract as HotkeySpec, expressed in macOS virtual keycodes."""

    def __init__(self, key: str, *, allow_bare: bool = False, layout=None):
        parts = [part.strip().lower() for part in key.split("+") if part.strip()]
        if not parts:
            raise ValueError("hotkey.key is empty")
        if len(parts) < 2 and not allow_bare:
            raise ValueError("hotkey.key must include at least one modifier and one key")
        self.modifier_groups = []
        for part in parts[:-1]:
            if part not in MAC_MODIFIER_ALIASES:
                raise ValueError(f"unsupported hotkey modifier: {part}")
            self.modifier_groups.append(MAC_MODIFIER_ALIASES[part])
        self.trigger_code = mac_key_code(parts[-1], layout)
        self.key = key

    @property
    def is_bare(self) -> bool:
        """A single key with no modifiers (e.g. rightcmd, fn): see
        HotkeySpec.is_bare — a bare MODIFIER is the terminal-safe summon."""
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


def _quartz_value(quartz, name: str, default):
    return getattr(quartz, name, default)


class MacHotkeyListener(HotkeyListener):
    def __init__(
        self,
        config: dict,
        *,
        on_toggle,
        on_hold_start,
        on_hold_stop,
        on_hold_cancel=None,
        use_layout: bool = True,
    ):
        # Letters follow the keyboard layout (the key labelled V); tests
        # pass False for the plain ANSI positions.
        self._use_layout = use_layout
        # The shared tap/hold/auto state machine is initialized by the base;
        # _make_spec swaps in the macOS keycode spec. Only the event-source
        # fields are added here.
        super().__init__(
            config,
            on_toggle=on_toggle,
            on_hold_start=on_hold_start,
            on_hold_stop=on_hold_stop,
            on_hold_cancel=on_hold_cancel,
        )
        self._mod_down: set[int] = set()
        self._runloop = None
        self._tap = None
        # True between a swallowed trigger key-down and its key-up, so the
        # auto-repeats and the release are swallowed with it.
        self._swallowing = False
        # "active" (consumes the trigger), "listen" (sees keys only), or ""
        # (no tap: no permission) once the tap thread has started.
        self.tap_kind = ""
        # keycode -> is it physically down right now? (CGEventSourceKeyState
        # on a Mac; None in tests). Catches a release the tap never saw.
        self._key_is_down = None

    def _make_spec(self, key: str):
        layout = None
        if self._use_layout:
            from voice_keyboard.macos.keylayout import default_keymap

            layout = default_keymap()
        return MacHotkeySpec(key, allow_bare=self._allow_bare, layout=layout)

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
        self._tap = None
        logger.info("Hotkey listener stopped")

    # ------------------------------------------------------------ the logic

    def _apply_flags(self, flags: int, keycode: int = -1) -> None:
        """Translate a flagsChanged event (its CGEventFlags and the keycode
        of the modifier that changed) into per-key transitions. The
        device-dependent bits say which side is down; events without them
        (some synthetic or remote-desktop input) fall back to the family
        bit, credited to the key that changed (else the left one)."""
        for group, mask in FLAG_MASKS:
            sided = [code for code in group if code in DEVICE_FLAG_MASKS]
            has_sides = any(flags & DEVICE_FLAG_MASKS[code] for code in sided)
            if has_sides:
                wanted = {code for code in sided if flags & DEVICE_FLAG_MASKS[code]}
            elif not flags & mask:
                wanted = set()
            elif group & self._mod_down:
                wanted = group & self._mod_down
            else:
                wanted = {keycode if keycode in group else min(group)}
            for code in sorted(group):
                if code in self._mod_down and code not in wanted:
                    self._mod_down.discard(code)
                    self._handle_key_event(code, 0)
            for code in sorted(group):
                if code in wanted and code not in self._mod_down:
                    self._mod_down.add(code)
                    self._handle_key_event(code, 1)

    def _should_swallow(self, code: int, down: bool) -> bool:
        """Consume the chord's trigger key so the focused app never sees it
        (the Windows hook's rule). Called BEFORE the state machine updates,
        so `_pressed` holds the modifiers only. Never a modifier."""
        if code != self._spec.trigger_code or code in MODIFIER_CODES:
            return False
        if not down:
            swallowed, self._swallowing = self._swallowing, False
            return swallowed
        if self._swallowing:
            return True  # auto-repeat of a swallowed press
        with self._lock:
            pressed = set(self._pressed)
        self._swallowing = all(group & pressed for group in self._spec.modifier_groups)
        return self._swallowing

    def _release_stale_modifiers(self, flags: int) -> None:
        """A key event's flags carry the modifier state too: a modifier we
        think is down but whose family bit is clear was released while the
        tap wasn't looking (a secure field, a disabled tap). Releases only,
        and never fn: arrow and function keys carry the fn bit on their own."""
        for group, mask in FLAG_MASKS:
            if KVK_FUNCTION in group or flags & mask:
                continue
            for code in sorted(group & self._mod_down):
                self._mod_down.discard(code)
                self._handle_key_event(code, 0)

    def _release_stale_trigger(self) -> None:
        """The chord's trigger recorded as held but physically up: its
        release was missed, and it must not complete the chord when the
        modifiers go down next (the Windows hook's resync)."""
        trigger = self._spec.trigger_code
        if self._key_is_down is None or trigger in MODIFIER_CODES or self._swallowing:
            return
        with self._lock:
            held = trigger in self._pressed
        try:
            if held and not self._key_is_down(trigger):
                self._handle_key_event(trigger, 0)
        except Exception:
            logger.debug("hotkey key-state resync failed", exc_info=True)

    def _on_tap_event(self, event_type: str, keycode: int, flags: Optional[int], *,
                      autorepeat: bool = False, injected: bool = False) -> bool:
        """Feed one tap event ("down", "up" or "flags") to the state
        machine. `flags` is the event's CGEventFlags (None: unknown). True =
        swallow it."""
        if injected:
            return False  # our own typing never triggers a hotkey
        if event_type == "flags":
            self._release_stale_trigger()
            self._apply_flags(flags or 0, keycode)
            return False
        if flags is not None:
            self._release_stale_modifiers(flags)
        down = event_type == "down"
        swallow = self._should_swallow(keycode, down)
        if not autorepeat:
            self._handle_key_event(keycode, 1 if down else 0)
        return swallow

    # ------------------------------------------------------------ the tap

    def _run_tap(self) -> None:  # pragma: no cover - requires macOS
        try:
            import Quartz
        except ImportError:
            logger.warning("Quartz (pyobjc) is missing: no global hotkey. Reinstall voice-keyboard.")
            return

        key_down = Quartz.kCGEventKeyDown
        key_up = Quartz.kCGEventKeyUp
        flags_changed = Quartz.kCGEventFlagsChanged
        disabled = {
            _quartz_value(Quartz, "kCGEventTapDisabledByTimeout", _TAP_DISABLED_BY_TIMEOUT),
            _quartz_value(Quartz, "kCGEventTapDisabledByUserInput", _TAP_DISABLED_BY_USER_INPUT),
        }
        user_data_field = _quartz_value(Quartz, "kCGEventSourceUserData", _FIELD_SOURCE_USER_DATA)
        hid_state = _quartz_value(Quartz, "kCGEventSourceStateHIDSystemState", 1)
        self._key_is_down = lambda code: bool(Quartz.CGEventSourceKeyState(hid_state, code))

        def callback(_proxy, event_type, event, _refcon):
            try:
                if event_type in disabled:
                    # Too slow once, or a secure field took focus: macOS
                    # switched the tap off. Switch it straight back on.
                    if self._tap is not None and not self._stop_event.is_set():
                        Quartz.CGEventTapEnable(self._tap, True)
                        logger.info("Hotkey event tap re-enabled")
                    return event
                injected = (
                    Quartz.CGEventGetIntegerValueField(event, user_data_field)
                    == INJECTED_EVENT_TAG
                )
                code = int(
                    Quartz.CGEventGetIntegerValueField(event, Quartz.kCGKeyboardEventKeycode)
                )
                if event_type == flags_changed:
                    self._on_tap_event(
                        "flags", code, int(Quartz.CGEventGetFlags(event)), injected=injected
                    )
                    return event
                if event_type in (key_down, key_up):
                    repeat = bool(
                        Quartz.CGEventGetIntegerValueField(
                            event, Quartz.kCGKeyboardEventAutorepeat
                        )
                    )
                    swallow = self._on_tap_event(
                        "down" if event_type == key_down else "up",
                        code,
                        int(Quartz.CGEventGetFlags(event)),
                        autorepeat=repeat,
                        injected=injected,
                    )
                    if swallow and self.tap_kind == "active":
                        return None
            except Exception:
                logger.exception("hotkey event handling failed")
            return event

        mask = (1 << key_down) | (1 << key_up) | (1 << flags_changed)
        tap = None
        for kind, option in (
            ("active", Quartz.kCGEventTapOptionDefault),
            ("listen", Quartz.kCGEventTapOptionListenOnly),
        ):
            tap = Quartz.CGEventTapCreate(
                Quartz.kCGSessionEventTap,
                Quartz.kCGHeadInsertEventTap,
                option,
                mask,
                callback,
                None,
            )
            if tap is not None:
                self.tap_kind = kind
                break
        if tap is None:
            self.tap_kind = ""
            logger.warning("Could not create the keyboard event tap. %s", PERMISSION_HINT)
            try:
                from voice_keyboard.macos.permissions import notify_missing

                notify_missing("hotkey")
            except Exception:
                logger.debug("permission notification failed", exc_info=True)
            return
        if self.tap_kind == "listen":
            logger.info(
                "Hotkey tap is listen-only (no Accessibility): %s also reaches the focused app",
                self._spec.key,
            )
        self._tap = tap
        source = Quartz.CFMachPortCreateRunLoopSource(None, tap, 0)
        self._runloop = Quartz.CFRunLoopGetCurrent()
        Quartz.CFRunLoopAddSource(self._runloop, source, Quartz.kCFRunLoopCommonModes)
        Quartz.CGEventTapEnable(tap, True)
        Quartz.CFRunLoopRun()
