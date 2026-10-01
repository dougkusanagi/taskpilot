"""Bateria visual: localizar elementos em screenshots (suíte `ground` do model_bench).

Usa o MESMO prompt de sistema e o MESMO parser do grounding de produção
(`vocaela.QWEN_GROUNDING_SYSTEM` / `parse_qwen_grounding`): ponto 0..1 do centro do elemento,
`null` quando ausente. Acerto = o ponto cai DENTRO da caixa do alvo. As caixas vêm da captura
(`evals/ground_capture.py`) e nunca são enviadas ao modelo. Não importa o loop nem executa
nada; só envia imagens congeladas ao servidor local.

    uv run python -m evals.model_bench --suite ground --model qwen3-vl --reps 1
"""
from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path

from PIL import Image

from evals.model_bench import Suite
from vocaela import QWEN_GROUNDING_SYSTEM, _prep_image, parse_qwen_grounding

GROUND = Path(__file__).with_name("ground")
_IMAGES: dict[str, str] = {}


def load_cases(path: Path = GROUND / "cases.json") -> list[dict]:
    if not path.is_file():
        raise ValueError("bateria visual não capturada; rode: "
                         "uv run --with playwright python -m evals.ground_capture")
    cases = json.loads(path.read_text(encoding="utf-8"))["cases"]
    ids = [c["id"] for c in cases]
    if not cases or len(ids) != len(set(ids)):
        raise ValueError("bateria visual vazia ou com IDs duplicados")
    for case in cases:
        if not (GROUND / case["image"]).is_file():
            raise ValueError(f"imagem ausente: {case['image']}; recapture a bateria")
    return cases


def quick_cases(cases: list[dict]) -> list[dict]:
    """Um caso por categoria, mais um alvo ausente (smoke representativo)."""
    seen, chosen = set(), []
    for case in cases:
        if case["category"] not in seen:
            seen.add(case["category"])
            chosen.append(case)
    absent = next((c for c in cases if c["bbox"] is None and c not in chosen), None)
    return chosen + ([absent] if absent else [])


def image_b64(case: dict) -> str:
    if case["image"] not in _IMAGES:
        # Mesmo pré-processamento de produção: lado maior 1024, JPEG q70.
        _IMAGES[case["image"]] = _prep_image(Image.open(GROUND / case["image"]))[0]
    return _IMAGES[case["image"]]


def make_messages(case: dict) -> list[dict]:
    text = f"Instruction: {case['instruction']}\nReturn ONLY the JSON point."
    return [{"role": "system", "content": QWEN_GROUNDING_SYSTEM},
            {"role": "user", "content": [
                {"type": "text", "text": text},
                {"type": "image_url",
                 "image_url": {"url": f"data:image/jpeg;base64,{image_b64(case)}"}}]}]


def raw_point(content: str) -> tuple[float, float] | None:
    """x,y numéricos do texto, mesmo fora de 0..1 (só diagnóstico de escala)."""
    nums = re.findall(r"-?\d+(?:\.\d+)?", content or "")
    if re.search(r'"x"', content or "") and len(nums) >= 2:
        return float(nums[0]), float(nums[1])
    return None


def inside(point: tuple[float, float], bbox: list[float]) -> bool:
    return bbox[0] <= point[0] <= bbox[2] and bbox[1] <= point[1] <= bbox[3]


def evaluate_response(case: dict, data: dict, elapsed_ms: float) -> dict:
    row = {"elapsed_ms": round(elapsed_ms, 1), "passed": False, "format_valid": False,
           "pure_json": False, "decision": None, "error_kind": "", "errors": [],
           "raw_response": data, "bbox": case["bbox"], "said_absent": False,
           "false_click": False, "hit_if_scale_1000": None}
    try:
        choice = data["choices"][0]
        row.update(finish_reason=choice.get("finish_reason"), usage=data.get("usage", {}),
                   response_model=data.get("model"))
        if choice.get("finish_reason") == "length":
            row["error_kind"] = "truncated"
            row["errors"] = ["saída truncada"]
            return row
        content = choice["message"].get("content") or ""
        try:
            json.loads(content)
            row["pure_json"] = True
        except json.JSONDecodeError:
            pass
        try:
            action = parse_qwen_grounding(content)
            point = (action.x, action.y)
            row["decision"] = {"x": point[0], "y": point[1]}
            row["format_valid"] = True
        except ValueError as exc:
            if "não visível" not in str(exc):
                raw = raw_point(content)
                if raw and case["bbox"] and max(raw) > 1:
                    row["hit_if_scale_1000"] = inside((raw[0] / 1000, raw[1] / 1000),
                                                      case["bbox"])
                row["error_kind"] = "format"
                row["errors"] = [str(exc)[:300]]
                return row
            row["format_valid"] = row["said_absent"] = True
            point = None
        if case["bbox"] is None:
            if point is None:
                row["passed"] = True
            else:
                row.update(error_kind="semantic", false_click=True,
                           errors=["clique falso: alvo inexistente, mas o modelo apontou um ponto"])
        elif point is None:
            row.update(error_kind="semantic", errors=["alvo visível declarado ausente"])
        elif inside(point, case["bbox"]):
            row["passed"] = True
        else:
            x0, y0, x1, y1 = case["bbox"]
            cx, cy = (x0 + x1) / 2, (y0 + y1) / 2
            row.update(error_kind="semantic", errors=["ponto fora da caixa do alvo"],
                       miss_distance=round(((point[0] - cx) ** 2 + (point[1] - cy) ** 2) ** .5, 4))
    except (ValueError, KeyError, IndexError, TypeError, AttributeError) as exc:
        row["error_kind"] = "format"
        row["errors"] = [str(exc)[:300]]
    return row


def extra_summary(rows: list[dict]) -> dict:
    absent = [r for r in rows if r.get("bbox") is None]
    present = [r for r in rows if r.get("bbox") is not None]
    out = {}
    if absent:
        out["Alvos ausentes tratados como ausentes"] = (
            f"{sum(r['passed'] for r in absent)}/{len(absent)}")
        out["Cliques falsos (alvo inexistente)"] = sum(r.get("false_click", False) for r in absent)
    if present:
        out["Alvos existentes localizados"] = f"{sum(r['passed'] for r in present)}/{len(present)}"
    scaled = [r for r in rows if r.get("hit_if_scale_1000") is not None]
    if scaled:
        out["Respostas em escala 0..1000 (rejeitadas; acertariam se aceitas)"] = (
            f"{sum(bool(r['hit_if_scale_1000']) for r in scaled)}/{len(scaled)}")
    return out


def fingerprint(cases: list[dict]) -> str:
    digest = hashlib.sha256()
    for name in sorted({c["image"] for c in cases}):
        digest.update((GROUND / name).read_bytes())
    return digest.hexdigest()


SUITE = Suite(
    name="ground", title="Localização de elementos em screenshots",
    scope=("Screenshots congelados de páginas reais e sintéticas; o modelo só vê pixels.",
           "Acerto = ponto dentro da caixa do alvo. Prompt e parser de grounding de produção.",
           "Não executa cliques; não prova E2E, DPI/multi-monitor nem uso no desktop real."),
    load=load_cases, messages=make_messages, evaluate=evaluate_response,
    system=QWEN_GROUNDING_SYSTEM, quick=quick_cases, use_schema=False,
    extra_summary=extra_summary, fingerprint=fingerprint)
