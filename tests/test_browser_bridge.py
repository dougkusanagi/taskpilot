import io
import json
import socket
import struct
import subprocess
import sys
import tempfile
import threading
import unittest
from pathlib import Path

from browser_bridge import protocol
from browser_bridge.broker import Broker, write_config
from browser_bridge.install import generate, manifest
from browser_bridge.native import config_from

EXTENSION = "abcdefghijklmnopabcdefghijklmnop"


class BridgeTests(unittest.TestCase):
    def test_frames_unicode_partial_and_limits(self):
        frame = protocol.encode({"texto": "reunião às 14h"})
        self.assertEqual(protocol.read(io.BytesIO(frame)), {"texto": "reunião às 14h"})
        self.assertIsNone(protocol.read(io.BytesIO(b"")))
        for raw in [
            frame[:2],
            frame[:-1],
            struct.pack("=I", protocol.MAX_BYTES + 1),
            struct.pack("=I", 2) + b"[]",
        ]:
            with self.assertRaises(ValueError):
                protocol.read(io.BytesIO(raw))

    def test_bad_token_origin_and_nonbrowser_actions(self):
        broker = Broker(EXTENSION).start()
        try:
            for hello in [
                {"role": "control", "token": "errado"},
                {"role": "extension", "token": broker.token, "origin": "chrome-extension://outra/"},
            ]:
                with socket.create_connection(broker.address) as connection:
                    with connection.makefile("rwb", buffering=0) as stream:
                        protocol.write(stream, hello)
                        self.assertEqual(protocol.read(stream), {"error": "auth_failed"})
            self.assertEqual(
                broker.request({"tool": "type", "text": "x"}, 1)["error"], "invalid_action"
            )
            self.assertEqual(broker.request({"tool": "read_page"}, True)["error"], "invalid_tab")
            self.assertEqual(
                broker.request({"tool": "read_page"}, 1)["error"], "extension_not_connected"
            )
        finally:
            broker.close()

    def test_confirmation_flag_comes_only_from_the_control_channel_and_only_for_click(self):
        broker = Broker(EXTENSION).start()
        seen = []
        try:
            with socket.create_connection(broker.address) as connection:
                with connection.makefile("rwb", buffering=0) as stream:
                    protocol.write(
                        stream,
                        {
                            "role": "extension",
                            "token": broker.token,
                            "origin": f"chrome-extension://{EXTENSION}/",
                        },
                    )
                    self.assertEqual(protocol.read(stream), {"ok": True})

                    def answer():
                        for _ in range(3):
                            message = protocol.read(stream)
                            seen.append(message)
                            protocol.write(stream, {"id": message["id"], "result": {"ok": 1}})

                    worker = threading.Thread(target=answer)
                    worker.start()
                    click = {"tool": "click", "ref": "a:e1"}
                    broker.request(click, 5)
                    broker.request(click, 5, confirmed=True)
                    broker.request({"tool": "read_page"}, 5, confirmed=True)
                    worker.join(5)
        finally:
            broker.close()
        self.assertNotIn("confirmed", seen[0])
        self.assertIs(seen[1]["confirmed"], True)
        self.assertNotIn("confirmed", seen[2])

    def _fake_extension(self, broker, replies, seen):
        connection = socket.create_connection(broker.address)
        stream = connection.makefile("rwb", buffering=0)
        protocol.write(
            stream,
            {
                "role": "extension",
                "token": broker.token,
                "origin": f"chrome-extension://{EXTENSION}/",
            },
        )
        self.assertEqual(protocol.read(stream), {"ok": True})

        def answer():
            for result in replies:
                message = protocol.read(stream)
                seen.append(message)
                protocol.write(stream, {"id": message["id"], "result": result})

        worker = threading.Thread(target=answer)
        worker.start()
        return connection, stream, worker

    def test_admin_commands_are_validated_and_forwarded_without_a_tab(self):
        broker = Broker(EXTENSION).start()
        seen = []
        connection = stream = worker = None
        try:
            self.assertEqual(broker.admin("grants_list")["error"], "extension_not_connected")
            connection, stream, worker = self._fake_extension(
                broker, [{"grants": []}, {"revoked": "http://a.test"}], seen
            )
            self.assertEqual(broker.admin("apagar_tudo")["error"], "invalid_admin")
            self.assertEqual(broker.admin("grants_revoke")["error"], "invalid_admin")
            self.assertEqual(broker.admin("grants_list")["result"], {"grants": []})
            broker.admin("grants_revoke", "http://a.test")
            worker.join(5)
        finally:
            if stream:
                stream.close()
                connection.close()
            broker.close()
        self.assertEqual(seen[0]["admin"], "grants_list")
        self.assertNotIn("tab", seen[0])
        self.assertEqual((seen[1]["admin"], seen[1]["origin"]), ("grants_revoke", "http://a.test"))

    def test_permissions_cli_lists_revokes_and_refuses_unsafe_config(self):
        import contextlib

        from browser_bridge import permissions

        broker = Broker(EXTENSION).start()
        seen = []
        grant = {
            "origin": "http://a.test",
            "level": "interact",
            "validity": "task",
            "expires_in_s": 90,
        }
        connection = stream = worker = None
        try:
            with tempfile.TemporaryDirectory() as temp:
                config = Path(temp) / "bridge.json"
                write_config(config, broker)
                connection, stream, worker = self._fake_extension(
                    broker,
                    [{"grants": [grant]}, {"grants": [grant]}, {"revoked": "http://a.test"}],
                    seen,
                )
                out = io.StringIO()
                with contextlib.redirect_stdout(out):
                    self.assertEqual(permissions.main(["list", "--config", str(config)]), 0)
                    self.assertEqual(permissions.main(["revoke-all", "--config", str(config)]), 0)
                worker.join(5)
                text = out.getvalue()
                self.assertIn("http://a.test  interact  task  90s", text)
                self.assertIn("Revogado: http://a.test", text)
                config.chmod(0o644)
                err = io.StringIO()
                with contextlib.redirect_stderr(err):
                    self.assertEqual(permissions.main(["list", "--config", str(config)]), 1)
                self.assertIn("não é privada", err.getvalue())
        finally:
            if stream:
                stream.close()
                connection.close()
            broker.close()

    def test_native_stdio_to_authenticated_socket_roundtrip(self):
        broker = Broker(EXTENSION).start()
        try:
            with tempfile.TemporaryDirectory() as temp:
                config = Path(temp) / "bridge.json"
                write_config(config, broker)
                origin = f"chrome-extension://{EXTENSION}/"
                process = subprocess.Popen(
                    [
                        sys.executable,
                        "-m",
                        "browser_bridge.native",
                        "--config",
                        str(config),
                        origin,
                    ],
                    stdin=subprocess.PIPE,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                )
                try:
                    self.assertTrue(broker.ready.wait(5))
                    result = []
                    worker = threading.Thread(
                        target=lambda: result.append(broker.request({"tool": "read_page"}, 7))
                    )
                    worker.start()
                    command = protocol.read(process.stdout)
                    self.assertEqual(command["tab"], 7)
                    self.assertEqual(command["action"]["tool"], "read_page")
                    protocol.write(
                        process.stdin, {"id": command["id"], "result": {"text": "reunião"}}
                    )
                    worker.join(timeout=5)
                    self.assertEqual(result[0]["result"]["text"], "reunião")
                finally:
                    process.stdin.close()
                    process.wait(timeout=5)
                    process.stdout.close()
                    process.stderr.close()
                with self.assertRaises(ValueError):
                    config_from(config, "chrome-extension://outra/")
                config.chmod(0o644)
                with self.assertRaises(ValueError):
                    config_from(config, origin)
        finally:
            broker.close()

    def test_linux_and_windows_manifests_without_installation(self):
        with tempfile.TemporaryDirectory() as temp:
            result = generate(Path(temp), EXTENSION, "/run/user/1000/config.json")
            data = json.loads(result.read_text())
            self.assertEqual(data["allowed_origins"], [f"chrome-extension://{EXTENSION}/"])
            win = manifest(EXTENSION, "C:\\TaskPilot\\native.cmd", "windows")
            self.assertEqual(win["type"], "stdio")
            with self.assertRaises(ValueError):
                manifest("*", "/tmp/native", "linux")

    def test_insecure_filesystem_does_not_receive_secret(self):
        from unittest.mock import patch

        broker = Broker(EXTENSION)
        try:
            with tempfile.TemporaryDirectory() as temp:
                target = Path(temp) / "config.json"
                target.write_text("placeholder")
                target.chmod(0o644)
                with patch("browser_bridge.broker.os.chmod"):
                    with self.assertRaises(ValueError):
                        write_config(target, broker)
                self.assertNotIn(broker.token, target.read_text())
        finally:
            broker.close()
