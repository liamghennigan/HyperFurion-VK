import errno
import logging
import os
import select
import sys
import threading
import time
from collections.abc import Callable
from typing import Optional

try:
    from evdev import InputDevice, ecodes as e, list_devices
except ImportError:  # non-Linux: create_hotkey_listener() picks the mac backend
    InputDevice = None
    e = None
    list_devices = None

logger = logging.getLogger(__name__)

if e is not None:
    MODIFIER_ALIASES = {
        "control": {e.KEY_LEFTCTRL, e.KEY_RIGHTCTRL},
        "ctrl": {e.KEY_LEFTCTRL, e.KEY_RIGHTCTRL},
        "shift": {e.KEY_LEFTSHIFT, e.KEY_RIGHTSHIFT},
        "alt": {e.KEY_LEFTALT, e.KEY_RIGHTALT},
        "super": {e.KEY_LEFTMETA, e.KEY_RIGHTMETA},
        "meta": {e.KEY_LEFTMETA, e.KEY_RIGHTMETA},
    }

    KEY_ALIASES = {
        "space": e.KEY_SPACE,
        "spacebar": e.KEY_SPACE,
        "enter": e.KEY_ENTER,
        "return": e.KEY_ENTER,
        "tab": e.KEY_TAB,
        # Punctuation keys: evdev names them KEY_DOT etc., not KEY_".",
        # so bindings like control+alt+. resolve (the assistant hotkey).
        ".": e.KEY_DOT,
        "period": e.KEY_DOT,
        ",": e.KEY_COMMA,
        "comma": e.KEY_COMMA,
        "/": e.KEY_SLASH,
        "slash": e.KEY_SLASH,
        ";": e.KEY_SEMICOLON,
        "'": e.KEY_APOSTROPHE,
        "-": e.KEY_MINUS,
        "minus": e.KEY_MINUS,
        "=": e.KEY_EQUAL,
        "[": e.KEY_LEFTBRACE,
        "]": e.KEY_RIGHTBRACE,
        "\\": e.KEY_BACKSLASH,
        "`": e.KEY_GRAVE,
    }
else:
    MODIFIER_ALIASES = {}
    KEY_ALIASES = {}


def create_hotkey_listener(
    config: dict,
    *,
    on_toggle: Callable[[], None],
    on_hold_start: Callable[[], None],
    on_hold_stop: Callable[[], None],
    on_hold_cancel: Optional[Callable[[], None]] = None,
):
    """Platform factory: evdev on Linux, a Quartz event tap on macOS, a
    low-level keyboard hook on Windows. on_hold_cancel (bare-key gesture
    aborted by another key) is honored on Linux and Windows; macOS falls
    back to on_hold_stop semantics."""
    if sys.platform == "darwin":
        from voice_keyboard.macos.hotkey import MacHotkeyListener

        return MacHotkeyListener(
            config,
            on_toggle=on_toggle,
            on_hold_start=on_hold_start,
            on_hold_stop=on_hold_stop,
        )
    if sys.platform == "win32":
        from voice_keyboard.windows.hotkey import WinHotkeyListener

        return WinHotkeyListener(
            config,
            on_toggle=on_toggle,
            on_hold_start=on_hold_start,
            on_hold_stop=on_hold_stop,
            on_hold_cancel=on_hold_cancel,
        )
    return HotkeyListener(
        config,
        on_toggle=on_toggle,
        on_hold_start=on_hold_start,
        on_hold_stop=on_hold_stop,
        on_hold_cancel=on_hold_cancel,
    )

IGNORED_DEVICE_NAMES = {"voice-keyboard"}
# How often the evdev listener looks for keyboards that appeared since it
# started (plugged in, Bluetooth reconnect, re-enumerated after resume).
RESCAN_INTERVAL_S = 3.0

