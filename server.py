"""Servidor próprio de modelos: baixa e sobe os 2 llama-server (planner+visão).

Meta: `uv run python main.py "..."` funciona SEM subir nada externo. O projeto
tem seu próprio runtime: na 1ª execução baixa o binário do `llama-server`
(llama.cpp, release Windows x64 CPU) e os GGUFs para `models/` (gitignored);
nas seguintes, só reusa. O "erro honesto" (loop.py) passa a valer só quando
algo de verdade falha (download, porta ocupada por servidor de outro modelo,
runtime ausente) — não quando ninguém subiu servidor externo.

Reuso: se a porta já tem um endpoint /v1/models vivo, usamos ele (seja de uma
execução anterior nossa, seja um llama-server que o usuário subiu apontado
para os mesmos GGUFs — não importa quem subiu). Porta ocupada SEM endpoint
vivo (ou com outro modelo) = erro honesto, nunca matamos processo alheio.
"""

from __future__ import annotations

import os
import shutil
import socket
import subprocess
import time
import urllib.parse
import urllib.request
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent
MODELS_DIR = ROOT / "models"
BIN_DIR = MODELS_DIR / "bin"

# Portas fora do padrão (config.json + AGENTS.md): não colidem com Ollama
# (11434), LM Studio (1234) nem OpenAI (443/80).
PLANNER_PORT = 8091
VISION_PORT = 8082

PLANNER_MODEL = "MiniCPM5-2B"
VISION_MODEL = "Vocaela-2-500M-1024R2"

# Fontes oficiais (model cards citados em planner.py/vocaela.py). URLs públicas
# (resolvidas sem token, conferidas por HEAD antes de implementar).
PLANNER_GGUF = {
    "file": "MiniCPM5-1B-Q4_K_M.gguf",  # ~657 MB
    "url": ("https://huggingface.co/openbmb/MiniCPM5-1B-GGUF/resolve/main/MiniCPM5-1B-Q4_K_M.gguf"),
}
# B1 (§3): planner 2B oficial, mesma arquitetura/flags do 1B (--jinja +
# enable_thinking=false). ~1,5 GB; no CPU é ~2x mais lento por token.
PLANNER_2B_GGUF = {
    "file": "MiniCPM5-2B-Q4_K_M.gguf",
    "url": ("https://huggingface.co/openbmb/MiniCPM5-2B-GGUF/resolve/main/MiniCPM5-2B-Q4_K_M.gguf"),
}
VISION_GGUF = {
    "file": "Vocaela-2-500M-1024R2-Q8_0.gguf",  # ~437 MB
    "url": (
        "https://huggingface.co/vocaela/Vocaela-2-500M-1024R2-GGUF/resolve/main/"
        "Vocaela-2-500M-1024R2-Q8_0.gguf"
    ),
}
VISION_MMPROJ = {
    "file": "mmproj-Vocaela-2-500M-1024R2-Q8_0.gguf",  # ~97 MB
    "url": (
        "https://huggingface.co/vocaela/Vocaela-2-500M-1024R2-GGUF/resolve/main/"
        "mmproj-Vocaela-2-500M-1024R2-Q8_0.gguf"
    ),
}

# U1: um unico Qwen3-VL faz planejamento visual e grounding. Os pesos GGUF
# sao a quantizacao Unsloth do checkpoint Qwen oficial; o projetor e exigido
# pelo llama.cpp para que a imagem nao seja silenciosamente ignorada.
QWEN3_VL_GGUF = {
    "file": "Qwen3-VL-2B-Instruct-Q4_K_M.gguf",
    "url": (
        "https://huggingface.co/unsloth/Qwen3-VL-2B-Instruct-GGUF/"
        "resolve/main/Qwen3-VL-2B-Instruct-Q4_K_M.gguf"
    ),
}
QWEN3_VL_MMPROJ = {
    "file": "mmproj-Qwen3-VL-2B-Instruct-F16.gguf",
    "url": (
        "https://huggingface.co/unsloth/Qwen3-VL-2B-Instruct-GGUF/resolve/main/mmproj-F16.gguf"
    ),
}

