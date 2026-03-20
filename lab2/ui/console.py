import os
import threading

from protocol.messages import MessageType, unpack_text, unpack_file
from network.connection import Connection
from network.client import connect_to_peer
from network.server import PeerServer

RECEIVED_DIR = "received_files"


class ConsoleUI:
    def __init__(self, nickname: str, server: PeerServer):
        self.nickname = nickname
        self.server = server
        self.peers: list[Connection] = []
        self._lock = threading.Lock()

        self.server.on_new_connection = self._handle_incoming

        os.makedirs(RECEIVED_DIR, exist_ok=True)

    def _handle_incoming(self, conn: Connection) -> None:
        conn.start_recv_loop(self._on_message, self._on_disconnect)

    def _on_message(
        self, conn: Connection, msg_type: MessageType, payload: bytes
    ) -> None:
        if msg_type == MessageType.NICKNAME:
            conn.nickname = payload.decode("utf-8")
            with self._lock:
                already_registered = conn in self.peers
                if not already_registered:
                    self.peers.append(conn)
            print(f"\n[+] {conn} подключился")
            if not already_registered:
                conn.send_nickname(self.nickname)
        elif msg_type == MessageType.TEXT:
            text = unpack_text(payload)
            print(f"\n[{conn.nickname}]: {text}")
        elif msg_type == MessageType.FILE:
            filename, data = unpack_file(payload)
            save_path = os.path.join(RECEIVED_DIR, filename)
            # Avoid overwriting existing files
            base, ext = os.path.splitext(filename)
            counter = 1
            while os.path.exists(save_path):
                save_path = os.path.join(RECEIVED_DIR, f"{base}_{counter}{ext}")
                counter += 1
            with open(save_path, "wb") as f:
                f.write(data)
            print(
                f"\n[{conn.nickname}] отправил файл: {filename} -> сохранён в {save_path}"
            )

    def _on_disconnect(self, conn: Connection) -> None:
        with self._lock:
            if conn in self.peers:
                self.peers.remove(conn)
        print(f"\n[-] {conn} отключился")

    def _broadcast(self, action) -> None:
        with self._lock:
            peers = list(self.peers)
        for peer in peers:
            try:
                action(peer)
            except (ConnectionError, OSError):
                self._on_disconnect(peer)

    def run(self) -> None:
        print(f"Сервер запущен на {self.server.host}:{self.server.port}")
        print("Введите /help для списка команд\n")

        while True:
            try:
                line = input()
            except (EOFError, KeyboardInterrupt):
                break

            line = line.strip()
            if not line:
                continue

            if line == "/quit":
                break
            elif line == "/help":
                self._print_help()
            elif line == "/peers":
                self._print_peers()
            elif line.startswith("/connect "):
                self._cmd_connect(line)
            elif line.startswith("/file "):
                self._cmd_file(line)
            elif line.startswith("/msg "):
                text = line[5:]
                self._broadcast(lambda p, t=text: p.send_text(t))
            else:
                # Любой текст без команды — отправить как сообщение
                self._broadcast(lambda p, t=line: p.send_text(t))

        self._shutdown()

    def _cmd_connect(self, line: str) -> None:
        parts = line.split()
        if len(parts) != 3:
            print("Использование: /connect <host> <port>")
            return
        host = parts[1]
        try:
            port = int(parts[2])
        except ValueError:
            print("Порт должен быть числом")
            return
        try:
            conn = connect_to_peer(host, port, self.nickname)
            with self._lock:
                self.peers.append(conn)
            conn.start_recv_loop(self._on_message, self._on_disconnect)
            print(f"[+] Подключено к {host}:{port}")
        except (ConnectionError, OSError) as e:
            print(f"Ошибка подключения: {e}")

    def _cmd_file(self, line: str) -> None:
        filepath = line[6:].strip()
        if not os.path.isfile(filepath):
            print(f"Файл не найден: {filepath}")
            return
        self._broadcast(lambda p, fp=filepath: p.send_file(fp))
        print(f"Файл отправлен: {filepath}")

    def _print_peers(self) -> None:
        with self._lock:
            if not self.peers:
                print("Нет подключённых пиров")
                return
            print("Подключённые пиры:")
            for i, p in enumerate(self.peers, 1):
                print(f"  {i}. {p}")

    def _print_help(self) -> None:
        print(
            "/connect <host> <port> — подключиться к пиру\n"
            "/msg <текст>          — отправить сообщение\n"
            "/file <путь>          — отправить файл\n"
            "/peers                — список пиров\n"
            "/help                 — справка\n"
            "/quit                 — выход\n"
            "Или просто введите текст для отправки сообщения"
        )

    def _shutdown(self) -> None:
        with self._lock:
            for p in self.peers:
                p.close()
            self.peers.clear()
        self.server.stop()
        print("Завершение работы...")
