"""Hands-free navigation: parsing spoken commands, the per-platform chord
tables (Linux and Windows, editors and terminals), the engine's barrier
and fresh segment, and the daemon pressing keys end to end."""

import asyncio
import sys

import pytest
from fakes import FakeStreamingSTT, RecordingInjector, _make_daemon
from waiting import wait_until

from voice_keyboard.config import _default_config_with_paths, validate_config
from voice_keyboard.flow import nav
from voice_keyboard.flow.engine import FlowConfig, FlowEngine
from voice_keyboard.flow.grammar import Grammar
from voice_keyboard.flow.registers import PROSE, TERMINAL


def _parse(text: str, *, decided: bool = True):
    cores = [token.casefold().strip(".,!?;:") for token in text.split()]
    return nav.parse_nav(cores, 0, decided=decided)


class TestParse:
    @pytest.mark.parametrize(
        "spoken, expected",
        [
            ("go left", ("move:char:left", 1, 2)),
            ("go left three words", ("move:word:left", 3, 4)),
            ("move right 2 characters", ("move:char:right", 2, 4)),
            ("go back a word", ("move:word:left", 1, 4)),
            ("go back one word", ("move:word:left", 1, 4)),
            ("go up two lines", ("move:line:up", 2, 4)),
            ("go down", ("move:line:down", 1, 2)),
            ("go to end of line", ("move:line:end", 1, 5)),
            ("go to the start of the document", ("move:doc:start", 1, 7)),
            ("Go to the beginning of the line.", ("move:line:start", 1, 7)),
            ("select previous word", ("select:word:left", 1, 3)),
            ("select the next four words", ("select:word:right", 4, 5)),
            ("select previous line", ("select:line:up", 1, 3)),
            ("select to end of line", ("select:line:end", 1, 5)),
            ("select all", ("select:all", 1, 2)),
            ("select this line", ("select:line:here", 1, 3)),
            ("delete previous word", ("delete:word:left", 1, 3)),
            ("delete next two words", ("delete:word:right", 2, 4)),
            ("delete the line", ("delete:line:here", 1, 3)),
            ("press tab", ("press:tab", 1, 2)),
            ("press escape twice", ("press:escape", 2, 3)),
            ("press page down", ("press:pagedown", 1, 3)),
            ("press down three times", ("press:down", 3, 4)),
            # Prose that merely starts with a verb stays words.
            ("go to the store", None),
            ("press the button", None),
            ("select previous", None),
            ("delete that", None),
            ("move left line", None),
            ("go up three words", None),
            ("delete previous line", None),
            ("press enter", None),
            ("press return", None),
        ],
    )
    def test_commands(self, spoken, expected) -> None:
        assert _parse(spoken) == expected

    def test_counts_are_capped(self) -> None:
        assert _parse("go left 50 words") is None
        assert _parse("go left twenty words") == ("move:word:left", 20, 4)

    @pytest.mark.parametrize(
        "spoken",
        ["go", "go left", "go to end of", "select previous", "go left three", "press page"],
    )
    def test_open_tail_waits(self, spoken) -> None:
        assert _parse(spoken, decided=False) == nav.PENDING

    def test_complete_tail_does_not_wait(self) -> None:
        assert _parse("select previous word", decided=False) == ("select:word:left", 1, 3)
        assert _parse("go to end of line", decided=False) == ("move:line:end", 1, 5)


