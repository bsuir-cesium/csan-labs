import socket

from network.connection import Connection


def connect_to_peer(host: str, port: int, nickname: str) -> Connection:
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.connect((host, port))
    conn = Connection(sock, (host, port))
    conn.send_nickname(nickname)
    return conn
