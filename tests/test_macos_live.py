"""Live macOS checks: the real pyobjc and Carbon calls, on a real Mac.

The rest of the macOS suite drives the logic through fakes; this module
proves the fakes match reality — the call shapes of Quartz, the
Accessibility API, Carbon's UCKeyTranslate and AVFoundation — on the CI
macOS runner and on a dev Mac. It never posts an event, creates an event
tap or shows a permission prompt: events are built and read back, AX values
are round-tripped, and the probes run as whatever this process is allowed.
"""

import sys
import threading
from unittest import mock

import pytest

pytestmark = pytest.mark.skipif(sys.platform != "darwin", reason="needs macOS")


@pytest.fixture
def quartz():
    import Quartz

    return Quartz


class RecordingQuartz:
    """The real Quartz module, except that CGEventPost records the event
    instead of posting it."""

    def __init__(self, real):
        self._real = real
        self.posted = []

    def __getattr__(self, name):
        return getattr(self._real, name)

    def CGEventPost(self, _tap, event):  # noqa: N802 - Quartz's name
        self.posted.append(event)


class TestInjectorAgainstRealQuartz:
    def _started(self, quartz):
        from voice_keyboard.macos.injector import MacTextInjector
        from voice_keyboard.macos.keylayout import LayoutKeymap

        recorder = RecordingQuartz(quartz)
        inj = MacTextInjector(layout=LayoutKeymap())
        with mock.patch.dict(sys.modules, {"Quartz": recorder}), \
                mock.patch("voice_keyboard.macos.permissions.notify_missing"):
            inj.start()
        return inj, recorder

    def test_text_events_carry_unicode_flags_and_our_tag(self, quartz) -> None:
        from voice_keyboard.macos.hotkey import INJECTED_EVENT_TAG

        inj, recorder = self._started(quartz)
        inj.type_text("héllo 🎙")
        assert recorder.posted, "nothing was built"
        down = recorder.posted[0]
        assert quartz.CGEventGetFlags(down) & 0x00FF0000 == 0  # no modifier leaks in
        assert quartz.CGEventGetIntegerValueField(down, quartz.kCGEventSourceUserData) == INJECTED_EVENT_TAG
        length, text = quartz.CGEventKeyboardGetUnicodeString(down, 20, None, None)
        assert text[:length] == "héllo 🎙"

    def test_return_is_a_real_key_and_shift_return_has_shift(self, quartz) -> None:
        inj, recorder = self._started(quartz)
        inj.shift_newline = True
        inj.type_text("\n")
        down = recorder.posted[0]
        assert quartz.CGEventGetIntegerValueField(down, quartz.kCGKeyboardEventKeycode) == 36
        assert quartz.CGEventGetFlags(down) & quartz.kCGEventFlagMaskShift

    def test_chords_carry_the_modifier_flags(self, quartz) -> None:
        inj, recorder = self._started(quartz)
        inj.press_combo(["cmd", "left"])
        key_down = recorder.posted[1]
        assert quartz.CGEventGetIntegerValueField(key_down, quartz.kCGKeyboardEventKeycode) == 123
        assert quartz.CGEventGetFlags(key_down) & quartz.kCGEventFlagMaskCommand


class TestAccessibilityAdapter:
    @pytest.fixture
    def ax(self):
        from voice_keyboard.macos.ax import AXBackend

        backend = AXBackend.create()
        assert backend is not None, "pyobjc-framework-ApplicationServices is missing"
        return backend

    def test_value_round_trips(self, ax, quartz) -> None:
        import ApplicationServices as hi

        assert ax.text_range(ax.make_range(7, 3)) == (7, 3)
        rect = hi.AXValueCreate(hi.kAXValueCGRectType, quartz.CGRectMake(10, 20, 30, 40))
        assert ax.rect(rect) == (10.0, 20.0, 30.0, 40.0)
        point = hi.AXValueCreate(hi.kAXValueCGPointType, quartz.CGPointMake(5, 6))
        assert ax.point(point) == (5.0, 6.0)
        size = hi.AXValueCreate(hi.kAXValueCGSizeType, quartz.CGSizeMake(7, 8))
        assert ax.size(size) == (7.0, 8.0)
        assert ax.rect(None) is None and ax.text_range(point) is None

    def test_queries_answer_or_decline_without_raising(self, ax) -> None:
        system = ax.system_wide()
        ax.set_timeout(system, 0.25)
        focused = ax.attribute(system, "AXFocusedApplication")
        assert isinstance(ax.last_error, int)
        if focused is not None:  # only with Accessibility permission
            assert isinstance(ax.pid(focused), int)
        assert ax.pid(None) is None and ax.attribute(None, "AXRole") is None

    def test_the_probe_runs(self) -> None:
        from voice_keyboard import focusprobe
        from voice_keyboard.macos.focus import probe_focus_info, probe_selection

        info = probe_focus_info(focusprobe.FocusInfo)
        assert info is None or isinstance(info, focusprobe.FocusInfo)
        found = focusprobe.probe_focus()  # falls back to the window list
        assert found is None or isinstance(found.app, str)
        read = probe_selection(100)
        assert read is None or isinstance(read, tuple)

    def test_app_info(self, ax) -> None:
        import os

        name, bundle = ax.app_info(os.getpid())
        assert isinstance(name, str) and isinstance(bundle, str)


class TestKeyboardLayout:
    def test_the_current_layout_maps_letters(self) -> None:
        from voice_keyboard.macos import keylayout

        assert threading.current_thread() is threading.main_thread()
        translate = keylayout._carbon_translator()
        if translate is None:
            pytest.skip("no Unicode keyboard layout data in this session")
        found = keylayout.build_char_map(translate)
        assert set("abcdefghijklmnopqrstuvwxyz") <= set(found)
        if translate(0) == "a":  # a US/ABC runner: the ANSI table agrees
            from voice_keyboard.macos.hotkey import MAC_KEYCODES

            assert all(found[c] == MAC_KEYCODES[c] for c in "abcdefghijklmnopqrstuvwxyz")


class TestPermissions:
    def test_every_check_runs(self) -> None:
        from voice_keyboard.macos import permissions

        assert permissions.accessibility_granted() in (True, False)
        assert permissions.input_monitoring_granted() in (True, False)
        assert permissions.post_event_granted() in (True, False)
        assert permissions.microphone_status() in (
            "authorized", "denied", "restricted", "not determined", None)
        names = [check.name for check in permissions.checks()]
        assert names[:3] == ["accessibility", "input monitoring", "microphone"]

    def test_doctor_runs(self, tmp_path) -> None:
        from voice_keyboard import doctor

        labels = [f.label for f in doctor.run(tmp_path / "config.toml", None)]
        assert {"typing", "hotkey", "mic access", "clipboard", "focus"} <= set(labels)
