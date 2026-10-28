"""Bateria de confirmação do alvo: um localizador aponta e um modelo de decisão confere.

Mede a ideia "o localizador sempre aponta algo; o d1 decide se o ponto é mesmo o alvo":
1. o localizador recebe o screenshot (prompt de produção do protocolo escolhido) e devolve um
   ponto ou "ausente" (resposta truncada também conta como ausente, como em produção);
2. com ponto, o verificador (`/v1/systemone`, pergunta `noul`) recebe um recorte em volta do
   ponto e responde a probabilidade de o elemento do centro ser o alvo pedido;
3. o relatório varre limites: abaixo do limite, o clique é cancelado.

Clique errado = alvo ausente com clique, ou alvo existente com clique fora da caixa.
Não executa cliques; usa a mesma bateria congelada da suíte `ground`.

    uv run python -m evals.verify_bench --grounder-url http://127.0.0.1:8191/v1 \\
        --grounder-model lfm3-vl --verifier-url http://127.0.0.1:8192/v1
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

import httpx
from PIL import Image

from evals.ground_bench import GROUND, evaluate_response, load_cases, messages_for
from vocaela import VERIFY_CROP_HALF, TargetVerifier

THRESHOLDS = (0.0, 0.05, 0.1, 0.15, 0.2, 0.3, 0.4, 0.5)
CROP_PX = VERIFY_CROP_HALF


def ask_verifier(url: str, image: Image.Image, x: float, y: float, instruction: str) -> float:
    """Mesmo caminho da produção (vocaela.TargetVerifier), sem aplicar limite."""
    return TargetVerifier(url, 0.0).probability(image, x, y, instruction)


def gate(rows: list[dict], threshold: float) -> dict:
    """Resultado do fluxo com o limite aplicado (puro)."""
    hits = wrong = blocked_hits = 0
    for r in rows:
        clicked = r["decision"] is not None and (r.get("p_verify") or 0) >= threshold
        if r["bbox"] is None:
            wrong += clicked
        elif r["decision"] is not None:
            if r["hit"]:
                hits += clicked
                blocked_hits += not clicked
            else:
                wrong += clicked
    present = sum(r["bbox"] is not None for r in rows)
    return {"threshold": threshold, "hits": hits, "present": present, "wrong_clicks": wrong,
            "hits_blocked": blocked_hits}


def run(args) -> dict:
    cases = load_cases(args.cases) if args.cases else load_cases()
    rows = []
    with httpx.Client(timeout=args.timeout, trust_env=False) as client:
        for rep in range(args.reps):
            for case in cases:
                with Image.open(GROUND / case["image"]) as im:
                    image = im.convert("RGB")
                payload = {"model": args.grounder_model, "temperature": 0.0,
                           "max_tokens": args.max_tokens, "stream": False,
                           "messages": messages_for(args.protocol, case["instruction"],
                                                    _b64(image))}
                t0 = time.perf_counter()
                data = client.post(f"{args.grounder_url}/chat/completions", json=payload).json()
                g_ms = (time.perf_counter() - t0) * 1000
                ev = evaluate_response(case, data, g_ms, "unit" if args.protocol == "prod"
                                       else "1000")
                row = {"case_id": case["id"], "rep": rep, "bbox": case["bbox"],
                       "decision": ev["decision"], "hit": bool(ev["passed"] and ev["decision"]),
                       "truncated": ev.get("truncated", False), "ground_ms": round(g_ms, 1),
                       "error_kind": ev["error_kind"], "p_verify": None, "verify_ms": None}
                if ev["decision"]:
                    t1 = time.perf_counter()
                    row["p_verify"] = round(ask_verifier(
                        args.verifier_url, image, ev["decision"]["x"], ev["decision"]["y"],
                        case["instruction"]), 4)
                    row["verify_ms"] = round((time.perf_counter() - t1) * 1000, 1)
                rows.append(row)
                print(f"{case['id']}: ponto={row['decision'] is not None} acerto={row['hit']} "
                      f"p={row['p_verify']}", flush=True)
    return {"rows": rows, "gates": [gate(rows, t) for t in THRESHOLDS]}


def _b64(image: Image.Image) -> str:
    from vocaela import _prep_image
    return _prep_image(image)[0]


def render(result: dict, args) -> str:
    rows = result["rows"]
    vms = sorted(r["verify_ms"] for r in rows if r["verify_ms"])
    gms = sorted(r["ground_ms"] for r in rows)
    lines = ["# Confirmação do alvo: localizador + verificador", "",
             f"Localizador `{args.grounder_model}` ({args.protocol}, max_tokens "
             f"{args.max_tokens}); verificador d1 (`noul` sobre recorte de "
             f"{2 * CROP_PX} px). {len(rows)} tentativas.", "",
             f"Latência p50: localizador {gms[len(gms) // 2]:.0f} ms; verificador "
             f"{vms[len(vms) // 2] if vms else 0:.0f} ms.", "",
             "| Limite | Acertos que clicam | Acertos bloqueados | Cliques errados |",
             "| ---: | ---: | ---: | ---: |"]
    for g in result["gates"]:
        lines.append(f"| {g['threshold']:.2f} | {g['hits']}/{g['present']} | "
                     f"{g['hits_blocked']} | {g['wrong_clicks']} |")
    lines += ["", "Limite 0,00 = sem verificador (o localizador sozinho)."]
    return "\n".join(lines) + "\n"


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--grounder-url", required=True)
    ap.add_argument("--grounder-model", required=True)
    ap.add_argument("--verifier-url", required=True)
    ap.add_argument("--protocol", default="p2d", choices=("prod", "p2d", "pyauto"))
    ap.add_argument("--max-tokens", type=int, default=512)
    ap.add_argument("--reps", type=int, default=1)
    ap.add_argument("--timeout", type=float, default=180)
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--cases", type=Path,
                    help="bateria independente; imagens relativas a evals/ground ou absolutas")
    args = ap.parse_args(argv)
    args.out.mkdir(parents=True, exist_ok=False)
    result = run(args)
    (args.out / "rows.json").write_text(json.dumps(result, ensure_ascii=False, indent=2),
                                        encoding="utf-8")
    (args.out / "relatorio.md").write_text(render(result, args), encoding="utf-8")
    print(render(result, args))
    return 0


if __name__ == "__main__":
    sys.exit(main())
