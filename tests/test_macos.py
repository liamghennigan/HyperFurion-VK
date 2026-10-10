"""macOS backend tests — everything provable without a Mac.

The Quartz calls themselves need real hardware (MACOS.md lists what is
written but untested there), but the logic layers are exercised here with
a recording fake Quartz: platform factories, Unicode chunking, real Return
and Tab keys, the no-Enter guard, explicit event flags, the injected-event
tag, layout-aware chords, the keycode/flag tables, left/right modifiers,
bare-key bindings, swallowing the trigger key, and the inherited combo
state machine driven through the flagsChanged translation.
"""

import sys
from types import ModuleType
from unittest import mock

import pytest

from voice_keyboard import hotkey as hotkey_mod
from voice_keyboard import injector as injector_mod
from voice_keyboard.macos import keylayout
from voice_keyboard.macos.hotkey import (
    DEVICE_FLAG_MASKS,
    FLAG_MASKS,
    INJECTED_EVENT_TAG,
    KVK_FUNCTION,
    KVK_LEFT_COMMAND,
    KVK_RIGHT_COMMAND,
    KVK_RIGHT_OPTION,
    MAC_KEYCODES,
    MacHotkeyListener,
    MacHotkeySpec,
)
from voice_keyboard.macos.injector import (
    CHUNK_UTF16_UNITS,
    KVK_RETURN,
    KVK_TAB,
    MacTextInjector,
    chunk_text,
    split_keys,
)

CONTROL = dict(FLAG_MASKS)[frozenset({59, 62})]
OPTION = dict(FLAG_MASKS)[frozenset({58, 61})]
COMMAND = dict(FLAG_MASKS)[frozenset({55, 54})]
SHIFT = dict(FLAG_MASKS)[frozenset({56, 60})]


def fake_quartz() -> ModuleType:
    """A Quartz stand-in that records every posted event as a dict."""
    quartz = ModuleType("Quartz")
    quartz.kCGHIDEventTap = 0
    quartz.kCGEventSourceUserData = 42
    quartz.posted = []
    quartz.CGEventCreateKeyboardEvent = mock.Mock(
        side_effect=lambda _source, code, down: {"code": code, "down": down, "text": "", "flags": None}
    )
    quartz.CGEventKeyboardSetUnicodeString = mock.Mock(
        side_effect=lambda event, units, text: event.update(text=text, units=units)
    )
    quartz.CGEventSetFlags = mock.Mock(side_effect=lambda event, flags: event.update(flags=flags))
    quartz.CGEventSetIntegerValueField = mock.Mock(
        side_effect=lambda event, field, value: event.update({f"field{field}": value})
    )
    quartz.CGEventPost = mock.Mock(side_effect=lambda _tap, event: quartz.posted.append(dict(event)))
    return quartz


class NoLayout:
    """A LayoutKeymap that knows no layout (ANSI positions throughout)."""

    def code_for(self, char):
        return None

    def prime(self):
        pass


def started(layout=None):
    quartz = fake_quartz()
    inj = MacTextInjector(layout=layout or NoLayout())
    with mock.patch.dict(sys.modules, {"Quartz": quartz}), \
            mock.patch("voice_keyboard.macos.permissions.accessibility_granted", return_value=True):
        inj.start()
    return inj, quartz


def typed(quartz) -> str:
    return "".join(e["text"] for e in quartz.posted if e["down"] and e["text"])