_PRETTY_KEYS = {
    "control": "Ctrl", "ctrl": "Ctrl", "alt": "Alt", "shift": "Shift",
    "rightctrl": "Right Ctrl", "leftctrl": "Left Ctrl",
    "rightalt": "Right Alt", "leftalt": "Left Alt", "altgr": "AltGr",
    "rightshift": "Right Shift", "leftshift": "Left Shift",
    "space": "Space", "enter": "Enter", "return": "Enter", "tab": "Tab",
    "period": ".", "comma": ",", "slash": "/",
}


def pretty_binding(key: str) -> str:
    """A binding as people write it: control+alt+v -> Ctrl+Alt+V,
    rightctrl -> Right Ctrl, super -> Win (Windows) / Super."""
    meta = "Win" if sys.platform == "win32" else "Super"
    parts = [p.strip().lower() for p in str(key).split("+") if p.strip()]
    pretty = []
    for part in parts:
        if part in {"super", "meta", "win"}:
            pretty.append(meta)
        elif part in _PRETTY_KEYS:
            pretty.append(_PRETTY_KEYS[part])
        else:
            pretty.append(part.upper() if len(part) <= 3 else part.title())
    return "+".join(pretty)


_MODIFIER_WORDS = {
    "ctrl": "control", "control": "control", "alt": "alt", "option": "alt",
    "shift": "shift", "super": "super", "meta": "super", "win": "super", "cmd": "super",
}


def binding_signature(key: str):
    """What a binding means, whatever its spelling or order: ctrl+alt+r,
    Alt+Control+R, and control + alt + r are one chord. Uses this
    platform's keycodes when it can, else normalized names."""
    try:
        spec = parse_binding(key, allow_bare=True)
    except ValueError:
        spec = None
    if spec is not None:
        groups = frozenset(frozenset(group) for group in spec.modifier_groups)
        return ("codes", groups, spec.trigger_code)
    parts = [part.strip().lower() for part in str(key).split("+") if part.strip()]
    if not parts:
        return None
    return ("names", frozenset(_MODIFIER_WORDS.get(p, p) for p in parts[:-1]), parts[-1])


def bindings_clash(first: str, second: str) -> bool:
    """True when two bindings are the same chord (both listeners would
    fire, and one would swallow the key from the other)."""
    a, b = binding_signature(first), binding_signature(second)
    return a is not None and a == b


def parse_binding(key: str, *, allow_bare: bool = False):
    """Parse a hotkey binding with this platform's keycode table; raises
    ValueError on a typo. None when no table can be checked (non-Linux
    hosts without their backend, e.g. macOS until a tap is built)."""
    if sys.platform == "win32":
        from voice_keyboard.windows.hotkey import WinHotkeySpec

        return WinHotkeySpec(key, allow_bare=allow_bare)
    if MODIFIER_ALIASES:
        return HotkeySpec(key, allow_bare=allow_bare)
    return None


class HotkeySpec:
    def __init__(self, key: str, *, allow_bare: bool = False):
        parts = [part.strip().lower() for part in key.split("+") if part.strip()]
        if not parts:
            raise ValueError("hotkey.key is empty")
        if len(parts) < 2 and not allow_bare:
            raise ValueError("hotkey.key must include at least one modifier and one key")

        modifier_parts = parts[:-1]
        key_part = parts[-1]
        self.modifier_groups = []
        for part in modifier_parts:
            if part not in MODIFIER_ALIASES:
                raise ValueError(f"unsupported hotkey modifier: {part}")
            self.modifier_groups.append(MODIFIER_ALIASES[part])

        self.trigger_code = _key_code(key_part)
        self.key = key

    @property
    def is_bare(self) -> bool:
        """A single bare key (e.g. rightctrl) with no modifiers. Bare
        MODIFIER triggers are the terminal-safe summon: a modifier alone
        never produces terminal input, so holding it can't spray escape
        codes into a shell the way a held symbol chord (Ctrl+Alt+.) does."""
        return not self.modifier_groups

    def is_pressed(self, pressed: set[int]) -> bool:
        return (
            self.trigger_code in pressed
            and all(group & pressed for group in self.modifier_groups)
        )

    @property
    def codes(self) -> set[int]:
        codes = {self.trigger_code}
        for group in self.modifier_groups:
            codes.update(group)
        return codes


