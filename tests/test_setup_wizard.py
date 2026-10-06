"""`voice-keyboard setup`: llama.cpp detection and the settings walkthrough."""

import json
import threading
import tomllib
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest

from voice_keyboard import setup_wizard
from voice_keyboard.setup_wizard import LlamaServer, Wizard, detect_llama_cpp, model_label, probe_llama_cpp


def _serve(models=None, api_key="", owned_by="llamacpp"):
    """A stand-in llama-server on an ephemeral loopback port."""
    models = models if models is not None else ["/models/Qwen3.5-2B-Instruct-Q4_K_M.gguf"]

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def _reply(self, status, body):
            data = json.dumps(body).encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def do_GET(self):
            if self.path == "/health":
                return self._reply(200, {"status": "ok"})
            if self.path == "/v1/models":
                if api_key and self.headers.get("Authorization") != f"Bearer {api_key}":
                    return self._reply(401, {"error": {"message": "Invalid API Key"}})
                return self._reply(200, {
                    "object": "list",
                    "data": [{"id": m, "object": "model", "owned_by": owned_by} for m in models],
                })
            self._reply(404, {})

    server = HTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server, f"http://127.0.0.1:{server.server_address[1]}"


@pytest.fixture
def llama():
    started = []

    def start(**kwargs):
        server, origin = _serve(**kwargs)
        started.append(server)
        return origin

    yield start
    for server in started:
        server.shutdown()
        server.server_close()


# ------------------------------------------------------------- detection

def test_probe_finds_llama_cpp_and_its_model(llama):
    origin = llama()
    server = probe_llama_cpp(origin)
    assert server == LlamaServer(f"{origin}/v1", ["/models/Qwen3.5-2B-Instruct-Q4_K_M.gguf"])
    # A /v1 base URL is accepted too.
    assert probe_llama_cpp(f"{origin}/v1") == server


def test_probe_ignores_other_openai_compatible_servers(llama):
    assert probe_llama_cpp(llama(owned_by="vllm")) is None


def test_probe_reports_a_server_that_needs_a_key(llama):
    origin = llama(api_key="sekrit")
    assert probe_llama_cpp(origin) == LlamaServer(f"{origin}/v1", needs_key=True)
    assert probe_llama_cpp(origin, api_key="sekrit").models
    assert probe_llama_cpp(origin, api_key="wrong") is None


def test_probe_nothing_listening():
    server, origin = _serve()
    server.server_close()  # the port is free again
    assert probe_llama_cpp(origin, timeout=0.3) is None


def test_detect_tries_extra_urls_then_default_ports(llama, monkeypatch):
    origin = llama()
    monkeypatch.setattr(setup_wizard, "LLAMA_CPP_PORTS", ())
    assert detect_llama_cpp((origin,)).base_url == f"{origin}/v1"
    port = origin.rsplit(":", 1)[1]
    monkeypatch.setenv("LLAMA_ARG_PORT", port)
    assert detect_llama_cpp().base_url == f"http://127.0.0.1:{port}/v1"


def test_model_label():
    assert model_label("/models/Qwen3.5-2B-Instruct-Q4_K_M.gguf") == "Qwen3.5-2B-Instruct-Q4_K_M"
    assert model_label("C:\\models\\phi-4-mini.GGUF") == "phi-4-mini"
    assert model_label("qwen") == "qwen"


# ----------------------------------------------------------- walkthrough

class Script:
    """Answers the wizard's prompts in order; fails on an unexpected one."""

    def __init__(self, *answers):
        self.answers = list(answers)
        self.prompts = []

    def __call__(self, prompt):
        self.prompts.append(prompt)
        if not self.answers:
            raise AssertionError(f"unexpected prompt: {prompt!r}")
        answer = self.answers.pop(0)
        if isinstance(answer, BaseException):
            raise answer
        return answer


def _wizard(tmp_path, ask, secret=None, server=None, **kwargs):
    out = []
    wizard = Wizard(
        tmp_path / "config.toml",
        ask=ask,
        secret=secret or Script(),
        out=out.append,
        detect=lambda extra=(): server,
        **kwargs,
    )
    return wizard, out