# Release fixa do llama.cpp (binários Windows x64 CPU); atualize o tag de vez
# em quando. CPU-only de propósito: roda em qualquer PC; quem quiser GPU pode
# subir llama-server externo — o reuso por porta cobre isso.
LLAMA_TAG = "b11053"
LLAMA_ZIP_URL = (
    "https://github.com/ggml-org/llama.cpp/releases/download/"
    f"{LLAMA_TAG}/llama-{LLAMA_TAG}-bin-win-cpu-x64.zip"
)
LLAMA_EXE = BIN_DIR / "llama-server.exe"
BACKEND_MARK = BIN_DIR / ".backend"  # backend do llama-server extraído (cpu|vulkan|cuda)

# Backends do llama.cpp (assets da release LLAMA_TAG, conferidos na API do GitHub):
#  cpu    18 MB  roda em qualquer PC (padrão histórico; ngl não tem efeito)
#  vulkan 32 MB  GPU NVIDIA/AMD/Intel só com o driver; ~60-80% da velocidade do CUDA
#  cuda   ~150 MB + cudart ~390 MB; a mais rápida em NVIDIA (precisa driver recente)
BACKENDS = ("cpu", "vulkan", "cuda")


def llama_urls(backend: str = "cpu") -> list[str]:
    """URLs do(s) zip(s) do llama.cpp para o backend (puro, testável)."""
    if backend not in BACKENDS:
        raise ValueError(f"runtime.backend inválido: {backend!r}; use {BACKENDS}")
    base = f"https://github.com/ggml-org/llama.cpp/releases/download/{LLAMA_TAG}/"
    if backend == "cuda":
        return [base + f"llama-{LLAMA_TAG}-bin-win-cuda-12.4-x64.zip",
                base + "cudart-llama-bin-win-cuda-12.4-x64.zip"]
    return [base + f"llama-{LLAMA_TAG}-bin-win-{backend}-x64.zip"]

# Parâmetros conservadores p/ máquina comum (CPU). Overridable via config.json
# → seção "runtime" (auto_start, host, ngl p/ GPU, threads, ctx).
DEFAULT_NGL = 0
DEFAULT_THREADS = max(2, (os.cpu_count() or 4) // 2)
DEFAULT_CTX = 4096
# Carga do GGUF (657 MB) com Defender/HDD pode levar minutos: _spawn_one faz
# polling até esse deadline em vez de uma única tentativa (que matava o
# servidor saudável ainda em `loading model` com um falso "não respondeu").
DEFAULT_STARTUP_TIMEOUT_S = 600.0


def _split_host_port(base_url: str) -> tuple[str, int]:
    """Host/porta de um base_url OpenAI-compatible (default 80 sem porta)."""
    netloc = urllib.parse.urlsplit(base_url if "://" in base_url else f"http://{base_url}").netloc
    host, sep, port = netloc.rpartition(":")
    if not sep or not port.isdigit():
        return (netloc or "127.0.0.1"), 80
    return (host.strip("[]") or "127.0.0.1"), int(port)


def needs_local_serve(base_urls: tuple[str, ...]) -> bool:
    """True se TODOS os base_urls apontam p/ esta máquina (127.0.0.1/localhost).

    URLs remotas (ex.: HOST_IP do Sandbox) são gerenciadas por quem as expõe:
    não baixamos nada nem subimos processo aqui (evitaria 1,2 GB dentro do
    repo mapeado do Sandbox)."""
    if not base_urls or not all(base_urls):
        return False
    return all(_split_host_port(u)[0] in ("127.0.0.1", "localhost", "::1") for u in base_urls)


# --- infra ---------------------------------------------------------------------
def _download(url: str, dest: Path, min_bytes: int = 1024 * 1024, progress=print) -> Path:
    """Baixa url -> dest (atômico via .part). Rejeita resposta < min_bytes
    (página de erro do HF/redirect quebra silencioso)."""
    dest.parent.mkdir(parents=True, exist_ok=True)
    part = dest.with_suffix(dest.suffix + ".part")
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "copilot-road-runner"})
        with urllib.request.urlopen(req, timeout=120) as r, part.open("wb") as f:
            total = r.headers.get("Content-Length")
            done = 0
            while True:
                chunk = r.read(1024 * 1024)
                if not chunk:
                    break
                f.write(chunk)
                done += len(chunk)
                if total and progress and done % (200 * 1024 * 1024) < 1024 * 1024:
                    progress(f"  {done / 1e6:.0f}/{int(total) / 1e6:.0f} MB")
        size = part.stat().st_size
        if size < min_bytes:
            raise RuntimeError(f"download suspeito ({size} bytes): {url}")
        part.replace(dest)
        return dest
    finally:
        part.unlink(missing_ok=True)


