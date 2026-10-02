"""Exporta dados de treino (SFT) em formato de chat, para ajuste fino futuro (QLoRA).

  planner  runs gravados com `main.py --record` (runs/<id>/sft.jsonl): só decisões de runs que
           terminaram em `done` e cujo passo teve efeito (nada de ação recusada ou "no visible
           effect"). Contém o que o usuário viu na tela: arquivo SÓ LOCAL, nunca compartilhar.
  ground   bateria visual capturada (evals/ground/cases.json): imagem + instrução -> ponto do
           centro da caixa, ou null para alvo ausente. Aumente a base adicionando páginas ao
           evals/ground_specs.json e recapturando.

    uv run python -m evals.export_sft planner --out data/planner-sft.jsonl
    uv run python -m evals.export_sft ground  --out data/ground-sft.jsonl
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
BAD_OUTCOME = ("no visible effect", "recusad", "falhou", "stuck", "vetad")


def useful_step(row: dict) -> bool:
    """Passo aproveitável: run concluído, resposta presente, desfecho sem sinais de falha."""
    if row.get("run_result") != "done" or not row.get("response") or not row.get("messages"):
        return False
    outcome = row.get("outcome") or {}
    text = " ".join(str(outcome.get(k, "")) for k in ("did", "verify", "confirm")).lower()
    return bool(outcome) and not any(bad in text for bad in BAD_OUTCOME)


def planner_rows(runs_dir: Path) -> list[dict]:
    out = []
    for path in sorted(runs_dir.glob("*/sft.jsonl")):
        for line in path.read_text(encoding="utf-8").splitlines():
            row = json.loads(line)
            if useful_step(row):
                out.append({"messages": [*row["messages"],
                                         {"role": "assistant", "content": row["response"]}],
                            "meta": {"run_id": row.get("run_id"), "step": row.get("step")}})
    return out


def ground_rows(cases_path: Path) -> list[dict]:
    from vocaela import QWEN_GROUNDING_SYSTEM

    out = []
    for case in json.loads(cases_path.read_text(encoding="utf-8"))["cases"]:
        if case["bbox"] is None:
            answer = {"x": None, "y": None}
        else:
            x0, y0, x1, y1 = case["bbox"]
            answer = {"x": round((x0 + x1) / 2, 4), "y": round((y0 + y1) / 2, 4)}
        out.append({"messages": [
            {"role": "system", "content": QWEN_GROUNDING_SYSTEM},
            {"role": "user", "content": [
                {"type": "text",
                 "text": f"Instruction: {case['instruction']}\nReturn ONLY the JSON point."},
                {"type": "image", "image": str(cases_path.parent / case["image"])}]},
            {"role": "assistant", "content": json.dumps(answer)}],
            "meta": {"case": case["id"], "category": case["category"]}})
    return out


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawTextHelpFormatter)
    ap.add_argument("kind", choices=("planner", "ground"))
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--runs", type=Path, default=ROOT / "runs")
    args = ap.parse_args(argv)
    if args.kind == "planner":
        rows = planner_rows(args.runs)
    else:
        from evals import ground_bench as gb

        gb.load_cases()  # valida imagens/capturas
        rows = ground_rows(gb.GROUND / "cases.json")
    if not rows:
        print("nenhuma linha aproveitável (planner: rode main.py --record e conclua tarefas)")
        return 1
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text("\n".join(json.dumps(r, ensure_ascii=False) for r in rows) + "\n",
                        encoding="utf-8")
    print(f"{len(rows)} exemplos em {args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
