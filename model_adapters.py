"""Adaptadores F4: perfis B1/D/U intercambiáveis (§3–§4, §7).

- Configuração tipada por perfil e capacidade (text/vision/grounding/
  structured_output), separada de nomes fixos (ver `config.PROFILES`).
- Um perfil unificado usa UM processo; aliases lógicos sem duplicar pesos
  (ver `unified_endpoints` + `server.ensure_servers_for_profile`).
- Capability smoke tests baratos antes de investir em ajustes de um modelo
  (§3.2): JSON restrito, Unicode PT-BR, leitura de texto, diálogo,
  localização, duas imagens, alvo ausente, cold/first/warm, backend GPU
  real (sem presumir ngl>0 em binário CPU), OOM/limite -> inelegível.
- Orçamento 6 GB (§7): pico incremental até ~5 GB (1 GB p/ desktop/apps),
  uma geração ativa por GPU, filas curtas, STT em CPU inicialmente.

Sem suporte/OOM/limite: perfil inelegível, nunca teste aprovado com
CPU/offload parcial silencioso.
"""
from __future__ import annotations

import time

CAPABILITIES = ("text", "vision", "grounding", "structured_output")


def capabilities_of(profile: dict) -> list[str]:
    caps = list(profile.get("capabilities", []))
    return [c for c in caps if c in CAPABILITIES]


def is_unified(profile: dict) -> bool:
    return profile.get("mode") == "unified"


def unified_endpoints(planner_url: str, vision_url: str,
                      profile: dict) -> tuple[str, str]:
    """Alias lógico sem duplicar pesos: unificado com mesmo checkpoint usa
    um endpoint só (puro, testável)."""
    if is_unified(profile) and profile.get("planner") == profile.get("vision"):
        return planner_url, planner_url
    return planner_url, vision_url


def vram_budget_ok(vram_total_mb: float | None, profile_name: str,
                   reserve_desktop_gb: float = 1.0) -> tuple[bool, str]:
    """Meta inicial: pico incremental do agente até ~5 GB em placa de 6 GB
    (reserva >=1 GB p/ desktop/apps). Sem total conhecido -> indeterminado."""
    if vram_total_mb is None:
        return True, "VRAM total desconhecida; medir em GPU física de 6 GB (§7)"
    headroom = vram_total_mb / 1024 - reserve_desktop_gb
    # U2 (4B) é a hipótese p/ 6 GB; demais perfis 2B cabem no mesmo teto.
    need_gb = 5.0 if profile_name in ("U2",) else 4.0
    if headroom >= need_gb - reserve_desktop_gb + 1.0:  # ~5 GB livres
        return True, f"{vram_total_mb:.0f} MB cobre o pico de ~5 GB"
    if vram_total_mb >= 5000:
        return True, f"{vram_total_mb:.0f} MB no limite; medir spill p/ compartilhada"
    return False, (f"{vram_total_mb:.0f} MB insuficiente p/ {profile_name} "
                   "sem spill sustentado; reportar motivo e perfil utilizável")


class CapabilityResult(dict):
    pass


def smoke_text(planner, prompt: str = "Responda {\"type\": \"done\"}") -> CapabilityResult:
    """Smoke barato do planner textual: JSON restrito + Unicode PT-BR."""
    t0 = time.perf_counter()
    try:
        dec, ms = planner.next_action("teste", "win", ["Button:Ol"], [],
                                      last_error="")
        ok = dec.type in ("done", "wait", "answer", "open_app", "focus_window",
                          "type_text", "press_key", "hotkey", "uia_click",
                          "visual_action")
        return CapabilityResult({"capability": "text", "ok": bool(ok),
                                 "ms": round((time.perf_counter() - t0) * 1000, 1),
                                 "planner_ms": round(ms, 1),
                                 "note": prompt[:60]})
    except Exception as e:
        return CapabilityResult({"capability": "text", "ok": False,
                                 "error": str(e)[:200]})


