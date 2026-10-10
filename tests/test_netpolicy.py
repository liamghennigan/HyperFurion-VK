"""A local server is reached directly: never through a proxy, and a redirect
from it is never followed (DESIGN A2). Otherwise a corporate proxy, or a
local reverse proxy that redirects, would receive Kai's question while Kai
says everything runs on this computer."""

import http.server
import json
import threading
from unittest import mock

import pytest
import requests

from voice_keyboard.config import _default_config_with_paths
from voice_keyboard.llm import LLMClient, create_llm_client
from voice_keyboard.netpolicy import NO_PROXIES
from voice_keyboard.recall import Embedder, create_embedder
from voice_keyboard.stt import BufferedRESTSTTClient, ChunkedRESTAdapter, create_stt_client
from voice_keyboard.tts import TTSClient, create_tts_client

DIRECT = {"proxies": NO_PROXIES, "allow_redirects": False}


def _ok(payload: dict) -> mock.Mock:
    response = mock.Mock(status_code=200, headers={})
    response.json.return_value = payload
    response.content = b"MP3"
    return response


def _chat_ok() -> mock.Mock:
    return _ok({"choices": [{"message": {"content": "hi"}}]})


def _local_speech_config(**flow) -> dict:
    cfg = _default_config_with_paths()
    cfg["stt"]["provider"] = "openai"
    cfg["tts"]["provider"] = "openai"
    cfg["providers"]["openai"]["base_url"] = "http://127.0.0.1:8000/v1"
    cfg["flow"].update(flow)
    return cfg


# ── the clients pass no proxies and no redirects to a local server ──────


@pytest.mark.parametrize("url, direct", [
    ("http://127.0.0.1:8080/v1", True),
    ("http://localhost:11434/v1", True),
    ("https://api.x.ai/v1", False),
    ("http://127.evil.com/v1", False),
])
def test_llm_client(url: str, direct: bool) -> None:
    client = LLMClient(base_url=url, api_key="k", model="m")
    with mock.patch("voice_keyboard.llm.requests.post", return_value=_chat_ok()) as post:
        client.complete("hello")
    kwargs = post.call_args.kwargs
    if direct:
        assert kwargs["proxies"] == NO_PROXIES and kwargs["allow_redirects"] is False
    else:
        assert "proxies" not in kwargs and "allow_redirects" not in kwargs


def test_llm_factory_counts_local_hosts() -> None:
    cfg = _default_config_with_paths()
    cfg["llm"].update(provider="openai", base_url="http://gpu-box:8080/v1", model="m")
    cfg["assistant"]["local_hosts"] = ["gpu-box"]
    with mock.patch("voice_keyboard.llm.requests.post", return_value=_chat_ok()) as post:
        create_llm_client(cfg).complete("hello")
    assert post.call_args.kwargs["proxies"] == NO_PROXIES
    cfg["assistant"]["local_hosts"] = []
    with mock.patch("voice_keyboard.llm.requests.post", return_value=_chat_ok()) as post:
        create_llm_client(cfg).complete("hello")
    assert "proxies" not in post.call_args.kwargs


def test_stt_client_and_its_interim_client() -> None:
    adapter = create_stt_client(_local_speech_config(live_rest="always"))
    assert isinstance(adapter, ChunkedRESTAdapter)
    for client in (adapter._inner, adapter._interim):
        session = mock.Mock()
        session.post.return_value = _ok({"text": "hello"})
        client._session = session
        assert client._transcribe_wav(b"RIFF") == "hello"
        kwargs = session.post.call_args.kwargs
        assert kwargs["proxies"] == NO_PROXIES and kwargs["allow_redirects"] is False


def test_stt_session_ignores_the_environment() -> None:
    client = BufferedRESTSTTClient(
        provider="openai", api_key="", base_url="http://127.0.0.1:8000/v1"
    )
    assert client.session.trust_env is False
    online = BufferedRESTSTTClient(provider="groq", api_key="k")
    assert online.session.trust_env is True


