import logging
import select
import sys
import threading
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
):
    """Platform factory: evdev on Linux, a Quartz event tap on macOS, a
    low-level keyboard hook on Windows."""
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
        )
    return HotkeyListener(
        config,
        on_toggle=on_toggle,
        on_hold_start=on_hold_start,
        on_hold_stop=on_hold_stop,
    )

IGNORED_DEVICE_NAMES = {"voice-keyboard"}


class HotkeySpec:
    def __init__(self, key: str):
        parts = [part.strip().lower() for part in key.split("+") if part.strip()]
        if len(parts) < 2:
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
    ):
        self._enabled = bool(config.get("enabled", True))
        self._mode = str(config.get("mode", "auto")).lower()
        self._hold_threshold_s = (
            float(config.get("hold_threshold_ms", self.DEFAULT_HOLD_THRESHOLD_MS)) / 1000.0
        )
        self._spec = HotkeySpec(str(config.get("key", "control+alt+v")))
        self._on_toggle = on_toggle
        self._on_hold_start = on_hold_start
        self._on_hold_stop = on_hold_stop
        self._pressed: set[int] = set()
        self._combo_latched = False
        self._auto_combo_pending = False
        self._hold_active = False
        self._auto_hold_timer: Optional[threading.Timer] = None
        self._devices: list[InputDevice] = []
        self._thread: Optional[threading.Thread] = None
        self._stop_event = threading.Event()
        self._lock = threading.Lock()

    def start(self) -> None:
        if not self._enabled or self._mode == "disabled":
            logger.info("Hotkey listener disabled")
            return

        self._devices = self._open_devices()
        if not self._devices:
            logger.warning("No readable keyboard devices found for hotkey %s", self._spec.key)
            return

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

    def _open_devices(self) -> list[InputDevice]:
        devices = []
        for path in list_devices():
            try:
                device = InputDevice(path)
                name = (device.name or "").strip().lower()
                if name in IGNORED_DEVICE_NAMES:
                    device.close()
                    continue
                key_codes = _key_capability_codes(device)
                if self._spec.trigger_code not in key_codes:
                    device.close()
                    continue
                if not any(group & key_codes for group in self._spec.modifier_groups):
                    device.close()
                    continue
                devices.append(device)
            except (OSError, PermissionError) as exc:
                logger.debug("Skipping input device %s: %s", path, exc)
        return devices

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

    def _run(self) -> None:
        devices = list(self._devices)
        while devices and not self._stop_event.is_set():
            try:
                readable, _, _ = select.select(devices, [], [], 0.5)
            except (OSError, ValueError):
                break

            for device in readable:
                try:
                    for event in device.read():
                        if event.type == e.EV_KEY:
                            self._handle_key_event(event.code, event.value)
                except OSError:
                    try:
                        devices.remove(device)
                    except ValueError:
                        pass

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

            combo_pressed = self._spec.is_pressed(self._pressed)
            if self._mode == "toggle":
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