def _extract_zip(zip_path: Path, dest: Path) -> None:
    dest.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(zip_path) as zf:
        zf.extractall(dest)
    zip_path.unlink(missing_ok=True)  # ~230 MB: não guardar
    # O release empacota em subpasta (llama-<tag>-bin-win-cpu-x64/);
    # normaliza p/ BIN_DIR/llama-server.exe, que o resto do código espera.
    if not LLAMA_EXE.exists():
        for cand in dest.rglob("llama-server.exe"):
            if cand.resolve() != LLAMA_EXE.resolve():
                # sobe a pasta INTEIRA (as DLLs do ggml/CUDA precisam ficar ao lado do exe)
                for item in cand.parent.iterdir():
                    target = LLAMA_EXE.parent / item.name
                    if not target.exists():
                        item.replace(target)
            break


def _endpoint_alive(base_url: str, timeout_s: float = 5.0) -> dict | None:
    """/v1/models vivo? Retorna {"models": [...]} ou None."""
    import httpx

    try:
        r = httpx.get(f"{base_url.rstrip('/')}/models", timeout=timeout_s)
        if r.status_code == 200:
            return {"models": [m.get("id", "?") for m in r.json().get("data", [])]}
    except Exception:
        pass
    return None


def _models_match(alive: dict, expected: str) -> bool:
    """Aceita caminho/alias do llama.cpp, mas nunca outro checkpoint."""
    needle = expected.lower().replace("_", "-")
    return any(needle in str(model).lower().replace("_", "-") for model in alive.get("models", []))


def _port_in_use(host: str, port: int) -> bool:
    """connect-test (bind-test dá falso positivo no Windows via SO_REUSEADDR)."""
    try:
        with socket.create_connection((host, port), timeout=0.5):
            return True
    except OSError:
        return False


# --- assets (download único, gitignored em models/) ------------------------------
def planner_gguf_for(cfg: dict) -> dict:
    """GGUF do planner conforme o modelo configurado (B0=1B, B1=2B).

    Puro, testável. Modelo desconhecido = 1B (default seguro) — perfil novo
    (D/U/G/E) exige fiar o próprio GGUF antes de sair do inelegível.
    """
    model = str(cfg.get("planner", {}).get("model", "") or "")
    if "QWEN3-VL" in model.upper():
        return QWEN3_VL_GGUF
    if "2B" in model.upper().replace(" ", "").replace("-", ""):
        return PLANNER_2B_GGUF
    return PLANNER_GGUF


