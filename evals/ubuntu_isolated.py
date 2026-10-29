"""Repetição da tarefa E2E do Ubuntu em sessões GNOME isoladas (teste dev, nunca o desktop).

Cada repetição sobe uma sessão nova e descartável: `dbus-run-session` + gnome-shell headless
(monitor virtual 1280x800) + PipeWire/WirePlumber, XDG_* em diretório temporário, nice 10 e
dois CPUs. Os modelos (llama-server próprio, pesos já baixados em `~/llm-bench/models`) sobem
uma vez e são reusados por todas as sessões: são HTTP sem estado de sessão.

Dentro da sessão, o controlador (Python do sistema, precisa de `gi`) lê um arquivo de comandos
próprio da sessão (`commands.jsonl`), abre o Editor de Texto, roda o agente, aprova o diálogo
do portal só pelo PID do portal no bus PRIVADO (rótulos PT e EN) e lê o texto final por AT-SPI.
Watchdog: 8 min ou memória disponível < 2 GiB encerram a sessão. O cleanup mata só processos
marcados com `TASKPILOT_TEST_RUN=<dir da sessão>` e confirma que nenhum sobrou.

Uso:
  uv run python -m evals.ubuntu_isolated --runs 10 --max-steps 6 --tag base
  uv run python -m evals.ubuntu_isolated --runs 3 --vision-max-tokens 1024 \
      --features dynschema,fewshot,plan
Saída: runs/ubuntu-iso-<tag>-<data>/ (uma pasta por sessão + results.json + results.md).
"""
from __future__ import annotations

import argparse
import json
import os
import re
import signal
import socket
import subprocess
import sys
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
GOAL_PREFIX = ("No Editor de Texto já aberto, use o mouse para clicar no botão + (nova aba), "
               "depois digite exatamente: ")
EXPECTED = "Olá, Ubuntu!"
DEADLINE_S = 480
MIN_AVAILABLE_KIB = 2 * 1024 ** 2
TAG_VAR = "TASKPILOT_TEST_RUN"
ALLOW_LABELS = ("Allow Remote Interaction", "Permitir interação remota")
SHARE_LABELS = ("Share", "Compartilhar")
PORTAL_IMPL = "org.freedesktop.impl.portal.desktop.gnome"
LLAMA = Path.home() / "llm-bench/rt/llama-b11954/llama-server"
MODELS = Path.home() / "llm-bench/models"
FIXTURE = Path(tempfile.gettempdir()) / "taskpilot-fixture" / "documento-inicial.txt"


# --- análise do log do agente (puro, testado) -------------------------------------------
def parse_agent_log(text: str) -> dict:
    """Resultado, passos e trilha de decisões a partir do stdout do `main.py`."""
    summary: dict = {}
    m = re.search(r"^\{\n.*?^\}", text, re.S | re.M)
    if m:
        try:
            summary = json.loads(m.group(0))
        except ValueError:
            summary = {}
    decisions = re.findall(r"^\[\d+\] PLANNER (.+?)(?: \[viu:.*?\])? \d+ms$", text, re.M)
    verifies = re.findall(r"^\[\d+\] VERIFY (.+)$", text, re.M)
    return {
        "result": summary.get("result") or ("crash" if "Traceback" in text else "sem_resumo"),
        "steps": summary.get("steps"),
        "planner_calls": summary.get("planner_calls"),
        "total_s": summary.get("total_s"),
        "decisions": decisions,
        "last_verify": verifies[-1][:200] if verifies else "",
        "vision_truncated": len(re.findall(r"trunc", text, re.I)),
        "first_verify": verifies[0][:200] if verifies else "",
    }


def text_ok(texts: list[str], expected: str = EXPECTED) -> bool:
    """O texto pedido está, exatamente, em algum campo de texto do editor (puro)."""
    return any(t.strip("\n") == expected for t in texts)


def markdown_table(rows: list[dict]) -> str:
    head = ("| # | resultado | passos | texto final ok | abas | 1º verify | decisões | causa |\n"
            "|---|---|---|---|---|---|---|---|\n")
    lines = []
    for r in rows:
        dec = " → ".join(d[:40] for d in r.get("decisions", []))
        lines.append(f"| {r['n']} | {r['result']} | {r.get('steps')} | "
                     f"{'sim' if r.get('text_ok') else 'não'} | {r.get('tabs')} | "
                     f"{r.get('first_verify', '')[:60]} | {dec} | {r.get('cause', '')} |")
    return head + "\n".join(lines) + "\n"


