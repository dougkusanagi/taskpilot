"""Roda baterias do model_bench com os modelos presos ao orçamento de uma GPU pequena.

Ferramenta de DESENVOLVIMENTO. Para cada configuração do plano JSON:
1. segura a VRAM excedente com `evals.vram_budget` (placa emulada, ex.: 6 GB - 1,5 GB);
2. sobe um `llama-server` por modelo, com GPU total e `--fit off` (sem cair para CPU em
   silêncio). Se não couber, a configuração é registrada como "não coube";
3. roda as suítes pedidas contra o servidor certo, amostrando a VRAM só dos processos dos
   modelos (pico por configuração, com todos os modelos carregados juntos);
4. encerra tudo e grava `runs/budget-<data>/resumo.json` + `resumo.md`.

    uv run python -m evals.budget_bench --plan evals/budget_plan.json \\
        --llama ~/llm-bench/rt/llama-b11954/llama-server --models ~/llm-bench/models

Não toca mouse/teclado/tela. Não prova E2E nem o comportamento do Windows (WDDM transborda
para a RAM em vez de falhar).
"""
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import threading
import time
from datetime import datetime, timezone
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parent.parent
MIN_GPU_MB = 300  # abaixo disso por servidor, os pesos não estão na GPU


def server_cmd(llama: str, models_dir: Path, srv: dict, port: int) -> list[str]:
    """Linha de comando do llama-server para uma entrada do plano (puro)."""
    cmd = [llama, "-m", str(models_dir / srv["gguf"]), "--port", str(port),
           "--host", "127.0.0.1", "--alias", srv["alias"],
           "-ngl", str(srv.get("ngl", 99)), "--fit", "off",
           "-c", str(srv.get("ctx", 4096)), "-np", "1",
           "-ctk", srv.get("kv", "q8_0"), "-ctv", srv.get("kv", "q8_0"), "-fa", "on"]
    if srv.get("mmproj"):
        cmd += ["--mmproj", str(models_dir / srv["mmproj"])]
    if srv.get("reasoning_budget") is not None:
        cmd += ["--reasoning-budget", str(srv["reasoning_budget"])]
    return cmd + [str(a) for a in srv.get("args", [])]


def suite_cmd(run: dict, url: str, model: str, out: Path) -> list[str]:
    """Linha de comando do model_bench para uma suíte (puro)."""
    cmd = [sys.executable, "-m", "evals.model_bench", "--suite", run["suite"], "--url", url,
           "--model", model, "--reps", str(run.get("reps", 1)), "--out", str(out),
           "--thinking", run.get("thinking", "native"),
           "--max-tokens", str(run.get("max_tokens", 2048)),
           "--timeout", str(run.get("timeout", 180))]
    if run.get("features"):
        cmd += ["--features", run["features"]]
    if run.get("limit"):
        cmd += ["--limit", str(run["limit"])]
    return cmd


def verify_cmd(run: dict, urls: dict, out: Path) -> list[str]:
    """Linha de comando da bateria localizador + verificador (puro)."""
    return [sys.executable, "-m", "evals.verify_bench", "--grounder-url", urls[run["model"]],
            "--grounder-model", run["model"], "--verifier-url", urls[run["verifier"]],
            "--protocol", run.get("protocol", "p2d"),
            "--max-tokens", str(run.get("max_tokens", 512)), "--reps", str(run.get("reps", 1)),
            "--out", str(out)]


def verify_summary(out: Path) -> dict:
    try:
        gates = json.loads((out / "rows.json").read_text(encoding="utf-8"))["gates"]
    except (OSError, ValueError, KeyError):
        return {"error": "sem rows.json"}
    return {"extra": {f"limite {g['threshold']:.2f}": f"{g['hits']}/{g['present']} acertos, "
                      f"{g['wrong_clicks']} cliques errados" for g in gates}}


