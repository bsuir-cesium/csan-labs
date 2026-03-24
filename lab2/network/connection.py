from __future__ import annotations

import socket
import threading
from collections.abc import Callable

from protocol.messages import (
    MessageType,
    pack_file,
    pack_nickname,
    pack_text,
    recv_message,
    send_message,
)

type OnMessage = Callable[[Connection, MessageType, bytes], None]
type OnDisconnect = Callable[[Connection], None]


class Connection:
    def __init__(
        self,
        sock: socket.socket,
        address: tuple[str, int],
        nickname: str = "unknown",
    ):
        self.sock = sock
        self.address = address
        self.nickname = nickname
        self._closed = False

    def send_text(self, text: str) -> None:
        send_message(self.sock, pack_text(text))

    def send_file(self, filepath: str) -> None:
        send_message(self.sock, pack_file(filepath))

    def send_nickname(self, name: str) -> None:
        send_message(self.sock, pack_nickname(name))

    def recv_loop(self, on_message: OnMessage, on_disconnect: OnDisconnect) -> None:
        try:
            while not self._closed:
                msg_type, payload = recv_message(self.sock)
                on_message(self, msg_type, payload)
        except (ConnectionError, OSError):
            pass
        finally:
            if not self._closed:
                self._closed = True
                on_disconnect(self)

    def start_recv_loop(self, on_message: OnMessage, on_disconnect: OnDisconnect) -> None:
        t = threading.Thread(
            target=self.recv_loop,
            args=(on_message, on_disconnect),
            daemon=True,
        )
        t.start()

    def close(self) -> None:
        if not self._closed:
            self._closed = True
            try:
                self.sock.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
            self.sock.close()

    def __str__(self) -> str:
        return f"{self.nickname} ({self.address[0]}:{self.address[1]})"
