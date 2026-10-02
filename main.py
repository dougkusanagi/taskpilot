"""CLI: python main.py "faça X"  →  uso real, local, sem parâmetros.

Arquitetura de 2 modelos (config.json + runtime próprio server.py,
que baixa e sobe tudo sozinho na 1ª vez):
  planner MiniCPM5-2B  http://127.0.0.1:8091/v1  (texto, sem screenshots)
  visão   Vocaela-2    http://127.0.0.1:8082/v1  (screenshot → ação visual)

O Sandbox é só p/ testes dev (via scripts); nunca passar
--config config.sandbox.json no host.
"""
from __future__ import annotations

import argparse
import json
import sys
import time

import psutil

import config as cfgmod


def _dpi_aware() -> None:
    """GetWindowRect, mss e pyautogui na MESMA unidade (pixel físico) em DPI≠100%.
    Precisa rodar antes de qualquer janela/import de pywinauto/pyautogui."""
    try:
        import ctypes

        try:
            ctypes.windll.shcore.SetProcessDpiAwareness(2)  # per-monitor
        except Exception:
            ctypes.windll.user32.SetProcessDPIAware()
    except Exception:
        pass


_dpi_aware()

# Console Windows pode estar em cp1252: nunca quebrar por unicode (→, ç, ã...).
for _s in (sys.stdout, sys.stderr):
    try:
        if _s and _s.encoding and _s.encoding.lower() not in ("utf-8", "utf8"):
            _s.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass


def locate_only(target: str, cfg: dict) -> None:
    """Dry-run do grounding Vocaela: screenshot → act → imprime, sem clicar."""
    import server
    from loop import _build_models, _ensure_local_servers
    from obs import capture_for_vision
    from vocaela import visual_to_action

    procs = _ensure_local_servers(cfg)
    try:
        img, origin, full = capture_for_vision(
            max_long_edge=int(cfg.get("screenshot_max_width", 1024)))
        _, vocaela = _build_models(cfg)
        va, vms = vocaela.act_sync(img, target)
        act = visual_to_action(va, (img.size[0], img.size[1]), origin)
        print(json.dumps({"target": target, "visual": va.model_dump(),
                          "origin": list(origin), "crop": list(img.size),
                          "screen": list(full),
                          "physical": {"x": act.x, "y": act.y,
                                       "x2": act.x2, "y2": act.y2},
                          "vision_ms": round(vms, 1)}, indent=2))
    finally:
        server.stop_servers(procs)


def main() -> None:
    ap = argparse.ArgumentParser(description="Computer Use local (MiniCPM5-2B + Vocaela-2)")
    ap.add_argument("instruction", nargs="?", default="", help="instrução do usuário")
    ap.add_argument("--config", default="config.json")
    ap.add_argument("--max-steps", type=int, default=None)
    ap.add_argument("--self-test", action="store_true",
                    help="só testa screenshot + métricas, sem clicar")
    ap.add_argument("--planner-url", default=None, help="override de planner.base_url")
    ap.add_argument("--vision-url", default=None, help="override de vision.base_url")
    ap.add_argument("--locate", default="",
                    help='dry-run Vocaela: --locate "Click the address bar"')
    ap.add_argument("--no-runtime", action="store_true",
                    help="não sobe llama-server local (usa endpoints como estão)")
    ap.add_argument("--profile", default="",
                    help="perfil opt-in (ex.: B1 = planner MiniCPM5-2B, ~1,5 GB; "
                         "baixa o GGUF na 1ª vez). Default: config.json (B0)")
    ap.add_argument("--dry-run", action="store_true",
                    help="F0: bloqueia TODOS os efeitos (bootstrap/foco/teclado/CLI); "
                         "só decide, sem clicar")
    ap.add_argument("--record", action="store_true",
                    help="grava em runs/<id>/sft.jsonl cada decisão do planner (prompt, resposta, "
                         "desfecho) p/ treino futuro; só local")
    ap.add_argument("--ui", action="store_true",
                    help="tray + janela Spotlight com ditado (requer: uv sync --extra ui)")
    args = ap.parse_args()

    cfg = cfgmod.load(args.config)
    if args.max_steps is not None:
        cfg["max_steps"] = args.max_steps
    if args.planner_url:
        cfg["planner"]["base_url"] = args.planner_url
    if args.vision_url:
        cfg["vision"]["base_url"] = args.vision_url
    if args.no_runtime:
        cfg.setdefault("runtime", {})["auto_start"] = False
    if args.profile:
        try:
            cfgmod.apply_profile(cfg, args.profile)
        except ValueError as e:
            print(e)
            raise SystemExit(2)
    if args.dry_run:
        cfg["dry_run"] = True
    if args.record:
        cfg["record"] = True

    if args.self_test:
        import safety
        from obs import take_screenshot

        safety.start(cfg.get("stop_hotkey"))
        t0 = time.perf_counter()
        path, (w, h) = take_screenshot()
        dt = (time.perf_counter() - t0) * 1000
        try:
            import config as _cfgmod
            import skills as _sk

            prof = _cfgmod.profile_of(cfg)
            nskills = len(_sk.list_skills())
        except Exception:
            prof, nskills = {"name": "B0"}, 0
        print(json.dumps({
            "ok": True,
            "screenshot": path,
            "size": [w, h],
            "ms": round(dt, 1),
            "cpu_pct": psutil.cpu_percent(interval=0.2),
            "mem_pct": psutil.virtual_memory().percent,
            "safety": safety.status(),
            "profile": prof,
            "skills": nskills,
            "config": cfg,
            "note": f"{safety.HOTKEY} aborta; dry-run sem cliques",
        }, indent=2))
        return

    if args.locate:
        locate_only(args.locate, cfg)
        return

    if args.ui:
        try:
            import webview  # noqa: F401
        except ImportError:
            print("UI indisponível: instale o extra `ui` (uv sync --extra ui).")
            raise SystemExit(2)
        from app import App

        App(cfg).run()
        return

    instruction = args.instruction.strip()
    if not instruction:
        try:
            instruction = input("> ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            return
    if not instruction:
        print("instrução vazia.")
        raise SystemExit(2)

    from loop import run
    summary = run(instruction, cfg, dry_run=bool(cfg.get("dry_run", False)))
    print(json.dumps(summary, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