# Open failures that won't fix themselves until the node changes (its
# permissions, or a new device behind it); anything else is retried.
_LASTING_ERRNOS = frozenset({errno.EACCES, errno.EPERM, errno.ENODEV, errno.ENXIO})


def _device_key(device):
    return getattr(device, "path", None) or id(device)


def _device_signature(path: str):
    """Identity of an input node: a recreated node (unplug/replug reusing
    the number) or changed permissions read as a different device."""
    try:
        st = os.stat(path)
    except OSError:
        return None
    return (st.st_rdev, st.st_ino, st.st_ctime_ns)


def _key_code(name: str) -> int:
    if name in KEY_ALIASES:
        return KEY_ALIASES[name]

    attr = f"KEY_{name.upper()}"
    if hasattr(e, attr):
        return getattr(e, attr)

    raise ValueError(f"unsupported hotkey key: {name}")


def _key_capability_codes(device: InputDevice) -> set[int]:
    caps = device.capabilities()
    raw_keys = caps.get(e.EV_KEY, [])
    codes: set[int] = set()
    for item in raw_keys:
        if isinstance(item, tuple):
            codes.add(int(item[0]))
        else:
            codes.add(int(item))
    return codes


class HotkeyListener:
    DEFAULT_HOLD_THRESHOLD_MS = 280

    def __init__(
        self,
        config: dict,
        *,
        on_toggle: Callable[[], None],
        on_hold_start: Callable[[], None],
        on_hold_stop: Callable[[], None],
        on_hold_cancel: Optional[Callable[[], None]] = None,
    ):
        self._enabled = bool(config.get("enabled", True))
        self._mode = str(config.get("mode", "auto")).lower()
        self._hold_threshold_s = (
            float(config.get("hold_threshold_ms", self.DEFAULT_HOLD_THRESHOLD_MS)) / 1000.0
        )
        # A bare-modifier binding (e.g. rightctrl) is allowed when the
        # caller opts in — the assistant's terminal-safe summon key.
        self._allow_bare = bool(config.get("allow_bare", False))
        # Subclasses override _make_spec to parse the combo into their
        # platform's keycodes; everything else — the tap/hold/auto state
        # machine — is shared, so they can call super().__init__().
        self._spec = self._make_spec(str(config.get("key", "control+alt+v")))
        self._on_toggle = on_toggle
        self._on_hold_start = on_hold_start
        self._on_hold_stop = on_hold_stop
        # Fired instead of on_hold_stop when a bare-key gesture is aborted
        # by another key (the user was really doing Ctrl+<something>).
        self._on_hold_cancel = on_hold_cancel or on_hold_stop
        self._pressed: set[int] = set()
        self._combo_latched = False
        self._auto_combo_pending = False
        self._hold_active = False
        self._gesture_aborted = False
        self._auto_hold_timer: Optional[threading.Timer] = None
        self._devices: list = []
        # Keys each device is holding down, so a keyboard that vanishes
        # mid-press doesn't leave them "held" forever.
        self._device_keys: dict = {}
        # Input nodes that aren't a usable keyboard (mice, power buttons,
        # unreadable), by node identity: the 3 s rescan skips them until the
        # node is recreated or its permissions change.
        self._rejected: dict = {}
        self._thread: Optional[threading.Thread] = None
        self._stop_event = threading.Event()
        self._lock = threading.Lock()

    def _make_spec(self, key: str):
        """The Linux (evdev) spec. macOS/Windows backends override this."""
        return HotkeySpec(key, allow_bare=self._allow_bare)

    def start(self) -> None:
        if not self._enabled or self._mode == "disabled":
            logger.info("Hotkey listener disabled")
            return

        self._devices = self._open_devices()
        if not self._devices:
            # Keep listening anyway: a keyboard plugged in (or permissions
            # fixed) later is picked up by the periodic rescan.
            logger.warning(
                "No readable keyboard devices found for hotkey %s yet; watching for one",
                self._spec.key,
            )

        self._stop_event.clear()
        self._thread = threading.Thread(target=self._run, name="voice-keyboard-hotkey", daemon=True)
        self._thread.start()
        logger.info("Hotkey listener started: %s (%s)", self._spec.key, self._mode)

    def stop(self) -> None:
        self._stop_event.set()
        self._cancel_auto_hold_timer()
        for device in self._devices:
            try:
                device.close()
            except OSError:
                pass
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=1.0)
        self._thread = None
        self._devices = []
        logger.info("Hotkey listener stopped")

    def _open_devices(self, skip_paths: frozenset = frozenset()) -> list[InputDevice]:
        devices = []
        for path in list_devices():
            if path in skip_paths:
                continue
            signature = _device_signature(path)
            if signature is not None and self._rejected.get(path) == signature:
                continue
            try:
                device = InputDevice(path)
            except OSError as exc:
                logger.debug("Skipping input device %s: %s", path, exc)
                if exc.errno in _LASTING_ERRNOS:
                    self._reject(path, signature)  # else (EMFILE, EIO, ...) retry
                continue
            try:
                usable = self._usable(device)
            except OSError as exc:
                logger.debug("Skipping input device %s: %s", path, exc)
                try:
                    device.close()
                except OSError:
                    pass
                if exc.errno in _LASTING_ERRNOS:
                    self._reject(path, signature)
                continue
            if usable:
                devices.append(device)
                self._rejected.pop(path, None)
                continue
            try:
                device.close()
            except OSError:
                pass
            self._reject(path, signature)
        return devices

    def _usable(self, device) -> bool:
        name = (device.name or "").strip().lower()
        if name in IGNORED_DEVICE_NAMES:
            return False
        key_codes = _key_capability_codes(device)
        if self._spec.trigger_code not in key_codes:
            return False
        return not self._spec.modifier_groups or any(
            group & key_codes for group in self._spec.modifier_groups
        )

    def _reject(self, path: str, signature) -> None:
        if signature is not None:
            self._rejected[path] = signature

    def _schedule_auto_hold_timer(self) -> None:
        self._cancel_auto_hold_timer()
        self._auto_hold_timer = threading.Timer(
            self._hold_threshold_s,
            self._auto_hold_elapsed,
        )
        self._auto_hold_timer.daemon = True
        self._auto_hold_timer.start()

    def _cancel_auto_hold_timer(self) -> None:
        if self._auto_hold_timer:
            self._auto_hold_timer.cancel()
            self._auto_hold_timer = None

    def _auto_hold_elapsed(self) -> None:
        callback = None
        with self._lock:
            self._auto_hold_timer = None
            if (
                self._mode == "auto"
                and self._combo_latched
                and self._auto_combo_pending
                and self._spec.is_pressed(self._pressed)
            ):
                self._auto_combo_pending = False
                self._hold_active = True
                callback = self._on_hold_start

        if callback:
            callback()

    def _rescan(self) -> None:
        """Adopt keyboards that appeared since the last scan."""
        known = frozenset(getattr(d, "path", None) for d in self._devices)
        for device in self._open_devices(skip_paths=known):
            if self._stop_event.is_set():
                device.close()
                return
            logger.info("Hotkey: now listening on %s", getattr(device, "name", "") or device)
            self._devices.append(device)

    def _drop(self, device) -> None:
        """Forget a device that went away (unplugged, suspended). Keys it was
        holding are let go — their key-ups will never come — and a gesture
        they were part of is cancelled, not completed: a vanishing keyboard
        is no tap and no deliberate release."""
        try:
            self._devices.remove(device)
        except ValueError:
            return
        try:
            device.close()
        except OSError:
            pass
        logger.info("Hotkey: keyboard %s went away", getattr(device, "name", "") or device)
        held = self._device_keys.pop(_device_key(device), set())
        still_held = set().union(*self._device_keys.values()) if self._device_keys else set()
        lost = held - still_held
        if lost and not self._stop_event.is_set():
            self._abandon_keys(lost)

    def _abandon_keys(self, codes: set) -> None:
        callback = None
        with self._lock:
            self._pressed -= codes
            if self._spec.is_pressed(self._pressed):
                return  # the chord is still held on another keyboard
            self._cancel_auto_hold_timer()
            if self._hold_active:
                callback = self._on_hold_cancel
            self._hold_active = False
            self._combo_latched = False
            self._auto_combo_pending = False
            self._gesture_aborted = False
        if callback:
            callback()

    def _note_key(self, device, code: int, value: int) -> None:
        keys = self._device_keys.setdefault(_device_key(device), set())
        if value == 1:
            keys.add(code)
        elif value == 0:
            keys.discard(code)

    def _run(self) -> None:
        last_scan = time.monotonic()
        while not self._stop_event.is_set():
            if time.monotonic() - last_scan >= RESCAN_INTERVAL_S:
                last_scan = time.monotonic()
                try:
                    self._rescan()
                except Exception:
                    logger.debug("Hotkey device rescan failed", exc_info=True)
            devices = list(self._devices)
            if not devices:
                self._stop_event.wait(0.5)
                continue
            try:
                readable, _, _ = select.select(devices, [], [], 0.5)
            except (OSError, ValueError):
                # A device closed or vanished mid-select: find the bad ones
                # one by one, drop them, and keep listening on the rest.
                dropped = False
                for device in devices:
                    try:
                        if device.fileno() < 0:
                            raise ValueError("closed")
                        select.select([device], [], [], 0)
                    except (OSError, ValueError):
                        self._drop(device)
                        dropped = True
                if not dropped:
                    self._stop_event.wait(0.5)  # transient: never spin on it
                continue

            for device in readable:
                try:
                    for event in device.read():
                        if event.type == e.EV_KEY:
                            self._note_key(device, event.code, event.value)
                            self._handle_key_event(event.code, event.value)
                except OSError:
                    self._drop(device)

    def _handle_key_event(self, code: int, value: int) -> None:
        if value == 2:
            return

        callback = None
        with self._lock:
            if value == 1:
                self._pressed.add(code)
            elif value == 0:
                self._pressed.discard(code)
            else:
                return

            # A bare-key gesture (e.g. hold rightctrl) must not swallow real
            # modifier use: any OTHER key pressed mid-gesture means the user
            # was doing Ctrl+<something>, so the gesture aborts — a pending
            # tap fizzles, an active hold cancels (not sends).
            if (
                getattr(self._spec, "is_bare", False)  # mac/win specs: chords only
                and value == 1
                and code != self._spec.trigger_code
                and (self._combo_latched or self._hold_active)
                and not self._gesture_aborted
            ):
                self._gesture_aborted = True
                self._auto_combo_pending = False
                self._cancel_auto_hold_timer()
                if self._hold_active:
                    self._hold_active = False
                    callback = self._on_hold_cancel

            combo_pressed = self._spec.is_pressed(self._pressed)

            if callback:
                pass  # abort already decided; fire it outside the lock
            elif self._gesture_aborted:
                # Swallow everything until the trigger is released.
                if self._spec.trigger_code not in self._pressed:
                    self._gesture_aborted = False
                    self._combo_latched = False
            elif self._mode == "toggle":
                if combo_pressed and not self._combo_latched:
                    self._combo_latched = True
                    callback = self._on_toggle
                elif not combo_pressed:
                    self._combo_latched = False
            elif self._mode == "hold":
                if combo_pressed and not self._hold_active:
                    self._hold_active = True
                    callback = self._on_hold_start
                elif not combo_pressed and self._hold_active:
                    self._hold_active = False
                    callback = self._on_hold_stop
            elif self._mode == "auto":
                if combo_pressed and not self._combo_latched:
                    self._combo_latched = True
                    self._auto_combo_pending = True
                    self._schedule_auto_hold_timer()
                elif not combo_pressed and self._combo_latched:
                    self._combo_latched = False
                    if self._auto_combo_pending:
                        self._auto_combo_pending = False
                        self._cancel_auto_hold_timer()
                        callback = self._on_toggle
                    elif self._hold_active:
                        self._hold_active = False
                        callback = self._on_hold_stop

        if callback:
            callback()
