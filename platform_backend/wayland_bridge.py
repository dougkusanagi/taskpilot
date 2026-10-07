"""Ponte Wayland (PROTÓTIPO): captura de tela + input pelos portais do xdg-desktop-portal.

Roda com o Python do SISTEMA (`/usr/bin/python3`, precisa de `python3-gi` e do plugin
`pipewiresrc` do GStreamer): o venv do projeto (3.12) não enxerga `gi`. Fala JSON-lines
em stdin/stdout com `platform_backend.wayland` (o cliente, no venv).

Uma única sessão do portal `RemoteDesktop` cobre as duas coisas: o ScreenCast dá o frame
(PipeWire) e o RemoteDesktop injeta mouse/teclado nas coordenadas desse mesmo stream — não
há mapeamento tela↔frame para errar. O usuário aprova UMA vez num diálogo do GNOME; o
`restore_token` guardado evita repetir o diálogo nas próximas execuções. O botão "parar de
compartilhar" do GNOME (barra superior) encerra a sessão e corta o input: é o botão de pânico.

Comandos (uma linha JSON cada) → resposta `{"ok": bool, ...}`:
  start | frame{path} | move{x,y} | button{code,state} | axis{axis,steps} | keysym{sym,state} | stop
  a11y{max_elements,timeout}   árvore AT-SPI da janela ativa (não precisa de `start`/consentimento)
Coordenadas de `move` são em pixels do FRAME (a ponte reescala p/ o tamanho lógico do stream).
"""
from __future__ import annotations

import json
import os
import sys
import threading
import time
import warnings
from pathlib import Path

import gi

warnings.filterwarnings("ignore", category=DeprecationWarning)
sys.path.insert(0, str(Path(__file__).resolve().parent))  # módulos irmãos, mesmo como script
import portal_rules  # noqa: E402

gi.require_version("Gst", "1.0")
from gi.repository import Gio, GLib, Gst  # noqa: E402

PORTAL = "org.freedesktop.portal.Desktop"
PORTAL_PATH = "/org/freedesktop/portal/desktop"
TOKEN_FILE = Path.home() / ".cache" / "taskpilot" / "wayland-restore-token"
CONSENT_TIMEOUT_S = 120.0


def _log(msg: str) -> None:
    print(msg, file=sys.stderr, flush=True)