class TestKeymaps:
    def test_editor_is_the_same_on_linux_and_windows_but_redo(self) -> None:
        linux = nav.keymap(terminal=False, platform="linux")
        windows = nav.keymap(terminal=False, platform="win32")
        assert windows.pop("edit:redo") == [["ctrl", "y"]]
        linux.pop("edit:redo")
        assert linux == windows

    @pytest.mark.parametrize(
        "platform, action, expected",
        [
            ("linux", "move:word:left", [["alt", "b"]]),
            ("linux", "move:line:start", [["ctrl", "a"]]),
            ("linux", "delete:word:left", [["ctrl", "w"]]),
            ("win32", "move:word:left", [["ctrl", "left"]]),
            ("win32", "move:line:start", [["home"]]),
            ("win32", "delete:word:left", [["ctrl", "backspace"]]),
        ],
    )
    def test_terminal_keys(self, platform, action, expected) -> None:
        table = nav.keymap(terminal=True, platform=platform)
        assert nav.chords_for(action, 1, table) == expected

    @pytest.mark.parametrize(
        "action, expected",
        [
            ("move:word:left", [["alt", "left"]]),
            ("select:word:left", [["shift", "alt", "left"]]),
            ("move:line:end", [["cmd", "right"]]),
            ("move:doc:start", [["cmd", "up"]]),
            ("select:all", [["cmd", "a"]]),
            ("delete:word:left", [["alt", "backspace"]]),
            ("delete:line:here", [["cmd", "left"], ["shift", "cmd", "right"], ["backspace"]]),
        ],
    )
    def test_mac_editor_keys(self, action, expected) -> None:
        assert nav.chords_for(action, 1, nav.keymap(terminal=False, platform="darwin")) == expected

    def test_mac_terminal_sends_meta_as_escape(self) -> None:
        table = nav.keymap(terminal=True, platform="darwin")
        assert nav.chords_for("move:word:left", 2, table) == [["escape"], ["b"], ["escape"], ["b"]]
        assert nav.chords_for("delete:word:right", 1, table) == [["escape"], ["d"]]
        assert nav.chords_for("move:line:start", 1, table) == [["ctrl", "a"]]
        assert nav.chords_for("delete:word:left", 1, table) == [["ctrl", "w"]]

    @pytest.mark.parametrize("platform", ["linux", "win32", "darwin"])
    def test_terminals_refuse_selection(self, platform) -> None:
        table = nav.keymap(terminal=True, platform=platform)
        assert nav.chords_for("select:word:left", 1, table) is None
        assert nav.chords_for("select:all", 1, table) is None

    def test_editor_chords_and_repeats(self) -> None:
        table = nav.keymap(terminal=False, platform="linux")  # macOS: option+arrows
        assert nav.chords_for("select:word:left", 2, table) == [
            ["shift", "ctrl", "left"],
            ["shift", "ctrl", "left"],
        ]
        assert nav.chords_for("select:all", 5, table) == [["ctrl", "a"]]
        assert nav.chords_for("press:tab", 3, table) == [["tab"]] * 3

    def test_no_table_ever_presses_enter(self) -> None:
        tables = [
            nav.keymap(terminal=terminal, platform=platform)
            for terminal in (False, True)
            for platform in ("linux", "win32", "darwin")
        ]
        actions = {action for table in tables for action in table}
        actions |= {f"press:{key}" for key in nav.PRESS_KEYS.values()}
        for table in tables:
            for action in actions:
                for chord in nav.chords_for(action, 1, table) or []:
                    names = set(chord)
                    assert not names & {"enter", "return", "kpenter"}, (action, chord)
                    assert not {"ctrl", "j"} <= names and not {"ctrl", "m"} <= names

    def test_every_key_name_resolves_on_every_backend(self) -> None:
        from voice_keyboard import injector as linux_injector
        from voice_keyboard.macos import injector as mac_injector
        from voice_keyboard.windows import injector as windows_injector

        tables = [
            nav.keymap(terminal=terminal, platform=platform)
            for terminal in (False, True)
            for platform in ("linux", "win32", "darwin")
        ]
        names = {
            name
            for table in tables
            for chords in table.values()
            for chord in chords or []
            for name in chord
        } | set(nav.PRESS_KEYS.values())
        for name in names:
            assert name in windows_injector.KEY_NAMES or len(name) == 1, name
            assert name in linux_injector.KEY_NAMES or len(name) == 1, name
            mac_injector.resolve_key(name)  # raises for an unknown key

    def test_overrides(self) -> None:
        table = nav.keymap(
            terminal=True,
            platform="linux",
            overrides={
                "move:word:left": "ctrl+left",
                "move:line:start": "",
                "move:line:end": "ctrl+m",  # would submit: ignored
            },
        )
        assert nav.chords_for("move:word:left", 1, table) == [["ctrl", "left"]]
        assert nav.chords_for("move:line:start", 1, table) is None
        assert nav.chords_for("move:line:end", 1, table) == [["ctrl", "e"]]

    def test_config_validation(self) -> None:
        config = _default_config_with_paths()
        config["xai"]["api_key"] = "test-api-key"
        config["nav"]["keys"]["terminal"] = {"move:word:left": "ctrl+left"}
        validate_config(config)
        config["nav"]["keys"]["terminal"] = {"move:word:left": "shift+enter"}
        with pytest.raises(RuntimeError, match="Enter"):
            validate_config(config)
        config["nav"]["keys"] = {"browser": {}}
        with pytest.raises(RuntimeError, match="nav.keys"):
            validate_config(config)


