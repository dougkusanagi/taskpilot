"""Montagem compartilhada do N2/episódios: Chrome descartável, extensão, ponte e fixtures."""

import os
import subprocess
import sys
import time
import uuid
from pathlib import Path
from urllib.request import Request, urlopen

from browser_bridge.broker import Broker, write_config
from browser_bridge.install import generate
from evals.browser.cdp import Chrome

SESSION_TOOL = "scripts/linux_session_tool.py"


def wait(predicate, timeout=10):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        value = predicate()
        if value:
            return value
        time.sleep(0.1)
    raise TimeoutError("estado independente não chegou")


def require_isolated_session():
    runtime = f"/run/user/{os.getuid()}/taskpilot-test-session"
    if (
        os.environ.get("XDG_RUNTIME_DIR") != runtime
        or os.environ.get("TASKPILOT_TEST_SESSION") != "1"
        or os.environ.get("WAYLAND_DISPLAY") != "wayland-tp"
    ):
        raise ValueError("rode por scripts/linux-test-session.sh run --; nunca no desktop real")


class Rig:
    """Perfil descartável do Chrome + ponte real. O perfil do usuário nunca é tocado."""

    def __init__(self, output: Path, first_path: str = "/webmail"):
        require_isolated_session()
        output.mkdir(parents=True, exist_ok=False)
        os.environ["TP_BROWSER_HEADFUL"] = "1"
        self.output, self.first_path = output, first_path
        self.chrome = self.broker = self.page = self.popup = self.tab = None
        self.extension_id = ""
        self._log = (output / "fixture.log").open("w")
        self.server = subprocess.Popen(
            [sys.executable, "-m", "evals.brain", "serve", "--port", "0"],
            stdout=subprocess.PIPE,
            stderr=self._log,
            text=True,
        )
        self.base = self.server.stdout.readline().strip().split(" ")[-1]

    def start(self):
        """Sobe Chrome, ponte e native messaging. Devolve se o host conectou."""
        self.chrome = Chrome(self.output)
        self.extension_id = self.chrome.metadata["extensionId"]
        self.broker = Broker(self.extension_id).start()
        config = Path(os.environ["XDG_RUNTIME_DIR"]) / f"native-{uuid.uuid4().hex}" / "config.json"
        write_config(config, self.broker)
        manifest = generate(self.output / "native", self.extension_id, str(config))
        for registry in [
            self.output / "home/.config/google-chrome/NativeMessagingHosts",
            self.output / "profile/NativeMessagingHosts",
        ]:
            registry.mkdir(parents=True, exist_ok=True)
            (registry / manifest.name).write_bytes(manifest.read_bytes())
        self.page = self.chrome.tab(self.base + self.first_path)
        self.popup = self.chrome.tab(f"chrome-extension://{self.extension_id}/popup.html")
        self.chrome.evaluate(self.popup, 'chrome.runtime.sendMessage({type:"connect"})')
        connected = self.broker.ready.wait(10)
        tabs = self.chrome.evaluate(self.popup, "chrome.tabs.query({})")
        self.tab = next(t["id"] for t in tabs if t.get("url") == self.base + self.first_path)
        return connected

    def request(self, action: dict) -> dict:
        response = self.broker.request(action, self.tab)
        return response.get("result", response)

    def share(self, screenshot: Path | None = None):
        """Botão real do popup; a aprovação por teclado vale só no GNOME isolado."""
        self.chrome.evaluate(
            self.popup,
            f'document.getElementById("tab").value="{self.tab}";'
            'document.getElementById("share").click()',
        )
        time.sleep(1)
        if screenshot:
            subprocess.run(["/usr/bin/python3", SESSION_TOOL, "shot", str(screenshot)], check=True)
        subprocess.run(
            ["/usr/bin/python3", SESSION_TOOL, "key", "Shift_L", "Tab", "Return"], check=True
        )
        wait(
            lambda: self.chrome.evaluate(
                self.popup, 'chrome.permissions.contains({origins:["http://localhost/*"]})'
            )
        )
        wait(
            lambda: self.chrome.evaluate(
                self.popup,
                'document.getElementById("status").textContent.startsWith("Aba compartilhada")',
            )
        )

    def set_level(self, level: str):
        self.chrome.evaluate(
            self.popup,
            f'chrome.runtime.sendMessage({{type:"share",tab:{self.tab},level:"{level}"}})',
        )

    def open_page(self, path: str):
        """Navegação do verificador (CDP), não da ferramenta do agente."""
        self.chrome.command("Page.navigate", {"url": self.base + path}, self.page)
        parts = path.strip("/").split("/")
        wait(
            lambda: self.chrome.evaluate(
                self.page,
                f'location.pathname.startsWith("/{parts[0]}") && '
                'location.hostname === "localhost" && document.readyState === "complete"',
            )
        )

    def reset_fixture(self):
        urlopen(Request(self.base + "/reset", data=b"", method="POST"), timeout=5).read()

    def close(self):
        if self.chrome:
            self.chrome.close()
        if self.broker:
            self.broker.close()
        self.server.terminate()
        self.server.wait(timeout=5)
        self.server.stdout.close()
        self._log.close()
