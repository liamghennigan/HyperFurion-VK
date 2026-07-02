"""Windows backend tests — everything provable off Windows.

SendInput/hook system calls need real hardware (beta, flagged in the
README), but the logic layers run anywhere: factories, UTF-16 unit
expansion, VK combo parsing, the inherited state machine driven through
hook events, injected-event filtering, and — fully live — the loopback
TCP IPC transport Windows uses instead of Unix sockets.
"""

import sys
import threading
from unittest import mock

import pytest

from voice_keyboard import hotkey as hotkey_mod
from voice_keyboard import injector as injector_mod
from voice_keyboard.ipc import IPCClient, IPCServer, parse_endpoint, recv_all
from voice_keyboard.windows.hotkey import (
    LLKHF_INJECTED,
    WM_KEYDOWN,
    WM_KEYUP,
    WinHotkeyListener,
    WinHotkeySpec,
    vk_for_key,
)
from voice_keyboard.windows.injector import utf16_units


class TestPlatformFactories:
    def test_win32_picks_sendinput_backends(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(sys, "platform", "win32")
        from voice_keyboard.windows.injector import WinTextInjector

        assert isinstance(injector_mod.create_injector(), WinTextInjector)
        listener = hotkey_mod.create_hotkey_listener(
            {"key": "control+alt+v", "mode": "toggle"},
            on_toggle=lambda: None,
            on_hold_start=lambda: None,
            on_hold_stop=lambda: None,
        )
        assert isinstance(listener, WinHotkeyListener)

    def test_subclass_inherits_full_base_state(self) -> None:
        # Guards the super().__init__() refactor: every field the inherited
        # state machine touches must be initialized on the Windows listener.
        listener = WinHotkeyListener(
            {"key": "control+alt+v", "mode": "auto"},
            on_toggle=lambda: None,
            on_hold_start=lambda: None,
            on_hold_stop=lambda: None,
        )
        for field in (
            "_pressed", "_combo_latched", "_auto_combo_pending", "_hold_active",
            "_auto_hold_timer", "_stop_event", "_lock", "_mode", "_thread_id",
        ):
            assert hasattr(listener, field), field


class TestInputStructSize:
    def test_input_struct_matches_os_layout(self) -> None:
        # ctypes lays structs out identically to the C ABI on the host, so on
        # a 64-bit interpreter sizeof(_INPUT) must be 40 (the size SendInput's
        # cbSize demands) — the union's largest member (MOUSEINPUT) present.
        import ctypes

        from voice_keyboard.windows.injector import _INPUT

        pointer_bits = ctypes.sizeof(ctypes.c_void_p) * 8
        expected = 40 if pointer_bits == 64 else 28
        assert ctypes.sizeof(_INPUT) == expected


class TestUtf16Units:
    def test_ascii_is_one_unit_each(self) -> None:
        assert utf16_units("abc") == [ord("a"), ord("b"), ord("c")]

    def test_astral_chars_become_surrogate_pairs(self) -> None:
        units = utf16_units("🎙")
        assert len(units) == 2
        assert 0xD800 <= units[0] <= 0xDBFF
        assert 0xDC00 <= units[1] <= 0xDFFF


class TestWinHotkeySpec:
    def test_parses_the_default_combo(self) -> None:
        spec = WinHotkeySpec("control+alt+v")
        assert spec.trigger_code == ord("V")
        assert len(spec.modifier_groups) == 2

    def test_win_modifier_and_aliases(self) -> None:
        spec = WinHotkeySpec("win+shift+space")
        assert spec.trigger_code == 0x20

    def test_rejects_unknown_key(self) -> None:
        with pytest.raises(ValueError, match="unsupported hotkey key"):
            vk_for_key("f13")


class TestWinStateMachine:
    def _listener(self, mode: str = "toggle"):
        toggled = mock.Mock()
        listener = WinHotkeyListener(
            {"key": "control+alt+v", "mode": mode},
            on_toggle=toggled,
            on_hold_start=mock.Mock(),
            on_hold_stop=mock.Mock(),
        )
        return listener, toggled

    def test_combo_fires_toggle_once_despite_key_repeat(self) -> None:
        listener, toggled = self._listener()
        listener._on_hook_event(WM_KEYDOWN, 0xA2, 0)  # left ctrl
        listener._on_hook_event(WM_KEYDOWN, 0xA4, 0)  # left alt
        listener._on_hook_event(WM_KEYDOWN, ord("V"), 0)
        listener._on_hook_event(WM_KEYDOWN, ord("V"), 0)  # LL hooks repeat
        listener._on_hook_event(WM_KEYUP, ord("V"), 0)
        listener._on_hook_event(WM_KEYUP, 0xA4, 0)
        listener._on_hook_event(WM_KEYUP, 0xA2, 0)
        assert toggled.call_count == 1

    def test_injected_events_are_ignored(self) -> None:
        listener, toggled = self._listener()
        for vk in (0xA2, 0xA4, ord("V")):
            listener._on_hook_event(WM_KEYDOWN, vk, LLKHF_INJECTED)
        toggled.assert_not_called()


class TestTcpIPC:
    def test_parse_endpoint(self) -> None:
        assert parse_endpoint("tcp:127.0.0.1:48765") == ("inet", ("127.0.0.1", 48765))
        assert parse_endpoint("tcp:9999") == ("inet", ("127.0.0.1", 9999))
        assert parse_endpoint("/run/user/1000/vk.sock") == ("unix", "/run/user/1000/vk.sock")

    def test_roundtrip_over_loopback_tcp(self) -> None:
        # The exact transport Windows uses, exercised live on any OS.
        endpoint = "tcp:127.0.0.1:48899"
        server = IPCServer(endpoint)
        server.start()

        def serve_one() -> None:
            conn = server.accept()
            request = recv_all(conn)
            assert b"status" in request
            conn.sendall(b'{"status": "ok", "state": "idle"}')
            conn.close()

        thread = threading.Thread(target=serve_one, daemon=True)
        thread.start()
        response = IPCClient(endpoint, timeout=3.0).send_command("status")
        thread.join(timeout=3.0)
        server.stop()
        assert response == {"status": "ok", "state": "idle"}