class Bridge:
    def __init__(self) -> None:
        self.bus = Gio.bus_get_sync(Gio.BusType.SESSION)
        self.loop = GLib.MainLoop()
        threading.Thread(target=self.loop.run, daemon=True).start()
        self.sender = self.bus.get_unique_name()[1:].replace(".", "_")
        self._n = 0
        self.session = ""
        self.node_id = 0
        self.stream_size = (0, 0)
        self.pipeline = None
        self.frame: tuple[bytes, int, int, float] | None = None
        self._frame_lock = threading.Lock()
        self.closed = False  # sessão do portal encerrada (botão "parar compartilhamento")

    # --- portal ---------------------------------------------------------------------
    def _token(self) -> str:
        self._n += 1
        return f"tp{os.getpid()}_{self._n}"

    def _request(self, iface: str, method: str, args: list, options: dict,
                 timeout: float = 15.0) -> tuple[int, dict]:
        """Chama método do portal que responde por Request/Response; devolve (code, results)."""
        token = self._token()
        options = {**options, "handle_token": GLib.Variant("s", token)}
        handle = f"/org/freedesktop/portal/desktop/request/{self.sender}/{token}"
        done = threading.Event()
        out: dict = {}

        def on_resp(_c, _s, _p, _i, _sig, params, *_a):
            out["code"], out["res"] = params.unpack()
            done.set()

        sid = self.bus.signal_subscribe(PORTAL, "org.freedesktop.portal.Request", "Response",
                                        handle, None, Gio.DBusSignalFlags.NONE, on_resp)
        try:
            types = "".join(a[0] for a in args)
            self.bus.call_sync(PORTAL, PORTAL_PATH, iface, method,
                               GLib.Variant(f"({types}a{{sv}})", (*[a[1] for a in args], options)),
                               None, Gio.DBusCallFlags.NONE, 10000, None)
            if not done.wait(timeout):
                raise TimeoutError(f"{method}: sem resposta do portal em {timeout:.0f}s")
        finally:
            self.bus.signal_unsubscribe(sid)
        return out["code"], out["res"]

    def start(self) -> dict:
        RD, SC = "org.freedesktop.portal.RemoteDesktop", "org.freedesktop.portal.ScreenCast"
        code, res = self._request(RD, "CreateSession", [], {
            "session_handle_token": GLib.Variant("s", self._token())})
        if code != 0:
            raise RuntimeError(f"CreateSession recusado (code={code})")
        self.session = res["session_handle"]
        sess = ("o", self.session)
        # Botão "parar compartilhamento" do GNOME = corte de emergência: o portal emite Closed.
        self.bus.signal_subscribe(PORTAL, "org.freedesktop.portal.Session", "Closed",
                                  self.session, None, Gio.DBusSignalFlags.NONE, self._on_closed)

        opts = {"types": GLib.Variant("u", 3), "persist_mode": GLib.Variant("u", 2)}
        if TOKEN_FILE.exists():
            opts["restore_token"] = GLib.Variant("s", TOKEN_FILE.read_text().strip())
        code, _ = self._request(RD, "SelectDevices", [sess], opts)
        if code != 0:
            raise RuntimeError(f"SelectDevices recusado (code={code})")
        code, _ = self._request(SC, "SelectSources", [sess], {
            "types": GLib.Variant("u", 1),          # monitor
            "multiple": GLib.Variant("b", False),   # 1 monitor = frame e input no mesmo espaço
            "cursor_mode": GLib.Variant("u", 1)})   # cursor fora do frame (como o mss no Windows)
        if code != 0:
            raise RuntimeError(f"SelectSources recusado (code={code})")
        if TOKEN_FILE.exists():
            _log("restaurando a sessão já aprovada (diálogo só se o token expirou)...")
        else:
            _log("aguardando aprovação no diálogo do GNOME (compartilhar tela + controle)...")
        code, res = self._request(RD, "Start", [sess, ("s", "")], {}, timeout=CONSENT_TIMEOUT_S)
        if code != 0:
            raise RuntimeError("usuário negou/cancelou o compartilhamento (code=%d)" % code)
        why = portal_rules.devices_error(int(res.get("devices", 0)), TOKEN_FILE)
        if why:
            self.stop()
            raise RuntimeError(why)
        if "restore_token" in res:
            TOKEN_FILE.parent.mkdir(parents=True, exist_ok=True)
            TOKEN_FILE.write_text(res["restore_token"])
            TOKEN_FILE.chmod(0o600)
        streams = res.get("streams") or []
        if not streams:
            self.stop()
            raise RuntimeError("portal não devolveu nenhum stream de tela")
        self.node_id, props = streams[0]
        self.stream_size = tuple(props.get("size", (0, 0)))
        self._open_pipeline()
        self._warm_up()
        return {"stream_size": list(self.stream_size), "node": int(self.node_id)}

    def _warm_up(self) -> None:
        """O 1º evento de teclado após abrir a sessão do RemoteDesktop é descartado pelo compositor
        (medido: `XYZ789` virava `YZ789`; com um Shift antes, saiu inteiro). Shift sozinho não
        digita nada nem aciona atalho: gasta esse evento sem efeito colateral."""
        self.keysym(0xFFE1, 1)
        self.keysym(0xFFE1, 0)
        time.sleep(0.15)

    def _open_pipeline(self) -> None:
        Gst.init(None)
        reply, fds = self.bus.call_with_unix_fd_list_sync(
            PORTAL, PORTAL_PATH, "org.freedesktop.portal.ScreenCast", "OpenPipeWireRemote",
            GLib.Variant("(oa{sv})", (self.session, {})), GLib.VariantType("(h)"),
            Gio.DBusCallFlags.NONE, 10000, None, None)
        fd = fds.get(reply.unpack()[0])
        # appsink guarda só o último frame (drop=true, 1 buffer): captura sempre pega o mais novo.
        # (o protótipo usava fakesink + "signal-handoff", propriedade que não existe: o correto
        # era "signal-handoffs"; o pipeline nunca subia com o portal real.)
        desc = (f"pipewiresrc fd={fd} path={self.node_id} do-timestamp=true ! videoconvert ! "
                "video/x-raw,format=RGB ! appsink name=sink emit-signals=true max-buffers=1 "
                "drop=true sync=false")
        self.pipeline = Gst.parse_launch(desc)
        sink = self.pipeline.get_by_name("sink")

        def on_sample(appsink):
            sample = appsink.emit("pull-sample")
            if sample is None:
                return Gst.FlowReturn.OK
            st = sample.get_caps().get_structure(0)
            buf = sample.get_buffer()
            ok, info = buf.map(Gst.MapFlags.READ)
            if not ok:
                return Gst.FlowReturn.OK
            try:
                with self._frame_lock:
                    self.frame = (bytes(info.data), st.get_value("width"),
                                  st.get_value("height"), time.time())
            finally:
                buf.unmap(info)
            return Gst.FlowReturn.OK

        sink.connect("new-sample", on_sample)
        self.pipeline.set_state(Gst.State.PLAYING)

    def _on_closed(self, *_a) -> None:
        self.closed = True
        _log("sessão encerrada (usuário parou o compartilhamento ou o compositor a fechou)")

    def _check_open(self) -> None:
        if self.closed:
            raise RuntimeError("SESSION_CLOSED: o compartilhamento foi encerrado; o controle parou")

    # --- comandos -------------------------------------------------------------------
    def save_frame(self, path: str, wait_s: float = 5.0) -> dict:
        from PIL import Image

        self._check_open()
        t0 = time.time()
        while time.time() - t0 < wait_s:
            with self._frame_lock:
                fr = self.frame
            if fr:
                break
            time.sleep(0.05)
        else:
            raise TimeoutError("nenhum frame recebido do PipeWire")
        data, w, h, ts = fr
        stride = len(data) // h
        img = Image.frombuffer("RGB", (w, h), data, "raw", "RGB", stride, 1)
        img.save(path)
        return {"size": [w, h], "frame_age_ms": int((time.time() - ts) * 1000)}

    def _notify(self, method: str, sig: str, *args) -> None:
        self._check_open()
        self.bus.call_sync(PORTAL, PORTAL_PATH, "org.freedesktop.portal.RemoteDesktop", method,
                           GLib.Variant(f"(oa{{sv}}{sig})", (self.session, {}, *args)),
                           None, Gio.DBusCallFlags.NONE, 5000, None)

    def move(self, x: float, y: float) -> None:
        fw, fh = self._frame_size()
        sw, sh = self.stream_size if all(self.stream_size) else (fw, fh)
        self._notify("NotifyPointerMotionAbsolute", "udd", self.node_id,
                     float(x) * sw / fw, float(y) * sh / fh)

    def _frame_size(self) -> tuple[int, int]:
        with self._frame_lock:
            fr = self.frame
        if not fr:
            raise RuntimeError("sem frame: capture antes de mover o ponteiro")
        return fr[1], fr[2]

    def button(self, code: int, state: int) -> None:
        self._notify("NotifyPointerButton", "iu", int(code), int(state))

    def axis(self, axis: int, steps: int) -> None:
        self._notify("NotifyPointerAxisDiscrete", "ui", int(axis), int(steps))

    def keysym(self, sym: int, state: int) -> None:
        self._notify("NotifyKeyboardKeysym", "iu", int(sym), int(state))

    def stop(self) -> None:
        try:
            if self.pipeline:
                self.pipeline.set_state(Gst.State.NULL)
            if self.session:
                self.bus.call_sync(PORTAL, self.session, "org.freedesktop.portal.Session",
                                   "Close", None, None, Gio.DBusCallFlags.NONE, 3000, None)
        except Exception:
            pass


