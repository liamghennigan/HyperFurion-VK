import threading
from unittest import mock

import pytest

from voice_keyboard.injector import SHIFT_MAP, CHAR_TO_KEY, TextInjector


class TestTextInjector:
    @pytest.fixture
    def injector(self) -> TextInjector:
        return TextInjector()

    def test_start_creates_uinput(self, injector: TextInjector) -> None:
        with mock.patch("voice_keyboard.injector.UInput") as mock_uinput:
            injector.start()
            mock_uinput.assert_called_once()
            assert injector._ui is not None

    def test_stop_closes_uinput(self, injector: TextInjector) -> None:
        with mock.patch("voice_keyboard.injector.UInput") as mock_uinput:
            injector.start()
            injector.stop()
            mock_uinput.return_value.close.assert_called_once()
            assert injector._ui is None

    def test_lowercase_a(self, injector: TextInjector) -> None:
        with mock.patch("voice_keyboard.injector.UInput") as mock_uinput:
            injector.start()
            injector.type_text("a")
            key_writes = [
                call.args
                for call in mock_uinput.return_value.write.call_args_list
            ]
            press = [c for c in key_writes if len(c) == 3 and c[1] == CHAR_TO_KEY["a"]]
            assert press[0][2] == 1
            assert press[-1][2] == 0

    def test_uppercase_uses_shift(self, injector: TextInjector) -> None:
        from evdev import ecodes as e
        with mock.patch("voice_keyboard.injector.UInput") as mock_uinput:
            injector.start()
            injector.type_text("A")
            key_writes = [
                call.args
                for call in mock_uinput.return_value.write.call_args_list
            ]
            first = key_writes[0]
            assert first[1] == e.KEY_LEFTSHIFT
            assert first[2] == 1

    def test_shifted_symbol(self, injector: TextInjector) -> None:
        with mock.patch("voice_keyboard.injector.UInput") as mock_uinput:
            injector.start()
            injector.type_text("!")
            key_writes = [
                call.args
                for call in mock_uinput.return_value.write.call_args_list
            ]
            first = key_writes[0]
            assert first[2] == 1

    def test_unsupported_character_logs_warning(self, injector: TextInjector, caplog: pytest.LogCaptureFixture) -> None:
        with mock.patch("voice_keyboard.injector.UInput"):
            injector.start()
            injector.type_text("é")
            assert "Unsupported character" in caplog.text

    def test_type_text_does_not_block_main_thread(self, injector: TextInjector) -> None:
        with mock.patch("voice_keyboard.injector.UInput") as mock_uinput:
            injector.start()
            t = threading.Thread(target=injector.type_text, args=("hi",))
            t.start()
            t.join(timeout=1)
            assert not t.is_alive()
            assert mock_uinput.return_value.write.called

    def test_press_key_without_start_raises(self, injector: TextInjector) -> None:
        with pytest.raises(RuntimeError, match="Injector not started"):
            injector._press_key(CHAR_TO_KEY["a"])

    def test_all_shift_map_keys_have_base_key(self) -> None:
        for shifted, base in SHIFT_MAP.items():
            assert base in CHAR_TO_KEY, f"Base key for {shifted!r} not found"
