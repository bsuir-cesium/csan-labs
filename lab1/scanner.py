import ipaddress
import platform
import re
import socket
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed

MAX_NETWORK_SIZE = 4096  # /20, больше — урезаем до /24
PING_TIMEOUT = 2
PING_WORKERS = 100
PORT_TIMEOUT = 0.5
PORT_WORKERS = 50

# Наиболее распространённые порты для сканирования
COMMON_PORTS = {
    21: "FTP",
    22: "SSH",
    23: "Telnet",
    25: "SMTP",
    53: "DNS",
    80: "HTTP",
    110: "POP3",
    111: "RPCbind",
    135: "MSRPC",
    139: "NetBIOS",
    143: "IMAP",
    443: "HTTPS",
    445: "SMB",
    993: "IMAPS",
    995: "POP3S",
    1433: "MSSQL",
    1723: "PPTP",
    3306: "MySQL",
    3389: "RDP",
    5432: "PostgreSQL",
    5900: "VNC",
    6379: "Redis",
    8080: "HTTP-Alt",
    8443: "HTTPS-Alt",
    27017: "MongoDB",
}


def get_interfaces():
    """Получить список сетевых интерфейсов с IP, маской и MAC-адресом."""
    system = platform.system()

    if system in ("Darwin", "Linux"):
        return _get_interfaces_unix(system)
    elif system == "Windows":
        return _get_interfaces_windows()
    else:
        print(f"Неподдерживаемая ОС: {system}")
        return []


def _get_interfaces_unix(system):
    """Парсинг ifconfig для macOS/Linux."""
    try:
        output = subprocess.check_output(
            ["ifconfig"], text=True, stderr=subprocess.DEVNULL
        )
    except (subprocess.CalledProcessError, FileNotFoundError):
        return []

    interfaces = []
    blocks = re.split(r"(?=^\S)", output, flags=re.MULTILINE)

    for block in blocks:
        if not block.strip():
            continue

        name_match = re.match(r"^(\S+?):", block)
        if not name_match:
            continue
        name = name_match.group(1)

        if name.startswith("lo"):
            continue

        # MAC-адрес
        mac_match = re.search(r"ether\s+([0-9a-f:]{17})", block)
        if not mac_match:
            mac_match = re.search(r"HWaddr\s+([0-9a-fA-F:]{17})", block)
        mac = mac_match.group(1).lower() if mac_match else None

        # IPv4 и маска
        if system == "Darwin":
            ip_match = re.search(
                r"inet\s+(\d+\.\d+\.\d+\.\d+)\s+netmask\s+(0x[0-9a-f]+)", block
            )
        else:
            ip_match = re.search(
                r"inet\s+(\d+\.\d+\.\d+\.\d+)\s+netmask\s+(\d+\.\d+\.\d+\.\d+)",
                block,
            )
            if not ip_match:
                ip_match = re.search(
                    r"inet addr:(\d+\.\d+\.\d+\.\d+)\s+.*Mask:(\d+\.\d+\.\d+\.\d+)",
                    block,
                )

        if not ip_match:
            continue

        ip_addr = ip_match.group(1)
        mask_raw = ip_match.group(2)

        # Конвертация маски из hex (macOS) в десятичный формат
        if mask_raw.startswith("0x"):
            mask_int = int(mask_raw, 16)
            mask = "{}.{}.{}.{}".format(
                (mask_int >> 24) & 0xFF,
                (mask_int >> 16) & 0xFF,
                (mask_int >> 8) & 0xFF,
                mask_int & 0xFF,
            )
        else:
            mask = mask_raw

        interfaces.append(
            {"name": name, "ip": ip_addr, "mask": mask, "mac": mac or "unknown"}
        )

    return interfaces


