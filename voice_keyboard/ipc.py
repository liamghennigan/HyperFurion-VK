import json
import logging
import os
import socket
import sys
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

# Default recv timeout for a single IPC command response. Generous enough to
# cover the slowest daemon command (tts: up to 30s + playback). For `start`,
# the client passes a shorter per-command timeout via send_command(timeout=).
CLIENT_RECV_TIMEOUT = 35.0


def _config_dir() -> Path:
    xdg = os.environ.get("XDG_CONFIG_HOME", "")
    if xdg:
        return Path(xdg) / "voice-keyboard"
    return Path.home() / ".config" / "voice-keyboard"


def _default_socket_path() -> str:
    if sys.platform == "win32":
        # Windows Python has no AF_UNIX; loopback TCP is the IPC transport.
        return "tcp:127.0.0.1:48765"
    return str(_config_dir() / "socket")


DEFAULT_SOCKET_PATH = _default_socket_path()


def parse_endpoint(socket_path: str) -> tuple[str, object]:
    """Resolve a socket_path into ("unix", path) or ("inet", (host, port)).

    Unix sockets are the default. `tcp:HOST:PORT` selects loopback TCP —
    the only option on Windows, where Python has no AF_UNIX.
    """
    if socket_path.startswith("tcp:"):
        rest = socket_path[len("tcp:"):]
        host, _, port = rest.rpartition(":")
        return "inet", (host or "127.0.0.1", int(port))
    return "unix", socket_path


def _connect_socket(socket_path: str, timeout: float | None = None) -> socket.socket:
    kind, target = parse_endpoint(socket_path)
    if kind == "inet":
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    else:
        sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    if timeout is not None:
        sock.settimeout(timeout)
    sock.connect(target)
    return sock


def recv_all(conn: socket.socket) -> bytes:
    """Read from `conn` until the peer half-closes (EOF). Returns all bytes."""
    chunks: list[bytes] = []
    while True:
        chunk = conn.recv(4096)
        if not chunk:
            break
        chunks.append(chunk)
    return b"".join(chunks)


class IPCServer:
    def __init__(self, socket_path: str = DEFAULT_SOCKET_PATH):
        self._socket_path = socket_path
        self._sock: Optional[socket.socket] = None

    def start(self) -> None:
        kind, target = parse_endpoint(self._socket_path)
        if kind == "inet":
            self._sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            self._sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            self._sock.bind(target)
            self._sock.listen(5)
            self._sock.setblocking(True)
            logger.info("IPC server listening on %s", self._socket_path)
            return

        socket_path = Path(self._socket_path)
        if socket_path.exists():
            try:
                test_sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
                test_sock.connect(self._socket_path)
                test_sock.close()
                raise RuntimeError(
                    f"Another daemon is already listening on {self._socket_path}"
                )
            except (ConnectionRefusedError, FileNotFoundError):
                pass

        socket_path.parent.mkdir(parents=True, mode=0o700, exist_ok=True)
        if socket_path.exists():
            os.unlink(socket_path)

        self._sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self._sock.bind(self._socket_path)
        os.chmod(self._socket_path, 0o600)
        self._sock.listen(5)
        self._sock.setblocking(True)
        logger.info("IPC server listening on %s", self._socket_path)

    def accept(self) -> socket.socket:
        if self._sock is None:
            raise RuntimeError("IPC server not started")
        conn, _addr = self._sock.accept()
        logger.debug("IPC connection accepted")
        return conn

    def stop(self) -> None:
        if self._sock is not None:
            self._sock.close()
            self._sock = None
        if parse_endpoint(self._socket_path)[0] == "inet":
            logger.info("IPC server stopped")
            return
        socket_path = Path(self._socket_path)
        if socket_path.exists():
            try:
                os.unlink(socket_path)
            except FileNotFoundError:
                pass
        logger.info("IPC server stopped")


class IPCClient:
    def __init__(
        self,
        socket_path: str = DEFAULT_SOCKET_PATH,
        timeout: float = CLIENT_RECV_TIMEOUT,
    ):
        self._socket_path = socket_path
        self._timeout = timeout

    def send_command(
        self,
        command: str,
        payload: Optional[dict] = None,
        timeout: Optional[float] = None,
    ) -> dict:
        msg = {"command": command}
        if payload is not None:
            msg["payload"] = payload
        sock = _connect_socket(
            self._socket_path, timeout if timeout is not None else self._timeout
        )
        try:
            sock.sendall(json.dumps(msg).encode("utf-8"))
            sock.shutdown(socket.SHUT_WR)
            response_data = recv_all(sock)
            if not response_data:
                raise RuntimeError("daemon closed connection without a response")
            return json.loads(response_data.decode("utf-8"))
        finally:
            sock.close()