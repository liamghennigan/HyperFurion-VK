from voice_keyboard.hotkey import HotkeyListener
from evdev import ecodes as e


def _listener(mode: str, events: list[str]) -> HotkeyListener:
    return HotkeyListener(
        {
            "enabled": True,
            "key": "control+space",
            "mode": mode,
            "hold_threshold_ms": 10_000,
        },
        on_toggle=lambda: events.append("toggle"),
        on_hold_start=lambda: events.append("start"),
        on_hold_stop=lambda: events.append("stop"),
    )


class TestHotkeySpec:
    def test_punctuation_keys_resolve(self) -> None:
        # The assistant hotkey is control+alt+. — evdev names it KEY_DOT,
        # so a plain KEY_"." lookup fails without the alias.
        from voice_keyboard.hotkey import HotkeySpec

        assert HotkeySpec("control+alt+.").trigger_code == e.KEY_DOT
        assert HotkeySpec("control+alt+period").trigger_code == e.KEY_DOT
        assert HotkeySpec("ctrl+alt+/").trigger_code == e.KEY_SLASH
        assert HotkeySpec("control+alt+minus").trigger_code == e.KEY_MINUS

    def test_assistant_combo_fires(self) -> None:
        events: list[str] = []
        listener = HotkeyListener(
            {"enabled": True, "key": "control+alt+.", "mode": "toggle",
             "hold_threshold_ms": 10_000},
            on_toggle=lambda: events.append("toggle"),
            on_hold_start=lambda: None,
            on_hold_stop=lambda: None,
        )
        listener._handle_key_event(e.KEY_LEFTCTRL, 1)
        listener._handle_key_event(e.KEY_LEFTALT, 1)
        listener._handle_key_event(e.KEY_DOT, 1)
        assert events == ["toggle"]


class TestHotkeyListener:
    def test_toggle_fires_once_per_combo_press(self) -> None:
        events: list[str] = []
        listener = _listener("toggle", events)

        listener._handle_key_event(e.KEY_LEFTCTRL, 1)
        listener._handle_key_event(e.KEY_SPACE, 1)
        listener._handle_key_event(e.KEY_SPACE, 2)
        listener._handle_key_event(e.KEY_SPACE, 0)
        listener._handle_key_event(e.KEY_SPACE, 1)

        assert events == ["toggle", "toggle"]

    def test_hold_fires_start_and_stop(self) -> None:
        events: list[str] = []
        listener = _listener("hold", events)

        listener._handle_key_event(e.KEY_LEFTCTRL, 1)
        listener._handle_key_event(e.KEY_SPACE, 1)
        listener._handle_key_event(e.KEY_SPACE, 2)
        listener._handle_key_event(e.KEY_LEFTCTRL, 0)

        assert events == ["start", "stop"]

    def test_hold_can_be_restarted_after_release(self) -> None:
        events: list[str] = []
        listener = _listener("hold", events)

        listener._handle_key_event(e.KEY_LEFTCTRL, 1)
        listener._handle_key_event(e.KEY_SPACE, 1)
        listener._handle_key_event(e.KEY_SPACE, 0)
        listener._handle_key_event(e.KEY_SPACE, 1)

        assert events == ["start", "stop", "start"]

    def test_auto_quick_tap_toggles_on_release(self) -> None:
        events: list[str] = []
        listener = _listener("auto", events)

        listener._handle_key_event(e.KEY_LEFTCTRL, 1)
        listener._handle_key_event(e.KEY_SPACE, 1)
        assert events == []

        listener._handle_key_event(e.KEY_SPACE, 0)
        listener._handle_key_event(e.KEY_SPACE, 1)
        listener._handle_key_event(e.KEY_SPACE, 0)

        assert events == ["toggle", "toggle"]
        listener.stop()

    def test_auto_hold_starts_after_threshold_and_stops_on_release(self) -> None:
        events: list[str] = []
        listener = _listener("auto", events)

        listener._handle_key_event(e.KEY_LEFTCTRL, 1)
        listener._handle_key_event(e.KEY_SPACE, 1)
        listener._auto_hold_elapsed()
        listener._handle_key_event(e.KEY_SPACE, 0)

        assert events == ["start", "stop"]
        listener.stop()


def _bare_listener(events: list[str]) -> HotkeyListener:
    """The terminal-safe summon: bare rightctrl, auto mode."""
    return HotkeyListener(
        {
            "enabled": True,
            "key": "rightctrl",
            "mode": "auto",
            "hold_threshold_ms": 10_000,
            "allow_bare": True,
        },
        on_toggle=lambda: events.append("toggle"),
        on_hold_start=lambda: events.append("start"),
        on_hold_stop=lambda: events.append("stop"),
        on_hold_cancel=lambda: events.append("cancel"),
    )


class TestBareModifierGesture:
    def test_bare_spec_requires_opt_in(self) -> None:
        from voice_keyboard.hotkey import HotkeySpec
        import pytest

        assert HotkeySpec("rightctrl", allow_bare=True).is_bare
        with pytest.raises(ValueError):
            HotkeySpec("rightctrl")  # dictation stays chord-only

    def test_bare_tap_and_hold_fire(self) -> None:
        events: list[str] = []
        listener = _bare_listener(events)
        # Quick tap → toggle on release.
        listener._handle_key_event(e.KEY_RIGHTCTRL, 1)
        listener._handle_key_event(e.KEY_RIGHTCTRL, 0)
        # Hold past threshold → start, release → stop.
        listener._handle_key_event(e.KEY_RIGHTCTRL, 1)
        listener._auto_hold_elapsed()
        listener._handle_key_event(e.KEY_RIGHTCTRL, 0)
        assert events == ["toggle", "start", "stop"]
        listener.stop()

    def test_other_key_aborts_pending_tap(self) -> None:
        # RightCtrl+C is real modifier use, not a summon: the gesture
        # fizzles and nothing fires.
        events: list[str] = []
        listener = _bare_listener(events)
        listener._handle_key_event(e.KEY_RIGHTCTRL, 1)
        listener._handle_key_event(e.KEY_C, 1)
        listener._handle_key_event(e.KEY_C, 0)
        listener._handle_key_event(e.KEY_RIGHTCTRL, 0)
        assert events == []
        # And the gesture re-arms cleanly afterwards.
        listener._handle_key_event(e.KEY_RIGHTCTRL, 1)
        listener._handle_key_event(e.KEY_RIGHTCTRL, 0)
        assert events == ["toggle"]
        listener.stop()

    def test_other_key_cancels_active_hold(self) -> None:
        # A slow Ctrl+<key> after the hold engaged must CANCEL (discard),
        # never send the accidental capture.
        events: list[str] = []
        listener = _bare_listener(events)
        listener._handle_key_event(e.KEY_RIGHTCTRL, 1)
        listener._auto_hold_elapsed()
        listener._handle_key_event(e.KEY_C, 1)
        listener._handle_key_event(e.KEY_RIGHTCTRL, 0)
        assert events == ["start", "cancel"]
        listener.stop()

    def test_chord_specs_ignore_extra_keys_unchanged(self) -> None:
        # The abort rule is bare-only: chords keep their subset-match
        # behavior (extra keys never break a chord).
        events: list[str] = []
        listener = _listener("toggle", events)
        listener._handle_key_event(e.KEY_LEFTCTRL, 1)
        listener._handle_key_event(e.KEY_A, 1)
        listener._handle_key_event(e.KEY_SPACE, 1)
        assert events == ["toggle"]
        listener.stop()
