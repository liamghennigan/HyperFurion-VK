"""Kai is on by default only when it runs on this computer.

The owner's rule: "Make Kai on by default if it's set to run locally. If
there's no local option, then it needs to be opt-in." Under the default
([assistant] enabled = "auto") Kai is on only when nothing it does leaves the
machine: the [stt] that hears the question, the [llm] that answers it (and no
xAI voice agent) and the [tts] that speaks the answer are all local servers.
"""

import pytest

from voice_keyboard.assistant.locality import kai_hops, kai_setting, kai_state, uses_voice_agent
from voice_keyboard.config import _default_config_with_paths, _is_local_endpoint, validate_config


def _defaults() -> dict:
    cfg = _default_config_with_paths()
    cfg["xai"]["api_key"] = "xai-test-key"
    cfg["providers"]["xai"]["api_key"] = "xai-test-key"
    return cfg


def _local(**assistant) -> dict:
    """Everything local: a speech server for [stt]/[tts], a local [llm]."""
    cfg = _defaults()
    cfg["providers"]["openai"]["base_url"] = "http://127.0.0.1:8000/v1"
    cfg["stt"]["provider"] = "openai"
    cfg["tts"]["provider"] = "openai"
    cfg["llm"].update(provider="openai", base_url="http://127.0.0.1:8080/v1",
                      model="qwen3.5-2b", api_key="local")
    cfg["assistant"].update(assistant)
    return cfg


# ── the local-address test (A1) ─────────────────────────────────────────


@pytest.mark.parametrize("url", [
    "http://localhost:8000/v1",
    "http://127.0.0.1:8080/v1",
    "http://127.5.6.7/v1",
    "http://[::1]:8000/v1",
    "http://192.168.1.5:8000/v1",
    "http://10.0.0.2/v1",
    "http://172.20.0.1/v1",
    "http://169.254.3.4/v1",
    "http://[fd12:3456::1]:8000/v1",
    "http://[fe80::1]:8000/v1",
    "http://0.0.0.0:8000/v1",
    "http://[::ffff:127.0.0.1]:8000/v1",
    "http://nas.local:8000/v1",
    "http://NAS.Local:8000/v1",
    "http://ollama.localhost:11434/v1",
    "http://box.home.arpa/v1",
    "http://gpu.internal:8080/v1",
])
def test_local_addresses(url: str) -> None:
    assert _is_local_endpoint(url)


@pytest.mark.parametrize("url", [
    "https://api.x.ai/v1",
    "http://127.evil.com/v1",
    "http://10.example.com/v1",
    "http://192.168.foo.net/v1",
    "http://myserver.lan:8000/v1",
    "http://gpu-box:8080/v1",
    "http://100.64.1.2:8080/v1",  # CGNAT / Tailscale: only through local_hosts
    "http://8.8.8.8/v1",
    "http://[::ffff:8.8.8.8]/v1",  # IPv4-mapped public address
    "http://[2002:808:808::1]/v1",  # 6to4 for 8.8.8.8
    "http://[2001::1]/v1",  # Teredo
    "http://[64:ff9b::808:808]/v1",  # NAT64
    "http://user:pw@127.0.0.1:8080/v1",  # userinfo
    "http://api.x.ai\\@127.0.0.1:8080/v1",  # parsers disagree on the host
    "http://127.0.0.1\\.evil.com/v1",
    "http://127.0.0.1%2f.evil.com/v1",
    "localhost:8080/v1",  # no scheme: no host to trust
    # A trailing dot makes the system resolver ask DNS instead of reading
    # an IP address or /etc/hosts: whatever DNS answers gets the request.
    "http://127.0.0.1.:8080/v1",
    "http://localhost.:8000/v1",
    "http://nas.local.:8000/v1",
    "http://[::1].:8000/v1",
    "",
    "not a url",
])
def test_online_addresses(url: str) -> None:
    assert not _is_local_endpoint(url)


