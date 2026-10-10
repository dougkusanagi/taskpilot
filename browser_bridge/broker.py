"""Socket TCP loopback autenticado; roteia mensagens, não decide/executa tarefas."""

from __future__ import annotations

import argparse
import hmac
import json
import os
import queue
import re
import secrets
import signal
import socket
import threading
import uuid
from pathlib import Path

from browser_bridge import protocol
from evals.brain.browser_contract import Decision

ADMIN = {"grants_list", "grants_revoke"}


class Broker:
    def __init__(self, extension_id: str, *, timeout: float = 10):
        if not re.fullmatch(r"[a-p]{32}", extension_id):
            raise ValueError("ID de extensão inválido")
        self.extension_id, self.timeout = extension_id, timeout
        self.token = secrets.token_hex(32)
        self.listener = socket.socket()
        self.listener.bind(("127.0.0.1", 0))
        self.listener.listen(8)
        self.listener.settimeout(0.2)
        self.address = self.listener.getsockname()
        self.stop_event = threading.Event()
        self.ready = threading.Event()
        self.lock, self.write_lock = threading.Lock(), threading.Lock()
        self.extension = None
        self.pending = {}
        self.connections = set()
        self.threads = []

    def start(self):
        thread = threading.Thread(target=self._accept, daemon=True)
        self.threads.append(thread)
        thread.start()
        return self

    def close(self):
        self.stop_event.set()
        self.listener.close()
        with self.lock:
            connections = list(self.connections)
        for connection in connections:
            try:
                connection.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
            connection.close()
        for thread in self.threads:
            thread.join(timeout=1)

    def _accept(self):
        while not self.stop_event.is_set():
            try:
                connection, _ = self.listener.accept()
            except socket.timeout:
                continue
            except OSError:
                break
            with self.lock:
                self.connections.add(connection)
            thread = threading.Thread(target=self._handle, args=(connection,), daemon=True)
            self.threads.append(thread)
            thread.start()

    def _handle(self, connection):
        is_extension = False
        try:
            connection.settimeout(self.timeout)
            with connection.makefile("rwb", buffering=0) as stream:
                hello = protocol.read(stream) or {}
                token = hello.get("token")
                valid = isinstance(token, str) and hmac.compare_digest(token, self.token)
                role = hello.get("role")
                if role == "extension":
                    valid &= hello.get("origin") == f"chrome-extension://{self.extension_id}/"
                elif role != "control":
                    valid = False
                if not valid:
                    protocol.write(stream, {"error": "auth_failed"})
                    return
                if role == "extension":
                    with self.lock:
                        if self.extension is not None:
                            protocol.write(stream, {"error": "extension_already_connected"})
                            return
                        self.extension = connection
                        is_extension = True
                    protocol.write(stream, {"ok": True})
                    self.ready.set()
                    connection.settimeout(None)
                    while message := protocol.read(stream):
                        if not isinstance(message.get("id"), str):
                            continue
                        with self.lock:
                            inbox = self.pending.get(message.get("id"))
                        if inbox is not None:
                            try:
                                inbox.put_nowait(message)
                            except queue.Full:
                                pass
                else:
                    protocol.write(stream, {"ok": True})
                    while message := protocol.read(stream):
                        protocol.write(stream, self._dispatch(message))
        except (OSError, ValueError):
            pass
        finally:
            with self.lock:
                self.connections.discard(connection)
                if is_extension and self.extension is connection:
                    self.extension = None
                    self.ready.clear()
                    for inbox in self.pending.values():
                        try:
                            inbox.put_nowait({"result": {"error": "extension_disconnected"}})
                        except queue.Full:
                            pass
            connection.close()

    def _admin(self, message):
        """Comandos de grants vindos do canal de controle autenticado (CLI/orquestrador)."""
        command, origin = message.get("admin"), message.get("origin")
        if command not in ADMIN or (command == "grants_revoke" and not isinstance(origin, str)):
            return {"error": "invalid_admin"}
        outgoing = {"admin": command} | ({"origin": origin} if command == "grants_revoke" else {})
        return self._forward(message, outgoing)

    def _forward(self, message, outgoing):
        correlation = uuid.uuid4().hex
        inbox = queue.Queue(maxsize=1)
        with self.lock:
            extension = self.extension
            if extension is None:
                return {"error": "extension_not_connected"}
            self.pending[correlation] = inbox
        try:
            with self.write_lock:
                extension.sendall(protocol.encode({"id": correlation, **outgoing}))
            response = inbox.get(timeout=self.timeout)
            return {"id": message.get("id"), "result": response.get("result", {})}
        except (OSError, queue.Empty):
            return {"error": "extension_timeout_or_disconnected"}
        finally:
            with self.lock:
                self.pending.pop(correlation, None)

    def _dispatch(self, message):
        if "admin" in message:
            return self._admin(message)
        if type(message.get("tab")) is not int or message["tab"] <= 0:
            return {"error": "invalid_tab"}
        try:
            action = Decision.validate_python(message.get("action"))
        except ValueError:
            return {"error": "invalid_action"}
        correlation = uuid.uuid4().hex
        inbox = queue.Queue(maxsize=1)
        with self.lock:
            extension = self.extension
            if extension is None:
                return {"error": "extension_not_connected"}
            self.pending[correlation] = inbox
        try:
            outgoing = {"id": correlation, "tab": message["tab"], "action": action.model_dump()}
            # A confirmação humana vem do orquestrador (canal autenticado), nunca da ação do modelo.
            if message.get("confirmed") is True and action.tool == "click":
                outgoing["confirmed"] = True
            with self.write_lock:
                extension.sendall(protocol.encode(outgoing))
            response = inbox.get(timeout=self.timeout)
            return {"id": message.get("id"), "result": response.get("result", {})}
        except (OSError, queue.Empty):
            return {"error": "extension_timeout_or_disconnected"}
        finally:
            with self.lock:
                self.pending.pop(correlation, None)

    def request(self, action: dict, tab: int, *, confirmed: bool = False):
        message = {"id": uuid.uuid4().hex, "action": action, "tab": tab}
        if confirmed:
            message["confirmed"] = True
        return control_call(self.address, self.token, message, self.timeout)

    def admin(self, command: str, origin: str | None = None):
        message = {"id": uuid.uuid4().hex, "admin": command}
        if origin is not None:
            message["origin"] = origin
        return control_call(self.address, self.token, message, self.timeout)


