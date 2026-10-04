import errno
import json
import logging
import os
import secrets
import socket
import sys
from pathlib import Path
from typing import Optional

from voice_keyboard import paths

logger = logging.getLogger(__name__)

# Default recv timeout for a single IPC command response. Generous enough to
# cover the slowest daemon command (tts: up to 30s + playback). For `start`,
# the client passes a shorter per-command timeout via send_command(timeout=).
CLIENT_RECV_TIMEOUT = 35.0


def _config_dir() -> Path:
    return paths.config_dir()


def _default_socket_path() -> str:
    if sys.platform == "win32":
        # Windows Python has no AF_UNIX; loopback TCP is the IPC transport.
        # Port 0: each daemon binds a free port of its own and publishes it
        # with its token, so people signed in side by side (fast user
        # switching, Remote Desktop) never fight over one machine-wide port.
        return "tcp:127.0.0.1:0"
    return str(_config_dir() / "socket")


DEFAULT_SOCKET_PATH = _default_socket_path()


def _token_path() -> Path:
    # Machine-local (never roams with a Windows profile): it names this
    # machine's daemon and its port.
    return paths.state_dir() / "ipc-token"


def read_ipc_endpoint() -> tuple[str, int]:
    """(token, port) the running daemon published; ("", 0) when none."""
    try:
        raw = _token_path().read_text(encoding="utf-8").strip()
    except OSError:
        return "", 0
    try:
        data = json.loads(raw)
        return str(data.get("token", "")), int(data.get("port", 0))
    except (ValueError, TypeError, AttributeError):
        return raw, 0


def read_ipc_token() -> str:
    return read_ipc_endpoint()[0]


def parse_endpoint(socket_path: str) -> tuple[str, object]:
    """Resolve a socket_path into ("unix", path) or ("inet", (host, port)).

    Unix sockets are the default. `tcp:HOST:PORT` selects loopback TCP —
    the only option on Windows, where Python has no AF_UNIX. Port 0 means
    "whatever port the daemon published" (see resolve_endpoint).
    """
    if socket_path.startswith("tcp:"):
        rest = socket_path[len("tcp:"):]
        host, _, port = rest.rpartition(":")
        return "inet", (host or "127.0.0.1", int(port))
    return "unix", socket_path


def resolve_endpoint(socket_path: str) -> tuple[str, object]:
    """parse_endpoint, with port 0 replaced by the running daemon's port.
    No published port means no daemon: ConnectionRefusedError."""
    kind, target = parse_endpoint(socket_path)
    if kind == "inet" and target[1] == 0:
        port = read_ipc_endpoint()[1]
        if not port:
            raise ConnectionRefusedError(
                errno.ECONNREFUSED, "the daemon is not running (no published port)"
            )
        target = (target[0], port)
    return kind, target


def _connect_socket(socket_path: str, timeout: float | None = None) -> socket.socket:
    kind, target = resolve_endpoint(socket_path)
    if kind == "inet":
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    else:
        sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    if timeout is not None:
        sock.settimeout(timeout)
    try:
        sock.connect(target)
    except OSError:
        # connect() failing (daemon down — the common CLI path) must not leak
        # the freshly created fd; the caller's try/finally hasn't started yet.
        sock.close()
        raise
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


def _address_in_use(exc: OSError) -> bool:
    return exc.errno in {errno.EADDRINUSE, getattr(errno, "WSAEADDRINUSE", 10048)}


def _write_private(path: Path, text: str) -> None:
    """Write a 0600 file atomically: a client never reads half a token."""
    path.parent.mkdir(parents=True, mode=0o700, exist_ok=True)
    tmp = path.with_name(f".{path.name}.{os.getpid()}")
    tmp.write_text(text, encoding="utf-8")
    try:
        os.chmod(tmp, 0o600)
    except OSError:
        pass
    os.replace(tmp, path)


class IPCServer:
    def __init__(self, socket_path: str = DEFAULT_SOCKET_PATH):
        self._socket_path = socket_path
        self._sock: Optional[socket.socket] = None
        self._token: Optional[str] = None
        self._endpoint = socket_path

    @property
    def endpoint(self) -> str:
        """Where the server listens (with the real port once started)."""
        return self._endpoint

    @property
    def required_token(self) -> Optional[str]:
        """Session token clients must echo; set only on loopback TCP,
        where socket permissions can't gate access the way a 0600 Unix
        socket does — without it any local process could drive typing."""
        return self._token

    def start(self) -> None:
        kind, target = parse_endpoint(self._socket_path)
        if kind == "inet":
            self._sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            if sys.platform == "win32":
                # Windows SO_REUSEADDR lets a second socket bind a port that
                # is IN USE — a hijack. Exclusive use is the safe equivalent.
                self._sock.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
            else:
                self._sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            try:
                self._sock.bind(target)
            except OSError as exc:
                self._sock.close()
                self._sock = None
                if _address_in_use(exc):
                    raise RuntimeError(
                        f"Another daemon is already listening on {self._socket_path}"
                    ) from exc
                raise RuntimeError(
                    f"Could not open the command channel on {self._socket_path}:"
                    f" {exc.strerror or exc}"
                ) from exc
            self._sock.listen(5)
            self._sock.setblocking(True)
            host, port = self._sock.getsockname()[:2]
            self._endpoint = f"tcp:{host}:{port}"
            self._token = secrets.token_hex(16)
            _write_private(_token_path(), json.dumps({"token": self._token, "port": port}))
            logger.info("IPC server listening on %s", self._endpoint)
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
            if self._token is not None and read_ipc_token() == self._token:
                # Only our own: another daemon may have published since.
                try:
                    os.unlink(_token_path())
                except OSError:
                    pass
            self._token = None
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
        if parse_endpoint(self._socket_path)[0] == "inet":
            token = read_ipc_token()
            if token:
                msg["token"] = token
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