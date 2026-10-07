"""CLI do MVP em reconstrução (ver docs/arquitetura-do-zero-2026-10-07.html).

O orquestrador (cérebro único + ferramentas semânticas -> OCR -> visão) ainda NÃO existe: o loop
do desenho antigo (planner texto + grounder) foi removido desta branch e continua no `master`.
Hoje só há o autoteste da plataforma. Sobe os modelos com `uv run python -m server --profile P1`.
"""
from __future__ import annotations

import argparse
import json
import sys
import time

import psutil

import config as cfgmod

for _s in (sys.stdout, sys.stderr):
    try:
        if _s and _s.encoding and _s.encoding.lower() not in ("utf-8", "utf8"):
            _s.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass


def main() -> None:
    ap = argparse.ArgumentParser(description="MVP em reconstrução")
    ap.add_argument("instruction", nargs="?", default="")
    ap.add_argument("--config", default="config.json")
    ap.add_argument("--self-test", action="store_true", help="screenshot + métricas, sem clicar")
    ap.add_argument("--platform", default="", choices=["", "wayland"])
    args = ap.parse_args()
    cfg = cfgmod.load(args.config)

    if args.self_test:
        import platform_backend
        import safety
        from obs import take_screenshot

        if args.platform:
            platform_backend.enable(args.platform)
        try:
            safety.start(cfg.get("stop_hotkey"))
            t0 = time.perf_counter()
            path, (w, h) = take_screenshot()
            print(json.dumps({"ok": True, "screenshot": path, "size": [w, h],
                              "ms": round((time.perf_counter() - t0) * 1000, 1),
                              "cpu_pct": psutil.cpu_percent(interval=0.2),
                              "mem_pct": psutil.virtual_memory().percent}, indent=2))
        finally:
            platform_backend.disable()
        return
    print("O orquestrador do MVP ainda não foi implementado. Veja "
          "docs/arquitetura-do-zero-2026-10-07.html e AGENTS.md.")
    raise SystemExit(2)


if __name__ == "__main__":
    main()