class TestGrammar:
    def test_off_by_default(self) -> None:
        result = Grammar().parse("select previous word".split(), flush=True)
        assert [item.kind for item in result.items] == ["word"] * 3

    def test_key_item(self) -> None:
        result = Grammar(nav=True).parse("select previous three words".split(), flush=True)
        (item,) = result.items
        assert (item.kind, item.text, item.count, item.span) == (
            "key", "select:word:left", 3, (0, 4)
        )

    def test_settled_command_does_not_grow_into_the_next_segment(self) -> None:
        tokens = "go left three words".split()
        result = Grammar(nav=True).parse(tokens, settled=2)
        assert result.items[0].text == "move:char:left"
        assert result.items[0].span == (0, 2)


def make_engine(register=PROSE) -> FlowEngine:
    return FlowEngine(FlowConfig(), Grammar(nav=True), register)


class TestEngine:
    def test_standalone_command_is_a_barrier(self) -> None:
        engine = make_engine()
        engine.on_transcript("hello world", is_final=True, now=0.0)
        engine.on_transcript("hello world select previous word", is_final=True, now=1.0)
        action = engine.pending_action()
        assert action is not None and action.action == "select:word:left"
        assert engine.desired_text() == "Hello world"
        # Molten words after the command wait for the keys.
        engine.on_transcript("hello world select previous word planet", is_final=False, now=1.1)
        assert engine.desired_text() == "Hello world"
        engine.complete_action(now=1.2)
        assert engine.pending_action() is None
        # A fresh segment, glued: the word replaces the selection.
        assert engine.desired_text() == "planet"
        result = engine.finalize("hello world select previous word planet", now=2.0)
        assert result.text == "planet"
        assert result.typed_before == "Hello world"

    def test_mid_sentence_command_is_typed(self) -> None:
        engine = make_engine()
        result = engine.finalize("please go left three words now", now=0.0)
        assert result.action is None
        assert result.text == "Please go left three words now"

    def test_moves_keep_spacing(self) -> None:
        engine = make_engine()
        engine.on_transcript("one two", is_final=True, now=0.0)
        engine.on_transcript("one two go to end of line", is_final=True, now=1.0)
        engine.complete_action(now=1.1)
        result = engine.finalize("one two go to end of line three", now=2.0)
        assert result.text == " three"

    def test_finalize_walks_through_several_commands(self) -> None:
        engine = make_engine()
        engine.on_transcript("alpha", is_final=True, now=0.0)
        engine.on_transcript("alpha go left", is_final=True, now=1.0)
        engine.on_transcript("alpha go left beta", is_final=True, now=2.0)
        engine.on_transcript("alpha go left beta press tab", is_final=True, now=3.0)
        result = engine.finalize("alpha go left beta press tab gamma", now=4.0)
        seen = []
        while result.action is not None:
            seen.append((result.text, result.action.action))
            result = engine.complete_action(now=5.0)
        assert seen == [("Alpha", "move:char:left"), (" beta", "press:tab")]
        # Tab likely moved to another field: dictation there starts fresh.
        assert result.text == "Gamma"

    def test_line_start_glues(self) -> None:
        engine = make_engine()
        engine.on_transcript("one", is_final=True, now=0.0)
        engine.on_transcript("one go to start of line", is_final=True, now=1.0)
        engine.complete_action(now=1.1)
        result = engine.finalize("one go to start of line two", now=2.0)
        assert result.text == "two"

    def test_scratch_cannot_cross_a_command(self) -> None:
        engine = make_engine()
        engine.on_transcript("keep this", is_final=True, now=0.0)
        engine.on_transcript("keep this go left", is_final=True, now=1.0)
        engine.complete_action(now=1.1)
        result = engine.finalize("keep this go left oops scratch that", now=2.0)
        assert result.text == ""
        assert result.typed_before == "Keep this"


