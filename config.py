"""Config de 2 modelos: MiniCPM5-2B (planner) + Vocaela-2-500M (visão).

Uso real é local e sem parâmetros: o runtime próprio (`server.py`) baixa
llama.cpp + GGUFs p/ `models/` na 1ª vez e sobe os endpoints abaixo
sozinho (nenhuma dependência de `llama-server` externo). Portas fora do
padrão (sem conflito com Ollama 11434 / LM Studio 1234):

  planner: http://127.0.0.1:8091/v1   (MiniCPM5-2B, texto, SEM screenshots)
  vision:  http://127.0.0.1:8082/v1   (Vocaela-2-500M-1024R2, screenshot+instrução)

URLs remotas (ex.: HOST_IP no `config.sandbox.json`, gerado dentro do
Sandbox) são só p/ testes dev via scripts — nunca baixam modelos aqui.

Default B1 desde 20/09 (pedido do usuário: isolar 1B→2B no uso real;
matriz registra a comparação pendente). Rollback: profile B0.

Migra config.json legado (base_url/vision_model) automaticamente.
"""
from __future__ import annotations

import json
from pathlib import Path

DEFAULTS: dict = {
    # F1/F4: perfil ativo (B0 = baseline obrigatório; trocar só após F7).
    # Configuração tipada por perfil e capacidade (text/vision/grounding/
    # structured_output), separada de nomes fixos de modelos.
    "profile": "B1",
    "planner": {
        "provider": "llama.cpp",
        "base_url": "http://127.0.0.1:8091/v1",
        "model": "MiniCPM5-2B",
        "temperature": 0.1,
        "timeout_s": 90,
        # Liga o raciocínio só quando há erro/repetição pendente (custa latência).
        # Desligado até medir no loop real (R3).
        "escalate_thinking": False,
        # Recursos do planner medidos no benchmark (planner.PLANNER_FEATURES): tools, fewshot,
        # recipes, dynschema, why. Vazio = prompt/schema históricos.
        "features": [],
    },
    "vision": {
        "provider": "llama.cpp",
        "base_url": "http://127.0.0.1:8082/v1",
        "model": "Vocaela-2-500M-1024R2",
        "timeout_s": 180,
        # Só perfis Qwen: zoom em 2 etapas e aceitar 0..1000/pixels (ver bateria `ground`).
        "zoom": False,
        "coords": "unit",
        "zoom_frac": 0.35,
    },
    # Ao abrir um navegador novo: flags que fazem o UIA enxergar o conteúdo da página.
    "launch": {"browser_args": ["--force-renderer-accessibility"]},
    "max_steps": 30,
    "screenshot_max_width": 1024,  # Vocaela-2-1024R2: treino em longest-edge 1024
    "verify_wait_ms": 500,
    "stop_hotkey": "ctrl+alt+esc",
    # --- runtime próprio dos modelos (server.py: baixa e sobe llama-server) ---
    "runtime": {
        "auto_start": True,  # endpoints locais caídos -> baixa GGUFs e sobe
        "host": "127.0.0.1",  # bind dos llama-server (0.0.0.0 p/ expor ao Sandbox)
        "ngl": 0,  # 0 = CPU; >0 offload p/ GPU (quem tem VRAM)
        # llama.cpp a baixar: cpu (padrão) | vulkan (GPU qualquer, 32 MB) | cuda (NVIDIA, rápida).
        # Com backend GPU e ngl=0 o runtime usa ngl=99. Troca de backend rebaixa o binário.
        "backend": "cpu",
        "parallel": 0,  # 0 = padrão do llama-server; 1 = sem slots extras de KV (menos VRAM)
        "kv_cache": "f16",  # f16 | q8_0 | q4_0 (quantiza o KV; usa flash attention)
        "mmproj_offload": True,  # False = encoder de imagem na CPU (libera ~0,8 GB de VRAM)
        "threads": 0,  # 0 = metade dos núcleos (server.DEFAULT_THREADS)
        "ctx": 4096,
        # Carga do GGUF com Defender/HDD leva minutos (polling até o deadline)
        "startup_timeout_s": 600,
    },
    # --- tray + janela Spotlight (main.py --ui; extra `ui`) ---
    "ui": {
        "hotkey": "ctrl+alt+space",  # mostra/esconde a janela
        "width": 720,
        "auto_hide": True,  # esconde a janela enquanto o agente age
    },
    # --- ditado ao vivo (faster-whisper int8 em CPU) ---
    "stt": {
        "engine": "faster-whisper",
        "model": "base",  # tiny|base|small (base ~74MB int8)
        "compute_type": "int8",
        "language": "pt",
        "partial_every_ms": 1000,  # retranscreve o buffer p/ mostrar parcial
        "silence_ms": 1500,  # silêncio contínuo -> transcrição final
        "silence_rms": 0.01,  # limiar de energia p/ "silêncio"
        "max_utterance_s": 30,
        "auto_send": True,  # final por silêncio envia sozinho
    },
}


