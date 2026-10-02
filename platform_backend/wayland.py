"""Cliente do backend Wayland (PROTÓTIPO): fala com `wayland_bridge.py` por JSON-lines.

Tudo que o agente precisa do SO nesta plataforma: frame da tela, input (API estilo pyautogui
para `actions.py`), lançar apps da whitelist. Sem UIA: o modo é visual (o snapshot vem vazio e
o loop cai em `visual_action`/OCR); AT-SPI fica para a fase seguinte.

Limites declarados (não são bugs): 1 monitor por sessão (o escolhido no diálogo do GNOME);
sem foco programático de janela (Wayland não permite: só clique/atalho); sem hotkey global de
parada — o botão "parar compartilhamento" do GNOME e Ctrl+C no terminal são os cortes.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path

from PIL import Image

BRIDGE = Path(__file__).with_name("wayland_bridge.py")
BTN = {"left": 272, "right": 273, "middle": 274}  # evdev BTN_*

# Whitelist Linux: nunca shell; aceita os nomes que o planner (Windows-cêntrico) emite.
LINUX_APPS = {
    "notepad": "gnome-text-editor", "notepad.exe": "gnome-text-editor",
    "bloco de notas": "gnome-text-editor", "editor de texto": "gnome-text-editor",
    "text editor": "gnome-text-editor", "gedit": "gnome-text-editor",
    "gnome-text-editor": "gnome-text-editor",
    "calc": "gnome-calculator", "calc.exe": "gnome-calculator", "calculator": "gnome-calculator",
    "calculadora": "gnome-calculator", "gnome-calculator": "gnome-calculator",
    "chrome": "google-chrome", "google chrome": "google-chrome", "google-chrome": "google-chrome",
}
BROWSERS = ("google-chrome",)
_NAMED = {
    "enter": 0xFF0D, "return": 0xFF0D, "esc": 0xFF1B, "escape": 0xFF1B, "tab": 0xFF09,
    "backspace": 0xFF08, "delete": 0xFFFF, "del": 0xFFFF, "space": 0x20,
    "up": 0xFF52, "down": 0xFF54, "left": 0xFF51, "right": 0xFF53,
    "home": 0xFF50, "end": 0xFF57, "pageup": 0xFF55, "pagedown": 0xFF56,
    "ctrl": 0xFFE3, "control": 0xFFE3, "alt": 0xFFE9, "shift": 0xFFE1,
    "win": 0xFFEB, "super": 0xFFEB, "cmd": 0xFFEB,
    **{f"f{i}": 0xFFBD + i for i in range(1, 13)},
}


def keysym_for_char(ch: str) -> int:
    """Keysym X11 de um caractere (puro): ASCII/Latin-1 = codepoint; resto = 0x01000000+cp."""
    cp = ord(ch)
    if ch == "\n":
        return 0xFF0D
    if ch == "\t":
        return 0xFF09
    if 0x20 <= cp <= 0x7E or 0xA0 <= cp <= 0xFF:
        return cp
    return 0x01000000 + cp


def keysym_for_key(name: str) -> int:
    n = name.strip().lower()
    if n in _NAMED:
        return _NAMED[n]
    if len(n) == 1:
        return keysym_for_char(n)
    raise ValueError(f"tecla desconhecida no backend Wayland: {name!r}")


class WaylandBackend:
    def __init__(self) -> None:
        self._proc: subprocess.Popen | None = None
        self._lock = threading.Lock()
        self.size = (0, 0)
        self._tmp = Path(tempfile.mkdtemp(prefix="taskpilot-wl-"))
        self.input = _Input(self)

    # --- ciclo de vida ---------------------------------------------------------------
    def _python(self) -> str:
        for cand in (os.environ.get("TASKPILOT_SYSTEM_PYTHON"), "/usr/bin/python3"):
            if cand and Path(cand).exists():
                return cand
        raise RuntimeError("python do sistema (com python3-gi) não encontrado; "
                           "defina TASKPILOT_SYSTEM_PYTHON")

    def _ensure(self) -> None:
        if self._proc is not None and self._proc.poll() is None:
            return
        self._proc = subprocess.Popen(
            [self._python(), "-u", str(BRIDGE)], stdin=subprocess.PIPE, stdout=subprocess.PIPE,
            stderr=sys.stderr, text=True, bufsize=1)
        r = self._call({"cmd": "start"}, timeout=150)
        self.size = tuple(r.get("frame_size") or (0, 0))
        got = self._call({"cmd": "frame", "path": str(self._tmp / "probe.png")}, timeout=20)
        self.size = tuple(got["size"])

    def _call(self, req: dict, timeout: float = 15.0) -> dict:
        assert self._proc and self._proc.stdin and self._proc.stdout
        with self._lock:
            self._proc.stdin.write(json.dumps(req) + "\n")
            self._proc.stdin.flush()
            box: dict = {}

            def rd():
                box["line"] = self._proc.stdout.readline()

            t = threading.Thread(target=rd, daemon=True)
            t.start()
            t.join(timeout)
            if t.is_alive() or not box.get("line"):
                raise RuntimeError(f"ponte Wayland não respondeu a {req.get('cmd')} "
                                   f"em {timeout:.0f}s (sessão encerrada?)")
            res = json.loads(box["line"])
        if not res.get("ok"):
            raise RuntimeError(f"ponte Wayland: {res.get('error')}")
        return res

    def close(self) -> None:
        if self._proc is not None and self._proc.poll() is None:
            try:
                self._call({"cmd": "stop"}, timeout=5)
            except Exception:
                self._proc.kill()
        self._proc = None
        shutil.rmtree(self._tmp, ignore_errors=True)

    # --- captura ---------------------------------------------------------------------
    def grab(self) -> tuple[Image.Image, tuple[int, int]]:
        """(imagem do monitor compartilhado, origem=(0,0)): mesmo espaço do input."""
        self._ensure()
        path = self._tmp / "frame.png"
        r = self._call({"cmd": "frame", "path": str(path)}, timeout=20)
        self.size = tuple(r["size"])
        with Image.open(path) as im:
            return im.convert("RGB"), (0, 0)

    def screen_rect(self) -> tuple[int, int, int, int]:
        self._ensure()
        return (0, 0, self.size[0], self.size[1])

    # --- apps ------------------------------------------------------------------------
    def open_app(self, target: str) -> str:
        key = target.strip().lower()
        exe = LINUX_APPS.get(key)
        if exe is None:
            raise ValueError(f"app fora da whitelist: {target!r}; use um de "
                             f"{sorted(set(LINUX_APPS.values()))}")
        if shutil.which(exe) is None:
            raise ValueError(f"{exe} não está instalado neste sistema")
        args = [exe] + (["--force-renderer-accessibility"] if exe in BROWSERS else [])
        subprocess.Popen(args, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                         stderr=subprocess.DEVNULL, start_new_session=True)
        time.sleep(2.5)
        return (f"opened {exe} (Wayland não permite focar por código: confirme na tela; "
                "se a janela não estiver em primeiro plano, clique nela)")

    def focus_window(self, title_substr: str) -> bool:
        """Wayland não tem foco programático de janelas de outro cliente: honesto = False."""
        return False


class _Input:
    """API mínima estilo pyautogui sobre a ponte (só o que actions.py usa)."""

    FAILSAFE = True
    PAUSE = 0.15

    def __init__(self, be: WaylandBackend) -> None:
        self._be = be

    def _c(self, req: dict) -> None:
        self._be._ensure()
        self._be._call(req)

    def moveTo(self, x, y, duration=0.0):  # noqa: N802 (nome da API pyautogui)
        self._c({"cmd": "move", "x": x, "y": y})
        time.sleep(max(duration, 0.0) * 0.3)

    def _press(self, button: str, clicks: int = 1):
        for _ in range(clicks):
            self._c({"cmd": "button", "code": BTN[button], "state": 1})
            self._c({"cmd": "button", "code": BTN[button], "state": 0})
            time.sleep(0.05)

    def click(self, x=None, y=None, clicks=1):
        self._press("left", int(clicks))

    def doubleClick(self, x=None, y=None):  # noqa: N802
        self._press("left", 2)

    def rightClick(self, x=None, y=None):  # noqa: N802
        self._press("right")

    def middleClick(self, x=None, y=None):  # noqa: N802
        self._press("middle")

    def mouseUp(self, button="left"):  # noqa: N802
        self._c({"cmd": "button", "code": BTN[button], "state": 0})

    def dragTo(self, x, y, duration=0.4):  # noqa: N802
        self._c({"cmd": "button", "code": BTN["left"], "state": 1})
        try:
            time.sleep(0.1)
            self.moveTo(x, y, duration)
        finally:
            self._c({"cmd": "button", "code": BTN["left"], "state": 0})

    def scroll(self, amount):
        # pyautogui: positivo = para cima; o portal: passo positivo = para baixo.
        steps = int(-amount / 100) or (-1 if amount > 0 else 1)
        self._c({"cmd": "axis", "axis": 0, "steps": steps})

    def hscroll(self, amount):
        steps = int(amount / 100) or (1 if amount > 0 else -1)
        self._c({"cmd": "axis", "axis": 1, "steps": steps})

    def hotkey(self, *keys):
        syms = [keysym_for_key(k) for k in keys]
        try:
            for s in syms:
                self._c({"cmd": "keysym", "sym": s, "state": 1})
        finally:
            for s in reversed(syms):
                self._c({"cmd": "keysym", "sym": s, "state": 0})
        time.sleep(0.05)

    def keyUp(self, key):  # noqa: N802
        self._c({"cmd": "keysym", "sym": keysym_for_key(key), "state": 0})

    def type_text(self, text: str) -> None:
        for ch in text:
            s = keysym_for_char(ch)
            self._c({"cmd": "keysym", "sym": s, "state": 1})
            self._c({"cmd": "keysym", "sym": s, "state": 0})
            time.sleep(0.015)