def keys(quartz) -> list[tuple[int, bool, int]]:
    return [(e["code"], e["down"], e["flags"]) for e in quartz.posted if not e["text"]]


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

    def test_darwin_passes_hold_cancel(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(sys, "platform", "darwin")
        cancel = mock.Mock()
        listener = hotkey_mod.create_hotkey_listener(
            {"key": "rightcmd", "mode": "auto", "allow_bare": True},
            on_toggle=mock.Mock(), on_hold_start=mock.Mock(), on_hold_stop=mock.Mock(),
            on_hold_cancel=cancel,
        )
        assert listener._on_hold_cancel is cancel

    def test_darwin_validates_bindings_with_the_mac_table(self, monkeypatch) -> None:
        monkeypatch.setattr(sys, "platform", "darwin")
        assert isinstance(hotkey_mod.parse_binding("rightcmd", allow_bare=True), MacHotkeySpec)
        assert isinstance(hotkey_mod.parse_binding("rightctrl", allow_bare=True), MacHotkeySpec)
        with pytest.raises(ValueError):
            hotkey_mod.parse_binding("control+alt+nope")
        # The same chord however it is spelled (Option is Alt, Cmd is Super).
        assert hotkey_mod.bindings_clash("control+option+r", "Alt+Ctrl+R")
        assert hotkey_mod.bindings_clash("cmd+shift+space", "super+shift+space")
        assert not hotkey_mod.bindings_clash("rightcmd", "rightctrl")

    def test_mac_labels(self, monkeypatch) -> None:
        monkeypatch.setattr(sys, "platform", "darwin")
        assert hotkey_mod.pretty_binding("super+shift+space") == "Cmd+Shift+Space"
        assert hotkey_mod.pretty_binding("rightcmd") == "Right Cmd"
        assert hotkey_mod.pretty_binding("rightoption") == "Right Option"
        assert hotkey_mod.pretty_binding("fn") == "fn"


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
        inj, quartz = started()
        text = "héllo wörld — dictated, not typed 🎙"
        inj.type_text(text)
        assert quartz.CGEventPost.call_count == 2 * len(chunk_text(text))
        assert typed(quartz) == text
        # Every event says how many UTF-16 units it carries (emoji count two).
        assert all(e["units"] == len(e["text"].encode("utf-16-le")) // 2 for e in quartz.posted)

    def test_type_text_requires_start(self) -> None:
        with pytest.raises(RuntimeError, match="not started"):
            MacTextInjector().type_text("hi")

    def test_every_event_has_explicit_flags_and_our_tag(self) -> None:
        # Hold-to-talk keeps Control+Option down while words land: typed
        # text must not inherit them (they would turn it into shortcuts).
        inj, quartz = started()
        inj.type_text("hi\nthere")
        inj.delete_chars(2)
        inj.press_combo(["cmd", "z"])
        assert quartz.posted
        assert all(e["flags"] is not None for e in quartz.posted)
        assert all(e["field42"] == INJECTED_EVENT_TAG for e in quartz.posted)
        text_events = [e for e in quartz.posted if e["text"]]
        assert all(e["flags"] == 0 for e in text_events)


class TestLineBreaks:
    def test_split_keys(self) -> None:
        assert split_keys("a\nb\tc") == ["a", "\n", "b", "\t", "c"]
        assert split_keys("one\r\ntwo\rthree") == ["one", "\n", "two", "\n", "three"]
        assert split_keys("\n\n") == ["\n", "\n"]

    def test_newline_and_tab_are_real_keys(self) -> None:
        inj, quartz = started()
        inj.type_text("Dear Sam,\n\tThanks")
        assert "\n" not in typed(quartz) and "\t" not in typed(quartz)
        assert typed(quartz) == "Dear Sam,Thanks"
        assert keys(quartz) == [
            (KVK_RETURN, True, 0), (KVK_RETURN, False, 0),
            (KVK_TAB, True, 0), (KVK_TAB, False, 0),
        ]

    def test_chat_app_line_break_is_shift_return(self) -> None:
        inj, quartz = started()
        inj.shift_newline = True
        inj.type_text("first\nsecond")
        assert keys(quartz) == [(KVK_RETURN, True, 0x20000), (KVK_RETURN, False, 0x20000)]
        assert typed(quartz) == "firstsecond"

    def test_suppress_enter_never_types_a_newline(self) -> None:
        # The intent channel / Kai's terminal route: Return is never pressed
        # and no line break reaches the event stream in any form.
        inj, quartz = started()
        inj.suppress_enter = True
        inj.type_text("git status\nrm -rf /\r\n")
        assert "\n" not in typed(quartz) and "\r" not in typed(quartz)
        assert "git status rm -rf /" in typed(quartz)
        assert all(code not in (KVK_RETURN, 76) for code, _down, _flags in keys(quartz))

    def test_suppress_enter_refuses_return_even_from_internal_paths(self) -> None:
        inj, quartz = started()
        inj.suppress_enter = True
        inj._press_newline()
        inj._tap_key(KVK_RETURN)
        inj._tap_key(76)  # keypad Enter
        assert quartz.posted == []

    def test_daemon_no_enter_path_reaches_the_mac_injector(self) -> None:
        # The daemon only arms the guard on injectors that expose it.
        inj = MacTextInjector()
        assert hasattr(inj, "suppress_enter") and hasattr(inj, "shift_newline")


class TestChords:
    def test_chord_presses_modifiers_then_the_key_with_their_flags(self) -> None:
        inj, quartz = started()
        inj.press_combo(["shift", "alt", "left"])
        shift, alt, left = 56, 58, 123
        assert [(e["code"], e["down"]) for e in quartz.posted] == [
            (shift, True), (alt, True), (left, True), (left, False), (alt, False), (shift, False),
        ]
        assert quartz.posted[2]["flags"] == 0x20000 | 0x80000  # the key carries both modifiers
        assert quartz.posted[3]["flags"] == 0x20000 | 0x80000  # so does its release
        assert quartz.posted[0]["flags"] == 0x20000
        assert quartz.posted[4]["flags"] == 0x20000  # alt's own key-up no longer carries alt
        assert quartz.posted[-1]["flags"] == 0  # shift releases last, flags clear

    def test_command_chords_and_escape_sequences(self) -> None:
        inj, quartz = started()
        inj.press_combo(["cmd", "a"])
        inj.press_combo(["escape"])
        inj.press_combo(["b"])
        codes = [(e["code"], e["down"]) for e in quartz.posted]
        assert codes[:4] == [(55, True), (0, True), (0, False), (55, False)]
        assert quartz.posted[1]["flags"] == 0x100000
        assert codes[4:] == [(53, True), (53, False), (11, True), (11, False)]

    def test_letters_follow_the_keyboard_layout(self) -> None:
        # AZERTY: the key that types "a" is where US keyboards have Q (12).
        # Pressing the ANSI "a" position there would be Command+Q: quit.
        class Azerty(NoLayout):
            def code_for(self, char):
                return {"a": 12, "q": 0, "z": 13, "w": 6}.get(char)

        inj, quartz = started(Azerty())
        inj.press_combo(["cmd", "a"])
        inj.press_combo(["cmd", "z"])
        assert [e["code"] for e in quartz.posted if e["down"]] == [55, 12, 55, 13]

    def test_unknown_key_is_refused_before_anything_is_posted(self) -> None:
        inj, quartz = started()
        with pytest.raises(ValueError, match="unknown key"):
            inj.press_combo(["ctrl", "notakey"])
        assert quartz.posted == []

    def test_requires_start(self) -> None:
        with pytest.raises(RuntimeError, match="not started"):
            MacTextInjector().press_combo(["left"])

    def test_nav_is_enabled_on_the_mac_backend(self) -> None:
        # The daemon turns [nav] on only where the injector can press chords.
        assert callable(getattr(MacTextInjector(), "press_combo", None))

    def test_delete_presses_backspace_without_modifiers(self) -> None:
        # Option held (hold-to-talk) + Delete would erase whole words.
        inj, quartz = started()
        inj.delete_chars(3)
        assert keys(quartz) == [(51, True, 0), (51, False, 0)] * 3

    def test_start_warns_without_accessibility(self, caplog) -> None:
        quartz = fake_quartz()
        notify, prompt = mock.Mock(), mock.Mock()
        with mock.patch.dict(sys.modules, {"Quartz": quartz}), \
                mock.patch("voice_keyboard.macos.permissions.accessibility_granted", return_value=False), \
                mock.patch("voice_keyboard.macos.permissions.prompt_accessibility", prompt), \
                mock.patch("voice_keyboard.macos.permissions.notify_missing", notify):
            MacTextInjector(layout=NoLayout()).start()
        assert "Accessibility" in caplog.text
        notify.assert_called_once_with("typing")
        prompt.assert_called_once()  # macOS's own prompt lists the app for us

    def test_start_is_quiet_with_accessibility(self) -> None:
        quartz = fake_quartz()
        prompt = mock.Mock()
        with mock.patch.dict(sys.modules, {"Quartz": quartz}), \
                mock.patch("voice_keyboard.macos.permissions.accessibility_granted", return_value=True), \
                mock.patch("voice_keyboard.macos.permissions.prompt_accessibility", prompt):
            MacTextInjector(layout=NoLayout()).start()
        prompt.assert_not_called()


class TestKeyLayout:
    AZERTY = {0: "q", 1: "s", 2: "d", 6: "w", 12: "a", 13: "z", 9: "v", 11: "b", 18: "&", 24: "-"}

    def test_build_char_map_lowest_code_wins_and_skips_blanks(self) -> None:
        table = {0: "q", 12: "a", 13: "z", 30: "a", 49: " ", 50: "", 36: "\r"}
        found = keylayout.build_char_map(lambda code: table.get(code, ""))
        assert found["a"] == 12 and found["q"] == 0 and found["z"] == 13
        assert " " not in found and "\r" not in found

    def test_keymap_maps_on_the_main_thread_only(self) -> None:
        calls = []

        def factory():
            calls.append(1)
            return lambda code: self.AZERTY.get(code, "")

        on_main = {"value": False}
        layout = keylayout.LayoutKeymap(factory, on_main_thread=lambda: on_main["value"])
        # Off the main thread nothing is asked (macOS would abort the
        # process); the ANSI fallback answers instead.
        assert layout.code_for("a") is None and calls == []
        on_main["value"] = True
        layout.prime()
        assert calls == [1]
        on_main["value"] = False
        assert layout.code_for("a") == 12 and layout.code_for("A") == 12
        assert layout.code_for("ab") is None

    def test_keymap_refreshes_after_a_while(self) -> None:
        now = {"t": 0.0}
        layouts = iter([{12: "a"}, {0: "a"}])
        layout = keylayout.LayoutKeymap(
            lambda: (lambda table: (lambda code: table.get(code, "")))(next(layouts)),
            clock=lambda: now["t"], on_main_thread=lambda: True,
        )
        assert layout.code_for("a") == 12
        now["t"] = keylayout.REFRESH_S + 1
        assert layout.code_for("a") == 0  # the user switched layouts

    def test_a_failing_layout_lookup_falls_back(self) -> None:
        def broken():
            raise OSError("no Carbon")

        layout = keylayout.LayoutKeymap(broken, on_main_thread=lambda: True)
        assert layout.code_for("a") is None

    def test_hotkey_letter_follows_layout(self) -> None:
        class Dvorak:
            def code_for(self, char):
                return {"v": 47}.get(char)

        assert MacHotkeySpec("control+alt+v", layout=Dvorak()).trigger_code == 47
        assert MacHotkeySpec("control+alt+v").trigger_code == MAC_KEYCODES["v"]


class TestMacHotkeySpec:
    def test_parses_the_default_combo(self) -> None:
        spec = MacHotkeySpec("control+alt+v")
        assert spec.trigger_code == MAC_KEYCODES["v"]
        assert len(spec.modifier_groups) == 2
        assert not spec.is_bare

    def test_command_and_option_aliases(self) -> None:
        assert MacHotkeySpec("cmd+shift+space").trigger_code == MAC_KEYCODES["space"]
        assert MacHotkeySpec("option+command+period").trigger_code == MAC_KEYCODES["."]
        assert MacHotkeySpec("control+alt+f13").trigger_code == 105

    def test_rejects_unknown_key(self) -> None:
        with pytest.raises(ValueError, match="unsupported hotkey key"):
            MacHotkeySpec("control+alt+f42")
        with pytest.raises(ValueError, match="unsupported hotkey modifier"):
            MacHotkeySpec("hyper+v")

    def test_requires_a_modifier_unless_bare_is_allowed(self) -> None:
        with pytest.raises(ValueError, match="modifier"):
            MacHotkeySpec("v")
        with pytest.raises(ValueError, match="modifier"):
            MacHotkeySpec("rightcmd")
        assert MacHotkeySpec("rightcmd", allow_bare=True).is_bare

    @pytest.mark.parametrize("name, code", [
        ("rightcmd", KVK_RIGHT_COMMAND), ("rightoption", KVK_RIGHT_OPTION),
        ("rightalt", KVK_RIGHT_OPTION), ("rightctrl", 62), ("fn", KVK_FUNCTION),
        ("globe", KVK_FUNCTION), ("leftcmd", KVK_LEFT_COMMAND),
    ])
    def test_bare_side_keys(self, name, code) -> None:
        assert MacHotkeySpec(name, allow_bare=True).trigger_code == code


class TestFlagsChanged:
    def _listener(self, key="control+alt+v", mode="toggle", **callbacks):
        cbs = {name: callbacks.get(name, mock.Mock())
               for name in ("on_toggle", "on_hold_start", "on_hold_stop", "on_hold_cancel")}
        listener = MacHotkeyListener(
            {"key": key, "mode": mode, "allow_bare": True, "hold_threshold_ms": 10_000},
            use_layout=False, **cbs,
        )
        return listener, cbs

    def test_device_bits_tell_left_from_right(self) -> None:
        listener, _ = self._listener()
        listener._apply_flags(COMMAND | DEVICE_FLAG_MASKS[KVK_RIGHT_COMMAND], KVK_RIGHT_COMMAND)
        assert listener._mod_down == {KVK_RIGHT_COMMAND}
        listener._apply_flags(
            COMMAND | DEVICE_FLAG_MASKS[KVK_RIGHT_COMMAND] | DEVICE_FLAG_MASKS[KVK_LEFT_COMMAND],
            KVK_LEFT_COMMAND,
        )
        assert listener._mod_down == {KVK_RIGHT_COMMAND, KVK_LEFT_COMMAND}
        listener._apply_flags(COMMAND | DEVICE_FLAG_MASKS[KVK_LEFT_COMMAND], KVK_RIGHT_COMMAND)
        assert listener._mod_down == {KVK_LEFT_COMMAND}
        listener._apply_flags(0, KVK_LEFT_COMMAND)
        assert listener._mod_down == set()

    def test_without_device_bits_the_changed_key_is_credited(self) -> None:
        listener, _ = self._listener()
        listener._apply_flags(OPTION, KVK_RIGHT_OPTION)
        assert listener._mod_down == {KVK_RIGHT_OPTION}
        listener._apply_flags(OPTION | SHIFT)  # no keycode: the left one
        assert listener._mod_down == {KVK_RIGHT_OPTION, 56}
        listener._apply_flags(0)
        assert listener._mod_down == set()

    def test_flags_plus_key_fires_toggle_once(self) -> None:
        listener, cbs = self._listener()
        listener._apply_flags(CONTROL | OPTION)            # mods down
        listener._handle_key_event(MAC_KEYCODES["v"], 1)   # v down
        listener._handle_key_event(MAC_KEYCODES["v"], 0)   # v up
        listener._apply_flags(0)                            # mods up
        assert cbs["on_toggle"].call_count == 1

    def test_key_without_modifiers_does_nothing(self) -> None:
        listener, cbs = self._listener()
        listener._handle_key_event(MAC_KEYCODES["v"], 1)
        listener._handle_key_event(MAC_KEYCODES["v"], 0)
        cbs["on_toggle"].assert_not_called()

    def test_hold_mode_fires_start_and_stop(self) -> None:
        listener, cbs = self._listener(mode="hold")
        listener._apply_flags(CONTROL | OPTION)
        listener._handle_key_event(MAC_KEYCODES["v"], 1)
        cbs["on_hold_start"].assert_called_once()
        listener._handle_key_event(MAC_KEYCODES["v"], 0)
        cbs["on_hold_stop"].assert_called_once()

    def test_bare_right_command_holds_to_talk(self) -> None:
        listener, cbs = self._listener(key="rightcmd", mode="hold")
        right = COMMAND | DEVICE_FLAG_MASKS[KVK_RIGHT_COMMAND]
        listener._on_tap_event("flags", KVK_RIGHT_COMMAND, right)
        cbs["on_hold_start"].assert_called_once()
        listener._on_tap_event("flags", KVK_RIGHT_COMMAND, 0)
        cbs["on_hold_stop"].assert_called_once()

    def test_left_command_is_not_the_right_one(self) -> None:
        listener, cbs = self._listener(key="rightcmd", mode="hold")
        listener._on_tap_event("flags", KVK_LEFT_COMMAND, COMMAND | DEVICE_FLAG_MASKS[KVK_LEFT_COMMAND])
        cbs["on_hold_start"].assert_not_called()

    def test_command_c_cancels_a_bare_command_gesture(self) -> None:
        # The user was copying, not summoning Kai: the hold is cancelled.
        listener, cbs = self._listener(key="rightcmd", mode="hold")
        held = COMMAND | DEVICE_FLAG_MASKS[KVK_RIGHT_COMMAND]
        listener._on_tap_event("flags", KVK_RIGHT_COMMAND, held)
        listener._on_tap_event("down", MAC_KEYCODES["c"], held)
        cbs["on_hold_cancel"].assert_called_once()
        cbs["on_hold_stop"].assert_not_called()

    def test_fn_key_as_a_bare_binding(self) -> None:
        listener, cbs = self._listener(key="fn", mode="hold")
        listener._on_tap_event("flags", KVK_FUNCTION, 0x800000)
        cbs["on_hold_start"].assert_called_once()
        listener._on_tap_event("flags", KVK_FUNCTION, 0)
        cbs["on_hold_stop"].assert_called_once()

    def test_arrow_keys_carry_the_fn_bit_without_pressing_fn(self) -> None:
        # macOS sets the fn flag on arrow and function keys: that is no
        # fn press, and no release of a held fn either.
        listener, cbs = self._listener(key="fn", mode="hold")
        listener._on_tap_event("down", 123, 0x800000)  # left arrow
        listener._on_tap_event("up", 123, 0x800000)
        cbs["on_hold_start"].assert_not_called()
        listener._on_tap_event("flags", KVK_FUNCTION, 0x800000)
        listener._on_tap_event("down", 123, 0)  # flags without fn: still no release
        assert KVK_FUNCTION in listener._mod_down

    def test_injected_events_are_ignored(self) -> None:
        listener, cbs = self._listener()
        listener._on_tap_event("flags", 59, CONTROL | OPTION, injected=True)
        listener._on_tap_event("down", MAC_KEYCODES["v"], CONTROL | OPTION, injected=True)
        listener._on_tap_event("up", MAC_KEYCODES["v"], CONTROL | OPTION, injected=True)
        cbs["on_toggle"].assert_not_called()
        assert listener._mod_down == set()

    def test_autorepeat_does_not_retrigger(self) -> None:
        listener, cbs = self._listener()
        listener._on_tap_event("flags", 59, CONTROL | OPTION)
        listener._on_tap_event("down", MAC_KEYCODES["v"], CONTROL | OPTION)
        for _ in range(5):
            listener._on_tap_event("down", MAC_KEYCODES["v"], CONTROL | OPTION, autorepeat=True)
        assert cbs["on_toggle"].call_count == 1

    def test_a_missed_modifier_release_is_caught_at_the_next_key(self) -> None:
        # Control+Option went up while the tap was off (a secure field):
        # plain V afterwards must stay plain V.
        listener, cbs = self._listener()
        listener._on_tap_event("flags", 59, CONTROL | OPTION)
        assert listener._mod_down == {59, 58}
        listener._on_tap_event("down", MAC_KEYCODES["v"], 0)  # the event says: no modifiers
        assert listener._mod_down == set()
        cbs["on_toggle"].assert_not_called()

    def test_a_missed_trigger_release_is_caught_before_the_chord(self) -> None:
        listener, cbs = self._listener()
        v = MAC_KEYCODES["v"]
        listener._on_tap_event("down", v, 0)  # V down; its key-up never arrives
        physically_down: set = set()
        listener._key_is_down = lambda code: code in physically_down
        listener._on_tap_event("flags", 59, CONTROL | OPTION)
        cbs["on_toggle"].assert_not_called()  # Control+Option alone is not the hotkey
        listener._on_tap_event("down", v, CONTROL | OPTION)
        cbs["on_toggle"].assert_called_once()


class TestSwallow:
    def _listener(self, key="control+alt+v"):
        return MacHotkeyListener(
            {"key": key, "mode": "toggle", "allow_bare": True},
            on_toggle=mock.Mock(), on_hold_start=mock.Mock(), on_hold_stop=mock.Mock(),
            use_layout=False,
        )

    def test_the_chord_trigger_never_reaches_the_app(self) -> None:
        listener = self._listener()
        v = MAC_KEYCODES["v"]
        held = CONTROL | OPTION
        assert listener._on_tap_event("flags", 59, held) is False
        assert listener._on_tap_event("down", v, held) is True
        assert listener._on_tap_event("down", v, held, autorepeat=True) is True
        assert listener._on_tap_event("up", v, held) is True
        listener._on_tap_event("flags", 59, 0)

    def test_released_modifiers_first_still_swallows_the_release(self) -> None:
        listener = self._listener()
        v = MAC_KEYCODES["v"]
        listener._on_tap_event("flags", 59, CONTROL | OPTION)
        assert listener._on_tap_event("down", v, CONTROL | OPTION) is True
        listener._on_tap_event("flags", 59, 0)  # modifiers up before V
        assert listener._on_tap_event("up", v, 0) is True  # the app never sees half a key

    def test_plain_v_and_partial_chords_pass_through(self) -> None:
        listener = self._listener()
        v = MAC_KEYCODES["v"]
        assert listener._on_tap_event("down", v, 0) is False
        assert listener._on_tap_event("up", v, 0) is False
        listener._on_tap_event("flags", 59, CONTROL)  # control only
        assert listener._on_tap_event("down", v, CONTROL) is False
        assert listener._on_tap_event("up", v, CONTROL) is False

    def test_bare_modifiers_are_never_swallowed(self) -> None:
        listener = self._listener("rightcmd")
        held = COMMAND | DEVICE_FLAG_MASKS[KVK_RIGHT_COMMAND]
        assert listener._on_tap_event("flags", KVK_RIGHT_COMMAND, held) is False
        assert listener._on_tap_event("down", MAC_KEYCODES["c"], held) is False
