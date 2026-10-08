"""Host stdio: origem exata da extensão e token privado, sem stdout de diagnóstico."""

import argparse
import json
import os
import socket
import sys
import threading
from pathlib import Path

from browser_bridge import protocol


def config_from(path: Path, origin: str):
    stat = path.stat()
    if os.name != "nt" and (stat.st_uid != os.getuid() or stat.st_mode & 0o077):
        raise ValueError("configuração não privada")
    config = json.loads(path.read_text())
    if origin != f"chrome-extension://{config['extension_id']}/":
        raise ValueError("origem da extensão recusada")
    host, port = config["address"]
    if host != "127.0.0.1" or type(port) is not int or not 0 < port < 65536:
        raise ValueError("socket deve ser loopback")
    return config


def relay(config: dict, origin: str, incoming, outgoing):
    with socket.create_connection(config["address"], timeout=5) as connection:
        with connection.makefile("rwb", buffering=0) as stream:
            protocol.write(
                stream, {"role": "extension", "token": config["token"], "origin": origin}
            )
            if protocol.read(stream) != {"ok": True}:
                raise ValueError("autenticação recusada")
            connection.settimeout(None)

            def socket_to_chrome():
                try:
                    while message := protocol.read(stream):
                        protocol.write(outgoing, message)
                except (OSError, ValueError):
                    pass

            reader = threading.Thread(target=socket_to_chrome, daemon=True)
            reader.start()
            try:
                while message := protocol.read(incoming):
                    protocol.write(stream, message)
            finally:
                connection.shutdown(socket.SHUT_RDWR)
                reader.join(timeout=2)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("origin")
    parser.add_argument("--parent-window")
    args = parser.parse_args()
    try:
        relay(
            config_from(args.config, args.origin), args.origin, sys.stdin.buffer, sys.stdout.buffer
        )
    except (OSError, ValueError, KeyError):
        print("TaskPilot: ponte indisponível ou autenticação recusada", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