def smoke_vision(vision, image, instruction: str = "Click the test target",
                 history: list[str] | None = None) -> CapabilityResult:
    """Smoke barato da visão: localização + formato 0..1 + alvo ausente."""
    from vocaela import VisualAction

    t0 = time.perf_counter()
    try:
        va, ms = vision.act_sync(image, instruction, history=history)
        assert isinstance(va, VisualAction)
        in_range = all(v is None or 0.0 <= v <= 1.0
                       for v in (va.x, va.y, va.x2, va.y2))
        return CapabilityResult({"capability": "vision", "ok": bool(in_range),
                                 "ms": round((time.perf_counter() - t0) * 1000, 1),
                                 "vision_ms": round(ms, 1), "type": va.type})
    except Exception as e:
        return CapabilityResult({"capability": "vision", "ok": False,
                                 "error": str(e)[:200]})


def smoke_distinguish_images(vision, img_a, img_b,
                             instruction: str = "Describe the difference") -> CapabilityResult:
    """Duas imagens com texto idêntico: imagem ignorada = reprovado (§3.1)."""
    try:
        va, _ = vision.act_sync(img_a, instruction)
        vb, _ = vision.act_sync(img_b, instruction)
        same = (va.type, va.text) == (vb.type, vb.text) and va.type == "answer"
        return CapabilityResult({"capability": "vision-distinguish",
                                 "ok": True, "identical_answer": bool(same),
                                 "note": "respostas idênticas em imagens diferentes "
                                         "sugerem imagem ignorada" if same else "ok"})
    except Exception as e:
        return CapabilityResult({"capability": "vision-distinguish", "ok": False,
                                 "error": str(e)[:200]})


def build_adapters(cfg: dict):
    """Constrói (planner, vision) conforme o perfil (puro/testável sem rede).

    Unificado com mesmo checkpoint: vision é alias lógico do mesmo endpoint
    (um processo; nunca carregar o mesmo checkpoint duas vezes).
    Visão Qwen (D1/D2/U1/U2/G1 usa Owl): grounding JSON próprio — o protocolo
    `<Action>` do Vocaela não é entendido pelo Qwen (run U1 20/09).
    """
    import config as cfgmod
    from planner import MiniCPMPlanner, QwenVLPlanner
    from vocaela import QwenGroundingAdapter, VocaelaAdapter

    prof = cfgmod.profile_of(cfg)
    pc, vc = cfg.get("planner", {}), cfg.get("vision", {})
    p_url = pc.get("base_url", "http://127.0.0.1:8091/v1")
    v_url = vc.get("base_url", "http://127.0.0.1:8082/v1")
    p_url, v_url = unified_endpoints(p_url, v_url, prof)
    planner_cls = QwenVLPlanner if is_unified(prof) else MiniCPMPlanner
    planner = planner_cls(base_url=p_url,
                              model=pc.get("model", prof.get("planner", "MiniCPM5-2B")),
                             temperature=float(pc.get("temperature", 0.1)),
                             timeout_s=float(pc.get("timeout_s", 90)),
                             features=tuple(pc.get("features", ())))
    v_model = vc.get("model", prof.get("vision", ""))
    if "qwen" in str(v_model).lower():
        vision = QwenGroundingAdapter(
            base_url=v_url, model=v_model,
            timeout_s=float(vc.get("timeout_s", 180)),
            max_long_edge=int(cfg.get("screenshot_max_width", 1024)),
            zoom=bool(vc.get("zoom", False)), coords=str(vc.get("coords", "unit")),
            zoom_frac=float(vc.get("zoom_frac", 0.35)))
    else:
        vision = VocaelaAdapter(
            base_url=v_url, model=v_model,
            timeout_s=float(vc.get("timeout_s", 180)),
            max_long_edge=int(cfg.get("screenshot_max_width", 1024)))
    return planner, vision, prof