# --- controlador: roda DENTRO da sessão, com /usr/bin/python3 ----------------------------
def controller() -> None:  # pragma: no cover - exige GNOME/portal reais
    import threading

    from gi.repository import Gio, GLib

    out = Path(os.environ[TAG_VAR])
    assert os.environ["XDG_RUNTIME_DIR"].startswith(tempfile.gettempdir() + "/taskpilot-e2e-")
    assert os.environ["WAYLAND_DISPLAY"] == "taskpilot-isolated" and "DISPLAY" not in os.environ
    procs, opened = [], []

    def start(cmd, name):
        f = (out / f"{name}.log").open("w")
        opened.append(f)
        p = subprocess.Popen(cmd, cwd=ROOT, stdout=f, stderr=subprocess.STDOUT,
                             start_new_session=True)
        procs.append(p)
        return p

    def log(obj):
        with (out / "controller.jsonl").open("a") as f:
            f.write(json.dumps(obj, ensure_ascii=False) + "\n")

    try:
        start(["pipewire"], "pipewire")
        start(["wireplumber"], "wireplumber")
        start(["gnome-shell", "--headless", "--no-x11", "--wayland", "--virtual-monitor",
               "1280x800", "--wayland-display", "taskpilot-isolated"], "gnome")
        time.sleep(6)
        bus = Gio.bus_get_sync(Gio.BusType.SESSION)
        loop = GLib.MainLoop()
        threading.Thread(target=loop.run, daemon=True).start()

        def call(dest, path, iface, method, args=None):
            return bus.call_sync(dest, path, iface, method, args, None,
                                 Gio.DBusCallFlags.NONE, 5000, None).unpack()

        rd_name = "org.gnome.Mutter.RemoteDesktop"
        rd = call(rd_name, "/org/gnome/Mutter/RemoteDesktop", rd_name, "CreateSession")[0]
        call(rd_name, rd, rd_name + ".Session", "Start")

        def key(sym):
            for down in (True, False):
                call(rd_name, rd, rd_name + ".Session", "NotifyKeyboardKeysym",
                     GLib.Variant("(ub)", (sym, down)))
            time.sleep(0.3)

        sc_name = "org.gnome.Mutter.ScreenCast"
        sc = call(sc_name, "/org/gnome/Mutter/ScreenCast", sc_name, "CreateSession",
                  GLib.Variant("(a{sv})", ({},)))[0]
        stream = call(sc_name, sc, sc_name + ".Session", "RecordMonitor",
                      GLib.Variant("(sa{sv})", ("", {"cursor-mode": GLib.Variant("u", 1)})))[0]
        props = call(sc_name, stream, "org.freedesktop.DBus.Properties", "GetAll",
                     GLib.Variant("(s)", (sc_name + ".Stream",)))[0]["Parameters"]
        assert props["output-name"] == "Meta-0" and tuple(props["size"]) == (1280, 800), props
        ready, node = threading.Event(), []

        def on_node(_c, _s, _p, _i, _sig, params, *_a):
            node.append(params.unpack()[0])
            ready.set()

        bus.signal_subscribe(sc_name, sc_name + ".Stream", "PipeWireStreamAdded", stream, None,
                             Gio.DBusSignalFlags.NONE, on_node)
        call(sc_name, sc, sc_name + ".Session", "Start")
        assert ready.wait(5), "nenhum nó PipeWire"
        (out / "isolation.json").write_text(json.dumps({
            "bus": os.environ["DBUS_SESSION_BUS_ADDRESS"], "runtime": os.environ["XDG_RUNTIME_DIR"],
            "wayland": os.environ["WAYLAND_DISPLAY"], "monitor": props,
            "cpu_affinity": sorted(os.sched_getaffinity(0))}, indent=2))

        def capture(name, tries=3):
            for i in range(tries):  # 1º frame às vezes não chega (3/10 em 29/10)
                try:
                    return capture_once(name)
                except RuntimeError:
                    if i == tries - 1:
                        raise
                    time.sleep(2)

        def capture_once(name):
            # gst-launch em processo próprio com timeout: no mesmo processo, um
            # set_state(NULL) travou a sessão até o watchdog (29/10).
            dest = out / f"{name}.png"
            try:
                subprocess.run(["gst-launch-1.0", "-q", "pipewiresrc", f"path={node[0]}",
                                "num-buffers=1", "!", "videoconvert", "!", "pngenc", "!",
                                "filesink", f"location={dest}"], timeout=12, check=True,
                               capture_output=True)
            except (subprocess.SubprocessError, OSError) as e:
                raise RuntimeError(f"captura falhou: {type(e).__name__}") from e
            if not dest.exists() or not dest.stat().st_size:
                raise RuntimeError("captura falhou: arquivo vazio")
            return str(dest)

        def approve() -> dict:
            return sub("approve")

        def sub(mode: str) -> dict:
            # AT-SPI em processo próprio: em processo, deu segfault no controlador (29/10).
            try:
                r = subprocess.run(["/usr/bin/python3", str(Path(__file__)), mode],
                                   capture_output=True, text=True, timeout=20)
                return json.loads(r.stdout.strip().splitlines()[-1])
            except (subprocess.SubprocessError, ValueError, IndexError) as e:
                return {"error": f"{mode}: {type(e).__name__}"}

        def editor_texts() -> dict:
            return sub("read")

        # Caminho fixo: o título da janela mostra a pasta, e o Qwen3-4B mudou de decisão
        # só porque o nome da pasta da sessão mudou (29/10). Sessões rodam uma por vez.
        fixture = FIXTURE
        fixture.parent.mkdir(exist_ok=True)
        fixture.write_text("")
        start(["gnome-text-editor", "--standalone", str(fixture)], "editor")
        time.sleep(2)
        def snap(name):  # screenshot do harness é diagnóstico: falhar não aborta a sessão
            try:
                return capture(name)
            except RuntimeError as e:
                log({"capture_error": name, "error": str(e)})
                return ""

        if not snap("initial"):  # sem frame do PipeWire: o agente também não teria
            log({"infra": "sem frame inicial do PipeWire"})
            return
        key(0xFF1B)  # preparo: o shell nasce na visão geral; Esc ativa a janela do editor
        time.sleep(1)
        snap("before")
        log({"ready": True, "before": editor_texts()})
        agent = None
        for line in (out / "commands.jsonl").read_text().splitlines():
            req = json.loads(line)
            cmd = req["cmd"]
            try:
                if cmd == "agent":
                    goal = GOAL_PREFIX + req.get("text", EXPECTED)
                    argv = req.get("argv") or [
                        "uv", "run", "--no-sync", "python", "main.py", goal, "--config",
                        str(out / "config.json"), "--platform", "wayland",
                        "--max-steps", str(req["max_steps"]), "--record"]
                    agent = start(argv, "agent")
                    approved, t0, last_diag = {}, time.monotonic(), 0.0
                    while agent.poll() is None and time.monotonic() - t0 < req.get("timeout", 300):
                        if not approved.get("share"):
                            try:
                                got = approve()
                            except Exception as e:  # diálogo fechando no meio do walk
                                got = {"error": str(e)[:200]}
                            if got and (got.get("share") or time.monotonic() - last_diag > 10):
                                last_diag = time.monotonic()
                                approved = got
                                log({"approve": got, "t": round(time.monotonic() - t0, 1)})
                        time.sleep(0.5)
                    if agent.poll() is None:
                        os.killpg(agent.pid, signal.SIGTERM)
                        log({"agent_timeout": True})
                    log({"agent_exit": agent.wait(10)})
                elif cmd == "capture":
                    log({"capture": capture(req.get("name", "frame"))})
                elif cmd == "sleep":
                    time.sleep(float(req.get("s", 1)))
                elif cmd == "read":
                    log({"final": editor_texts()})
                elif cmd == "quit":
                    break
            except Exception as e:
                log({"cmd": cmd, "error": f"{type(e).__name__}: {e}"[:300]})
    finally:
        for p in reversed(procs):
            if p.poll() is None:
                try:
                    os.killpg(p.pid, signal.SIGTERM)
                except ProcessLookupError:
                    pass
        for p in reversed(procs):
            try:
                p.wait(timeout=5)
            except subprocess.TimeoutExpired:
                os.killpg(p.pid, signal.SIGKILL)
                p.wait()
        for f in opened:
            f.close()


