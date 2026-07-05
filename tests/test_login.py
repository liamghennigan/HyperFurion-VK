"""`voice-keyboard login` config writing + relay-base derivation.

The login command's one risky bit is editing the user's config.toml in place:
it must set the hosted key + provider selection without trampling the rest of
the file. These tests pin that behavior against a real tomllib round-trip.
"""

import tomllib

from voice_keyboard.client import _apply_hosted_login, _relay_base, _set_toml_value


def _parsed(text: str) -> dict:
    return tomllib.loads(text)


class TestApplyHostedLogin:
    def test_fresh_config_is_valid_and_complete(self) -> None:
        out = _apply_hosted_login("", "hfk_abc123")
        cfg = _parsed(out)  # must be valid TOML
        assert cfg["providers"]["hyperfurion"]["api_key"] == "hfk_abc123"
        assert cfg["stt"]["provider"] == "hyperfurion"
        assert cfg["tts"]["provider"] == "hyperfurion"

    def test_existing_provider_is_switched_and_rest_preserved(self) -> None:
        existing = (
            "# my hand-tuned config\n"
            "[stt]\n"
            'provider = "xai"\n'
            'language = "en"\n'
            "\n"
            "[tts]\n"
            'provider = "xai"\n'
            "\n"
            "[audio]\n"
            "sample_rate = 16000\n"
        )
        out = _apply_hosted_login(existing, "hfk_new")
        cfg = _parsed(out)
        assert cfg["stt"]["provider"] == "hyperfurion"
        assert cfg["tts"]["provider"] == "hyperfurion"
        # untouched neighbors survive
        assert cfg["stt"]["language"] == "en"
        assert cfg["audio"]["sample_rate"] == 16000
        assert cfg["providers"]["hyperfurion"]["api_key"] == "hfk_new"
        assert "# my hand-tuned config" in out  # comment preserved

    def test_existing_key_is_replaced_not_duplicated(self) -> None:
        existing = "[providers.hyperfurion]\n" 'api_key = "hfk_old"\n'
        out = _apply_hosted_login(existing, "hfk_fresh")
        cfg = _parsed(out)
        assert cfg["providers"]["hyperfurion"]["api_key"] == "hfk_fresh"
        assert out.count("api_key") == 1  # no duplicate key (invalid TOML)

    def test_key_with_special_chars_is_escaped(self) -> None:
        out = _apply_hosted_login("", 'weird"\\key')
        assert _parsed(out)["providers"]["hyperfurion"]["api_key"] == 'weird"\\key'

    def test_idempotent(self) -> None:
        once = _apply_hosted_login("", "hfk_x")
        twice = _apply_hosted_login(once, "hfk_x")
        assert _parsed(twice) == _parsed(once)


class TestSetTomlValue:
    def test_inserts_missing_table(self) -> None:
        out = _set_toml_value("[a]\nx = 1\n", "b", "y", "z")
        cfg = _parsed(out)
        assert cfg["a"]["x"] == 1 and cfg["b"]["y"] == "z"


class TestRelayBase:
    def test_default_when_unset(self) -> None:
        assert _relay_base({}) == "https://api.hyperfurion.com"

    def test_custom_base_url_honored(self) -> None:
        cfg = {"providers": {"hyperfurion": {"base_url": "http://localhost:8787"}}}
        assert _relay_base(cfg) == "http://localhost:8787"

    def test_v1_suffix_stripped(self) -> None:
        cfg = {"providers": {"hyperfurion": {"base_url": "https://api.hyperfurion.com/v1"}}}
        assert _relay_base(cfg) == "https://api.hyperfurion.com"
