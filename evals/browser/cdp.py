"""Cliente do pipe de teste Node; não usa porta de depuração nem perfil real."""

import json
import subprocess
import time
from pathlib import Path


class Chrome:
    def __init__(self, directory):
        self.process = subprocess.Popen(
            ["node", "evals/browser/driver.mjs", str(directory)],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            text=True,
            bufsize=1,
        )
        self.metadata = json.loads(self.process.stdout.readline())
        self.sequence = 0

    def command(self, method, params=None, session=None):
        self.sequence += 1
        self.process.stdin.write(
            json.dumps(
                {
                    "id": self.sequence,
                    "method": method,
                    "params": params or {},
                    "sessionId": session,
                }
            )
            + "\n"
        )
        self.process.stdin.flush()
        response = json.loads(self.process.stdout.readline())
        if "error" in response:
            raise ValueError(response["error"])
        return response["result"]

    def tab(self, url):
        target = self.command("Target.createTarget", {"url": url})["targetId"]
        session = self.command("Target.attachToTarget", {"targetId": target, "flatten": True})[
            "sessionId"
        ]
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            try:
                if self.evaluate(
                    session, "document.readyState === 'complete' && location.href !== 'about:blank'"
                ):
                    return session
            except ValueError:
                pass
            time.sleep(0.05)
        raise TimeoutError("navegação de teste")

    def evaluate(self, session, expression):
        result = self.command(
            "Runtime.evaluate",
            {
                "expression": expression,
                "awaitPromise": True,
                "returnByValue": True,
                "userGesture": True,
            },
            session,
        )
        if "exceptionDetails" in result:
            raise ValueError(str(result["exceptionDetails"]))
        return result["result"].get("value")

    def screenshot(self, session, path: Path):
        import base64

        # Aba em segundo plano não renderiza: captureScreenshot trava até o timeout.
        self.command("Page.bringToFront", {}, session)
        image = self.command("Page.captureScreenshot", {}, session)
        path.write_bytes(base64.b64decode(image["data"]))

    def close(self):
        self.process.stdin.close()
        try:
            self.process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            self.process.terminate()
            self.process.wait(timeout=5)
        self.process.stdout.close()
