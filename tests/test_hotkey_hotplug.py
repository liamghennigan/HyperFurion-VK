"""Keyboard hot-plug for the evdev hotkey listener (Linux).

Devices used to be opened once at start: a keyboard plugged in later (or
re-enumerated after resume / a Bluetooth reconnect) never fired the
hotkey, and a listener that found no keyboard at start never ran at all.
"""

import os
import sys
import threading
import time

import pytest
from evdev import ecodes as e

from voice_keyboard import hotkey as hotkey_mod
from voice_keyboard.hotkey import HotkeyListener

# The evdev listener is Linux-only, and these fakes select() on pipes,
# which Windows' select() can't do.
pytestmark = pytest.mark.skipif(sys.platform == "win32", reason="evdev is Linux-only")


class FakeEvent:
    def __init__(self, code: int, value: int):
        self.type = e.EV_KEY
        self.code = code
        self.value = value


class FakeDevice:
    """An evdev InputDevice backed by a pipe, so select() works on it."""

    registry: dict = {}

    def __init__(self, path: str):
        info = FakeDevice.registry[path]
        self.path = path
        self.name = info["name"]
        self._caps = info["caps"]
        self._read_fd, self._write_fd = os.pipe()
        self._pending: list[FakeEvent] = []
        self._gone = False
        self.closed = False
        info["opened"].append(self)

    def capabilities(self):
        return {e.EV_KEY: list(self._caps)}

    def fileno(self) -> int:
        return -1 if self.closed else self._read_fd

    def press(self, *events: tuple[int, int]) -> None:
        self._pending.extend(FakeEvent(code, value) for code, value in events)
        os.write(self._write_fd, b"x")

    def unplug(self) -> None:
        self._gone = True
        os.write(self._write_fd, b"x")

    def read(self):
        os.read(self._read_fd, 4096)
        if self._gone:
            raise OSError(19, "No such device")
        events, self._pending = self._pending, []
        return events

    def close(self) -> None:
        if not self.closed:
            self.closed = True
            os.close(self._read_fd)
            os.close(self._write_fd)


KEYBOARD_CAPS = (e.KEY_LEFTCTRL, e.KEY_RIGHTCTRL, e.KEY_SPACE)


@pytest.fixture
def devices(monkeypatch: pytest.MonkeyPatch):
    FakeDevice.registry = {}
    monkeypatch.setattr(hotkey_mod, "InputDevice", FakeDevice)
    monkeypatch.setattr(hotkey_mod, "list_devices", lambda: list(FakeDevice.registry))
    monkeypatch.setattr(hotkey_mod, "RESCAN_INTERVAL_S", 0.05)

    def plug(path: str, name: str = "keyboard", caps=KEYBOARD_CAPS) -> None:
        FakeDevice.registry[path] = {"name": name, "caps": caps, "opened": []}

    def opened(path: str) -> FakeDevice:
        for _ in range(100):
            if FakeDevice.registry[path]["opened"]:
                return FakeDevice.registry[path]["opened"][-1]
            time.sleep(0.02)
        raise AssertionError(f"{path} was never opened")

    yield plug, opened
    for info in FakeDevice.registry.values():
        for device in info["opened"]:
            device.close()


def _listener(toggled: threading.Event) -> HotkeyListener:
    return HotkeyListener(
        {"enabled": True, "key": "control+space", "mode": "toggle"},
        on_toggle=toggled.set,
        on_hold_start=lambda: None,
        on_hold_stop=lambda: None,
    )


CHORD = ((e.KEY_LEFTCTRL, 1), (e.KEY_SPACE, 1), (e.KEY_SPACE, 0), (e.KEY_LEFTCTRL, 0))