def _get_interfaces_windows():
    """Парсинг ipconfig /all для Windows."""
    try:
        output = subprocess.check_output(
            ["ipconfig", "/all"], text=True, stderr=subprocess.DEVNULL
        )
    except (subprocess.CalledProcessError, FileNotFoundError):
        return []

    interfaces = []
    blocks = re.split(r"\r?\n(?=\S)", output)

    for block in blocks:
        name_match = re.match(r"(.+?):", block)
        ip_match = re.search(r"IPv4.*?:\s*(\d+\.\d+\.\d+\.\d+)", block)
        mask_match = re.search(r"Subnet Mask.*?:\s*(\d+\.\d+\.\d+\.\d+)", block)
        mac_match = re.search(r"Physical Address.*?:\s*([0-9A-Fa-f-]{17})", block)

        if not (ip_match and mask_match):
            continue

        ip_addr = ip_match.group(1)
        if ip_addr.startswith("127."):
            continue

        interfaces.append(
            {
                "name": name_match.group(1).strip() if name_match else "unknown",
                "ip": ip_addr,
                "mask": mask_match.group(1),
                "mac": (
                    mac_match.group(1).replace("-", ":").lower()
                    if mac_match
                    else "unknown"
                ),
            }
        )

    return interfaces


def get_network_hosts(ip_addr, mask):
    """Вычислить список хостов в подсети на основе IP и маски."""
    network = ipaddress.IPv4Network(f"{ip_addr}/{mask}", strict=False)

    if network.num_addresses > MAX_NETWORK_SIZE:
        print(
            f"  Сеть {network} слишком большая "
            f"({network.num_addresses} адресов), ограничиваем до /24"
        )
        network = ipaddress.IPv4Network(f"{ip_addr}/24", strict=False)

    return [str(host) for host in network.hosts()]


def ping_host(ip_addr):
    """Пинг одного хоста. Возвращает IP если хост доступен, иначе None."""
    system = platform.system()
    if system == "Windows":
        cmd = ["ping", "-n", "1", "-w", "500", ip_addr]
    else:
        cmd = ["ping", "-c", "1", "-W", "1", ip_addr]

    try:
        result = subprocess.run(
            cmd,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=PING_TIMEOUT,
        )
        if result.returncode == 0:
            return ip_addr
    except subprocess.TimeoutExpired:
        pass
    return None


def get_arp_table():
    """Прочитать ARP-таблицу системы."""
    try:
        output = subprocess.check_output(
            ["arp", "-a"], text=True, stderr=subprocess.DEVNULL
        )
    except (subprocess.CalledProcessError, FileNotFoundError):
        return {}

    arp_entries = {}
    for line in output.splitlines():
        # macOS/Linux: hostname (ip) at mac on iface
        match = re.search(
            r"\((\d+\.\d+\.\d+\.\d+)\)\s+at\s+([0-9a-f:]{11,17})", line, re.IGNORECASE
        )
        if match:
            ip_addr = match.group(1)
            mac = match.group(2).lower()
            if mac not in ("(incomplete)", "ff:ff:ff:ff:ff:ff"):
                arp_entries[ip_addr] = mac
            continue

        # Windows: ip   mac   type
        match = re.search(
            r"(\d+\.\d+\.\d+\.\d+)\s+([0-9a-f-]{17})\s+dynamic", line, re.IGNORECASE
        )
        if match:
            ip_addr = match.group(1)
            mac = match.group(2).replace("-", ":").lower()
            arp_entries[ip_addr] = mac

    return arp_entries


def resolve_hostname(ip_addr):
    """Определить имя хоста по IP через обратный DNS."""
    try:
        return socket.gethostbyaddr(ip_addr)[0]
    except (socket.herror, socket.gaierror, OSError):
        return ""


def scan_port(ip_addr, port):
    """Проверить один порт через TCP connect. Возвращает порт если открыт, иначе None."""
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.settimeout(PORT_TIMEOUT)
    try:
        result = sock.connect_ex((ip_addr, port))
        if result == 0:
            return port
    except OSError:
        pass
    finally:
        sock.close()
    return None