@pytest.fixture(autouse=True)
def _config_home(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg"))
    monkeypatch.delenv("HFVK_LLAMA_URL", raising=False)
    monkeypatch.delenv("LLAMA_ARG_PORT", raising=False)


def test_detected_llama_becomes_the_default_model(tmp_path):
    server = LlamaServer("http://127.0.0.1:8080/v1", ["/m/qwen.gguf"])
    ask = Script(
        "",                 # use qwen? yes
        "2", "xai",         # own key, xAI
        "",                 # hotkey
        "",                 # mode
        "",                 # language
        "", "", "",         # Kai, history, navigation
        "",                 # save
    )
    wizard, out = _wizard(tmp_path, ask, secret=Script("xai-real-key"), server=server)
    assert wizard.run() == 0
    config = tomllib.loads((tmp_path / "config.toml").read_text())
    assert config["llm"] == {
        "provider": "openai",
        "base_url": "http://127.0.0.1:8080/v1",
        "model": "/m/qwen.gguf",
        "api_key": "local",
    }
    assert config["stt"]["provider"] == "xai"
    assert config["tts"]["provider"] == "xai"
    assert config["providers"]["xai"]["api_key"] == "xai-real-key"
    assert any("running qwen" in line for line in out)
    assert "Setup complete." in out
    # The language-model menu is skipped once llama.cpp was chosen.
    assert not any("Which model should it use" in line for line in out)


def test_declining_llama_leaves_llm_alone(tmp_path):
    server = LlamaServer("http://127.0.0.1:8080/v1", ["qwen"])
    ask = Script("n", "", "", "", "", "", "", "", "")
    wizard, _ = _wizard(tmp_path, ask, server=server)
    assert wizard.run() == 0
    config = tomllib.loads((tmp_path / "config.toml").read_text())
    assert "llm" not in config


def test_router_mode_picks_one_of_several_models(tmp_path):
    server = LlamaServer("http://127.0.0.1:8080/v1", ["a.gguf", "b.gguf"])
    ask = Script("2", "", "", "", "", "", "", "", "", "")
    wizard, _ = _wizard(tmp_path, ask, server=server)
    assert wizard.run() == 0
    assert tomllib.loads((tmp_path / "config.toml").read_text())["llm"]["model"] == "b.gguf"


def test_llama_with_api_key(tmp_path):
    server = LlamaServer("http://127.0.0.1:8080/v1", needs_key=True)
    probed = []

    def probe(url, api_key=""):
        probed.append((url, api_key))
        return LlamaServer(url, ["qwen"])

    ask = Script("", "", "", "", "", "", "", "", "")
    wizard, _ = _wizard(tmp_path, ask, secret=Script("sekrit"), server=server, probe=probe)
    assert wizard.run() == 0
    assert probed == [("http://127.0.0.1:8080/v1", "sekrit")]
    assert tomllib.loads((tmp_path / "config.toml").read_text())["llm"]["api_key"] == "sekrit"


def test_existing_config_is_edited_in_place(tmp_path):
    path = tmp_path / "config.toml"
    path.write_text(
        "# my notes\n[stt]\nprovider = \"xai\"\nlanguage = \"en\"\n\n"
        "[providers.xai]\napi_key = \"xai-mine\"\n\n[hotkey]\nkey = \"control+alt+v\"\n"
    )
    ask = Script(
        "4",                # keep speech
        "3", "http://127.0.0.1:8081", "",  # local llm, model from the probe
        "super+h", "hold",  # hotkey
        "de",               # language
        "n", "y", "y",      # Kai off, history on, navigation on
        "",                 # save
    )
    wizard, out = _wizard(
        tmp_path, ask, secret=Script(""),
        probe=lambda url, api_key="": LlamaServer(url, ["phi.gguf"]),
    )
    assert wizard.run() == 0
    text = path.read_text()
    assert text.startswith("# my notes\n")
    config = tomllib.loads(text)
    assert config["providers"]["xai"]["api_key"] == "xai-mine"
    assert config["hotkey"] == {"key": "super+h", "mode": "hold"}
    assert config["stt"]["language"] == "de"
    assert config["tts"]["language"] == "de"
    assert config["assistant"]["enabled"] is False
    assert config["flow"]["history"] is True
    assert config["nav"]["enabled"] is True
    assert config["llm"]["base_url"] == "http://127.0.0.1:8081/v1"
    assert config["llm"]["model"] == "phi.gguf"
    assert "Keep the current setup (xai)" in "\n".join(out)


def test_invalid_hotkey_is_asked_again(tmp_path):
    ask = Script("", "", "control+alt+notakey", "control+alt+b", "", "", "", "", "", "")
    wizard, out = _wizard(tmp_path, ask)
    assert wizard.run() == 0
    assert tomllib.loads((tmp_path / "config.toml").read_text())["hotkey"]["key"] == "control+alt+b"


def test_groq_asks_for_a_voice_provider(tmp_path):
    ask = Script("2", "groq", "elevenlabs", "", "", "", "", "", "", "", "")
    wizard, _ = _wizard(tmp_path, ask, secret=Script("gsk", "el"))
    assert wizard.run() == 0
    config = tomllib.loads((tmp_path / "config.toml").read_text())
    assert config["stt"]["provider"] == "groq"
    assert config["tts"]["provider"] == "elevenlabs"
    assert config["providers"]["groq"]["api_key"] == "gsk"
    assert config["providers"]["elevenlabs"]["api_key"] == "el"


def test_hosted_signs_in_after_saving(tmp_path):
    calls = []
    ask = Script("1", "", "", "", "", "", "", "", "")
    wizard, _ = _wizard(tmp_path, ask, login=lambda: calls.append((tmp_path / "config.toml").exists()) or True)
    assert wizard.run() == 0
    assert calls == [True]


def test_ctrl_c_changes_nothing(tmp_path):
    ask = Script("2", "xai", KeyboardInterrupt())
    wizard, out = _wizard(tmp_path, ask, secret=Script("k"))
    assert wizard.run() == 1
    assert not (tmp_path / "config.toml").exists()
    assert "Setup cancelled; nothing was changed." in out


def test_declining_to_save_changes_nothing(tmp_path):
    ask = Script("2", "xai", "", "", "", "", "", "", "", "n")
    wizard, _ = _wizard(tmp_path, ask, secret=Script("k"))
    assert wizard.run() == 1
    assert not (tmp_path / "config.toml").exists()


def test_no_key_yet_says_so(tmp_path):
    ask = Script("", "", "", "", "", "", "", "")
    wizard, out = _wizard(tmp_path, ask)
    assert wizard.run() == 0
    assert (tmp_path / "config.toml").exists()
    assert any("can't start yet" in line for line in out)


def test_setup_needs_a_terminal(monkeypatch, capsys):
    monkeypatch.setattr("sys.stdin", open("/dev/null") if __import__("os").name == "posix" else None)
    assert setup_wizard.main() == 2
    assert "interactive" in capsys.readouterr().err
