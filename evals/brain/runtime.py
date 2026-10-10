"""Launcher opt-in da bancada: apenas arquivos instalados, nunca baixa recursos."""

import argparse
import hashlib
import signal
import subprocess
import time
from pathlib import Path

from evals.brain.metrics import VramMonitor
from evals.brain.runner import save
from server import _lmstudio_candidates, _runtime_env


def launch(
    model: str,
    gguf: Path,
    mmproj: Path | None,
    output: Path,
    port: int = 8091,
    context: int = 8192,
    kv: str = "q8_0",
    ngl: int = 99,
    ubatch: int | None = None,
):
    if context not in {4096, 8192, 16384} or kv not in {"q8_0", "q4_0"}:
        raise ValueError("contexto ou KV fora da matriz")
    if not 0 <= ngl <= 99 or ubatch not in {None, 128, 256, 512}:
        raise ValueError("ngl ou ubatch fora da matriz")
    for path in [gguf] + ([mmproj] if mmproj else []):
        if not path.is_file():
            raise ValueError(f"arquivo instalado ausente: {path}")
    candidates = _lmstudio_candidates("cuda")
    if not candidates:
        raise ValueError("llama-server CUDA local ausente; nenhum download foi feito")
    exe = candidates[0]
    command = [
        str(exe),
        "-m",
        str(gguf),
        "--alias",
        model,
        "--host",
        "127.0.0.1",
        "--port",
        str(port),
        "-ngl",
        str(ngl),
        "-c",
        str(context),
        "-np",
        "1",
        "-ctk",
        kv,
        "-ctv",
        kv,
        "-fa",
        "on",
        "-t",
        "6",
        "--jinja",
        "--reasoning-format",
        "deepseek",
    ]
    if ubatch:
        command += ["-ub", str(ubatch)]
    if mmproj:
        command += ["--mmproj", str(mmproj)]
    else:
        command += ["--no-mmproj"]
    output.mkdir(parents=True, exist_ok=True)
    with (output / "server.log").open("w") as log:
        process = subprocess.Popen(command, env=_runtime_env(exe), stdout=log, stderr=log)
        metadata = {
            "model": model,
            "pid": process.pid,
            "command": command,
            "gguf": str(gguf),
            "mmproj": str(mmproj) if mmproj else None,
            "context": context,
            "kv": kv,
            "ngl": ngl,
            "ubatch": ubatch,
            "binary_sha256": hashlib.sha256(exe.read_bytes()).hexdigest(),
            "peak_vram_mib": None,
        }
        print(f"llama-server PID {process.pid}; metadados: {output / 'runtime.json'}", flush=True)

        def stop(*_):
            if process.poll() is None:
                process.terminate()

        signal.signal(signal.SIGTERM, stop)
        signal.signal(signal.SIGINT, stop)
        with VramMonitor(process.pid) as monitor:
            try:
                while process.poll() is None:
                    metadata["peak_vram_mib"] = monitor.peak_mib
                    save(output / "runtime.json", metadata)
                    time.sleep(1)
            finally:
                stop()
                try:
                    process.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=5)
                metadata.update(exit_code=process.returncode, peak_vram_mib=monitor.peak_mib)
                save(output / "runtime.json", metadata)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Runtime opt-in da bancada (somente instalado)")
    parser.add_argument("--model", required=True)
    parser.add_argument("--gguf", type=Path, required=True)
    parser.add_argument("--mmproj", type=Path)
    parser.add_argument("--output", type=Path, default=Path("runs/brain-runtime"))
    parser.add_argument("--port", type=int, default=8091)
    parser.add_argument("--context", type=int, choices=[4096, 8192, 16384], default=8192)
    parser.add_argument("--kv", choices=["q8_0", "q4_0"], default="q8_0")
    parser.add_argument("--ngl", type=int, default=99, help="camadas na GPU (offload parcial)")
    parser.add_argument("--ubatch", type=int, choices=[128, 256, 512])
    args = parser.parse_args()
    launch(
        args.model,
        args.gguf,
        args.mmproj,
        args.output,
        args.port,
        args.context,
        args.kv,
        args.ngl,
        args.ubatch,
    )
