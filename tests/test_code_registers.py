"""Semantic registers: speech compiled to code/shell through the grammar +
compiler pipeline, with the prefix-stability property the engine needs."""

import pytest

from voice_keyboard.config import _default_config_with_paths, validate_config
from voice_keyboard.flow.grammar import Grammar
from voice_keyboard.flow.registers import (
    PYTHON,
    SHELL,
    initial_state,
    register_for_app,
    render_items,
    resolve_register,
)


def _compile(text: str, register) -> str:
    grammar = Grammar(
        enabled=True,
        numbers="always",
        numbers_on=register.numbers_on,
        numbers_min=register.numbers_min,
    )
    items = grammar.parse(text.split(), flush=True).items
    rendered, _state = render_items(items, initial_state(register), register)
    return rendered


class TestPythonRegister:
    def test_flagship_for_loop(self) -> None:
        assert _compile("for i in range ten colon", PYTHON) == "for i in range(10):"

    def test_dot_and_underscore_and_equals(self) -> None:
        assert (
            _compile("self dot audio underscore thread equals true", PYTHON)
            == "self.audio_thread = true"
        )

    def test_callable_stays_honestly_open(self) -> None:
        assert _compile("print hello", PYTHON) == "print(hello"

    def test_explicit_close_paren(self) -> None:
        assert _compile("len items close paren", PYTHON) == "len(items)"

    def test_no_smart_caps(self) -> None:
        assert _compile("return value period", PYTHON) == "return value."


class TestShellRegister:
    def test_flagship_pipe_grep(self) -> None:
        assert _compile("pipe grep dash i error", SHELL) == "| grep -i error"

    def test_dash_flag_joins(self) -> None:
        assert _compile("ls dash la", SHELL) == "ls -la"

    def test_tilde_slash_path(self) -> None:
        assert _compile("cd tilde slash projects", SHELL) == "cd ~/projects"

    def test_star_dot_glob(self) -> None:
        assert _compile("rm star dot pyc", SHELL) == "rm *.pyc"

    def test_numbers_become_digits(self) -> None:
        assert _compile("head dash n twenty three", SHELL) == "head -n 23"


class TestPrefixStability:
    @pytest.mark.parametrize(
        "text,register",
        [
            ("for i in range ten colon new line print i close paren", PYTHON),
            ("pipe grep dash i error dash n twenty three", SHELL),
        ],
    )
    def test_item_by_item_render_matches_full_render(self, text, register) -> None:
        grammar = Grammar(
            enabled=True,
            numbers="always",
            numbers_on=register.numbers_on,
            numbers_min=register.numbers_min,
        )
        items = grammar.parse(text.split(), flush=True).items
        full, _ = render_items(items, initial_state(register), register)

        accumulated = ""
        state = initial_state(register)
        for item in items:
            delta, state = render_items([item], state, register)
            accumulated += delta
        assert accumulated == full


class TestRegisterPlumbing:
    def test_registers_resolve(self) -> None:
        assert resolve_register("python").compiler == "python"
        assert resolve_register("shell").compiler == "shell"
        assert resolve_register("prose").compiler == ""

    def test_config_map_selects_semantic_register(self) -> None:
        register = register_for_app("code", "text", config_map={"code": "python"})
        assert register.name == "python"

    def test_config_validates_new_registers(self) -> None:
        cfg = _default_config_with_paths()
        cfg["xai"]["api_key"] = "test-api-key"
        cfg["registers"]["default"] = "python"
        cfg["registers"]["map"] = {"kitty": "shell"}
        validate_config(cfg)