def test_online_stt_keeps_normal_requests() -> None:
    client = BufferedRESTSTTClient(provider="groq", api_key="k")
    session = mock.Mock()
    session.post.return_value = _ok({"text": "hello"})
    client._session = session
    client._transcribe_wav(b"RIFF")
    assert "proxies" not in session.post.call_args.kwargs


def test_tts_client() -> None:
    client = create_tts_client(_local_speech_config())
    session = mock.Mock()
    session.post.return_value = _ok({})
    client._session = session
    client._synthesize_openai("hello")
    kwargs = session.post.call_args.kwargs
    assert kwargs["proxies"] == NO_PROXIES and kwargs["allow_redirects"] is False
    online = TTSClient(api_key="k", provider="openai")
    assert online._direct is False


def test_embedder() -> None:
    cfg = _default_config_with_paths()
    cfg["recall"].update(base_url="http://localhost:11434/v1", model="nomic-embed-text")
    embedder = create_embedder(cfg)
    response = _ok({"data": [{"embedding": [1.0]}]})
    with mock.patch("voice_keyboard.recall.requests.post", return_value=response) as post:
        embedder.embed(["a"])
    assert post.call_args.kwargs["proxies"] == NO_PROXIES
    assert Embedder(base_url="https://api.openai.com/v1", model="m")._direct is False


def test_a_redirect_from_a_local_server_is_an_error() -> None:
    client = LLMClient(base_url="http://127.0.0.1:8080/v1", api_key="", model="m")
    redirect = mock.Mock(status_code=307, headers={"location": "https://evil.example/v1"})
    with mock.patch("voice_keyboard.llm.requests.post", return_value=redirect):
        with pytest.raises(RuntimeError, match="redirect"):
            client.complete("hello")


# ── for real: a proxy in the environment never sees a local request ─────


class _Server:
    """A local OpenAI-compatible stub; `redirect` makes it answer 307."""

    def __init__(self, redirect: str = ""):
        hits = self.hits = []

        class Handler(http.server.BaseHTTPRequestHandler):
            def do_POST(self):  # noqa: N802
                hits.append(self.path)
                length = int(self.headers.get("content-length", 0))
                self.rfile.read(length)
                if redirect:
                    self.send_response(307)
                    self.send_header("Location", redirect)
                    self.end_headers()
                    return
                body = json.dumps({"choices": [{"message": {"content": "direct"}}]}).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, *args):
                pass

        self.httpd = http.server.HTTPServer(("127.0.0.1", 0), Handler)
        self.url = f"http://127.0.0.1:{self.httpd.server_port}/v1"
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()

    def close(self):
        self.httpd.shutdown()
        self.httpd.server_close()


@pytest.fixture
def dead_proxy(monkeypatch: pytest.MonkeyPatch) -> str:
    """Every proxy variable pointing at a port nothing listens on."""
    proxy = "http://127.0.0.1:9"
    for name in ("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "http_proxy", "https_proxy", "all_proxy"):
        monkeypatch.setenv(name, proxy)
    for name in ("NO_PROXY", "no_proxy"):
        monkeypatch.delenv(name, raising=False)
    return proxy


def test_local_llm_bypasses_a_proxy(dead_proxy: str) -> None:
    server = _Server()
    try:
        # Plain requests would go to the dead proxy...
        with pytest.raises(requests.RequestException):
            requests.post(server.url + "/chat/completions", json={}, timeout=3)
        # ...the client goes straight to the local server.
        client = LLMClient(base_url=server.url, api_key="", model="m", timeout=5)
        assert client.complete("hello") == "direct"
        assert server.hits == ["/v1/chat/completions"]
    finally:
        server.close()


def test_local_llm_does_not_follow_a_redirect() -> None:
    target = _Server()
    server = _Server(redirect=target.url + "/chat/completions")
    try:
        client = LLMClient(base_url=server.url, api_key="", model="m", timeout=5)
        with pytest.raises(RuntimeError, match="redirect"):
            client.complete("hello")
        assert target.hits == []
    finally:
        server.close()
        target.close()
