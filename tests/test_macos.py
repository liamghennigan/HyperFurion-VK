"""macOS backend tests — everything provable without a Mac.

The Quartz calls themselves need real hardware (flagged in the README as
beta), but the logic layers are exercised here: platform factories,
Unicode chunking, the keycode/flag tables, and the inherited combo state
machine driven through the flagsChanged translation.
"""

import sys
from types import ModuleType
from unittest import mock

import pytest

from voice_keyboard import hotkey as hotkey_mod
from voice_keyboard import injector as injector_mod
from voice_keyboard.macos.hotkey import (
    FLAG_MASKS,
    MAC_KEYCODES,
    MacHotkeyListener,
    MacHotkeySpec,
)
from voice_keyboard.macos.injector import CHUNK_UTF16_UNITS, MacTextInjector, chunk_text


class TestPlatformFactories:
    def test_linux_default(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(sys, "platform", "linux")
        assert isinstance(injector_mod.create_injector(), injector_mod.TextInjector)

    def test_darwin_picks_quartz_backends(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(sys, "platform", "darwin")
        assert isinstance(injector_mod.create_injector(), MacTextInjector)
        listener = hotkey_mod.create_hotkey_listener(
            {"key": "control+alt+v", "mode": "toggle"},
            on_toggle=lambda: None,
            on_hold_start=lambda: None,
            on_hold_stop=lambda: None,
        )
        assert isinstance(listener, MacHotkeyListener)


class TestUnicodeChunking:
    def test_short_text_is_one_chunk(self) -> None:
        assert chunk_text("hello") == ["hello"]

    def test_long_text_respects_the_utf16_budget(self) -> None:
        text = "a" * 100
        chunks = chunk_text(text)
        assert "".join(chunks) == text
        assert all(len(c) <= CHUNK_UTF16_UNITS for c in chunks)

    def test_astral_chars_count_double_and_never_split(self) -> None:
        text = "🎙" * 30  # each is two UTF-16 units
        chunks = chunk_text(text)
        assert "".join(chunks) == text
        for chunk in chunks:
            units = len(chunk.encode("utf-16-le")) // 2
            assert units <= CHUNK_UTF16_UNITS
            assert len(chunk) * 2 == units  # no torn surrogate pairs

    def test_type_text_posts_key_down_and_up_per_chunk(self) -> None:
        quartz = ModuleType("Quartz")
        quartz.kCGHIDEventTap = 0
        quartz.CGEventCreateKeyboardEvent = mock.Mock(side_effect=lambda *_: object())
        quartz.CGEventKeyboardSetUnicodeString = mock.Mock()
        quartz.CGEventPost = mock.Mock()
        inj = MacTextInjector()
        with mock.patch.dict(sys.modules, {"Quartz": quartz}):
            inj.start()
            inj.type_text("héllo wörld — dictated, not typed")
        assert quartz.CGEventPost.call_count == 2 * len(
            chunk_text("héllo wörld — dictated, not typed")
        )

    def test_type_text_requires_start(self) -> None:
        with pytest.raises(RuntimeError, match="not started"):
            MacTextInjector().type_text("hi")

    def test_suppress_enter_never_types_a_newline(self) -> None:
        # The intent channel / Kai's terminal route: a typed "\n" is Return
        # in Terminal, so it must never reach the event stream.
        quartz = ModuleType("Quartz")
        quartz.kCGHIDEventTap = 0
        quartz.CGEventCreateKeyboardEvent = mock.Mock(side_effect=lambda *_: object())
        quartz.CGEventKeyboardSetUnicodeString = mock.Mock()
        quartz.CGEventPost = mock.Mock()
        inj = MacTextInjector()
        with mock.patch.dict(sys.modules, {"Quartz": quartz}):
            inj.start()
            inj.suppress_enter = True
            inj.type_text("git status\nrm -rf /\r\n")
        typed = "".join(c.args[2] for c in quartz.CGEventKeyboardSetUnicodeString.call_args_list)
        assert "\n" not in typed and "\r" not in typed
        assert "git status rm -rf /" in typed

    def test_daemon_no_enter_path_reaches_the_mac_injector(self) -> None:
        # The daemon only arms the guard on injectors that expose it.
        assert hasattr(MacTextInjector(), "suppress_enter")


class TestChords:
    def _quartz(self):
        quartz = ModuleType("Quartz")
        quartz.kCGHIDEventTap = 0
        quartz.posted = []
        quartz.CGEventCreateKeyboardEvent = mock.Mock(side_effect=lambda _s, code, down: {"code": code, "down": down})
        quartz.CGEventSetFlags = mock.Mock(side_effect=lambda ev, flags: ev.__setitem__("flags", flags))
        quartz.CGEventPost = mock.Mock(side_effect=lambda _tap, ev: quartz.posted.append(dict(ev)))
        return quartz

    def test_chord_presses_modifiers_then_the_key_with_their_flags(self) -> None:
        quartz = self._quartz()
        inj = MacTextInjector()
        with mock.patch.dict(sys.modules, {"Quartz": quartz}):
            inj.start()
            inj.press_combo(["shift", "alt", "left"])
        shift, alt, left = 56, 58, 123
        assert [(e["code"], e["down"]) for e in quartz.posted] == [
            (shift, True), (alt, True), (left, True), (left, False), (alt, False), (shift, False),
        ]
        assert quartz.posted[2]["flags"] == 0x20000 | 0x80000  # the key carries both modifiers
        assert quartz.posted[0]["flags"] == 0x20000
        assert quartz.posted[-1]["flags"] == 0x20000  # shift releases last, still flagged

    def test_command_chords_and_escape_sequences(self) -> None:
        quartz = self._quartz()
        inj = MacTextInjector()
        with mock.patch.dict(sys.modules, {"Quartz": quartz}):
            inj.start()
            inj.press_combo(["cmd", "a"])
            inj.press_combo(["escape"])
            inj.press_combo(["b"])
        codes = [(e["code"], e["down"]) for e in quartz.posted]
        assert codes[:4] == [(55, True), (0, True), (0, False), (55, False)]
        assert quartz.posted[1]["flags"] == 0x100000
        assert codes[4:] == [(53, True), (53, False), (11, True), (11, False)]

    def test_unknown_key_is_refused_before_anything_is_posted(self) -> None:
        quartz = self._quartz()
        inj = MacTextInjector()
        with mock.patch.dict(sys.modules, {"Quartz": quartz}):
            inj.start()
            with pytest.raises(ValueError, match="unknown key"):
                inj.press_combo(["ctrl", "notakey"])
        assert quartz.posted == []

    def test_requires_start(self) -> None:
        with pytest.raises(RuntimeError, match="not started"):
            MacTextInjector().press_combo(["left"])

    def test_nav_is_enabled_on_the_mac_backend(self) -> None:
        # The daemon turns [nav] on only where the injector can press chords.
        assert callable(getattr(MacTextInjector(), "press_combo", None))


class TestMacHotkeySpec:
    def test_parses_the_default_combo(self) -> None:
        spec = MacHotkeySpec("control+alt+v")
        assert spec.trigger_code == MAC_KEYCODES["v"]
        assert len(spec.modifier_groups) == 2

    def test_command_alias(self) -> None:
        spec = MacHotkeySpec("cmd+shift+space")
        assert spec.trigger_code == MAC_KEYCODES["space"]

    def test_rejects_unknown_key(self) -> None:
        with pytest.raises(ValueError, match="unsupported hotkey key"):
            MacHotkeySpec("control+alt+f13")

    def test_requires_a_modifier(self) -> None:
        with pytest.raises(ValueError, match="modifier"):
            MacHotkeySpec("v")


class TestMacStateMachine:
    def _listener(self, mode: str = "toggle") -> tuple[MacHotkeyListener, mock.Mock]:
        toggled = mock.Mock()
        listener = MacHotkeyListener(
            {"key": "control+alt+v", "mode": mode},
            on_toggle=toggled,
            on_hold_start=mock.Mock(),
            on_hold_stop=mock.Mock(),
        )
        return listener, toggled

    def test_flags_plus_key_fires_toggle_once(self) -> None:
        listener, toggled = self._listener()
        control_mask = dict((g, m) for g, m in FLAG_MASKS)[frozenset({59, 62})]
        option_mask = dict((g, m) for g, m in FLAG_MASKS)[frozenset({58, 61})]
        listener._apply_flags(control_mask | option_mask)  # mods down
        listener._handle_key_event(MAC_KEYCODES["v"], 1)   # v down
        listener._handle_key_event(MAC_KEYCODES["v"], 0)   # v up
        listener._apply_flags(0)                            # mods up
        assert toggled.call_count == 1

    def test_key_without_modifiers_does_nothing(self) -> None:
        listener, toggled = self._listener()
        listener._handle_key_event(MAC_KEYCODES["v"], 1)
        listener._handle_key_event(MAC_KEYCODES["v"], 0)
        toggled.assert_not_called()

    def test_hold_mode_fires_start_and_stop(self) -> None:
        started, stopped = mock.Mock(), mock.Mock()
        listener = MacHotkeyListener(
            {"key": "control+alt+v", "mode": "hold"},
            on_toggle=mock.Mock(),
            on_hold_start=started,
            on_hold_stop=stopped,
        )
        masks = dict(FLAG_MASKS)
        listener._apply_flags(masks[frozenset({59, 62})] | masks[frozenset({58, 61})])
        listener._handle_key_event(MAC_KEYCODES["v"], 1)
        started.assert_called_once()
        listener._handle_key_event(MAC_KEYCODES["v"], 0)
        stopped.assert_called_once()