def approve_portal() -> dict:  # pragma: no cover - exige portal real
    """Liga 'Permitir interação remota' e aperta 'Compartilhar', só no app do PID do portal."""
    import gi

    gi.require_version("Atspi", "2.0")
    from gi.repository import Atspi, Gio, GLib

    assert os.environ["XDG_RUNTIME_DIR"].startswith(tempfile.gettempdir() + "/taskpilot-e2e-")
    bus = Gio.bus_get_sync(Gio.BusType.SESSION)
    try:
        pid = bus.call_sync("org.freedesktop.DBus", "/org/freedesktop/DBus", "org.freedesktop.DBus",
                            "GetConnectionUnixProcessID", GLib.Variant("(s)", (PORTAL_IMPL,)),
                            None, Gio.DBusCallFlags.NONE, 5000, None).unpack()[0]
    except GLib.Error:
        return {"diag": {"pid": None}}

    def kids(n):
        return [n.get_child_at_index(i) for i in range(n.get_child_count())]

    done: dict = {}

    def walk(n, depth=0):
        if depth > 30:
            return
        act = n.get_action_iface()
        if ((n.get_name() or "") in ALLOW_LABELS and n.get_role_name() == "switch"
                and act and act.get_n_actions()):
            if not n.get_state_set().contains(Atspi.StateType.CHECKED):
                act.do_action(0)
                time.sleep(0.2)
            done["allow"] = n.get_state_set().contains(Atspi.StateType.CHECKED)
        for c in kids(n):
            walk(c, depth + 1)

    def share(n, depth=0):
        if depth > 30 or "share" in done:
            return
        act = n.get_action_iface()
        if ((n.get_name() or "") in SHARE_LABELS and n.get_role_name() in ("button", "push button")
                and act and act.get_n_actions()):
            done["share"] = bool(act.do_action(0))
            return
        for c in kids(n):
            share(c, depth + 1)

    for app in kids(Atspi.get_desktop(0)):
        if app.get_process_id() == pid:
            walk(app)
            if done.get("allow"):  # só compartilha com o controle remoto ligado
                share(app)
    done.setdefault("diag", {"pid": pid})
    return done