def ensure_assets(progress=print, cfg: dict | None = None) -> dict:
    """Baixa o que falta (llama.cpp + GGUFs) e extrai. Idempotente.

    Retorna Paths {exe, planner_gguf, vision_gguf, mmproj}.
    Erro honesto (RuntimeError) se download/extração falhar.
    """
    MODELS_DIR.mkdir(parents=True, exist_ok=True)

    backend = str((cfg or {}).get("runtime", {}).get("backend", "cpu"))
    urls = llama_urls(backend)
    have = BACKEND_MARK.read_text(encoding="utf-8").strip() if BACKEND_MARK.exists() else "cpu"
    if LLAMA_EXE.exists() and have != backend:
        progress(f"runtime.backend mudou ({have} -> {backend}): baixando outro llama.cpp...")
        shutil.rmtree(BIN_DIR, ignore_errors=True)
    if not LLAMA_EXE.exists():
        progress(f"baixando llama.cpp {LLAMA_TAG} ({backend}, 1ª vez)...")
        for n, url in enumerate(urls):
            zp = MODELS_DIR / f"llama{n}.zip"
            _download(url, zp, min_bytes=10_000_000, progress=progress)
            _extract_zip(zp, BIN_DIR)
        if not LLAMA_EXE.exists():
            raise RuntimeError(
                f"llama-server.exe não apareceu em {BIN_DIR} após extrair {urls}"
            )
        BACKEND_MARK.parent.mkdir(parents=True, exist_ok=True)
        BACKEND_MARK.write_text(backend, encoding="utf-8")

    selected = planner_gguf_for(cfg or {})
    unified_qwen = selected is QWEN3_VL_GGUF
    inv: dict[str, tuple[Path, dict, int]] = {
        "planner_gguf": (
            MODELS_DIR / planner_gguf_for(cfg or {})["file"],
            planner_gguf_for(cfg or {}),
            100_000_000,
        ),
    }
    if unified_qwen:
        inv["planner_mmproj"] = (MODELS_DIR / QWEN3_VL_MMPROJ["file"], QWEN3_VL_MMPROJ, 100_000_000)
    else:
        inv["vision_gguf"] = (MODELS_DIR / VISION_GGUF["file"], VISION_GGUF, 100_000_000)
        inv["mmproj"] = (MODELS_DIR / VISION_MMPROJ["file"], VISION_MMPROJ, 10_000_000)
    assets: dict[str, Path] = {"exe": LLAMA_EXE}
    for key, (dest, src, min_bytes) in inv.items():
        if not dest.exists():
            progress(f"baixando {src['file']} (1ª vez)...")
            _download(src["url"], dest, min_bytes=min_bytes, progress=progress)
        assets[key] = dest
    return assets


# --- spawn -----------------------------------------------------------------------
def _server_args(role: str, gguf: Path, mmproj: Path | None, port: int, cfg: dict) -> list[str]:
    rt = cfg.get("runtime", {})
    host = str(rt.get("host", "127.0.0.1"))
    ngl = int(rt.get("ngl", DEFAULT_NGL))
    threads = int(rt.get("threads", DEFAULT_THREADS))
    if threads <= 0:
        threads = DEFAULT_THREADS
    ctx = int(rt.get("ctx", DEFAULT_CTX))
    backend = str(rt.get("backend", "cpu"))
    if backend != "cpu" and ngl == 0:
        ngl = 99  # GPU: com ngl=0 o backend GPU rodaria tudo na CPU
    args = [
        str(LLAMA_EXE),
        "-m",
        str(gguf),
        "--host",
        host,
        "--port",
        str(port),
        "-c",
        str(ctx),
        "-t",
        str(threads),
        "-ngl",
        str(ngl),
    ]
    if mmproj is not None:
        args += ["--mmproj", str(mmproj)]
        if not bool(rt.get("mmproj_offload", True)):
            # encoder de imagem na CPU: libera ~0,8 GB e o buffer de computação na GPU
            args += ["--no-mmproj-offload"]
    parallel = int(rt.get("parallel", 0) or 0)
    if parallel > 0:
        args += ["-np", str(parallel)]  # 1 = uma geração por vez (sem slots extras de KV)
    kv = str(rt.get("kv_cache", "f16"))
    if kv != "f16":
        if kv not in ("q8_0", "q4_0"):
            raise ValueError(f"runtime.kv_cache inválido: {kv!r}; use f16, q8_0 ou q4_0")
        args += ["-fa", "on", "-ctk", kv, "-ctv", kv]  # KV quantizado exige flash attention
    if role == "planner":
        # jinja: o template oficial do MiniCPM5 (planner.py conta com ele p/
        # chat_template_kwargs.enable_thinking).
        args += ["--jinja"]
    return args


def _tail(log: Path, chars: int = 2000) -> str:
    """Últimas linhas não-vazias do log (diagnóstico de startup)."""
    try:
        lines = log.read_text(encoding="utf-8", errors="replace").splitlines()
        return "\n".join(ln for ln in lines if ln.strip())[-chars:]
    except Exception:
        return ""


