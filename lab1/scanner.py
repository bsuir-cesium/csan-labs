import ipaddress
import re
import socket
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed

PING_TIMEOUT = 2
PING_WORKERS = 100


def get_interfaces():
    try:
        output = subprocess.check_output(
            ["ifconfig"], text=True, stderr=subprocess.DEVNULL
        )
    except (subprocess.CalledProcessError, FileNotFoundError):
        return []

    interfaces = []
    blocks = re.split(r"(?=^\S)", output, flags=re.MULTILINE)

    # Интерфейсы, которые не нужно сканировать:
    # lo     — loopback
    # awdl   — Apple Wireless Direct Link (AirDrop)
    # llw    — Low Latency WLAN (AirDrop companion)
    # bridge — виртуальный мост (Thunderbolt Bridge, VM)
    # ap     — точка доступа (Personal Hotspot)
    # gif    — tunnel interface
    # stf    — 6to4 tunnel
    # anpi   — Apple Network Port Interface (диагностика)
    SKIP_PREFIXES = ("lo", "awdl", "llw", "bridge", "ap", "gif", "stf", "anpi")

    for block in blocks:
        if not block.strip():
            continue

        name_match = re.match(r"^(\S+?):", block)
        if not name_match:
            continue
        name = name_match.group(1)

        if name.startswith(SKIP_PREFIXES):
            continue

        # Пропускаем интерфейсы без флага RUNNING
        flags_match = re.search(r"flags=\d+<([^>]*)>", block)
        if not flags_match or "RUNNING" not in flags_match.group(1):
            continue

        mac_match = re.search(r"ether\s+([0-9a-f:]{17})", block)
        mac = mac_match.group(1) if mac_match else None

        # Два формата:
        #   обычный:       inet 172.20.10.6 netmask 0xfffffff0
        #   point-to-point: inet 172.19.0.1 --> 172.19.0.1 netmask 0xfffffff0
        ip_match = re.search(
            r"inet\s+(\d+\.\d+\.\d+\.\d+)\s+(?:-->\s+\S+\s+)?netmask\s+(0x[0-9a-f]+)",
            block,
        )
        if not ip_match:
            continue

        ip_addr = ip_match.group(1)
        mask_int = int(ip_match.group(2), 16)
        mask = "{}.{}.{}.{}".format(
            (mask_int >> 24) & 0xFF,
            (mask_int >> 16) & 0xFF,
            (mask_int >> 8) & 0xFF,
            mask_int & 0xFF,
        )

        # Определяем тип интерфейса
        if name.startswith("en"):
            iface_type = "Wi-Fi/Ethernet"
        elif name.startswith("utun"):
            iface_type = "VPN"
        elif name.startswith("veth") or name.startswith("docker"):
            iface_type = "Docker"
        elif name.startswith("vmnet"):
            iface_type = "VMware"
        elif name.startswith("vboxnet"):
            iface_type = "VirtualBox"
        else:
            iface_type = "Другой"

        interfaces.append(
            {
                "name": name,
                "type": iface_type,
                "ip": ip_addr,
                "mask": mask,
                "mac": mac or "unknown",
            }
        )

    return interfaces


def get_network_hosts(ip_addr, mask):
    network = ipaddress.IPv4Network(f"{ip_addr}/{mask}", strict=False)
    return [str(host) for host in network.hosts()]


def ping_host(ip_addr):
    try:
        result = subprocess.run(
            ["ping", "-c", "1", "-W", "1", ip_addr],
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
    try:
        output = subprocess.check_output(
            ["arp", "-a"], text=True, stderr=subprocess.DEVNULL
        )
    except (subprocess.CalledProcessError, FileNotFoundError):
        return {}

    arp_entries = {}
    for line in output.splitlines():
        match = re.search(
            r"^(\S+)\s+\((\d+\.\d+\.\d+\.\d+)\)\s+at\s+([0-9a-f:]{11,17})",
            line,
            re.IGNORECASE,
        )
        if match:
            name = match.group(1)
            ip_addr = match.group(2)
            mac = match.group(3).lower()
            if mac not in ("(incomplete)", "ff:ff:ff:ff:ff:ff"):
                arp_entries[ip_addr] = (mac, name if name != "?" else "")

    return arp_entries


def scan_network(hosts):
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
    print(f"  {'IP':<18} {'MAC':<20} {'Имя хоста'}")
    print(f"  {'-' * 18} {'-' * 20} {'-' * 30}")
    for ip_addr, mac, name in rows:
        print(f"  {ip_addr:<18} {mac:<20} {name}")


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

        print(f"--- Интерфейс: {iface['name']} ({iface['type']}) ---")
        print(f"  IP-адрес:  {iface['ip']}")
        print(f"  Маска:     {iface['mask']}")
        print(f"  MAC-адрес: {iface['mac']}")
        print(f"  Сеть:      {network}")
        print()

        hosts = get_network_hosts(iface["ip"], iface["mask"])
        print(f"  Сканирование {len(hosts)} адресов...")

        alive_hosts = scan_network(hosts)

        arp = get_arp_table()

        rows = []
        rows.append((iface["ip"], iface["mac"], f"{hostname} (этот компьютер)"))

        for ip in sorted(alive_hosts, key=lambda x: ipaddress.IPv4Address(x)):
            if ip == iface["ip"]:
                continue
            mac, name = arp.get(ip, ("unknown", ""))
            rows.append((ip, mac, name))

        print(f"\n  Найдено активных узлов: {len(rows)}\n")
        print_table(rows)
        print()

    print("=" * 68)
    print("Сканирование завершено.")


if __name__ == "__main__":
    main()