def read_editor() -> dict:  # pragma: no cover - exige AT-SPI real
    """Textos, abas e janela ativa do Editor de Texto (leitura, sem input)."""
    import gi

    gi.require_version("Atspi", "2.0")
    from gi.repository import Atspi

    def kids(n):
        return [n.get_child_at_index(i) for i in range(n.get_child_count())]

    texts, tabs, active = [], 0, ""
    for app in kids(Atspi.get_desktop(0)):
        if "editor" not in (app.get_name() or "").lower():
            continue

        def walk(n, depth=0):
            nonlocal tabs
            if depth > 80:
                return
            if n.get_role_name() == "page tab":
                tabs += 1
            if n.get_role_name() == "text" and "Text" in n.get_interfaces():
                c = Atspi.Text.get_character_count(n)
                texts.append(Atspi.Text.get_text(n, 0, min(c, 8192)) or "")
            for c in kids(n):
                walk(c, depth + 1)

        for w in kids(app):
            if w.get_state_set().contains(Atspi.StateType.ACTIVE):
                active = w.get_name() or ""
            walk(w)
    return {"texts": texts, "tabs": tabs, "active": active}


# --- orquestrador: roda no venv (uv) ------------------------------------------------------
def _own_pids(out: Path) -> list[int]:
    tag = f"{TAG_VAR}={out}".encode()
    own = []
    for d in Path("/proc").iterdir():
        if d.name.isdigit():
            try:
                if tag in (d / "environ").read_bytes().split(b"\0"):
                    own.append(int(d.name))
            except OSError:
                pass
    return own


def _mem_available_kib() -> int:
    for line in Path("/proc/meminfo").read_text().splitlines():
        if line.startswith("MemAvailable:"):
            return int(line.split()[1])
    return 0


