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