def _wait_alive(
    role: str,
    base_url: str,
    proc: subprocess.Popen,
    log: Path,
    timeout_s: float = 600.0,
    poll_s: float = 2.0,
    progress=print,
) -> dict:
    """Poll `/v1/models` até o llama-server responder (puro, testável).

    O binário só escuta DEPOIS de carregar o GGUF (657 MB + Defender/HDD
    = minutos); uma única tentativa com timeout longo falha rápido em
    'connection refused' e matava o processo saudável. Aqui: deadline real,
    saída precoce detectada com a causa (tail do log), e nota de progresso
    a cada 30 s com a última linha do log.
    """
    deadline = time.monotonic() + max(1.0, timeout_s)
    last_note = 0.0
    while True:
        rc = proc.poll()
        if rc is not None:
            raise RuntimeError(
                f"llama-server ({role}) saiu cedo (code {rc}). log: {log}\n{_tail(log)}"
            )
        alive = _endpoint_alive(base_url, timeout_s=5.0)
        if alive:
            return alive
        now = time.monotonic()
        if now >= deadline:
            break
        if now - last_note >= 30.0:
            last_note = now
            last = _tail(log, chars=300).splitlines()
            progress(
                f"  {role}: ainda carregando... ({last[-1][-120:] if last else 'sem log ainda'})"
            )
        time.sleep(min(poll_s, max(0.1, deadline - now)))
    try:
        proc.kill()
    except Exception:
        pass
    raise RuntimeError(
        f"llama-server ({role}) não respondeu em {timeout_s:.0f}s. "
        f"Causa provável: carga lenta (Defender/HDD) — aumente "
        f"runtime.startup_timeout_s. log: {log}\n{_tail(log)}"
    )


def _spawn_one(
    role: str, base_url: str, port: int, assets: dict, cfg: dict, progress=print
) -> subprocess.Popen:
    gguf = assets["planner_gguf" if role == "planner" else "vision_gguf"]
    mmproj = assets.get("planner_mmproj") if role == "planner" else assets.get("mmproj")
    args = _server_args(role, gguf, mmproj, port, cfg)
    log = MODELS_DIR / f"llama-server-{role}.log"
    host = str(cfg.get("runtime", {}).get("host", "127.0.0.1"))
    progress(f"subindo {role} ({Path(gguf).name}) em {host}:{port}...")
    with log.open("ab") as f:
        f.write(f"\n===== {time.strftime('%Y-%m-%d %H:%M:%S')} {' '.join(args)}\n".encode())
        f.flush()
        proc = subprocess.Popen(
            args, stdout=f, stderr=f, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0)
        )
    timeout_s = float(cfg.get("runtime", {}).get("startup_timeout_s", DEFAULT_STARTUP_TIMEOUT_S))
    try:
        alive = _wait_alive(role, base_url, proc, log, timeout_s=timeout_s, progress=progress)
    except Exception:
        try:
            if proc.poll() is None:
                proc.kill()
        except Exception:
            pass
        raise
    progress(f"{role}: no ar em {base_url} (modelos={alive['models']})")
    return proc


def ensure_servers(base_urls: tuple[str, str], cfg: dict, progress=print) -> dict:
    """Garante os 2 endpoints vivos; baixa assets e sobe llama-server se preciso.

    - Endpoint vivo na porta -> REUSA (nossa execução anterior ou servidor
      externo; quem subiu não importa).
    - Porta ocupada sem endpoint (ou inacessível) -> erro honesto (não matamos
      processo alheio).
    - Porta livre -> baixa assets (1ª vez) e sobe nosso llama-server.

    Retorna {"planner": Popen|None, "vision": Popen|None} (None = reusado).
    """
    out: dict = {"planner": None, "vision": None}
    needed: dict[str, tuple[str, int]] = {}

    for role, base_url in zip(("planner", "vision"), base_urls):
        alive = _endpoint_alive(base_url)
        if alive:
            progress(f"{role}: reusando {base_url} (modelos={alive['models']})")
            continue
        host, port = _split_host_port(base_url)
        if _port_in_use(host, port):
            raise RuntimeError(
                f"porta {port} ocupada sem endpoint /v1/models acessível ({role}); "
                f"feche o processo ou aponte {role}.base_url para ele. Log: "
                f"{MODELS_DIR / f'llama-server-{role}.log'}"
            )
        needed[role] = (base_url, port)

    if needed:
        progress("modelos não estão no ar; garantindo runtime local...")
        assets = ensure_assets(progress=progress, cfg=cfg)
        for role, (base_url, port) in needed.items():
            out[role] = _spawn_one(role, base_url, port, assets, cfg, progress=progress)
    return out