def run_session(out: Path, cfg: dict, max_steps: int,
                cmds: list[dict] | None = None) -> dict:  # pragma: no cover
    out.mkdir(parents=True)
    (out / "config.json").write_text(json.dumps(cfg, indent=2))
    cmds = cmds or [{"cmd": "agent", "max_steps": max_steps, "timeout": DEADLINE_S - 90},
            {"cmd": "capture", "name": "final"}, {"cmd": "read"}, {"cmd": "quit"}]
    (out / "commands.jsonl").write_text("".join(json.dumps(c) + "\n" for c in cmds))
    watchdog: dict = {}
    with tempfile.TemporaryDirectory(prefix="taskpilot-e2e-", ignore_cleanup_errors=True) as tmp:
        root = Path(tmp)
        runtime = root / "runtime"
        runtime.mkdir(mode=0o700)
        env = {**os.environ, TAG_VAR: str(out), "XDG_RUNTIME_DIR": str(runtime),
               "XDG_CONFIG_HOME": str(root / "config"), "XDG_CACHE_HOME": str(root / "cache"),
               "XDG_DATA_HOME": str(root / "data"), "XDG_STATE_HOME": str(root / "state"),
               "XDG_CURRENT_DESKTOP": "GNOME", "XDG_SESSION_TYPE": "wayland",
               "WAYLAND_DISPLAY": "taskpilot-isolated", "GDK_BACKEND": "wayland"}
        for k in ("DISPLAY", "AT_SPI_BUS_ADDRESS", "XDG_SESSION_ID", "DBUS_STARTER_ADDRESS",
                  "DBUS_STARTER_BUS_TYPE", "PIPEWIRE_REMOTE", "PULSE_SERVER",
                  "TASKPILOT_PLATFORM", "DBUS_SESSION_BUS_ADDRESS"):
            env.pop(k, None)
        proc = subprocess.Popen(["dbus-run-session", "--", "/usr/bin/python3", str(Path(__file__)),
                                 "controller"], env=env, cwd=ROOT, start_new_session=True,
                                stdout=(out / "session.log").open("w"), stderr=subprocess.STDOUT)
        deadline = time.monotonic() + DEADLINE_S
        try:
            while proc.poll() is None:
                avail = _mem_available_kib()
                if avail < MIN_AVAILABLE_KIB or time.monotonic() > deadline:
                    watchdog = {"stopped": True, "available_kib": avail,
                                "reason": "memory" if avail < MIN_AVAILABLE_KIB else "deadline"}
                    break
                try:
                    proc.wait(timeout=1)
                except subprocess.TimeoutExpired:
                    pass
        finally:
            pids = _own_pids(out)
            for pid in pids:
                try:
                    os.kill(pid, signal.SIGTERM)
                except ProcessLookupError:
                    pass
            try:
                proc.wait(timeout=10)
            except subprocess.TimeoutExpired:
                os.killpg(proc.pid, signal.SIGKILL)
                proc.wait()
            time.sleep(1)
            for pid in _own_pids(out):
                try:
                    os.kill(pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
            time.sleep(0.5)
            left = _own_pids(out)
            (out / "cleanup.json").write_text(json.dumps(
                {"test_pids": pids, "left_after_cleanup": left, "confirmed": not left,
                 "exit_code": proc.returncode, "watchdog": watchdog}))
    log_path = out / "agent.log"
    agent_log = log_path.read_text(errors="replace") if log_path.exists() else ""
    row = parse_agent_log(agent_log)
    final: dict = {}
    approve: dict = {}
    if (out / "controller.jsonl").exists():
        for line in (out / "controller.jsonl").read_text().splitlines():
            ev = json.loads(line)
            final = ev.get("final", final)
            approve = ev.get("approve", approve)
    row.update(text_ok=text_ok(final.get("texts", [])), tabs=final.get("tabs"),
               texts=final.get("texts"), approve=approve, watchdog=watchdog,
               cleanup_confirmed=not left)
    if watchdog:
        row["result"] = f"watchdog_{watchdog['reason']}"
    elif not agent_log or "nenhum frame recebido do PipeWire" in agent_log:
        row["result"] = "infra"  # a sessão falhou antes do agente; ver session.log
    return row


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def start_models(out: Path, entries: list[dict]) -> tuple[list, list[str]]:  # pragma: no cover
    from evals.budget_bench import server_cmd, wait_ready

    procs, urls = [], []
    for e in entries:
        port = _free_port()
        p = subprocess.Popen(server_cmd(str(LLAMA), MODELS, e, port),
                             stdout=(out / f"{e['alias']}.log").open("w"), stderr=subprocess.STDOUT)
        procs.append(p)
        if not wait_ready(p, port, timeout=120):
            raise RuntimeError(f"modelo não iniciou: {e['alias']}")
        urls.append(f"http://127.0.0.1:{port}/v1")
    return procs, urls


def agent_config(urls: list[str], features: list[str], vision_max_tokens: int,
                 max_steps: int) -> dict:
    return {"profile": "B1",
            "planner": {"base_url": urls[0], "model": "Qwen3-4B-Instruct-2507", "temperature": 0,
                        "features": features, "timeout_s": 45},
            "vision": {"base_url": urls[1], "model": "LFM3-VL-2B", "protocol": "p2d",
                       "coords": "1000", "max_tokens": vision_max_tokens, "timeout_s": 90,
                       "verifier": {"base_url": urls[2], "threshold": 0.2}},
            "runtime": {"auto_start": False}, "max_steps": max_steps,
            "screenshot_max_width": 1280, "verify_wait_ms": 500, "record": True,
            "platform": {"backend": "wayland"}}


MODEL_ENTRIES = [
    {"alias": "Qwen3-4B-Instruct-2507", "gguf": "Qwen3-4B-Instruct-2507-Q4_K_M.gguf", "ctx": 4096,
     "args": ["-t", "2", "--jinja"]},
    {"alias": "LFM3-VL-2B", "gguf": "LFM3-VL-2B-QAD-Q4_0.gguf",
     "mmproj": "mmproj-LFM3-VL-2B-F16.gguf", "ctx": 4096,
     "args": ["--no-mmproj-offload", "-t", "2"]},
    {"alias": "d1", "gguf": "d1-2B-Q4_K_M.gguf", "mmproj": "mmproj-d1-2B-F16.gguf", "ctx": 1024,
     "kv": "f16", "ngl": 0, "args": ["--no-mmproj-offload", "-t", "2"]},
]


def main(argv: list[str] | None = None) -> int:  # pragma: no cover
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--runs", type=int, default=10)
    ap.add_argument("--max-steps", type=int, default=6)
    ap.add_argument("--vision-max-tokens", type=int, default=512)
    ap.add_argument("--features", default="dynschema,fewshot,plan,why")
    ap.add_argument("--tag", default="base")
    args = ap.parse_args(argv)
    if not sys.platform.startswith("linux"):
        print("só Linux com gnome-shell headless")
        return 2
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    top = ROOT / "runs" / f"ubuntu-iso-{args.tag}-{stamp}"
    top.mkdir(parents=True)
    os.nice(10)
    cpus = sorted(os.sched_getaffinity(0))
    os.sched_setaffinity(0, cpus[-2:])
    feats = [f for f in args.features.split(",") if f]
    cfg_meta = {"runs": args.runs, "max_steps": args.max_steps, "features": feats,
                "vision_max_tokens": args.vision_max_tokens}
    (top / "params.json").write_text(json.dumps(cfg_meta, indent=2))
    procs: list = []
    rows: list[dict] = []
    try:
        procs, urls = start_models(top, MODEL_ENTRIES)
        cfg = agent_config(urls, feats, args.vision_max_tokens, args.max_steps)
        n, infra = 0, 0
        while n < args.runs and infra <= args.runs:
            row = run_session(top / f"s{n + 1:02d}" + (f"-infra{infra}" if infra else ""),
                              cfg, args.max_steps)
            if row["result"] in ("infra", "watchdog_deadline") and not row["decisions"]:
                infra += 1  # sessão quebrou antes do agente decidir: repete, conta à parte
                (top / "infra.json").write_text(json.dumps({"infra_sessions": infra}))
                continue
            n += 1
            row["n"] = n
            rows.append(row)
            print(json.dumps({k: row[k] for k in ("n", "result", "steps", "text_ok")}), flush=True)
            (top / "results.json").write_text(json.dumps(rows, indent=2, ensure_ascii=False))
            (top / "results.md").write_text(markdown_table(rows))
    finally:
        for p in procs:
            if p.poll() is None:
                p.terminate()
        for p in procs:
            try:
                p.wait(timeout=10)
            except subprocess.TimeoutExpired:
                p.kill()
                p.wait()
    done = sum(r["result"] == "done" and r.get("text_ok") for r in rows)
    print(f"done com texto certo: {done}/{len(rows)}  -> {top}")
    return 0


if __name__ == "__main__":
    if sys.argv[1:2] == ["controller"]:
        controller()
    elif sys.argv[1:2] == ["approve"]:
        print(json.dumps(approve_portal()))
    elif sys.argv[1:2] == ["read"]:
        print(json.dumps(read_editor(), ensure_ascii=False))
    else:
        raise SystemExit(main())