class TestDaemon:
    @pytest.fixture(autouse=True)
    def inline(self, tmp_path, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path))

        async def _to_thread(func, /, *args, **kwargs):
            await asyncio.sleep(0)
            return func(*args, **kwargs)

        monkeypatch.setattr(asyncio, "to_thread", _to_thread)
        monkeypatch.setattr("voice_keyboard.client._show_overlay", lambda *a, **k: None)

    def _daemon(self, events, injector, *, app="test-editor", enabled=True):
        daemon = _make_daemon(FakeStreamingSTT(events), injector, app=app)
        daemon._config["nav"]["enabled"] = enabled
        return daemon

    def test_live_command_presses_keys_after_the_text(self) -> None:
        events = [
            {"type": "transcript.partial", "text": "hello world", "is_final": True},
            {"type": "transcript.partial", "text": "select previous word", "is_final": True},
            {"type": "transcript.partial", "text": "planet", "is_final": True},
        ]
        injector = RecordingInjector()
        screens = []
        original = injector.press_combo

        def press(names):
            screens.append(injector.screen)
            original(names)

        injector.press_combo = press
        daemon = self._daemon(events, injector)

        async def run() -> str:
            with daemon._audio_patch, daemon._stt_patch, daemon._probe_patch:
                await daemon._start_recording()
                assert await wait_until(lambda: injector.combos)
                assert await wait_until(lambda: injector.screen.endswith("planet"))
                return await daemon._stop_recording()

        final = asyncio.run(run())
        # A word is option+arrow on macOS, ctrl+arrow elsewhere.
        word = "alt" if sys.platform == "darwin" else "ctrl"
        assert injector.combos == [["shift", word, "left"]]
        assert screens == ["Hello world"]  # the text landed before the keys
        assert injector.screen == "Hello worldplanet"  # (the fake has no caret)
        assert final == "Hello world planet"

    def test_terminal_gets_terminal_keys(self) -> None:
        events = [{"type": "transcript.partial", "text": "go to start of line", "is_final": True}]
        injector = RecordingInjector()
        daemon = self._daemon(events, injector, app="kitty")

        async def run() -> None:
            with daemon._audio_patch, daemon._stt_patch, daemon._probe_patch:
                await daemon._start_recording()
                assert await wait_until(lambda: injector.combos)
                await daemon._stop_recording()

        asyncio.run(run())
        expected = [["home"]] if sys.platform == "win32" else [["ctrl", "a"]]
        assert injector.combos == expected
        assert injector.screen == ""

    def test_disabled_types_the_words(self) -> None:
        events = [{"type": "transcript.partial", "text": "go left", "is_final": True}]
        injector = RecordingInjector()
        daemon = self._daemon(events, injector, enabled=False)

        async def run() -> str:
            with daemon._audio_patch, daemon._stt_patch, daemon._probe_patch:
                await daemon._start_recording()
                assert await wait_until(lambda: injector.screen)
                return await daemon._stop_recording()

        assert asyncio.run(run()) == "Go left"
        assert injector.combos == []

    def test_held_hotkey_defers_keys_to_stop(self) -> None:
        events = [
            {"type": "transcript.partial", "text": "draft", "is_final": True},
            {"type": "transcript.partial", "text": "press tab", "is_final": True},
        ]
        injector = RecordingInjector()
        daemon = self._daemon(events, injector)
        held = {"down": True}
        daemon._hotkey_combo_held = lambda: held["down"]

        async def run() -> None:
            with daemon._audio_patch, daemon._stt_patch, daemon._probe_patch:
                await daemon._start_recording()
                assert await wait_until(lambda: injector.screen == "Draft")
                await asyncio.sleep(0.6)  # several ticks: still held
                assert injector.combos == []
                held["down"] = False
                await daemon._stop_recording()

        asyncio.run(run())
        assert injector.combos == [["tab"]]

    def test_unbound_command_is_refused_not_typed(self) -> None:
        events = [{"type": "transcript.partial", "text": "select all", "is_final": True}]
        injector = RecordingInjector()
        daemon = self._daemon(events, injector, app="kitty")

        async def run() -> str:
            with daemon._audio_patch, daemon._stt_patch, daemon._probe_patch:
                await daemon._start_recording()
                await asyncio.sleep(0.3)
                return await daemon._stop_recording()

        assert asyncio.run(run()) == ""
        assert injector.combos == []
        assert injector.screen == ""


