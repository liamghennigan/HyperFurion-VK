from voice_keyboard.cheatsheet import render
from voice_keyboard.config import DEFAULT_CONFIG


def _config(**flow):
    config = {key: dict(value) if isinstance(value, dict) else value for key, value in DEFAULT_CONFIG.items()}
    config["flow"] = {**DEFAULT_CONFIG["flow"], **flow}
    return config


def test_lists_the_live_grammar():
    text = render(_config())
    for said in ("scratch that", "new number", "question mark", "emoji rocket", "cap that", "snake case <words>"):
        assert said in text
    assert "off: set [nav] enabled = true" in text


def test_user_config_is_merged():
    config = _config(vocabulary={"hyper furion": "HyperFurion"}, commands={"new_line": ["next line"]})
    config["nav"] = {"enabled": True}
    config["snippets"] = {"my email": "me@example.com"}
    text = render(config)
    assert "hyper furion" in text and "HyperFurion" in text
    assert "next line" in text and "new line" not in text
    assert "select that" in text
    assert "vk, my email" in text


def test_filter_keeps_matching_lines():
    text = render(_config(), "percent")
    assert "percent sign" in text and "25%" in text
    assert "emoji rocket" not in text
    assert "Nothing matches" in render(_config(), "zzzz")


def test_fold_examples_type_what_they_promise():
    """Every number/date/address example in the cheat sheet is what the
    engine really types."""
    from voice_keyboard.cheatsheet import _FOLDS
    from voice_keyboard.trial import run

    config = _config()
    checked = 0
    for said, typed in _FOLDS:
        alternatives, results = said.split(" / "), typed.split(" / ")
        if len(alternatives) != len(results) or "…" in said or " to " in said or said.startswith("spell"):
            continue
        for words, expect in zip(alternatives, results):
            got = run(config, words.split())
            assert got.casefold() == expect.casefold() and got[1:] == expect[1:], (words, got, expect)
            checked += 1
    assert checked >= 12