def ensure_servers_for_profile(
    base_urls: tuple[str, str], cfg: dict, profile: dict | None = None, progress=print
) -> dict:
    """F4: unificado com mesmo checkpoint sobe UM processo (alias lógico).

    Dois endpoints iguais (ver `model_adapters.unified_endpoints`) viram um
    serviço só; `vision` reusa o planner (sem duplicar pesos). Demais casos
    delegam p/ `ensure_servers`.
    """
    prof = profile or {}
    try:
        from model_adapters import is_unified
    except Exception:
        return ensure_servers(base_urls, cfg, progress=progress)
    if is_unified(prof) and base_urls[0] == base_urls[1]:
        url = base_urls[0]
        alive = _endpoint_alive(url)
        if alive:
            expected = str(prof.get("planner", ""))
            if expected and not _models_match(alive, expected):
                raise RuntimeError(
                    f"endpoint unificado {url} esta servindo "
                    f"{alive['models']}, nao {expected}; encerre o servidor "
                    "antigo antes de testar este perfil"
                )
            progress(f"unified: reusando {url} (modelos={alive['models']})")
            return {"planner": None, "vision": None}
        host, port = _split_host_port(url)
        if _port_in_use(host, port):
            raise RuntimeError(
                f"porta {port} ocupada sem endpoint /v1/models acessível (unified); "
                f"feche o processo ou aponte base_url para ele."
            )
        progress("modelos não estão no ar; garantindo runtime local...")
        assets = ensure_assets(progress=progress, cfg=cfg)
        proc = _spawn_one("planner", url, port, assets, cfg, progress=progress)
        progress(f"unified: serviço único em {url} (sem duplicar pesos)")
        return {"planner": proc, "vision": None}
    return ensure_servers(base_urls, cfg, progress=progress)


def gpu_status(cfg: dict) -> dict:
    """GPU real e orçamento (§7, §3.2): offload efetivo + dedicada/compartilhada.

    Não presume que ngl>0 num binário CPU habilita GPU: reporta backend,
    memória e o aviso quando o binário é CPU-only.
    """
    try:
        import telemetry as _tel

        gpu = _tel.collect_env().get("gpu", {})
    except Exception:
        gpu = {}
    ngl = int(cfg.get("runtime", {}).get("ngl", DEFAULT_NGL))
    exe = str(LLAMA_EXE)
    cpu_only = "cpu" in exe.lower() or "bin" in exe.lower()
    warn = ""
    if ngl > 0 and cpu_only:
        warn = (
            "ngl>0 pedido num binário CPU-only: NÃO habilita GPU; "
            "use build CUDA/Vulkan testado e mostre offload efetivo"
        )
    return {"gpu": gpu, "ngl": ngl, "cpu_only_binary": cpu_only, "warn": warn}