class TestCriticRegressions:
    def test_unicode_digit_count_does_not_crash(self) -> None:
        # "²" is not a count (and must not crash int()): a plain "go left".
        assert _parse("go left ² words") == ("move:char:left", 1, 2)
        Grammar(nav=True).parse("go left ² words".split(), flush=True)

    @pytest.mark.parametrize("platform", ["linux", "win32"])
    def test_terminal_line_moves_are_refused(self, platform) -> None:
        table = nav.keymap(terminal=True, platform=platform)
        assert nav.chords_for("move:line:up", 2, table) is None
        assert nav.chords_for("press:up", 1, table) == [["up"]]

    def test_ctrl_o_and_unknown_keys_are_rejected(self) -> None:
        with pytest.raises(ValueError, match="Enter"):
            nav.parse_override("ctrl+o")
        with pytest.raises(ValueError, match="unknown key"):
            nav.parse_override("ctrl+lefft")

    def test_refused_command_keeps_spacing_and_text(self) -> None:
        engine = make_engine(TERMINAL)
        engine.on_transcript("ls -la", is_final=True, now=0.0)
        engine.on_transcript("ls -la select previous word", is_final=True, now=1.0)
        engine.complete_action(now=1.1, pressed=False)
        result = engine.finalize("ls -la select previous word grep foo", now=2.0)
        assert result.text == "ls -la grep foo"
        assert result.typed_before == ""

    def test_spell_that_after_select_types_over_the_selection(self) -> None:
        engine = make_engine()
        engine.on_transcript("hello wrold", is_final=True, now=0.0)
        engine.on_transcript("hello wrold select previous word", is_final=True, now=1.0)
        engine.complete_action(now=1.1)
        result = engine.finalize(
            "hello wrold select previous word spell that w o r l d", now=2.0
        )
        assert result.text == "world"