def test_local_hosts_names_ips_and_networks() -> None:
    extra = ["gpu-box", "100.64.0.0/10", "203.0.113.7", "Ollama."]
    assert _is_local_endpoint("http://gpu-box:8080/v1", extra=extra)
    assert _is_local_endpoint("http://GPU-BOX:8080/v1", extra=extra)
    assert _is_local_endpoint("http://ollama:11434/v1", extra=extra)
    assert _is_local_endpoint("http://100.101.102.103:8080/v1", extra=extra)
    assert _is_local_endpoint("http://203.0.113.7/v1", extra=extra)
    assert not _is_local_endpoint("http://gpu-box.evil.com/v1", extra=extra)
    assert not _is_local_endpoint("http://203.0.113.8/v1", extra=extra)
    # Never an escape hatch around the one-host rule.
    assert not _is_local_endpoint("http://user@gpu-box/v1", extra=extra)


def test_keyless_validation_ignores_local_hosts() -> None:
    # local_hosts only decides whether Kai counts as local; a keyless
    # server still has to be a local address.
    cfg = _defaults()
    cfg["stt"]["provider"] = "openai"
    cfg["tts"]["provider"] = "openai"
    cfg["providers"]["openai"]["base_url"] = "http://gpu-box:8000/v1"
    cfg["assistant"]["local_hosts"] = ["gpu-box"]
    with pytest.raises(RuntimeError, match="providers.openai.api_key"):
        validate_config(cfg)


# ── the setting ─────────────────────────────────────────────────────────


@pytest.mark.parametrize("value, setting", [
    (True, "on"), (False, "off"), ("auto", "auto"), ("AUTO", "auto"), (" Auto ", "auto"),
])
def test_kai_setting(value, setting) -> None:
    cfg = _defaults()
    cfg["assistant"]["enabled"] = value
    assert kai_setting(cfg) == setting


def test_missing_setting_is_auto() -> None:
    assert kai_setting({}) == "auto"
    assert kai_setting({"assistant": {}}) == "auto"


def test_default_is_auto() -> None:
    assert _defaults()["assistant"]["enabled"] == "auto"


# ── the verdict ─────────────────────────────────────────────────────────


def test_defaults_are_online_so_kai_is_off() -> None:
    state = kai_state(_defaults())
    assert state.setting == "auto"
    assert state.local is False
    assert state.on is False
    assert "xAI" in state.why()
    assert state.why().startswith("off")


def test_everything_local_turns_kai_on() -> None:
    state = kai_state(_local())
    assert state.local is True
    assert state.on is True
    assert state.why() == "on: everything Kai uses runs on this computer"
    roles = {hop.role: hop for hop in state.hops}
    assert set(roles) == {"hears you", "thinks", "speaks"}
    assert all(hop.local for hop in state.hops)
    assert "qwen3.5-2b" in roles["thinks"].service
    assert "127.0.0.1:8080" in roles["thinks"].service


@pytest.mark.parametrize("change", [
    lambda c: c["stt"].update(provider="groq"),
    lambda c: (c["stt"].update(provider="hyperfurion"),
               c["providers"]["hyperfurion"].update(base_url="http://127.0.0.1:8787")),
    lambda c: c["tts"].update(provider="elevenlabs"),
    lambda c: c["providers"]["openai"].update(base_url=""),
    lambda c: c["llm"].update(provider="xai", base_url=""),
    lambda c: c["llm"].update(base_url="https://api.together.xyz/v1"),
    lambda c: c["llm"].update(model="gpt-oss:120b-cloud"),
    lambda c: c["llm"].update(model="qwen3-coder:480b-cloud"),
    lambda c: c["llm"].update(model="deepseek-v3.1:671b-cloud"),
    lambda c: c["llm"].update(model="", provider="custom"),
], ids=[
    "stt-groq", "stt-relay-on-localhost", "tts-elevenlabs", "speech-openai-cloud",
    "llm-xai", "llm-remote", "llm-ollama-cloud", "llm-ollama-cloud-2", "llm-ollama-cloud-3",
    "no-llm",
])
def test_each_online_part_keeps_kai_off(change) -> None:
    cfg = _local()
    change(cfg)
    state = kai_state(cfg)
    assert state.local is False
    assert state.on is False


def test_relay_on_localhost_counts_as_xai() -> None:
    cfg = _local()
    cfg["stt"]["provider"] = "hyperfurion"
    cfg["providers"]["hyperfurion"]["base_url"] = "http://127.0.0.1:8787"
    hears = next(hop for hop in kai_state(cfg).hops if hop.role == "hears you")
    assert hears.local is False
    assert "HyperFurion relay" in hears.service and "xAI" in hears.service


