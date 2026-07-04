"""The multiplayer keyboard: your phone as a roaming microphone.

EXPERIMENTAL, off by default ([remote_mic]). When enabled, the daemon
serves one HTTPS page on the LAN (self-signed certificate, token in the
URL — accept the browser warning once). The page captures the phone mic,
downsamples to 16 kHz mono PCM in an AudioWorklet, and streams it over
WSS; a RemoteAudioSource feeds those frames into a perfectly normal
dictation session on this machine. Start/stop lives on the phone page.

Voice never leaves your network: phone → this daemon → whatever STT
provider the config already uses. No relay involvement, no accounts.
"""

import asyncio
import json
import logging
import queue
import secrets
import ssl
import subprocess
import threading
from pathlib import Path
from typing import Callable, Optional
from urllib.parse import parse_qs, urlparse

from voice_keyboard.history import _state_dir

logger = logging.getLogger(__name__)

SILENCE_CHUNK = b"\x00" * 640  # 20 ms of 16 kHz mono s16le


class RemoteAudioSource:
    """Duck-types AudioCapture: start/stop/read_chunk/sample_rate, fed by
    websocket frames instead of PyAudio."""

    def __init__(self, sample_rate: int = 16000):
        self.sample_rate = sample_rate
        self.running = False
        self._frames: queue.Queue = queue.Queue(maxsize=256)

    def start(self) -> None:
        self.running = True

    def stop(self) -> None:
        self.running = False
        try:
            self._frames.put_nowait(b"")
        except queue.Full:
            pass

    def push(self, data: bytes) -> None:
        try:
            self._frames.put_nowait(bytes(data))
        except queue.Full:
            logger.debug("Remote mic queue full; dropping a frame")

    def read_chunk(self) -> bytes:
        try:
            chunk = self._frames.get(timeout=0.3)
        except queue.Empty:
            return SILENCE_CHUNK
        return chunk or SILENCE_CHUNK


def check_token(path: str, token: str) -> bool:
    """True when the request path carries the session token (?t=...)."""
    if not token:
        return False
    try:
        query = parse_qs(urlparse(path).query)
    except ValueError:
        return False
    return secrets.compare_digest(str(query.get("t", [""])[0]), token)


def ensure_certificate() -> tuple[Path, Path]:
    """A self-signed cert in the state dir — getUserMedia demands a secure
    context, and the LAN has no CA. Generated once, mode 600."""
    state = _state_dir()
    state.mkdir(parents=True, mode=0o700, exist_ok=True)
    cert = state / "remote-mic-cert.pem"
    key = state / "remote-mic-key.pem"
    if cert.exists() and key.exists():
        return cert, key
    subprocess.run(
        [
            "openssl", "req", "-x509", "-newkey", "rsa:2048",
            "-keyout", str(key), "-out", str(cert),
            "-days", "825", "-nodes", "-subj", "/CN=voice-keyboard",
        ],
        check=True,
        capture_output=True,
        timeout=30,
    )
    key.chmod(0o600)
    cert.chmod(0o600)
    return cert, key


PAGE_HTML = """<!doctype html>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>voice-keyboard — remote mic</title>
<style>
  body { font: 18px/1.5 system-ui; background:#111; color:#eee;
         display:flex; flex-direction:column; align-items:center;
         justify-content:center; min-height:100vh; margin:0; }
  button { font-size:1.6rem; padding:1.2rem 2.6rem; border-radius:1rem;
           border:0; background:#2c7; color:#052; }
  button.live { background:#e55; color:#fff; }
  #status { margin-top:1.2rem; opacity:.8; min-height:1.5em; }
</style>
<h1>remote mic</h1>
<button id="btn">start dictating</button>
<div id="status">audio stays on your network</div>
<script>
const token = new URLSearchParams(location.search).get("t") || "";
const btn = document.getElementById("btn");
const status = document.getElementById("status");
let ws = null, ctx = null, node = null, stream = null, live = false;

const WORKLET = `
registerProcessor("down16k", class extends AudioWorkletProcessor {
  constructor() { super(); this.acc = 0; this.buf = []; }
  process(inputs) {
    const ch = inputs[0][0];
    if (!ch) return true;
    const ratio = sampleRate / 16000;
    for (let i = 0; i < ch.length; i++) {
      this.acc += 1;
      if (this.acc >= ratio) {
        this.acc -= ratio;
        const s = Math.max(-1, Math.min(1, ch[i]));
        this.buf.push(s < 0 ? s * 0x8000 : s * 0x7fff);
        if (this.buf.length >= 320) {
          this.port.postMessage(Int16Array.from(this.buf).buffer);
          this.buf = [];
        }
      }
    }
    return true;
  }
});`;

async function start() {
  status.textContent = "connecting…";
  ws = new WebSocket(`wss://${location.host}/mic?t=${token}`);
  ws.binaryType = "arraybuffer";
  await new Promise((ok, no) => { ws.onopen = ok; ws.onerror = no; });
  stream = await navigator.mediaDevices.getUserMedia({ audio: true });
  ctx = new AudioContext();
  const url = URL.createObjectURL(new Blob([WORKLET], { type: "text/javascript" }));
  await ctx.audioWorklet.addModule(url);
  node = new AudioWorkletNode(ctx, "down16k");
  node.port.onmessage = (e) => { if (ws.readyState === 1) ws.send(e.data); };
  ctx.createMediaStreamSource(stream).connect(node);
  ws.send(JSON.stringify({ type: "start" }));
  live = true;
  btn.textContent = "stop";
  btn.classList.add("live");
  status.textContent = "listening — words land on the desktop";
  ws.onclose = () => { if (live) stop(); };
}

function stop() {
  live = false;
  try { if (ws && ws.readyState === 1) ws.send(JSON.stringify({ type: "stop" })); } catch (e) {}
  if (node) node.disconnect();
  if (ctx) ctx.close();
  if (stream) stream.getTracks().forEach((t) => t.stop());
  setTimeout(() => { try { ws && ws.close(); } catch (e) {} }, 300);
  btn.textContent = "start dictating";
  btn.classList.remove("live");
  status.textContent = "stopped";
}

btn.onclick = () => (live ? stop() : start().catch((e) => {
  status.textContent = "failed: " + e;
}));
</script>
"""