class TestDaemonRegressions:
    @pytest.fixture(autouse=True)
    def inline(self, tmp_path, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path))

        async def _to_thread(func, /, *args, **kwargs):
            await asyncio.sleep(0)
            return func(*args, **kwargs)

        monkeypatch.setattr(asyncio, "to_thread", _to_thread)
        monkeypatch.setattr("voice_keyboard.client._show_overlay", lambda *a, **k: None)
        monkeypatch.setattr("voice_keyboard.daemon.NAV_RELEASE_WAIT_S", 0.1)

    def _run(self, events, injector, *, held=lambda: False) -> tuple:
        daemon = _make_daemon(FakeStreamingSTT(events), injector)
        daemon._config["nav"]["enabled"] = True
        daemon._hotkey_combo_held = held

        async def run() -> str:
            with daemon._audio_patch, daemon._stt_patch, daemon._probe_patch:
                await daemon._start_recording()
                await wait_until(lambda: injector.screen)
                await asyncio.sleep(0.3)
                return await daemon._stop_recording()

        return asyncio.run(run()), daemon

    def test_last_typed_is_only_the_segment_after_the_command(self) -> None:
        events = [
            {"type": "transcript.partial", "text": "hello world", "is_final": True},
            {"type": "transcript.partial", "text": "go to end of line", "is_final": True},
            {"type": "transcript.partial", "text": "more", "is_final": True},
        ]
        final, daemon = self._run(events, RecordingInjector())
        assert final == "Hello world more"
        assert daemon._last_typed == " more"

    def test_modifiers_still_held_at_stop_refuse_the_keys(self) -> None:
        events = [
            {"type": "transcript.partial", "text": "draft", "is_final": True},
            {"type": "transcript.partial", "text": "press tab", "is_final": True},
        ]
        injector = RecordingInjector()
        self._run(events, injector, held=lambda: True)
        assert injector.combos == []


def test_undo_redo_paste_are_editor_commands():
    from voice_keyboard.flow.nav import chords_for, keymap, parse_nav

    assert parse_nav(["undo", "that"], 0, decided=True) == ("edit:undo", 1, 2)
    assert parse_nav(["redo", "it", "twice"], 0, decided=True) == ("edit:redo", 2, 3)
    assert parse_nav(["paste"], 0, decided=True) == ("edit:paste", 1, 1)
    assert chords_for("edit:undo", 1, keymap(terminal=False, platform="linux")) == [["ctrl", "z"]]
    assert chords_for("edit:redo", 1, keymap(terminal=False, platform="win32")) == [["ctrl", "y"]]
    assert chords_for("edit:paste", 1, keymap(terminal=False, platform="darwin")) == [["cmd", "v"]]
    # A terminal has no undo, and a paste can carry a line break: refused.
    for platform in ("linux", "win32", "darwin"):
        for action in ("edit:undo", "edit:redo", "edit:paste"):
            assert chords_for(action, 1, keymap(terminal=True, platform=platform)) is None


def test_select_that_presses_one_shift_left_per_character():
    from voice_keyboard.flow.nav import MAX_SELECT_THAT, chords_for, keymap, parse_nav

    assert parse_nav(["select", "that"], 0, decided=False) == ("select:that", 1, 2)
    editor = keymap(terminal=False, platform="linux")
    assert chords_for("select:that", 3, editor) == [["shift", "left"]] * 3
    assert chords_for("select:that", 0, editor) is None  # nothing said yet
    assert chords_for("select:that", MAX_SELECT_THAT + 1, editor) is None
    assert chords_for("select:that", 3, keymap(terminal=True, platform="linux")) is None


def test_undo_and_paste_need_a_surely_known_non_terminal():
    from voice_keyboard.daemon import _surely_not_a_terminal
    from voice_keyboard.focusprobe import FocusInfo

    assert not _surely_not_a_terminal(None)  # unknown focus
    assert not _surely_not_a_terminal(FocusInfo(app=""))
    assert not _surely_not_a_terminal(FocusInfo(app="gnome-terminal-server"))
    assert not _surely_not_a_terminal(FocusInfo(app="WindowsTerminal.exe"))
    assert not _surely_not_a_terminal(FocusInfo(app="code", role="terminal"))
    assert _surely_not_a_terminal(FocusInfo(app="gedit", role="text"))


def test_refusals_say_why():
    from voice_keyboard.daemon import _nav_refusal
    from voice_keyboard.flow.engine import NavAction

    assert _nav_refusal(NavAction("select:that", 0)) == "Nothing to select yet"
    assert _nav_refusal(NavAction("select:that", 9999)) == "Too long to select by voice"
    assert _nav_refusal(NavAction("edit:paste", 1)) == "Can't paste here"
    assert _nav_refusal(NavAction("select:all", 1)) == "Can't select that here"
