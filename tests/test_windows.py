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
import time
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

    def test_roundtrip_over_loopback_tcp(self, monkeypatch, tmp_path) -> None:
        # The exact transport Windows uses, exercised live on any OS.
        monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path))
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

    @pytest.fixture
    def beta(self, monkeypatch, tmp_path):
        """An early beta's leftovers: settings under ~/.config, history
        under ~/.local/state (where its daemon kept them)."""
        monkeypatch.setattr(sys, "platform", "win32")
        for name in ("XDG_CONFIG_HOME", "XDG_STATE_HOME"):
            monkeypatch.delenv(name, raising=False)
        monkeypatch.setenv("APPDATA", str(tmp_path / "Roaming"))
        monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "Local"))
        monkeypatch.setattr(paths.Path, "home", lambda: tmp_path / "home")
        legacy_cfg = tmp_path / "home" / ".config" / "voice-keyboard"
        legacy_cfg.mkdir(parents=True)
        (legacy_cfg / "config.toml").write_text('[providers.xai]\napi_key = "xai-real-key"\n')
        legacy_state = tmp_path / "home" / ".local" / "state" / "voice-keyboard"
        (legacy_state / "memory").mkdir(parents=True)
        (legacy_state / "history.jsonl").write_text("old\n")
        (legacy_state / "dictionary.json").write_text("{}")
        (legacy_state / "memory" / "kai.json").write_text("{}")
        return tmp_path

    def test_beta_leftovers_are_copied_once_and_never_overwrite(self, beta) -> None:
        new_state = beta / "Local" / "voice-keyboard"
        new_state.mkdir(parents=True)
        (new_state / "history.jsonl").write_text("new\n")  # already started over

        moved, failed = paths.migrate_windows_beta()
        assert len(moved) == 2 and failed == []
        assert "xai-real-key" in (beta / "Roaming" / "voice-keyboard" / "config.toml").read_text()
        assert (new_state / "history.jsonl").read_text() == "new\n"
        assert (new_state / "dictionary.json").exists()
        assert (new_state / "memory" / "kai.json").exists()
        assert (beta / "home" / ".config" / "voice-keyboard" / "config.toml").exists()
        assert not list(new_state.glob("*.migrating"))
        assert paths.config_dir() == beta / "Roaming" / "voice-keyboard"

        (new_state / "dictionary.json").unlink()  # the user cleared it
        assert paths.migrate_windows_beta() == ([], [])
        assert not (new_state / "dictionary.json").exists()

    def test_the_beta_installers_starter_config_gives_way(self, beta) -> None:
        # The beta's installer copied the example (placeholder keys) into
        # %APPDATA%, but its daemon only ever read ~/.config.
        target = beta / "Roaming" / "voice-keyboard" / "config.toml"
        target.parent.mkdir(parents=True)
        target.write_text('[xai]\napi_key = "xai-your-api-key-here"\n')
        moved, failed = paths.migrate_windows_beta()
        assert failed == [] and "beta-starter" in moved[0]
        assert "xai-real-key" in target.read_text()
        assert "your-api-key-here" in target.with_name("config.toml.beta-starter").read_text()

    def test_real_settings_are_never_replaced(self, beta) -> None:
        target = beta / "Roaming" / "voice-keyboard" / "config.toml"
        target.parent.mkdir(parents=True)
        target.write_text('[providers.xai]\napi_key = "xai-newer-key"\n')
        paths.migrate_windows_beta()
        assert "xai-newer-key" in target.read_text()
        assert not target.with_name("config.toml.beta-starter").exists()

    def test_a_failed_copy_is_retried_next_time(self, beta, monkeypatch) -> None:
        real_copy = paths._copy_file

        def locked(src, dest):
            if dest.name == "dictionary.json":
                raise PermissionError(13, "in use by another process")
            real_copy(src, dest)

        monkeypatch.setattr(paths, "_copy_file", locked)
        moved, failed = paths.migrate_windows_beta()
        assert len(failed) == 1 and "dictionary.json" in failed[0]
        new_state = beta / "Local" / "voice-keyboard"
        assert not (new_state / "dictionary.json").exists()
        assert not (new_state / ".beta-migrated").exists()
        monkeypatch.setattr(paths, "_copy_file", real_copy)
        moved, failed = paths.migrate_windows_beta()
        assert failed == [] and (new_state / "dictionary.json").exists()
        assert (new_state / ".beta-migrated").exists()

    def test_beta_migration_is_windows_only(self, monkeypatch, tmp_path) -> None:
        monkeypatch.setattr(sys, "platform", "linux")
        assert paths.migrate_windows_beta() == ([], [])

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
    @pytest.fixture(autouse=True)
    def private_state(self, monkeypatch, tmp_path):
        monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))

    def test_second_daemon_on_the_port_fails_clearly(self) -> None:
        endpoint = "tcp:127.0.0.1:48911"
        first = IPCServer(endpoint)
        first.start()
        try:
            with pytest.raises(RuntimeError, match="already listening"):
                IPCServer(endpoint).start()
        finally:
            first.stop()

    def test_other_bind_errors_say_what_happened(self, monkeypatch) -> None:
        import errno
        import socket as socket_mod

        def refuse(self, address):
            raise OSError(errno.EACCES, "forbidden by its access permissions")

        monkeypatch.setattr(socket_mod.socket, "bind", refuse)
        with pytest.raises(RuntimeError, match="Could not open the command channel.*forbidden"):
            IPCServer("tcp:127.0.0.1:0").start()