def main() -> None:
    b = Bridge()

    def reply(obj: dict) -> None:
        print(json.dumps(obj), flush=True)

    for line in sys.stdin:
        try:
            req = json.loads(line)
            cmd = req["cmd"]
            if cmd == "start":
                reply({"ok": True, **b.start()})
            elif cmd == "frame":
                reply({"ok": True, **b.save_frame(req["path"])})
            elif cmd == "move":
                b.move(req["x"], req["y"])
                reply({"ok": True})
            elif cmd == "button":
                b.button(req["code"], req["state"])
                reply({"ok": True})
            elif cmd == "axis":
                b.axis(req["axis"], req["steps"])
                reply({"ok": True})
            elif cmd == "keysym":
                b.keysym(req["sym"], req["state"])
                reply({"ok": True})
            elif cmd == "a11y":
                import a11y_atspi

                reply({"ok": True, **a11y_atspi.snapshot(
                    max_elements=int(req.get("max_elements", 120)),
                    timeout=float(req.get("timeout", 3.0)))})
            elif cmd == "stop":
                b.stop()
                reply({"ok": True})
                return
            else:
                reply({"ok": False, "error": f"cmd desconhecido: {cmd}"})
        except Exception as e:  # nunca morre calado: o cliente vê o erro
            reply({"ok": False, "error": f"{type(e).__name__}: {e}"[:300]})
    b.stop()


if __name__ == "__main__":
    main()
