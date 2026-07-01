import json
import os
import socket
import tempfile
import threading
import time
from pathlib import Path

import pytest

from voice_keyboard.ipc import IPCClient, IPCServer, recv_all


def _unix_stream_sockets_available() -> bool:
    with tempfile.TemporaryDirectory() as tmp:
        path = str(Path(tmp) / "probe.sock")
        sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        try:
            sock.bind(path)
            return True
        except PermissionError:
            return False
        finally:
            sock.close()


pytestmark = pytest.mark.skipif(
    not _unix_stream_sockets_available(),
    reason="AF_UNIX stream sockets are blocked in this sandbox",
)


class TestIPC:
    @pytest.fixture
    def socket_path(self, tmp_path: Path) -> str:
        return str(tmp_path / "voice-keyboard.sock")

    def test_server_client_round_trip(self, socket_path: str) -> None:
        server = IPCServer(socket_path)
        server.start()

        def handler():
            conn = server.accept()
            data = recv_all(conn)
            msg = json.loads(data.decode("utf-8"))
            response = {"status": "ok", "echo": msg}
            conn.sendall(json.dumps(response).encode("utf-8"))
            conn.close()

        t = threading.Thread(target=handler, daemon=True)
        t.start()
        time.sleep(0.05)

        client = IPCClient(socket_path)
        response = client.send_command("start", {"foo": "bar"})
        assert response["status"] == "ok"
        assert response["echo"]["command"] == "start"
        assert response["echo"]["payload"]["foo"] == "bar"

        server.stop()
        assert not os.path.exists(socket_path)

    def test_recv_all_handles_large_payload(self, socket_path: str) -> None:
        """Verify recv_all reassembles a message larger than 4096 bytes."""
        server = IPCServer(socket_path)
        server.start()
        big_text = "x" * 20_000

        def handler():
            conn = server.accept()
            data = recv_all(conn)
            msg = json.loads(data.decode("utf-8"))
            response = {"status": "ok", "text": msg["payload"]["text"]}
            conn.sendall(json.dumps(response).encode("utf-8"))
            conn.close()

        t = threading.Thread(target=handler, daemon=True)
        t.start()
        time.sleep(0.05)

        client = IPCClient(socket_path)
        response = client.send_command("tts", {"text": big_text})
        assert response["status"] == "ok"
        assert response["text"] == big_text
        server.stop()

    def test_client_raises_on_empty_response(self, socket_path: str) -> None:
        server = IPCServer(socket_path)
        server.start()

        def handler():
            conn = server.accept()
            recv_all(conn)
            conn.close()

        t = threading.Thread(target=handler, daemon=True)
        t.start()
        time.sleep(0.05)

        client = IPCClient(socket_path)
        with pytest.raises(RuntimeError, match="closed connection without a response"):
            client.send_command("status")
        server.stop()

    def test_server_refuses_to_listen_on_occupied_socket(self, socket_path: str) -> None:
        server = IPCServer(socket_path)
        server.start()
        with pytest.raises(RuntimeError, match="Another daemon is already listening"):
            another = IPCServer(socket_path)
            another.start()
        server.stop()

    def test_server_removes_stale_socket(self, socket_path: str) -> None:
        # Create a stale socket file with no listener.
        parent = Path(socket_path).parent
        parent.mkdir(parents=True, exist_ok=True)
        Path(socket_path).touch()
        server = IPCServer(socket_path)
        server.start()
        assert os.path.exists(socket_path)
        server.stop()

    def test_client_connect_fails_cleanly_when_server_absent(self, socket_path: str) -> None:
        client = IPCClient(socket_path)
        with pytest.raises((ConnectionRefusedError, FileNotFoundError)):
            client.send_command("status")

    def test_client_per_command_timeout_override(self, socket_path: str) -> None:
        """send_command(timeout=...) must override the client default."""
        server = IPCServer(socket_path)
        server.start()

        ready = threading.Event()

        def handler():
            conn = server.accept()
            ready.set()
            # Sleep briefly without responding. The client will time out
            # waiting for a response and close its socket; this thread will
            # then exit when join() is called.
            time.sleep(1)
            conn.close()

        t = threading.Thread(target=handler, daemon=True)
        t.start()
        ready.wait(timeout=2)

        client = IPCClient(socket_path, timeout=10.0)
        # With a 0.2s per-call timeout and a silent server, we should time out.
        with pytest.raises(TimeoutError):
            client.send_command("status", timeout=0.2)
        t.join(timeout=3)
        server.stop()

        # Second round: a fresh server with a prompt handler succeeds.
        server2 = IPCServer(socket_path)
        server2.start()
        ready2 = threading.Event()

        def handler2():
            conn = server2.accept()
            ready2.set()
            recv_all(conn)
            conn.sendall(b'{"status": "ok"}')
            conn.close()

        t2 = threading.Thread(target=handler2, daemon=True)
        t2.start()
        ready2.wait(timeout=2)
        response = client.send_command("status", timeout=5.0)
        assert response["status"] == "ok"
        server2.stop()