def test_ollama_cloud_is_named() -> None:
    cfg = _local()
    cfg["llm"]["model"] = "gpt-oss:120b-cloud"
    thinks = next(hop for hop in kai_state(cfg).hops if hop.role == "thinks")
    assert thinks.service == "Ollama's cloud"


def test_no_language_model_says_so() -> None:
    cfg = _local()
    cfg["llm"].update(provider="custom", base_url="", model="")
    state = kai_state(cfg)
    assert state.on is False
    assert "[llm]" in state.why()
    assert "no language model" in state.why()


def test_one_online_part_is_named_with_its_section() -> None:
    cfg = _local()
    cfg["llm"].update(provider="xai", base_url="", model="grok-4.3")
    why = kai_state(cfg).why()
    assert "xAI" in why and "[llm]" in why
    assert "[stt]" not in why and "[tts]" not in why


def test_voice_agent_is_online() -> None:
    cfg = _local(agent_id="agent_123", brain="auto")
    assert uses_voice_agent(cfg)
    state = kai_state(cfg)
    assert state.on is False
    assert any("voice agent" in hop.service for hop in state.hops if not hop.local)


def test_voice_agent_with_local_brain_is_not_a_hop() -> None:
    cfg = _local(agent_id="agent_123", brain="local")
    assert not uses_voice_agent(cfg)
    assert kai_state(cfg).on is True


def test_agent_id_without_any_key_is_no_agent() -> None:
    cfg = _local(agent_id="agent_123", brain="auto")
    cfg["xai"]["api_key"] = ""
    cfg["providers"]["xai"]["api_key"] = ""
    assert not uses_voice_agent(cfg)
    assert kai_state(cfg).on is True


def test_explicit_true_is_on_with_online_services() -> None:
    state = kai_state(_defaults() | {"assistant": {"enabled": True}})
    assert state.on is True
    assert state.local is False
    assert state.why().startswith("on (you turned it on)")
    assert "xAI" in state.why()


def test_explicit_false_is_off_even_when_local() -> None:
    state = kai_state(_local(enabled=False))
    assert state.on is False
    assert state.local is True
    assert state.why() == "off ([assistant] enabled = false)"


def test_auto_in_any_case() -> None:
    assert kai_state(_local(enabled="AUTO")).on is True


@pytest.mark.parametrize("change", [
    lambda c: c["llm"].update(api_key=""),
    lambda c: c["llm"].update(api_key="openai-your-api-key-here"),
    lambda c: c["assistant"].update(web_enabled=False),
    lambda c: c["assistant"].update(web_enabled=True),
    lambda c: c["assistant"].update(privacy_mode="cloud"),
    lambda c: c["assistant"].update(can_act=True),
    lambda c: c["wake"].update(enabled=True),
])
def test_keys_and_unrelated_switches_never_change_the_verdict(change) -> None:
    cfg = _local()
    change(cfg)
    assert kai_state(cfg).on is True
    online = _defaults()
    change(online)
    assert kai_state(online).on is False


def test_online_key_missing_is_still_online() -> None:
    cfg = _defaults()
    cfg["xai"]["api_key"] = ""
    cfg["providers"]["xai"]["api_key"] = ""
    assert kai_state(cfg).on is False


def test_local_hosts_make_named_servers_local() -> None:
    cfg = _local(local_hosts=["gpu-box"])
    cfg["llm"]["base_url"] = "http://gpu-box:8080/v1"
    assert kai_state(cfg).on is True
    cfg["assistant"]["local_hosts"] = []
    assert kai_state(cfg).on is False


# ── [recall] (A3): never a reason to stay off under "auto" ──────────────


def test_online_recall_is_not_a_hop_under_auto() -> None:
    cfg = _local()
    cfg["recall"].update(base_url="https://api.openai.com/v1", model="text-embedding-3-small")
    state = kai_state(cfg)
    assert state.on is True
    assert state.uses_embeddings is False
    assert all(hop.role != "looks up your history" for hop in state.hops)


def test_local_recall_is_used_under_auto() -> None:
    cfg = _local()
    cfg["recall"].update(base_url="http://localhost:11434/v1", model="nomic-embed-text")
    state = kai_state(cfg)
    assert state.on is True
    assert state.uses_embeddings is True