def scan_ports(ip_addr):
    """Сканировать распространённые порты на хосте. Возвращает список открытых портов."""
    open_ports = []
    with ThreadPoolExecutor(max_workers=PORT_WORKERS) as executor:
        futures = {
            executor.submit(scan_port, ip_addr, port): port
            for port in COMMON_PORTS
        }
        for future in as_completed(futures):
            result = future.result()
            if result is not None:
                open_ports.append(result)
    return sorted(open_ports)


def format_ports(ports):
    """Форматировать список портов для вывода."""
    if not ports:
        return "нет открытых"
    return ", ".join(f"{p}/{COMMON_PORTS.get(p, '?')}" for p in ports)


def scan_network(hosts):
    """Параллельный пинг всех хостов в подсети."""
    alive = []
    total = len(hosts)
    done = 0
    workers = min(PING_WORKERS, total)

    with ThreadPoolExecutor(max_workers=workers) as executor:
        futures = {executor.submit(ping_host, ip): ip for ip in hosts}
        for future in as_completed(futures):
            done += 1
            print(f"\r  Сканирование: {done}/{total}", end="", flush=True)
            result = future.result()
            if result:
                alive.append(result)

    print()
    return alive


def print_table(rows):
    """Вывести таблицу узлов."""
    print(f"  {'IP':<18} {'MAC':<20} {'Имя хоста'}")
    print(f"  {'-' * 18} {'-' * 20} {'-' * 30}")
    for ip_addr, mac, name, ports in rows:
        print(f"  {ip_addr:<18} {mac:<20} {name}")
        if ports is not None:
            print(f"  {'':18} {'':20} Порты: {format_ports(ports)}")


def main():
    print("=" * 68)
    print("            СКАНЕР ЛОКАЛЬНОЙ СЕТИ")
    print("=" * 68)

    hostname = socket.gethostname()
    print(f"\nИмя компьютера: {hostname}")

    interfaces = get_interfaces()
    if not interfaces:
        print("Активные сетевые интерфейсы не найдены.")
        sys.exit(1)

    print(f"Активных интерфейсов: {len(interfaces)}\n")

    for iface in interfaces:
        network = ipaddress.IPv4Network(f"{iface['ip']}/{iface['mask']}", strict=False)

        print(f"--- Интерфейс: {iface['name']} ---")
        print(f"  IP-адрес:  {iface['ip']}")
        print(f"  Маска:     {iface['mask']}")
        print(f"  MAC-адрес: {iface['mac']}")
        print(f"  Сеть:      {network}")
        print()

        hosts = get_network_hosts(iface["ip"], iface["mask"])
        print(f"  Сканирование {len(hosts)} адресов...")

        alive_hosts = scan_network(hosts)

        # Считать ARP-таблицу после пинга
        arp = get_arp_table()

        rows = []
        # Собственный компьютер — первой строкой
        print("\n  Сканирование портов на активных хостах...")
        local_ports = scan_ports(iface["ip"])
        rows.append(
            (iface["ip"], iface["mac"], f"{hostname} (этот компьютер)", local_ports)
        )

        remote_hosts = sorted(
            [ip for ip in alive_hosts if ip != iface["ip"]],
            key=lambda x: ipaddress.IPv4Address(x),
        )
        for i, ip in enumerate(remote_hosts, 1):
            print(
                f"\r  Порты: {i}/{len(remote_hosts)} ({ip})",
                end="",
                flush=True,
            )
            mac = arp.get(ip, "unknown")
            name = resolve_hostname(ip)
            open_ports = scan_ports(ip)
            rows.append((ip, mac, name, open_ports))
        if remote_hosts:
            print()

        print(f"\n  Найдено активных узлов: {len(rows)}\n")
        print_table(rows)
        print()

    print("=" * 68)
    print("Сканирование завершено.")


if __name__ == "__main__":
    main()
