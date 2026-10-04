"""Windows backend tests — everything provable off Windows.

SendInput/hook system calls need Windows, but the logic layers run
anywhere: factories, UTF-16 unit expansion, the exact SendInput event
streams the injector builds (through a fake user32), VK combo parsing,
the inherited state machine driven through hook events, trigger-key
swallowing, injected-event filtering, the Windows config/state paths,
and — fully live — the loopback TCP IPC transport Windows uses instead
of Unix sockets.
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
            vk_for_key("f99")


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


# ---------------------------------------------------------------- injector

from voice_keyboard.windows import injector as win_injector  # noqa: E402
from voice_keyboard.windows.injector import (  # noqa: E402
    KEYEVENTF_EXTENDEDKEY,
    KEYEVENTF_KEYUP,
    KEYEVENTF_UNICODE,
    VK_BACK,
    VK_LCONTROL,
    VK_LMENU,
    VK_LSHIFT,
    VK_MASK,
    VK_RETURN,
    VK_TAB,
    WinTextInjector,
)


class FakeUser32:
    """Records SendInput batches as (vk, scan, flags) tuples."""

    def __init__(self, held=(), layout=None):
        self.batches: list[list[tuple[int, int, int]]] = []
        self.held = set(held)
        self.layout = layout or {}

    def SendInput(self, count, array, size):
        self.batches.append(
            [(int(e.ki.wVk), int(e.ki.wScan), int(e.ki.dwFlags)) for e in array[:count]]
        )
        return count

    def GetAsyncKeyState(self, vk):
        return -32768 if vk in self.held else 0  # high bit set = down

    def MapVirtualKeyW(self, vk, kind):
        return 0x100 + vk  # a recognizable fake scan code

    def VkKeyScanW(self, ch):
        return self.layout.get(ch, -1)

    @property
    def events(self):
        return [e for batch in self.batches for e in batch]


def _injector(user32: FakeUser32) -> WinTextInjector:
    inj = WinTextInjector()
    inj._user32 = user32
    return inj


def _down_up(events):
    """Pair a flat event list into keystrokes, asserting each down is
    followed by its own up."""
    strokes = []
    for down, up in zip(events[::2], events[1::2]):
        assert down[0] == up[0] and down[1] == up[1]
        assert not down[2] & KEYEVENTF_KEYUP and up[2] & KEYEVENTF_KEYUP
        strokes.append(down)
    return strokes


class TestWinInjectorText:
    def test_requires_start(self) -> None:
        with pytest.raises(RuntimeError, match="not started"):
            WinTextInjector().type_text("x")

    def test_plain_text_is_unicode_packets(self) -> None:
        user32 = FakeUser32()
        _injector(user32).type_text("hé")
        assert _down_up(user32.events) == [
            (0, ord("h"), KEYEVENTF_UNICODE),
            (0, ord("é"), KEYEVENTF_UNICODE),
        ]

    def test_newline_and_tab_are_real_keys(self) -> None:
        user32 = FakeUser32()
        _injector(user32).type_text("a\r\nb\tc\rd")
        strokes = _down_up(user32.events)
        assert [s[0] for s in strokes] == [0, VK_RETURN, 0, VK_TAB, 0, VK_RETURN, 0]
        assert strokes[1][1] == 0x100 + VK_RETURN  # scan code filled in

    def test_suppress_enter_never_emits_return(self) -> None:
        user32 = FakeUser32()
        inj = _injector(user32)
        inj.suppress_enter = True
        inj.type_text("git status\nrm -rf /\r\n")
        vks = [e[0] for e in user32.events]
        assert VK_RETURN not in vks
        units = [e[1] for e in _down_up(user32.events)]
        assert "".join(map(chr, units)) == "git status rm -rf / "

    def test_astral_char_is_a_surrogate_pair(self) -> None:
        user32 = FakeUser32()
        _injector(user32).type_text("🎙")
        strokes = _down_up(user32.events)
        assert len(strokes) == 2
        assert all(flags == KEYEVENTF_UNICODE for _, _, flags in strokes)

    def test_long_text_is_batched(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(win_injector.time, "sleep", lambda s: None)
        user32 = FakeUser32()
        _injector(user32).type_text("x" * 40)
        assert [len(b) for b in user32.batches] == [32, 32, 16]

    def test_delete_chars_is_backspace(self) -> None:
        user32 = FakeUser32()
        _injector(user32).delete_chars(3)
        assert [s[0] for s in _down_up(user32.events)] == [VK_BACK] * 3

    def test_held_hotkey_modifiers_are_released_first(self) -> None:
        # Hold-to-talk: Ctrl+Alt are still physically down while molten
        # typing runs — Ctrl+Backspace would delete a whole word.
        user32 = FakeUser32(held={VK_LCONTROL, VK_LMENU})
        _injector(user32).delete_chars(1)
        events = user32.events
        assert events[0] == (VK_MASK, 0, 0)  # inert key: no Alt-menu pop
        assert events[1] == (VK_MASK, 0, KEYEVENTF_KEYUP)
        releases = {e[0] for e in events[2:4]}
        assert releases == {VK_LCONTROL, VK_LMENU}
        assert all(e[2] & KEYEVENTF_KEYUP for e in events[2:4])
        assert [s[0] for s in _down_up(events[4:])] == [VK_BACK]

    def test_ctrl_only_needs_no_mask(self) -> None:
        user32 = FakeUser32(held={VK_LCONTROL})
        _injector(user32).type_text("a")
        assert user32.events[0] == (VK_LCONTROL, 0x100 + VK_LCONTROL, KEYEVENTF_KEYUP)


class TestWinInjectorCombos:
    def test_chord_wraps_modifiers(self) -> None:
        user32 = FakeUser32()
        _injector(user32).press_combo(["ctrl", "t"])
        assert [(vk, flags) for vk, _, flags in user32.events] == [
            (VK_LCONTROL, 0),
            (ord("T"), 0),
            (ord("T"), KEYEVENTF_KEYUP),
            (VK_LCONTROL, KEYEVENTF_KEYUP),
        ]

    def test_navigation_keys_are_extended(self) -> None:
        user32 = FakeUser32()
        _injector(user32).press_combo(["ctrl", "shift", "Left"])
        left = [e for e in user32.events if e[0] == 0x25]
        assert left and all(e[2] & KEYEVENTF_EXTENDEDKEY for e in left)

    def test_enter_is_allowed_in_a_combo(self) -> None:
        user32 = FakeUser32()
        inj = _injector(user32)
        inj.suppress_enter = True  # guards dictated TEXT only
        inj.press_combo(["Return"])
        assert user32.events[0][0] == VK_RETURN

    def test_shifted_punctuation_uses_the_layout(self) -> None:
        user32 = FakeUser32(layout={"?": 0x1BF})  # shift + VK_OEM_2 (US)
        _injector(user32).press_combo(["ctrl", "?"])
        downs = [e[0] for e in user32.events if not e[2] & KEYEVENTF_KEYUP]
        assert downs == [VK_LCONTROL, VK_LSHIFT, 0xBF]

    def test_unknown_key_raises(self) -> None:
        with pytest.raises(ValueError, match="unknown key"):
            _injector(FakeUser32()).press_combo(["ctrl", "nope"])

    def test_function_keys(self) -> None:
        user32 = FakeUser32()
        _injector(user32).press_combo(["F5"])
        assert user32.events[0][0] == 0x74


# ------------------------------------------------------------------ hotkey

from voice_keyboard.windows.hotkey import WM_SYSKEYDOWN  # noqa: E402

VK_LCTRL, VK_RCTRL, VK_LALT, VK_LWIN = 0xA2, 0xA3, 0xA4, 0x5B


def _win_listener(key="control+alt+v", mode="toggle", **cfg):
    callbacks = {
        "on_toggle": mock.Mock(),
        "on_hold_start": mock.Mock(),
        "on_hold_stop": mock.Mock(),
        "on_hold_cancel": mock.Mock(),
    }
    listener = WinHotkeyListener({"key": key, "mode": mode, **cfg}, **callbacks)
    listener._send_mask = mock.Mock()
    return listener, callbacks


class TestWinHotkeyBindings:
    def test_bare_modifier_needs_opt_in(self) -> None:
        with pytest.raises(ValueError, match="at least one modifier"):
            WinHotkeySpec("rightctrl")
        spec = WinHotkeySpec("rightctrl", allow_bare=True)
        assert spec.is_bare and spec.trigger_code == VK_RCTRL

    def test_punctuation_and_function_keys(self) -> None:
        assert WinHotkeySpec("control+alt+.").trigger_code == 0xBE
        assert WinHotkeySpec("ctrl+alt+period").trigger_code == 0xBE
        assert WinHotkeySpec("ctrl+f9").trigger_code == 0x78
        assert WinHotkeySpec("ctrl+alt+r").trigger_code == ord("R")

    def test_codes_cover_modifiers(self) -> None:
        spec = WinHotkeySpec("control+alt+v")
        assert {VK_LCTRL, VK_RCTRL, VK_LALT, ord("V")} <= spec.codes

    def test_factory_passes_hold_cancel(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(sys, "platform", "win32")
        cancel = mock.Mock()
        listener = hotkey_mod.create_hotkey_listener(
            {"key": "rightctrl", "mode": "auto", "allow_bare": True},
            on_toggle=mock.Mock(),
            on_hold_start=mock.Mock(),
            on_hold_stop=mock.Mock(),
            on_hold_cancel=cancel,
        )
        assert listener._on_hold_cancel is cancel

    def test_parse_binding_uses_the_windows_table(self, monkeypatch) -> None:
        monkeypatch.setattr(sys, "platform", "win32")
        assert isinstance(hotkey_mod.parse_binding("rightctrl", allow_bare=True), WinHotkeySpec)
        with pytest.raises(ValueError):
            hotkey_mod.parse_binding("control+alt+nope")


class TestWinHotkeySwallow:
    def test_chord_trigger_is_swallowed_with_repeats_and_release(self) -> None:
        listener, cb = _win_listener()
        assert listener._on_hook_event(WM_KEYDOWN, VK_LCTRL, 0) is False
        assert listener._on_hook_event(WM_KEYDOWN, VK_LALT, 0) is False
        assert listener._on_hook_event(WM_KEYDOWN, ord("V"), 0) is True
        assert listener._on_hook_event(WM_KEYDOWN, ord("V"), 0) is True  # repeat
        assert listener._on_hook_event(WM_KEYUP, ord("V"), 0) is True
        assert listener._on_hook_event(WM_KEYUP, VK_LALT, 0) is False
        assert listener._on_hook_event(WM_KEYUP, VK_LCTRL, 0) is False
        cb["on_toggle"].assert_called_once()

    def test_plain_letter_passes_through(self) -> None:
        listener, cb = _win_listener()
        assert listener._on_hook_event(WM_KEYDOWN, ord("V"), 0) is False
        assert listener._on_hook_event(WM_KEYUP, ord("V"), 0) is False
        cb["on_toggle"].assert_not_called()

    def test_alt_chord_sends_the_menu_mask(self) -> None:
        listener, _ = _win_listener()
        listener._on_hook_event(WM_KEYDOWN, VK_LCTRL, 0)
        listener._on_hook_event(WM_SYSKEYDOWN, VK_LALT, 0)
        listener._on_hook_event(WM_SYSKEYDOWN, ord("V"), 0)
        for _ in range(100):
            if listener._send_mask.called:
                break
            threading.Event().wait(0.01)
        listener._send_mask.assert_called_once()

    def test_win_chord_masks_the_start_menu(self) -> None:
        listener, cb = _win_listener(key="win+shift+space")
        listener._on_hook_event(WM_KEYDOWN, VK_LWIN, 0)
        listener._on_hook_event(WM_KEYDOWN, 0xA0, 0)
        assert listener._on_hook_event(WM_KEYDOWN, 0x20, 0) is True
        for _ in range(100):
            if listener._send_mask.called:
                break
            threading.Event().wait(0.01)
        listener._send_mask.assert_called_once()
        cb["on_toggle"].assert_called_once()

    def test_bare_modifier_is_never_swallowed(self) -> None:
        listener, _ = _win_listener(key="rightctrl", mode="hold", allow_bare=True)
        assert listener._on_hook_event(WM_KEYDOWN, VK_RCTRL, 0) is False
        assert listener._on_hook_event(WM_KEYUP, VK_RCTRL, 0) is False

    def test_injected_events_are_not_swallowed(self) -> None:
        listener, cb = _win_listener()
        for vk in (VK_LCTRL, VK_LALT, ord("V")):
            assert listener._on_hook_event(WM_KEYDOWN, vk, LLKHF_INJECTED) is False
        cb["on_toggle"].assert_not_called()


class TestWinBareKeyGesture:
    def test_hold_then_other_key_cancels(self) -> None:
        # Right Ctrl held to talk to Kai, then C pressed: the user was doing
        # Ctrl+C — the capture is cancelled, never sent.
        listener, cb = _win_listener(key="rightctrl", mode="hold", allow_bare=True)
        listener._on_hook_event(WM_KEYDOWN, VK_RCTRL, 0)
        cb["on_hold_start"].assert_called_once()
        listener._on_hook_event(WM_KEYDOWN, ord("C"), 0)
        cb["on_hold_cancel"].assert_called_once()
        listener._on_hook_event(WM_KEYUP, ord("C"), 0)
        listener._on_hook_event(WM_KEYUP, VK_RCTRL, 0)
        cb["on_hold_stop"].assert_not_called()

    def test_hold_and_release_sends(self) -> None:
        listener, cb = _win_listener(key="rightctrl", mode="hold", allow_bare=True)
        listener._on_hook_event(WM_KEYDOWN, VK_RCTRL, 0)
        listener._on_hook_event(WM_KEYUP, VK_RCTRL, 0)
        cb["on_hold_start"].assert_called_once()
        cb["on_hold_stop"].assert_called_once()


# ------------------------------------------------------------------- paths

from voice_keyboard import paths  # noqa: E402


class TestWindowsPaths:
    def test_config_in_appdata(self, monkeypatch, tmp_path) -> None:
        monkeypatch.setattr(sys, "platform", "win32")
        monkeypatch.delenv("XDG_CONFIG_HOME", raising=False)
        monkeypatch.setenv("APPDATA", str(tmp_path / "Roaming"))
        monkeypatch.setattr(paths.Path, "home", lambda: tmp_path / "home")
        assert paths.config_dir() == tmp_path / "Roaming" / "voice-keyboard"

    def test_legacy_dotconfig_still_honored(self, monkeypatch, tmp_path) -> None:
        monkeypatch.setattr(sys, "platform", "win32")
        monkeypatch.delenv("XDG_CONFIG_HOME", raising=False)
        monkeypatch.setenv("APPDATA", str(tmp_path / "Roaming"))
        monkeypatch.setattr(paths.Path, "home", lambda: tmp_path / "home")
        legacy = tmp_path / "home" / ".config" / "voice-keyboard"
        legacy.mkdir(parents=True)
        (legacy / "config.toml").write_text("")
        assert paths.config_dir() == legacy
        preferred = tmp_path / "Roaming" / "voice-keyboard"
        preferred.mkdir(parents=True)
        (preferred / "config.toml").write_text("")
        assert paths.config_dir() == preferred

    def test_state_and_logs_in_localappdata(self, monkeypatch, tmp_path) -> None:
        monkeypatch.setattr(sys, "platform", "win32")
        monkeypatch.delenv("XDG_STATE_HOME", raising=False)
        monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "Local"))
        assert paths.state_dir() == tmp_path / "Local" / "voice-keyboard"
        assert paths.log_dir() == tmp_path / "Local" / "voice-keyboard" / "logs"

    def test_xdg_wins_everywhere(self, monkeypatch, tmp_path) -> None:
        monkeypatch.setattr(sys, "platform", "win32")
        monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "x"))
        assert paths.config_dir() == tmp_path / "x" / "voice-keyboard"

    def test_config_module_follows(self, monkeypatch, tmp_path) -> None:
        from voice_keyboard import config, history, ipc

        monkeypatch.setattr(sys, "platform", "win32")
        monkeypatch.delenv("XDG_CONFIG_HOME", raising=False)
        monkeypatch.delenv("XDG_STATE_HOME", raising=False)
        monkeypatch.setenv("APPDATA", str(tmp_path / "Roaming"))
        monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "Local"))
        assert config._config_dir() == ipc._config_dir() == paths.config_dir()
        assert history.history_path().parent == tmp_path / "Local" / "voice-keyboard"


class TestIpcSingleListener:
    def test_second_daemon_on_the_port_fails_clearly(self) -> None:
        endpoint = "tcp:127.0.0.1:48911"
        first = IPCServer(endpoint)
        first.start()
        try:
            with pytest.raises(RuntimeError, match="already listening"):
                IPCServer(endpoint).start()
        finally:
            first.stop()


class TestHookKeyStateQuirks:
    """AltGr and missed key releases (review findings: AltGr was eaten as
    Ctrl+Alt; releases on the secure desktop left Ctrl+Alt stuck)."""

    LCTRL, LALT, RALT, V = 0xA2, 0xA4, 0xA5, ord("V")
    FAKE_CTRL_SCAN = 0x21D  # AltGr's synthetic Left Ctrl

    def test_altgr_is_not_ctrl_alt(self) -> None:
        listener, cb = _win_listener()
        # AltGr+V ('@' on Hungarian/Czech/... layouts): fake LCtrl + RAlt + V.
        assert listener._on_hook_event(WM_KEYDOWN, self.LCTRL, 0, self.FAKE_CTRL_SCAN) is False
        assert listener._on_hook_event(WM_SYSKEYDOWN, self.RALT, 0, 0x38) is False
        assert listener._on_hook_event(WM_KEYDOWN, self.V, 0, 0x2F) is False  # reaches the app
        listener._on_hook_event(WM_KEYUP, self.V, 0, 0x2F)
        listener._on_hook_event(WM_KEYUP, self.RALT, 0, 0x38)
        listener._on_hook_event(WM_KEYUP, self.LCTRL, 0, self.FAKE_CTRL_SCAN)
        cb["on_toggle"].assert_not_called()
        # A real Ctrl+Alt+V still works.
        listener._on_hook_event(WM_KEYDOWN, self.LCTRL, 0, 0x1D)
        listener._on_hook_event(WM_KEYDOWN, self.LALT, 0, 0x38)
        assert listener._on_hook_event(WM_KEYDOWN, self.V, 0, 0x2F) is True
        cb["on_toggle"].assert_called_once()

    def test_missed_releases_dont_leave_modifiers_stuck(self) -> None:
        listener, cb = _win_listener()
        listener._on_hook_event(WM_KEYDOWN, self.LCTRL, 0, 0x1D)
        listener._on_hook_event(WM_KEYDOWN, self.LALT, 0, 0x38)
        # Ctrl+Alt+Del: the releases happen on the Winlogon desktop, unseen.
        listener._key_is_down = lambda vk: False
        assert listener._on_hook_event(WM_KEYDOWN, self.V, 0, 0x2F) is False
        listener._on_hook_event(WM_KEYUP, self.V, 0, 0x2F)
        cb["on_toggle"].assert_not_called()
        assert not listener._pressed

    def test_hold_to_talk_survives_our_own_modifier_release(self) -> None:
        # Molten typing releases the held Ctrl+Alt logically (injected
        # key-ups) and the swallowed V never updates Windows' key state: the
        # resync must not mistake either for the user letting go.
        listener, cb = _win_listener(mode="hold")
        held = set()
        listener._key_is_down = lambda vk: vk in held
        for vk in (self.LCTRL, self.LALT):
            held.add(vk)
            listener._on_hook_event(WM_KEYDOWN, vk, 0)
        assert listener._on_hook_event(WM_KEYDOWN, self.V, 0) is True
        cb["on_hold_start"].assert_called_once()
        for vk in (self.LCTRL, self.LALT):  # the injector's releases
            held.discard(vk)
            listener._on_hook_event(WM_KEYUP, vk, LLKHF_INJECTED)
        for _ in range(3):  # auto-repeat of the held trigger
            assert listener._on_hook_event(WM_KEYDOWN, self.V, 0) is True
        cb["on_hold_stop"].assert_not_called()
        assert listener._on_hook_event(WM_KEYUP, self.V, 0) is True
        cb["on_hold_stop"].assert_called_once()

    def test_unswallowed_trigger_with_a_missed_release_is_a_fresh_press(self) -> None:
        listener, cb = _win_listener()
        listener._on_hook_event(WM_KEYDOWN, self.V, 0)  # plain v, not swallowed
        listener._key_is_down = lambda vk: False  # its release went unseen
        listener._on_hook_event(WM_KEYDOWN, self.LCTRL, 0)
        listener._on_hook_event(WM_KEYDOWN, self.LALT, 0)
        listener._key_is_down = lambda vk: vk in (self.LCTRL, self.LALT)
        assert listener._on_hook_event(WM_KEYDOWN, self.V, 0) is True
        cb["on_toggle"].assert_called_once()