class PidSampler:
    """Pico de VRAM somado de um conjunto de PIDs (nvidia-smi --query-compute-apps)."""

    def __init__(self, pids: list[int]):
        self.pids, self.peak, self._stop = set(pids), 0, threading.Event()
        self._thread = threading.Thread(target=self._loop, daemon=True)

    @staticmethod
    def usage(pids: set[int]) -> int:
        try:
            out = subprocess.run(
                ["nvidia-smi", "--query-compute-apps=pid,used_memory",
                 "--format=csv,noheader,nounits"],
                capture_output=True, text=True, timeout=5, check=True).stdout
        except (OSError, subprocess.SubprocessError):
            return 0
        return sum(int(m) for p, m in (ln.split(",") for ln in out.splitlines() if "," in ln)
                   if int(p) in pids)

    def _loop(self):
        while not self._stop.wait(.5):
            self.peak = max(self.peak, self.usage(self.pids))

    def __enter__(self):
        self.peak = self.usage(self.pids)
        self._thread.start()
        return self

    def __exit__(self, *exc):
        self._stop.set()
        self._thread.join(timeout=2)


def wait_ready(proc: subprocess.Popen, port: int, timeout: float = 240) -> bool:
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if proc.poll() is not None:
            return False
        try:
            if httpx.get(f"http://127.0.0.1:{port}/health", timeout=2,
                         trust_env=False).status_code == 200:
                return True
        except httpx.HTTPError:
            pass
        time.sleep(1)
    return False


def oom_reason(log: str) -> str:
    """Motivo curto da falha de carga a partir do log do servidor (puro)."""
    for pat in (r"out of memory", r"failed to allocate", r"cudaMalloc failed",
                r"unknown model architecture[^\n]*", r"error loading model[^\n]*"):
        m = re.search(pat, log, re.I)
        if m:
            return m.group(0)[:160]
    return "servidor não ficou pronto"