class RemoteMicServer:
    """One HTTPS/WSS port: the page for humans, the mic stream for audio.

    Runs its own asyncio loop on a thread; session start/stop crosses into
    the daemon loop through the callables it is constructed with (the same
    bridge pattern the IPC thread uses).
    """

    def __init__(
        self,
        *,
        port: int,
        token: str,
        on_start: Callable[[RemoteAudioSource], None],
        on_stop: Callable[[], None],
    ):
        self.port = port
        self.token = token or secrets.token_hex(8)
        self._on_start = on_start
        self._on_stop = on_stop
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._thread: Optional[threading.Thread] = None
        self._stop_event: Optional[asyncio.Event] = None
        self.source: Optional[RemoteAudioSource] = None

    def start(self) -> None:
        self._thread = threading.Thread(
            target=self._run, name="voice-keyboard-remote-mic", daemon=True
        )
        self._thread.start()

    def stop(self) -> None:
        if self._loop is not None and self._stop_event is not None:
            self._loop.call_soon_threadsafe(self._stop_event.set)
        if self._thread is not None:
            self._thread.join(timeout=3.0)
            self._thread = None

    def _run(self) -> None:
        try:
            asyncio.run(self._serve())
        except Exception:
            logger.exception("Remote mic server failed")

    async def _serve(self) -> None:
        import websockets

        cert, key = ensure_certificate()
        ssl_ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        ssl_ctx.load_cert_chain(certfile=str(cert), keyfile=str(key))

        self._loop = asyncio.get_running_loop()
        self._stop_event = asyncio.Event()

        async def handler(ws):
            path = getattr(getattr(ws, "request", None), "path", "") or getattr(ws, "path", "")
            if not check_token(path, self.token):
                await ws.close(4401, "bad token")
                return
            source: Optional[RemoteAudioSource] = None
            try:
                async for message in ws:
                    if isinstance(message, (bytes, bytearray)):
                        if source is not None:
                            source.push(bytes(message))
                        continue
                    try:
                        event = json.loads(message)
                    except ValueError:
                        continue
                    if event.get("type") == "start" and source is None:
                        source = RemoteAudioSource()
                        self.source = source
                        self._on_start(source)
                    elif event.get("type") == "stop" and source is not None:
                        self._on_stop()
                        source = None
                        self.source = None
            finally:
                if source is not None:
                    self._on_stop()
                    self.source = None

        def http_response(connection, request):
            # Non-websocket requests get the page (or a 403 without token).
            if request.path.startswith("/mic"):
                return None  # proceed with the websocket handshake
            try:
                if check_token(request.path, self.token):
                    return connection.respond(200, PAGE_HTML)
                return connection.respond(403, "missing or bad token\n")
            except Exception:  # older websockets: no respond(); let it 101/404
                return None

        async with websockets.serve(
            handler,
            "0.0.0.0",
            self.port,
            ssl=ssl_ctx,
            process_request=http_response,
            max_size=1 << 20,
        ):
            logger.info(
                "Remote mic ready: https://<this-machine>:%d/?t=%s "
                "(self-signed — accept the warning once)",
                self.port,
                self.token,
            )
            await self._stop_event.wait()