# --- perfis F4 (§3): candidatos, não promessas de caber em 6 GB ---------------
# Capacidades: text / vision / grounding / structured_output.
# Perfil unificado usa UM processo (não carrega o mesmo checkpoint 2x).
PROFILES: dict = {
    "B0": {"planner": "MiniCPM5-1B", "vision": "Vocaela-2-500M-1024R2",
           "mode": "dual", "capabilities": ["text", "vision", "grounding",
                                            "structured_output"],
           "note": "Baseline atual, obrigatório"},
    "B1": {"planner": "MiniCPM5-2B", "vision": "Vocaela-2-500M-1024R2",
           "mode": "dual", "capabilities": ["text", "vision", "grounding",
                                            "structured_output"],
           "note": "Isolar efeito 1B -> 2B"},
    "D1": {"planner": "MiniCPM5-2B", "vision": "Qwen3-VL-2B-Instruct",
           "mode": "dual", "capabilities": ["text", "vision", "grounding",
                                            "structured_output"],
           "note": "Planejamento textual + percepção/ação visual geral"},
    "D2": {"planner": "MiniCPM5-2B", "vision": "Qwen3.5-2B",
           "mode": "dual", "capabilities": ["text", "vision", "grounding",
                                            "structured_output"],
           "note": "Visão geral mais recente vs D1"},
    "U1": {"planner": "Qwen3-VL-2B-Instruct", "vision": "Qwen3-VL-2B-Instruct",
           "mode": "unified", "capabilities": ["text", "vision", "grounding",
                                               "structured_output"],
           "note": "Qwen3-VL 2B unico: planeja e enxerga no mesmo processo"},
    "U2": {"planner": "Qwen3.5-4B", "vision": "Qwen3.5-4B",
           "mode": "unified", "capabilities": ["text", "vision", "grounding",
                                               "structured_output"],
           "note": "Qualidade com um único conjunto de pesos; hipótese p/ 6 GB"},
    "G1": {"planner": "MiniCPM5-2B", "vision": "GUI-Owl-1.5-2B-Instruct",
           "mode": "dual", "capabilities": ["text", "vision", "grounding",
                                            "structured_output"],
           "note": "Opcional: especialista GUI vs vencedor D1/D2"},
    "E1": {"planner": "Empero-Qwen3.8-2B-Distill", "vision": "Vocaela-2-500M-1024R2",
           "mode": "dual", "capabilities": ["text"],
           "note": "Opcional: avaliar como planner textual"},
    "E2": {"planner": "Empero-Qwen3.8-2B-Distill", "vision": "Empero-Qwen3.8-2B-Distill",
           "mode": "unified", "capabilities": [],
           "note": "Só se artefato multimodal completo passar no teste (§3.1)"},
}


def apply_profile(cfg: dict, name: str) -> dict:
    """Ativa um perfil opt-in (ex.: B1): fixa profile + modelos do perfil.

    Erro honesto se o perfil não existe. Default segue B0; rollback é só
    remover a chave `profile` (ou voltar a B0).
    """
    key = str(name or "").strip().upper()
    if key not in PROFILES:
        raise ValueError(f"perfil desconhecido: {name!r}; "
                         f"use um de {sorted(PROFILES)}")
    cfg["profile"] = key
    cfg.setdefault("planner", {})["model"] = PROFILES[key]["planner"]
    cfg.setdefault("vision", {})["model"] = PROFILES[key]["vision"]
    return cfg


def profile_of(cfg: dict) -> dict:
    """Perfil ativo + modelos efetivos (opt-in; default B1)."""
    name = str(cfg.get("profile", "B1") or "B1").upper()
    base = dict(PROFILES.get(name, PROFILES["B1"]))
    base["name"] = name if name in PROFILES else "B1"
    # Overrides explícitos em planner.model/vision.model vencem o perfil.
    defaults = DEFAULTS
    if cfg.get("planner", {}).get("model", defaults["planner"]["model"]) \
            != PROFILES[base["name"]]["planner"] \
            and "planner" in cfg:
        base["planner"] = cfg["planner"]["model"]
    if cfg.get("vision", {}).get("model", defaults["vision"]["model"]) \
            != PROFILES[base["name"]]["vision"] \
            and "vision" in cfg:
        base["vision"] = cfg["vision"]["model"]
    return base


def _migrate_legacy(cfg: dict) -> dict:
    """Aceita config.json antigo e mapeia p/ novo."""
    legacy_url = cfg.pop("base_url", None)
    legacy_model = cfg.pop("vision_model", None)  # MAI-UI: aposentado do fluxo
    if legacy_url:
        for sec in ("planner", "vision"):
            cur = cfg.get(sec, {}).get("base_url", "")
            if sec not in cfg or cur in ("", DEFAULTS[sec]["base_url"]):
                cfg.setdefault(sec, {}).update(
                    {**DEFAULTS[sec], **cfg.get(sec, {}), "base_url": legacy_url})
        print(f"config legado migrado: base_url {legacy_url} aplicado a planner+vision; "
              f"ajuste config.json p/ 8091/8082.")
    if legacy_model and legacy_model != DEFAULTS["vision"]["model"]:
        print(f"modelo legado {legacy_model!r} fora do fluxo principal "
              f"(agora: Vocaela-2-500M-1024R2).")
    for junk in ("scorer_threshold",):
        cfg.pop(junk, None)  # scorer saiu do fluxo principal
    return cfg


def load(path: str | Path = "config.json") -> dict:
    cfg = json.loads(json.dumps(DEFAULTS))  # deep copy simples
    p = Path(path)
    if p.exists():
        try:
            raw = json.loads(p.read_text(encoding="utf-8"))
            # merge raso por seção p/ não perder defaults aninhados
            for k, v in raw.items():
                if isinstance(v, dict) and isinstance(cfg.get(k), dict):
                    cfg[k] = {**cfg[k], **v}
                else:
                    cfg[k] = v
            cfg = _migrate_legacy(cfg)
        except Exception as e:
            print(f"config.json inválido ({e}); usando defaults.")
    return cfg