def summary_of(out: Path) -> dict:
    """Taxa, latência e extras do relatório do model_bench (puro sobre o arquivo)."""
    try:
        data = json.loads((out / "summary.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {"error": "sem summary.json"}
    groups = data.get("results") or []
    if not groups:
        return {"error": "sem grupos"}
    s = groups[0]["summary"]
    lat = s.get("latency_ms") or {}
    return {"passed": s.get("passed"), "attempts": s.get("planned"),
            "pass_rate": s.get("success_rate"), "p50_ms": lat.get("p50"),
            "p95_ms": lat.get("p95"), "execution_status": s.get("execution_status"),
            "extra": s.get("extra", {})}


def run_config(cfg: dict, args, base: Path) -> dict:
    result = {"name": cfg["name"], "servers": cfg["servers"], "runs": []}
    procs, logs = [], []
    try:
        for i, srv in enumerate(cfg["servers"]):
            port = args.port + i
            log = base / f"{cfg['name']}-{srv['alias']}.log"
            fh = log.open("w")
            proc = subprocess.Popen(server_cmd(args.llama, args.models, srv, port),
                                    stdout=fh, stderr=subprocess.STDOUT)
            procs.append(proc)
            logs.append(log)
            if not wait_ready(proc, port):
                text = log.read_text(errors="replace")
                result.update(fits=False, reason=f"{srv['alias']}: {oom_reason(text)}")
                return result
        gpu_mb = PidSampler.usage({p.pid for p in procs})
        if gpu_mb < MIN_GPU_MB * sum(s.get("ngl", 99) > 0 for s in cfg["servers"]):
            # Runtime sem CUDA (ex.: biblioteca ausente) roda na CPU em silêncio: não medir.
            result.update(fits=False, reason=f"servidores usam só {gpu_mb} MB de GPU; "
                                             "runtime sem aceleração")
            return result
        result["fits"] = True
        with PidSampler([p.pid for p in procs]) as sampler:
            result["vram_idle_mb"] = sampler.peak
            for run in cfg["runs"]:
                urls = {s["alias"]: f"http://127.0.0.1:{args.port + i}/v1"
                        for i, s in enumerate(cfg["servers"])}
                out = base / f"{cfg['name']}-{run['suite']}-{run.get('tag', run['model'])}"
                cmd = (verify_cmd(run, urls, out) if run["suite"] == "verify"
                       else suite_cmd(run, urls[run["model"]], run["model"], out))
                t0 = time.monotonic()
                code = subprocess.run(cmd, cwd=ROOT, stdout=subprocess.DEVNULL,
                                      stderr=subprocess.DEVNULL).returncode
                alive = all(p.poll() is None for p in procs)
                result["runs"].append({**run, "exit": code, "seconds": round(time.monotonic() - t0),
                                       "servers_alive": alive,
                                       **(verify_summary(out) if run["suite"] == "verify"
                                          else summary_of(out))})
                print(f"  {cfg['name']} {run['suite']} {run.get('tag', '')}: "
                      f"{result['runs'][-1].get('pass_rate')} exit={code}", flush=True)
            result["vram_peak_mb"] = sampler.peak
        return result
    finally:
        for p in procs:
            p.terminate()
        for p in procs:
            try:
                p.wait(timeout=20)
            except subprocess.TimeoutExpired:
                p.kill()


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--plan", type=Path, required=True)
    ap.add_argument("--llama", required=True, help="caminho do llama-server")
    ap.add_argument("--models", type=Path, required=True, help="diretório dos GGUFs")
    ap.add_argument("--card-mb", type=int, default=6144)
    ap.add_argument("--reserve-mb", type=int, default=1536)
    ap.add_argument("--port", type=int, default=8191)
    ap.add_argument("--only", default="", help="nomes de configurações, separados por vírgula")
    args = ap.parse_args(argv)
    args.llama = os.path.expanduser(args.llama)
    args.models = args.models.expanduser()
    plan = json.loads(args.plan.read_text(encoding="utf-8"))
    only = {n for n in args.only.split(",") if n}
    configs = [c for c in plan["configs"] if not only or c["name"] in only]
    base = ROOT / "runs" / ("budget-" + datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S"))
    base.mkdir(parents=True)
    ballast = subprocess.Popen([sys.executable, "-m", "evals.vram_budget", "--card-mb",
                                str(args.card_mb), "--reserve-mb", str(args.reserve_mb)],
                               cwd=ROOT, stdout=subprocess.PIPE, text=True)
    line = ballast.stdout.readline().strip()
    print(line, flush=True)
    results = {"time_utc": datetime.now(timezone.utc).isoformat(), "card_mb": args.card_mb,
               "reserve_mb": args.reserve_mb, "ballast": line, "llama": args.llama,
               "configs": []}
    try:
        if ballast.poll() is not None:
            raise SystemExit("não foi possível reservar a VRAM excedente")
        for cfg in configs:
            print(f"== {cfg['name']}", flush=True)
            results["configs"].append(run_config(cfg, args, base))
            (base / "resumo.json").write_text(json.dumps(results, ensure_ascii=False, indent=2),
                                              encoding="utf-8")
    finally:
        ballast.terminate()
        ballast.wait(timeout=20)
    (base / "resumo.md").write_text(render(results), encoding="utf-8")
    print(f"Resumo: {base / 'resumo.md'}")
    return 0


def render(results: dict) -> str:
    lines = [f"# Bancada com orçamento de VRAM ({results['card_mb']} MB, "
             f"{results['reserve_mb']} MB para o sistema)", "", results["ballast"], "",
             "| Configuração | Suíte | Variante | Acertos | p50 ms | p95 ms | Extras | VRAM pico |",
             "| --- | --- | --- | ---: | ---: | ---: | --- | ---: |"]
    for c in results["configs"]:
        if not c.get("fits"):
            lines.append(f"| {c['name']} | — | — | não coube: {c.get('reason', '')} | | | | |")
            continue
        for r in c["runs"]:
            extra = "; ".join(f"{k}: {v}" for k, v in (r.get("extra") or {}).items())
            if not r.get("servers_alive", True):
                extra = "SERVIDOR CAIU (falta de memória?); resultado inválido. " + extra
            rate = r.get("pass_rate")
            rate = f"{r.get('passed')}/{r.get('attempts')} ({rate:.1%})" if rate is not None \
                else r.get("error", "ver extras")
            lines.append(f"| {c['name']} | {r['suite']} | {r.get('tag', '')} | {rate} | "
                         f"{r.get('p50_ms')} | {r.get('p95_ms')} | {extra} | "
                         f"{c.get('vram_peak_mb')} MB |")
    return "\n".join(lines) + "\n"


if __name__ == "__main__":
    sys.exit(main())
