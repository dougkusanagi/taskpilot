#!/usr/bin/python3
"""Ferramentas DEV da sessão de teste (scripts/linux-test-session.sh): screenshot, teclas, cliques
e aprovação do diálogo de compartilhamento, pela API D-Bus direta do Mutter.

Roda com o Python do SISTEMA (precisa de `gi`/GStreamer) e só dentro da sessão descartável: usa
`org.gnome.Mutter.ScreenCast/RemoteDesktop` do barramento privado dela, sem passar pelo portal e,
portanto, sem diálogo. O AGENTE nunca usa isto: ele usa o portal (platform_backend/).

  shot ARQUIVO.png            screenshot do monitor virtual
  key SYM [SYM...]            pressiona teclas (nomes: Return, Tab, Escape, a, ... ou 0xFF0D)
  click X Y                   clique esquerdo em pixels do monitor
  approve [SEGUNDOS]          espera o diálogo do portal (AT-SPI), liga a interação e confirma
"""
from __future__ import annotations

import sys
import time

import gi

gi.require_version("Gst", "1.0")
from gi.repository import Gio, GLib, Gst  # noqa: E402

Gst.init(None)
BUS = Gio.bus_get_sync(Gio.BusType.SESSION)

KEYSYMS = {"Return": 0xFF0D, "Tab": 0xFF09, "Escape": 0xFF1B, "space": 0x20, "Up": 0xFF52,
           "Down": 0xFF54, "Left": 0xFF51, "Right": 0xFF53, "Shift_L": 0xFFE1, "Control_L": 0xFFE3,
           "Alt_L": 0xFFE9, "Super_L": 0xFFEB}


def call(name, path, iface, method, sig="", args=(), reply="*"):
    return BUS.call_sync(name, path, iface, method, GLib.Variant(f"({sig})", args) if sig else None,
                         None, Gio.DBusCallFlags.NONE, 10000, None).unpack()


def connector() -> str:
    state = call("org.gnome.Mutter.DisplayConfig", "/org/gnome/Mutter/DisplayConfig",
                 "org.gnome.Mutter.DisplayConfig", "GetCurrentState")
    return state[1][0][0][0]  # primeiro monitor: ((connector, vendor, product, serial), ...)


def screen_size() -> tuple[int, int]:
    state = call("org.gnome.Mutter.DisplayConfig", "/org/gnome/Mutter/DisplayConfig",
                 "org.gnome.Mutter.DisplayConfig", "GetCurrentState")
    cur = next(m for m in state[1][0][1] if m[6].get("is-current"))
    return int(cur[1]), int(cur[2])


class Session:
    """RemoteDesktop + ScreenCast ligados (mesmo espaço de coordenadas)."""

    def __init__(self) -> None:
        rd = "org.gnome.Mutter.RemoteDesktop"
        self.rd_path = call(rd, "/org/gnome/Mutter/RemoteDesktop", rd, "CreateSession")[0]
        sid = BUS.call_sync(rd, self.rd_path, "org.freedesktop.DBus.Properties", "Get",
                            GLib.Variant("(ss)", (rd + ".Session", "SessionId")), None,
                            Gio.DBusCallFlags.NONE, 5000, None).unpack()[0]
        sc = "org.gnome.Mutter.ScreenCast"
        self.sc_path = BUS.call_sync(
            sc, "/org/gnome/Mutter/ScreenCast", sc, "CreateSession",
            GLib.Variant("(a{sv})", ({"remote-desktop-session-id": GLib.Variant("s", sid)},)),
            None, Gio.DBusCallFlags.NONE, 5000, None).unpack()[0]
        self.stream = ""
        self.node = 0

    def start(self, with_stream: bool = False, cursor: bool = False) -> None:
        sc = "org.gnome.Mutter.ScreenCast"
        if with_stream:
            opts = {"cursor-mode": GLib.Variant("u", 1)} if cursor else {}
            self.stream = BUS.call_sync(
                sc, self.sc_path, sc + ".Session", "RecordMonitor",
                GLib.Variant("(sa{sv})", (connector(), opts)), None,
                Gio.DBusCallFlags.NONE, 5000, None).unpack()[0]
            got = {}
            BUS.signal_subscribe(sc, sc + ".Stream", "PipeWireStreamAdded", self.stream, None,
                                 Gio.DBusSignalFlags.NONE,
                                 lambda *a: got.setdefault("node", a[5].unpack()[0]))
        rd = "org.gnome.Mutter.RemoteDesktop"
        call(rd, self.rd_path, rd + ".Session", "Start")
        if with_stream:
            ctx = GLib.MainContext.default()
            t0 = time.time()
            while "node" not in got and time.time() - t0 < 10:
                ctx.iteration(False)
                time.sleep(0.02)
            if "node" not in got:
                raise RuntimeError("PipeWire não anunciou o stream do monitor")
            self.node = got["node"]

    def stop(self) -> None:
        try:
            call("org.gnome.Mutter.RemoteDesktop", self.rd_path,
                 "org.gnome.Mutter.RemoteDesktop.Session", "Stop")
        except Exception:
            pass

    def key(self, sym: int) -> None:
        rd = "org.gnome.Mutter.RemoteDesktop"
        for state in (True, False):
            call(rd, self.rd_path, rd + ".Session", "NotifyKeyboardKeysym", "ub", (sym, state))
            time.sleep(0.05)

    def move(self, x: float, y: float) -> None:
        rd = "org.gnome.Mutter.RemoteDesktop"
        call(rd, self.rd_path, rd + ".Session", "NotifyPointerMotionAbsolute", "sdd",
             (self.stream, float(x), float(y)))
        time.sleep(0.1)

    def click(self, x: float, y: float) -> None:
        rd = "org.gnome.Mutter.RemoteDesktop"
        self.move(x, y)
        for state in (True, False):
            call(rd, self.rd_path, rd + ".Session", "NotifyPointerButton", "ib", (272, state))
            time.sleep(0.05)


