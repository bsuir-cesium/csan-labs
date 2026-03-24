import socket
import threading
from collections.abc import Callable

from network.connection import Connection


class PeerServer:
    def __init__(self, host: str, port: int):
        self.host = host
        self.port = port
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.sock.bind((host, port))
        self._running = False
        self.on_new_connection: Callable[[Connection], None] | None = None

    def start(self) -> None:
        self._running = True
        self.sock.listen(5)
        t = threading.Thread(target=self._accept_loop, daemon=True)
        t.start()

    def _accept_loop(self) -> None:
        while self._running:
            try:
                client_sock, addr = self.sock.accept()
                conn = Connection(client_sock, addr)
                if self.on_new_connection:
                    self.on_new_connection(conn)
            except OSError:
                break

    def stop(self) -> None:
        self._running = False
        try:
            self.sock.close()
        except OSError:
            pass