def control_call(address, token: str, message: dict, timeout: float = 10):
    """Uma chamada autenticada ao canal de controle do broker."""
    with socket.create_connection(tuple(address), timeout=timeout) as connection:
        with connection.makefile("rwb", buffering=0) as stream:
            protocol.write(stream, {"role": "control", "token": token})
            if protocol.read(stream) != {"ok": True}:
                raise ValueError("autenticação recusada")
            protocol.write(stream, message)
            return protocol.read(stream)


def write_config(path: Path, broker: Broker):
    path.parent.mkdir(parents=True, exist_ok=True)
    flags = os.O_WRONLY | os.O_CREAT | os.O_TRUNC | getattr(os, "O_NOFOLLOW", 0)
    fd = os.open(path, flags, 0o600)
    with os.fdopen(fd, "w") as stream:
        os.chmod(path, 0o600)
        if os.name != "nt" and os.fstat(stream.fileno()).st_mode & 0o077:
            raise ValueError("filesystem não protege configuração; use XDG_RUNTIME_DIR (0600)")
        json.dump(
            {"address": broker.address, "token": broker.token, "extension_id": broker.extension_id},
            stream,
        )
    if os.name != "nt" and path.stat().st_mode & 0o077:
        raise ValueError("filesystem não protege configuração; use XDG_RUNTIME_DIR (modo 0600)")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--extension-id", required=True)
    parser.add_argument("--config", type=Path, required=True)
    args = parser.parse_args()
    broker = Broker(args.extension_id).start()
    try:
        write_config(args.config, broker)
        signal.signal(signal.SIGINT, lambda *_: broker.stop_event.set())
        signal.signal(signal.SIGTERM, lambda *_: broker.stop_event.set())
        print(f"Ponte em {broker.address}; configuração privada em {args.config}", flush=True)
        broker.stop_event.wait()
    finally:
        broker.close()
