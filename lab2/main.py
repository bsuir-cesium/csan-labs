import sys

from network.server import PeerServer
from ui.console import ConsoleUI


def main() -> None:
    nickname = input("Введите никнейм: ").strip()
    if not nickname:
        nickname = "anonymous"

    port_str = input("Порт для прослушивания (по умолчанию 5000): ").strip()
    port = int(port_str) if port_str else 5000

    try:
        server = PeerServer("0.0.0.0", port)
    except OSError:
        print(f"Ошибка: порт {port} занят или недоступен", file=sys.stderr)
        sys.exit(1)

    server.start()

    ui = ConsoleUI(nickname, server)
    ui.run()


if __name__ == "__main__":
    main()
