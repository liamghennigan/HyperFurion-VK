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