class TestPerSessionPort:
    """Windows' default endpoint is tcp:127.0.0.1:0: every daemon binds a
    free port and publishes it with its token (review finding: one fixed
    port meant a second signed-in user could never start)."""

    def _serve_one(self, server, reply: bytes) -> threading.Thread:
        def serve() -> None:
            conn = server.accept()
            recv_all(conn)
            conn.sendall(reply)
            conn.close()

        thread = threading.Thread(target=serve, daemon=True)
        thread.start()
        return thread

    def test_two_users_side_by_side(self, monkeypatch, tmp_path) -> None:
        servers = {}
        for user in ("alice", "bob"):
            monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / user))
            server = IPCServer("tcp:127.0.0.1:0")
            server.start()
            servers[user] = server
        try:
            ports = {u: int(s.endpoint.rsplit(":", 1)[1]) for u, s in servers.items()}
            assert ports["alice"] != ports["bob"] and 0 not in ports.values()
            for user, server in servers.items():
                monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / user))
                thread = self._serve_one(server, b'{"status": "ok", "who": "%s"}' % user.encode())
                reply = IPCClient("tcp:127.0.0.1:0", timeout=3.0).send_command("status")
                thread.join(timeout=3.0)
                assert reply == {"status": "ok", "who": user}
        finally:
            for user, server in servers.items():
                monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / user))
                server.stop()
        assert not (tmp_path / "alice" / "voice-keyboard" / "ipc-token").exists()

    def test_no_daemon_means_connection_refused(self, monkeypatch, tmp_path) -> None:
        monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path))
        with pytest.raises(ConnectionRefusedError):
            IPCClient("tcp:127.0.0.1:0", timeout=1.0).send_command("status")

    def test_token_and_port_come_from_one_read(self, monkeypatch, tmp_path) -> None:
        # A daemon restarting between two reads must not get its old token
        # paired with its new port.
        from voice_keyboard import ipc

        reads = []
        monkeypatch.setattr(ipc, "read_ipc_endpoint", lambda: reads.append(1) or ("tok", 1))
        with pytest.raises(OSError):
            IPCClient("tcp:127.0.0.1:0", timeout=0.5).send_command("status")
        assert len(reads) == 1

    def test_a_failed_publish_never_leaves_the_port_bound(self, monkeypatch, tmp_path) -> None:
        from voice_keyboard import ipc

        monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path))

        def refuse(path, text):
            raise PermissionError(13, "in use")

        monkeypatch.setattr(ipc, "_write_private", refuse)
        server = IPCServer("tcp:127.0.0.1:0")
        with pytest.raises(RuntimeError, match="Could not publish"):
            server.start()
        assert server._sock is None and server.required_token is None

    def test_a_pre_2_2_daemon_is_still_reachable(self, monkeypatch, tmp_path) -> None:
        # Mid-upgrade the old daemon still runs: a bare token for the fixed
        # port 48765, next to the config (2.2 previews)...
        from voice_keyboard import ipc

        monkeypatch.setattr(sys, "platform", "win32")
        for name in ("XDG_STATE_HOME", "XDG_CONFIG_HOME"):
            monkeypatch.delenv(name, raising=False)
        monkeypatch.setenv("APPDATA", str(tmp_path / "Roaming"))
        monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "Local"))
        monkeypatch.setattr(paths.Path, "home", lambda: tmp_path / "home")
        monkeypatch.setattr(ipc, "_session_id", lambda: 1)
        appdata = tmp_path / "Roaming" / "voice-keyboard"
        appdata.mkdir(parents=True)
        (appdata / "config.toml").write_text("")
        (appdata / "ipc-token").write_text("preview")
        assert ipc.read_ipc_endpoint() == ("preview", ipc.LEGACY_PORT)
        # ...or under ~/.config (the early beta), even with the config now
        # in %APPDATA%.
        (appdata / "ipc-token").unlink()
        beta = tmp_path / "home" / ".config" / "voice-keyboard"
        beta.mkdir(parents=True)
        (beta / "ipc-token").write_text("beta")
        assert ipc.read_ipc_endpoint() == ("beta", ipc.LEGACY_PORT)
        # A running 2.2 daemon's own file always wins.
        local = tmp_path / "Local" / "voice-keyboard"
        local.mkdir(parents=True)
        (local / "ipc-token-1").write_text('{"token": "new", "port": 50123}')
        assert ipc.read_ipc_endpoint() == ("new", 50123)

    def test_each_windows_session_has_its_own_token_file(self, monkeypatch, tmp_path) -> None:
        # The same person signed in twice (Remote Desktop) runs a daemon in
        # each session; each session's CLI must reach its own.
        from voice_keyboard import ipc

        monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path))
        monkeypatch.setattr(ipc, "_session_id", lambda: 2)
        assert ipc._token_path().name == "ipc-token-2"
        monkeypatch.setattr(ipc, "_session_id", lambda: None)
        assert ipc._token_path().name == "ipc-token"

    def test_stopping_leaves_a_newer_daemons_token_alone(self, monkeypatch, tmp_path) -> None:
        from voice_keyboard import ipc

        monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path))
        old = IPCServer("tcp:127.0.0.1:0")
        old.start()
        new = IPCServer("tcp:127.0.0.1:0")
        new.start()  # publishes over the old one's file
        old.stop()
        assert ipc.read_ipc_endpoint() == (new.required_token, int(new.endpoint.rsplit(":", 1)[1]))
        new.stop()
        assert ipc.read_ipc_endpoint() == ("", 0)


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
        # The stale V must not complete the chord on the Alt press...
        cb["on_toggle"].assert_not_called()
        listener._key_is_down = lambda vk: vk in (self.LCTRL, self.LALT)
        # ...only a real V press does.
        assert listener._on_hook_event(WM_KEYDOWN, self.V, 0) is True
        cb["on_toggle"].assert_called_once()

    def test_altgr_cannot_hide_a_stale_left_ctrl(self) -> None:
        # Ctrl+Alt+Del: Ctrl and Alt are released on the secure desktop.
        # Back in the app, AltGr+V ('@'): Windows reports Left Ctrl down
        # once AltGr's fake Ctrl passes, so the stale one must go at it.
        listener, cb = _win_listener()
        held = {self.LCTRL, self.LALT}
        listener._key_is_down = lambda vk: vk in held
        listener._on_hook_event(WM_KEYDOWN, self.LCTRL, 0, 0x1D)
        listener._on_hook_event(WM_KEYDOWN, self.LALT, 0, 0x38)
        held.clear()  # the unseen releases
        listener._on_hook_event(WM_KEYDOWN, self.LCTRL, 0, self.FAKE_CTRL_SCAN)
        held.update({self.LCTRL, self.RALT})  # AltGr is held: Windows says so
        listener._on_hook_event(WM_SYSKEYDOWN, self.RALT, 0, 0x38)
        assert listener._on_hook_event(WM_KEYDOWN, self.V, 0, 0x2F) is False
        cb["on_toggle"].assert_not_called()

    def test_a_missed_release_cant_hide_behind_our_own_injection(self) -> None:
        # Hold-to-talk typed text (our injector released Ctrl+Alt), then the
        # user's real releases were missed (the hook was dropped). Once they
        # type anything else, plain V must be plain V again.
        listener, cb = _win_listener(mode="hold")
        held = set()
        listener._key_is_down = lambda vk: vk in held
        for vk in (self.LCTRL, self.LALT):
            held.add(vk)
            listener._on_hook_event(WM_KEYDOWN, vk, 0)
        listener._on_hook_event(WM_KEYDOWN, self.V, 0)
        for vk in (self.LCTRL, self.LALT):
            held.discard(vk)
            listener._on_hook_event(WM_KEYUP, vk, LLKHF_INJECTED)
        listener._on_hook_event(WM_KEYUP, self.V, 0)
        cb["on_hold_stop"].assert_called_once()
        # (Ctrl and Alt let go unseen.) The user types "a", then "v".
        listener._on_hook_event(WM_KEYDOWN, ord("A"), 0)
        listener._on_hook_event(WM_KEYUP, ord("A"), 0)
        assert listener._on_hook_event(WM_KEYDOWN, self.V, 0) is False
        cb["on_hold_start"].assert_called_once()  # only the first, real hold

    def test_only_fresh_real_presses_count_as_the_user_typing(self) -> None:
        from voice_keyboard.windows import hotkey as win_hotkey

        listener, _ = _win_listener()
        listener._on_hook_event(WM_KEYDOWN, ord("A"), 0)
        first = win_hotkey.last_user_keydown()
        assert first > 0
        time.sleep(0.01)
        listener._on_hook_event(WM_KEYDOWN, ord("A"), 0)  # auto-repeat
        listener._on_hook_event(WM_KEYDOWN, ord("B"), LLKHF_INJECTED)  # ours
        assert win_hotkey.last_user_keydown() == first
        listener._on_hook_event(WM_KEYUP, ord("A"), 0)
        listener._on_hook_event(WM_KEYDOWN, ord("C"), 0)
        assert win_hotkey.last_user_keydown() > first

    def test_another_key_during_a_bare_hold_cancels_it(self) -> None:
        # Kai's Right Ctrl held, released by our injector, then the user hits
        # C (Right Ctrl+C was real modifier use): cancel, never send.
        listener, cb = _win_listener(key="rightctrl", mode="hold", allow_bare=True)
        RCTRL = 0xA3
        held = {RCTRL}
        listener._key_is_down = lambda vk: vk in held
        listener._on_hook_event(WM_KEYDOWN, RCTRL, 0)
        held.discard(RCTRL)
        listener._on_hook_event(WM_KEYUP, RCTRL, LLKHF_INJECTED)
        listener._on_hook_event(WM_KEYDOWN, ord("C"), 0)
        cb["on_hold_cancel"].assert_called_once()
        cb["on_hold_stop"].assert_not_called()

    def test_a_held_bare_modifier_trigger_survives_its_auto_repeat(self) -> None:
        # Kai's Right Ctrl held to talk; our injector releases it while
        # typing; the physical key keeps auto-repeating. Inside the hook
        # Windows calls it up (our release) until each repeat passes.
        listener, cb = _win_listener(key="rightctrl", mode="hold", allow_bare=True)
        RCTRL = 0xA3
        held = {RCTRL}
        listener._key_is_down = lambda vk: vk in held
        listener._on_hook_event(WM_KEYDOWN, RCTRL, 0)
        cb["on_hold_start"].assert_called_once()
        held.discard(RCTRL)
        listener._on_hook_event(WM_KEYUP, RCTRL, LLKHF_INJECTED)
        for _ in range(3):
            listener._on_hook_event(WM_KEYDOWN, RCTRL, 0)
            held.add(RCTRL)  # the repeat passed: Windows says down again
        cb["on_hold_stop"].assert_not_called()
        cb["on_hold_start"].assert_called_once()