def parse_sym(name: str) -> int:
    if name in KEYSYMS:
        return KEYSYMS[name]
    if name.lower().startswith("0x"):
        return int(name, 16)
    if len(name) == 1:
        return ord(name)
    raise SystemExit(f"tecla desconhecida: {name}")


def shot(path: str, cursor: bool = False) -> None:
    s = Session()
    s.start(with_stream=True, cursor=cursor)
    try:
        pipe = Gst.parse_launch(
            f"pipewiresrc path={s.node} num-buffers=3 ! videoconvert ! pngenc snapshot=true ! "
            f"filesink location={path}")
        pipe.set_state(Gst.State.PLAYING)
        msg = pipe.get_bus().timed_pop_filtered(
            10 * Gst.SECOND, Gst.MessageType.EOS | Gst.MessageType.ERROR)
        pipe.set_state(Gst.State.NULL)
        if msg is None or msg.type == Gst.MessageType.ERROR:
            raise SystemExit(f"screenshot falhou: {msg.parse_error()[0] if msg else 'timeout'}")
    finally:
        s.stop()
    print(path)


def _portal_dialog():
    """Raiz AT-SPI do diálogo do portal (app xdg-desktop-portal-gnome) ou None."""
    gi.require_version("Atspi", "2.0")
    from gi.repository import Atspi

    Atspi.init()
    desk = Atspi.get_desktop(0)
    for i in range(desk.get_child_count()):
        app = desk.get_child_at_index(i)
        if "portal-gnome" in (app.get_name() or "") and app.get_child_count() > 0:
            return app
    return None


def _walk(node):
    yield node
    for i in range(node.get_child_count()):
        try:
            yield from _walk(node.get_child_at_index(i))
        except Exception:
            continue


def _actionable(node) -> bool:
    ai = node.get_action_iface()
    return ai is not None and ai.get_n_actions() > 0


def approve(timeout_s: float, interaction: bool = True) -> None:
    """Espera o diálogo do portal (AT-SPI), liga "Permitir interação remota" e confirma.

    Nada é acionado sem o diálogo existir. Sem foco/ponteiro: usa só ações AT-SPI."""
    gi.require_version("Atspi", "2.0")
    from gi.repository import Atspi

    t0 = time.time()
    while time.time() - t0 < timeout_s:
        app = _portal_dialog()
        if app is not None:
            nodes = list(_walk(app))
            switches = [n for n in nodes if n.get_role_name() == "switch" and _actionable(n)]
            buttons = [n for n in nodes if n.get_role_name() == "button" and _actionable(n)]
            if buttons and (switches or not interaction):
                time.sleep(0.5)  # deixa o diálogo terminar de montar
                if interaction:
                    sw = switches[0]
                    if not sw.get_state_set().contains(Atspi.StateType.CHECKED):
                        sw.get_action_iface().do_action(0)  # toggle
                        time.sleep(0.3)
                    if not sw.get_state_set().contains(Atspi.StateType.CHECKED):
                        raise SystemExit("não consegui ligar a interação remota no diálogo")
                # Cancelar vem antes de Compartilhar no cabeçalho; o primário é o último botão.
                primary = buttons[-1]
                print(f"confirmando: {primary.get_name()!r}")
                primary.get_action_iface().do_action(0)
                return
        time.sleep(0.5)
    raise SystemExit("o diálogo do portal não apareceu a tempo (a acessibilidade está ligada?)")


def main() -> None:
    cmd = sys.argv[1] if len(sys.argv) > 1 else ""
    if cmd == "shot" and len(sys.argv) in (3, 4):
        shot(sys.argv[2], cursor=len(sys.argv) == 4 and sys.argv[3] == "--cursor")
    elif cmd == "move" and len(sys.argv) == 4:
        s = Session()
        s.start(with_stream=True)
        try:
            s.move(float(sys.argv[2]), float(sys.argv[3]))
        finally:
            s.stop()
    elif cmd == "key" and len(sys.argv) >= 3:
        s = Session()
        s.start()
        try:
            for name in sys.argv[2:]:
                s.key(parse_sym(name))
        finally:
            s.stop()
    elif cmd == "click" and len(sys.argv) == 4:
        s = Session()
        s.start(with_stream=True)
        try:
            s.click(float(sys.argv[2]), float(sys.argv[3]))
        finally:
            s.stop()
    elif cmd == "approve":
        approve(float(sys.argv[2]) if len(sys.argv) > 2 else 60.0)
    else:
        print(__doc__)
        raise SystemExit(2)


if __name__ == "__main__":
    main()
