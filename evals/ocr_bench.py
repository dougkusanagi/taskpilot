"""Precisão da ferramenta `click_text` (OCR + busca) sobre a bateria visual.

Não envolve modelo: para cada alvo com `label` (o texto visível que o planner passaria), roda o
OCR local nas screenshots congeladas, procura o texto com `ocr.find_text` e confere se o ponto
cai dentro da caixa do alvo. Mede também recusas honestas (ambíguo/ausente) e o tempo.

    uv run --extra ocr python -m evals.ocr_bench   # ou: uv run --with rapidocr-onnxruntime ...
"""
from __future__ import annotations

import argparse
import collections
import json
import statistics
import sys
import time

from PIL import Image

import ocr
from evals import ground_bench as gb


def run(cases: list[dict], lang: str = "pt") -> dict:
    rows, cache = [], {}
    for case in cases:
        label = case.get("label")
        if not label or case["bbox"] is None:
            continue
        if case["image"] not in cache:
            with Image.open(gb.GROUND / case["image"]) as im:
                cache[case["image"]] = ocr.read_words(im.convert("RGB"), lang)
        read = cache[case["image"]]
        if not read["ok"]:
            rows.append({"id": case["id"], "category": case["category"],
                         "status": "ocr_unavailable", "reason": read["reason"], "hit": False,
                         "ms": read["ms"]})
            continue
        w, h = read["size"]
        found = ocr.find_text(read["words"], label)
        hit = False
        if found["status"] == "ok":
            cx, cy = found["center"][0] / w, found["center"][1] / h
            hit = gb.inside((cx, cy), case["bbox"])
        rows.append({"id": case["id"], "category": case["category"], "label": label,
                     "status": found["status"], "hit": hit, "ms": read["ms"],
                     "n_lines": len(read["words"])})
    by_status = collections.Counter(r["status"] for r in rows)
    by_cat = collections.defaultdict(lambda: [0, 0])
    for r in rows:
        by_cat[r["category"]][1] += 1
        by_cat[r["category"]][0] += r["hit"]
    wrong = [r for r in rows if r["status"] == "ok" and not r["hit"]]
    return {"backend": ocr.backend(), "n": len(rows), "hits": sum(r["hit"] for r in rows),
            "status": dict(by_status), "wrong_clicks": len(wrong),
            "by_category": {k: f"{v[0]}/{v[1]}" for k, v in sorted(by_cat.items())},
            "ocr_ms_p50": statistics.median([r["ms"] for r in rows]) if rows else None,
            "rows": rows}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--json", action="store_true", help="imprime o resultado completo")
    args = ap.parse_args(argv)
    if not ocr.available():
        print("Sem backend OCR: " + ocr.status()["reason"])
        return 2
    started = time.perf_counter()
    result = run(gb.load_cases())
    result["total_s"] = round(time.perf_counter() - started, 1)
    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        print(f"backend={result['backend']}  alvos com texto: {result['n']}  "
              f"acertos: {result['hits']}  cliques errados: {result['wrong_clicks']}  "
              f"status: {result['status']}  OCR p50: {result['ocr_ms_p50']} ms")
        for cat, value in result["by_category"].items():
            print(f"  {cat:16} {value}")
        for r in result["rows"]:
            if not r["hit"]:
                print(f"  falhou: {r['id']:14} {r['label']!r:30} {r['status']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