def test_ollama_cloud_embeddings_are_online() -> None:
    cfg = _local()
    cfg["recall"].update(base_url="http://localhost:11434/v1", model="embed:cloud")
    assert kai_state(cfg).uses_embeddings is False


def test_online_recall_is_named_under_true() -> None:
    cfg = _local(enabled=True)
    cfg["recall"].update(base_url="https://api.openai.com/v1", model="text-embedding-3-small")
    state = kai_state(cfg)
    assert state.on is True
    assert state.uses_embeddings is True
    hop = next(hop for hop in state.hops if hop.role == "looks up your history")
    assert hop.local is False and hop.service == "OpenAI"
    assert state.local is False


def test_recall_without_model_is_no_hop() -> None:
    cfg = _local(enabled=True)
    cfg["recall"].update(base_url="https://api.openai.com/v1", model="")
    assert kai_state(cfg).uses_embeddings is False


def test_memory_off_means_no_history_lookups() -> None:
    cfg = _local(enabled=True, memory_enabled=False)
    cfg["recall"].update(base_url="http://localhost:11434/v1", model="nomic-embed-text")
    state = kai_state(cfg)
    assert state.uses_embeddings is False
    assert all(hop.role != "looks up your history" for hop in state.hops)


# ── robustness ──────────────────────────────────────────────────────────


@pytest.mark.parametrize("cfg", [
    {}, {"flow": {}}, {"assistant": "nope"}, {"llm": 3, "stt": None, "tts": []},
    {"assistant": {"enabled": "yes"}}, {"assistant": {"local_hosts": "gpu-box"}},
])
def test_partial_or_odd_configs_never_raise_and_never_turn_kai_on(cfg) -> None:
    state = kai_state(cfg)
    assert state.on is False


def test_hops_follow_role_order() -> None:
    roles = [hop.role for hop in kai_hops(_defaults())]
    assert roles == ["hears you", "thinks", "speaks"]


# ── "on this computer" only when it is (review) ─────────────────────────


def test_a_server_on_your_network_isnt_called_this_computer() -> None:
    cfg = _local()
    cfg["providers"]["openai"]["base_url"] = "http://192.168.1.5:8000/v1"
    state = kai_state(cfg)
    assert state.on is True
    assert state.why() == "on: everything Kai uses runs on this computer or your own network"
    roles = {hop.role: hop for hop in state.hops}
    assert roles["hears you"].here is False
    assert roles["thinks"].here is True


@pytest.mark.parametrize("url", [
    "http://127.0.0.1:8000/v1", "http://localhost:8000/v1", "http://[::1]:8000/v1",
    "http://0.0.0.0:8000/v1", "http://ollama.localhost:8000/v1",
    "http://[::ffff:127.0.0.1]:8000/v1",
])
def test_this_computer_is_loopback(url: str) -> None:
    cfg = _local()
    cfg["providers"]["openai"]["base_url"] = url
    cfg["llm"]["base_url"] = url
    assert kai_state(cfg).why() == "on: everything Kai uses runs on this computer"


def test_local_hosts_count_as_your_network() -> None:
    cfg = _local(local_hosts=["gpu-box"])
    cfg["llm"]["base_url"] = "http://gpu-box:8080/v1"
    assert kai_state(cfg).why().endswith("runs on this computer or your own network")


def test_consent_with_cloud_privacy_names_folder_lists_too() -> None:
    # privacy_mode = "cloud" sends excerpts of files you name, and the list
    # of a folder you name (assistant/context.py _directory_chunk).
    from voice_keyboard.assistant.locality import hop_payload

    cfg = _defaults()
    cfg["assistant"]["privacy_mode"] = "cloud"
    thinks = next(hop for hop in kai_state(cfg).hops if hop.section == "[llm]")
    assert "excerpts of files and lists of folders you name" in hop_payload(thinks, cfg, "linux")


def test_nothing_to_hear_with_is_not_local() -> None:
    # "none" is a [tts] choice only (nothing speaks); an [stt] that says it
    # is a broken setting, never a local one.
    cfg = _local()
    cfg["stt"]["provider"] = "none"
    hears = next(hop for hop in kai_state(cfg).hops if hop.section == "[stt]")
    assert hears.local is False
    assert kai_state(cfg).on is False