class TestHotplug:
    def test_keyboard_plugged_in_after_start(self, devices) -> None:
        plug, opened = devices
        toggled = threading.Event()
        listener = _listener(toggled)
        listener.start()  # no keyboard yet: it must keep watching
        try:
            assert listener._thread is not None and listener._thread.is_alive()
            plug("/dev/input/event7")
            opened("/dev/input/event7").press(*CHORD)
            assert toggled.wait(2), "hotkey on a hot-plugged keyboard never fired"
        finally:
            listener.stop()

    def test_unplug_and_replug_keeps_working(self, devices) -> None:
        plug, opened = devices
        plug("/dev/input/event3")
        toggled = threading.Event()
        listener = _listener(toggled)
        listener.start()
        try:
            first = opened("/dev/input/event3")
            first.unplug()
            for _ in range(100):
                if first not in listener._devices:
                    break
                time.sleep(0.02)
            assert first not in listener._devices
            assert listener._thread.is_alive(), "a vanished keyboard killed the listener"
            del FakeDevice.registry["/dev/input/event3"]
            plug("/dev/input/event4")  # it comes back under a new node
            opened("/dev/input/event4").press(*CHORD)
            assert toggled.wait(2)
        finally:
            listener.stop()

    def test_non_keyboards_and_our_own_device_are_ignored(self, devices) -> None:
        plug, _ = devices
        plug("/dev/input/event1", name="Logitech mouse", caps=(e.BTN_LEFT,))
        plug("/dev/input/event2", name="voice-keyboard")
        listener = _listener(threading.Event())
        listener.start()
        try:
            time.sleep(0.2)
            assert listener._devices == []
        finally:
            listener.stop()

    def test_a_keyboard_vanishing_mid_hold_releases_its_keys(self, devices) -> None:
        plug, opened = devices
        plug("/dev/input/event6")
        started, stopped = threading.Event(), threading.Event()
        listener = HotkeyListener(
            {"enabled": True, "key": "control+space", "mode": "hold"},
            on_toggle=lambda: None, on_hold_start=started.set, on_hold_stop=stopped.set,
        )
        listener.start()
        try:
            keyboard = opened("/dev/input/event6")
            keyboard.press((e.KEY_LEFTCTRL, 1), (e.KEY_SPACE, 1))
            assert started.wait(2)
            keyboard.unplug()  # the key-ups will never arrive
            assert stopped.wait(2), "the hold outlived the keyboard"
            assert not listener._pressed
        finally:
            listener.stop()

    def test_non_keyboards_are_not_reopened_every_rescan(self, devices, monkeypatch) -> None:
        plug, _ = devices
        signatures = {"/dev/input/event1": (13, 1, 1)}
        monkeypatch.setattr(hotkey_mod, "_device_signature", lambda path: signatures.get(path))
        plug("/dev/input/event1", name="Logitech mouse", caps=(e.BTN_LEFT,))
        listener = _listener(threading.Event())
        listener.start()
        try:
            time.sleep(0.4)  # ~8 rescans
            assert len(FakeDevice.registry["/dev/input/event1"]["opened"]) == 1
            # The node is recreated (a new device under the same number):
            # it is looked at again.
            FakeDevice.registry["/dev/input/event1"]["caps"] = KEYBOARD_CAPS
            signatures["/dev/input/event1"] = (13, 2, 2)
            for _ in range(100):
                if listener._devices:
                    break
                time.sleep(0.02)
            assert [d.path for d in listener._devices] == ["/dev/input/event1"]
        finally:
            listener.stop()

    def test_a_broken_device_is_dropped_without_spinning(self, devices) -> None:
        plug, opened = devices
        plug("/dev/input/event8")
        listener = _listener(threading.Event())
        listener.start()
        try:
            broken = opened("/dev/input/event8")
            for _ in range(100):
                if broken in listener._devices:
                    break
                time.sleep(0.02)
            # Its descriptor dies behind the listener's back: select() on
            # the set fails with EBADF from now on.
            os.close(broken._read_fd)
            os.close(broken._write_fd)
            broken.closed = True
            broken.fileno = lambda: 1023  # stale: neither closed nor valid
            for _ in range(100):
                if broken not in listener._devices:
                    break
                time.sleep(0.02)
            assert broken not in listener._devices
            assert listener._thread.is_alive()
        finally:
            listener.stop()

    def test_a_keyboard_vanishing_mid_tap_is_no_tap(self, devices) -> None:
        # Auto mode: chord down, then the keyboard goes before the hold
        # threshold. Its "release" must not read as a quick tap (toggle).
        plug, opened = devices
        plug("/dev/input/event9")
        toggled = threading.Event()
        listener = HotkeyListener(
            {"enabled": True, "key": "control+space", "mode": "auto", "hold_threshold_ms": 5000},
            on_toggle=toggled.set, on_hold_start=lambda: None, on_hold_stop=lambda: None,
        )
        listener.start()
        try:
            keyboard = opened("/dev/input/event9")
            keyboard.press((e.KEY_LEFTCTRL, 1), (e.KEY_SPACE, 1))
            for _ in range(100):
                if listener._combo_latched:
                    break
                time.sleep(0.02)
            keyboard.unplug()
            for _ in range(100):
                if keyboard not in listener._devices:
                    break
                time.sleep(0.02)
            time.sleep(0.1)
            assert not toggled.is_set()
            assert not listener._pressed and not listener._combo_latched
        finally:
            listener.stop()

    def test_nothing_fires_once_stopping(self) -> None:
        fired = []
        listener = HotkeyListener(
            {"enabled": True, "key": "control+space", "mode": "hold"},
            on_toggle=lambda: fired.append("toggle"), on_hold_start=lambda: None,
            on_hold_stop=lambda: fired.append("stop"),
        )

        class Gone:
            path = "/dev/input/event5"

            def close(self):
                pass

        gone = Gone()
        listener._devices = [gone]
        listener._handle_key_event(e.KEY_LEFTCTRL, 1)
        listener._handle_key_event(e.KEY_SPACE, 1)
        listener._device_keys = {gone.path: {e.KEY_LEFTCTRL, e.KEY_SPACE}}
        listener._stop_event.set()  # stop() is closing every device
        listener._drop(gone)
        assert fired == []

    def test_a_transient_select_error_waits_instead_of_spinning(self, devices, monkeypatch) -> None:
        import select as real_select

        plug, opened = devices
        plug("/dev/input/event10")
        calls = []

        class FlakySelect:
            @staticmethod
            def select(r, w, x, timeout=None):
                calls.append(len(r))
                if len(r) > 0 and timeout:
                    raise OSError(4, "Interrupted system call")  # every full wait
                return real_select.select(r, w, x, timeout)

        listener = _listener(threading.Event())
        listener.start()
        try:
            opened("/dev/input/event10")
            for _ in range(100):
                if listener._devices:
                    break
                time.sleep(0.02)
            monkeypatch.setattr(hotkey_mod, "select", FlakySelect)
            time.sleep(0.6)
            monkeypatch.undo()
            assert len(calls) < 20, f"{len(calls)} select() calls in 0.6 s: spinning"
            assert listener._devices, "a healthy keyboard was dropped"
        finally:
            listener.stop()

    def test_only_lasting_open_errors_are_remembered(self, devices, monkeypatch) -> None:
        import errno

        plug, _ = devices
        signatures = {"/dev/input/event11": (13, 1, 1), "/dev/input/event12": (13, 2, 1)}
        monkeypatch.setattr(hotkey_mod, "_device_signature", lambda path: signatures.get(path))
        attempts = {"/dev/input/event11": 0, "/dev/input/event12": 0}
        errors = {"/dev/input/event11": errno.EMFILE, "/dev/input/event12": errno.EACCES}

        def failing(path):
            attempts[path] += 1
            raise OSError(errors[path], "nope")

        monkeypatch.setattr(hotkey_mod, "InputDevice", failing)
        plug("/dev/input/event11")
        plug("/dev/input/event12")
        listener = _listener(threading.Event())
        listener.start()
        try:
            time.sleep(1.3)  # with no keyboard yet the loop rescans every 0.5 s
        finally:
            listener.stop()
        assert attempts["/dev/input/event11"] >= 2  # out of descriptors: retried
        assert attempts["/dev/input/event12"] == 1  # no permission: remembered

    def test_stop_closes_everything(self, devices) -> None:
        plug, opened = devices
        plug("/dev/input/event5")
        listener = _listener(threading.Event())
        listener.start()
        device = opened("/dev/input/event5")
        thread = listener._thread
        listener.stop()
        assert not thread.is_alive()
        assert device.closed