def record_manifest(profile: dict, cfg: dict) -> dict:
    """Manifesto F4: revisão/hash/template/projetor/runtime/parâmetros por perfil.

    Best-effort (sem hashear 600 MB a cada boot): registra nomes, tamanhos,
    mtime e o que já estiver em models/manifest.json.
    """
    import json as _json

    man_path = MODELS_DIR / "manifest.json"
    prev = {}
    try:
        if man_path.exists():
            prev = _json.loads(man_path.read_text(encoding="utf-8"))
    except Exception:
        prev = {}
    rec = {
        "profile": profile.get("name", "?"),
        "planner": profile.get("planner"),
        "vision": profile.get("vision"),
        "mode": profile.get("mode"),
        "runtime": {
            "tag": LLAMA_TAG,
            "ctx": int(cfg.get("runtime", {}).get("ctx", DEFAULT_CTX)),
            "ngl": int(cfg.get("runtime", {}).get("ngl", DEFAULT_NGL)),
            "threads": int(cfg.get("runtime", {}).get("threads", DEFAULT_THREADS)),
        },
        "files": {},
    }
    for f in (
        MODELS_DIR / PLANNER_GGUF["file"],
        MODELS_DIR / PLANNER_2B_GGUF["file"],
        MODELS_DIR / VISION_GGUF["file"],
        MODELS_DIR / VISION_MMPROJ["file"],
    ):
        try:
            if f.exists():
                st = f.stat()
                rec["files"][f.name] = {"bytes": st.st_size, "mtime": st.st_mtime}
        except Exception:
            pass
    try:
        prev[rec["profile"]] = rec
        man_path.write_text(_json.dumps(prev, indent=2), encoding="utf-8")
    except Exception:
        pass
    return rec


def stop_servers(procs: dict) -> None:
    """Encerra os llama-server que NÓS subimos nesta execução (reusados: nunca)."""
    for role, proc in procs.items():
        if proc is None:
            continue
        try:
            proc.terminate()
            proc.wait(timeout=10)
        except Exception:
            try:
                proc.kill()
            except Exception:
                pass


def cli_base_urls(host: str, profile: dict) -> tuple[str, str]:
    """Endpoints da CLI; perfil unificado compartilha a porta do planner."""
    planner = f"http://{host}:{PLANNER_PORT}/v1"
    vision = f"http://{host}:{VISION_PORT}/v1"
    try:
        from model_adapters import unified_endpoints

        return unified_endpoints(planner, vision, profile)
    except Exception:
        return planner, vision


def main() -> None:
    """CLI: sobe os 2 llama-server e fica no ar (Ctrl+C para).

    Uso: uv run python -m server [--host 0.0.0.0]
    O loop (main.py) já sobe sozinho em 127.0.0.1; a CLI existe p/ deixar os
    endpoints permanentes ou acessíveis ao Sandbox (--host 0.0.0.0).
    """
    import argparse

    import config as cfgmod

    ap = argparse.ArgumentParser(description="Runtime próprio dos 2 modelos")
    ap.add_argument(
        "--host",
        default="127.0.0.1",
        help="bind dos llama-server (127.0.0.1 ou 0.0.0.0 p/ Sandbox)",
    )
    ap.add_argument("--config", default="config.json")
    ap.add_argument(
        "--profile",
        default="",
        help="perfil opt-in (ex.: B1 = planner MiniCPM5-2B); default segue config.json (B0)",
    )
    args = ap.parse_args()

    cfg = cfgmod.load(args.config)
    if args.profile:
        cfgmod.apply_profile(cfg, args.profile)
    cfg.setdefault("runtime", {})["host"] = args.host
    profile = cfgmod.profile_of(cfg)
    endpoint_host = "127.0.0.1"
    if args.host != "127.0.0.1":
        # bind liberado: os endpoints externos ficam no IP da máquina (p/ Sandbox)
        import socket as _s

        ip = ""
        try:
            with _s.socket(_s.AF_INET, _s.SOCK_DGRAM) as sk:
                sk.connect(("8.8.8.8", 80))
                ip = sk.getsockname()[0]
        except Exception:
            ip = "127.0.0.1"
        endpoint_host = ip
    base_urls = cli_base_urls(endpoint_host, profile)

    procs = {"planner": None, "vision": None}
    try:
        procs = ensure_servers_for_profile(base_urls, cfg, profile=profile)
        print(f"planner: {base_urls[0]} (modelo={profile.get('planner')})")
        print(f"vision: {base_urls[1]} (modelo={profile.get('vision')})")
        print("Ctrl+C para encerrar.")
        while True:
            time.sleep(3600)
    except KeyboardInterrupt:
        print("\nencerrando...")
    finally:
        stop_servers(procs)


if __name__ == "__main__":
    main()
