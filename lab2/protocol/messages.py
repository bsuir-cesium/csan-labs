import os
import socket
import struct
from enum import IntEnum


class MessageType(IntEnum):
    TEXT = 0x01
    FILE = 0x02
    NICKNAME = 0x03


HEADER_FORMAT = "!BI"  # 1 byte type + 4 bytes payload length
HEADER_SIZE = struct.calcsize(HEADER_FORMAT)


def pack_text(text: str) -> bytes:
    payload = text.encode("utf-8")
    header = struct.pack(HEADER_FORMAT, MessageType.TEXT, len(payload))
    return header + payload


def pack_file(filepath: str) -> bytes:
    filename = os.path.basename(filepath).encode("utf-8")
    with open(filepath, "rb") as f:
        file_data = f.read()
    payload = struct.pack("!H", len(filename)) + filename + file_data
    header = struct.pack(HEADER_FORMAT, MessageType.FILE, len(payload))
    return header + payload


def pack_nickname(name: str) -> bytes:
    payload = name.encode("utf-8")
    header = struct.pack(HEADER_FORMAT, MessageType.NICKNAME, len(payload))
    return header + payload


def send_message(sock: socket.socket, data: bytes) -> None:
    sock.sendall(data)


def recv_exactly(sock: socket.socket, n: int) -> bytes:
    buf = bytearray()
    while len(buf) < n:
        chunk = sock.recv(n - len(buf))
        if not chunk:
            raise ConnectionError("Соединение разорвано")
        buf.extend(chunk)
    return bytes(buf)


def recv_message(sock: socket.socket) -> tuple[MessageType, bytes]:
    header = recv_exactly(sock, HEADER_SIZE)
    msg_type, payload_len = struct.unpack(HEADER_FORMAT, header)
    payload = recv_exactly(sock, payload_len) if payload_len > 0 else b""
    return MessageType(msg_type), payload


def unpack_text(payload: bytes) -> str:
    return payload.decode("utf-8")


def unpack_file(payload: bytes) -> tuple[str, bytes]:
    filename_len = struct.unpack("!H", payload[:2])[0]
    filename = payload[2 : 2 + filename_len].decode("utf-8")
    file_data = payload[2 + filename_len :]
    return filename, file_data
