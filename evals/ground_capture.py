"""Captura da bateria visual (ferramenta de DESENVOLVIMENTO; o runner não precisa dela).

Abre cada página de ground_specs.json num Chrome headless, tira o screenshot do viewport e
grava a caixa de cada alvo (DOM/JS, só no momento da captura) normalizada 0..1. O modelo
só recebe os pixels. Páginas reais mudam com o tempo: as imagens e as caixas saem da MESMA
captura, então continuam coerentes entre si. Imagens em evals/ground/images (gitignored).

    uv run --with playwright python -m evals.ground_capture
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
GROUND = HERE / "ground"
SPECS = HERE / "ground_specs.json"


def norm_box(box: dict, vw: int, vh: int) -> list[float]:
    x0, y0 = box["x"] / vw, box["y"] / vh
    x1, y1 = (box["x"] + box["width"]) / vw, (box["y"] + box["height"]) / vh
    return [round(max(0, x0), 5), round(max(0, y0), 5), round(min(1, x1), 5), round(min(1, y1), 5)]


def main() -> int:
    from playwright.sync_api import sync_playwright

    specs = json.loads(SPECS.read_text(encoding="utf-8"))
    (GROUND / "images").mkdir(parents=True, exist_ok=True)
    cases, skipped = [], []
    with sync_playwright() as p:
        browser = p.chromium.launch(channel="chrome", headless=True)
        for page_spec in specs["pages"]:
            vw, vh = page_spec["viewport"]
            ctx = browser.new_context(viewport={"width": vw, "height": vh}, locale="en-US")
            page = ctx.new_page()
            url = page_spec.get("url") or (GROUND / page_spec["file"]).resolve().as_uri()
            try:
                page.goto(url, wait_until="domcontentloaded", timeout=25000)
                page.wait_for_timeout(1800)
            except Exception as exc:  # rede/site fora: pula a página inteira
                skipped.append((page_spec["id"], f"carga: {str(exc)[:80]}"))
                ctx.close()
                continue
            image = f"images/{page_spec['id']}.png"
            page.screenshot(path=str(GROUND / image))
            for n, target in enumerate(page_spec["targets"]):
                case = {"id": f"{page_spec['id']}-{n}", "page": page_spec["id"],
                        "category": page_spec["category"], "image": image,
                        "instruction": target["instruction"], "viewport": [vw, vh],
                        "source": page_spec.get("url") or page_spec["file"]}
                if target.get("absent"):
                    case["bbox"] = None
                else:
                    try:
                        if "js" in target:
                            box = page.evaluate(f"({target['js']})")
                        else:
                            loc = page.locator(target["selector"]).first
                            loc.wait_for(state="visible", timeout=4000)
                            box = loc.bounding_box()
                        if not box or box["width"] < 2 or box["height"] < 2 or box["x"] < 0 \
                                or box["y"] < 0 or box["x"] + box["width"] > vw \
                                or box["y"] + box["height"] > vh:
                            raise ValueError("alvo fora do viewport/invisível")
                        case["bbox"] = norm_box(box, vw, vh)
                    except Exception as exc:
                        skipped.append((case["id"], str(exc)[:80]))
                        continue
                cases.append(case)
            ctx.close()
        browser.close()
    out = {"version": specs["version"], "cases": cases}
    (GROUND / "cases.json").write_text(json.dumps(out, ensure_ascii=False, indent=2) + "\n",
                                       encoding="utf-8")
    print(f"{len(cases)} casos em {GROUND / 'cases.json'}")
    for name, why in skipped:
        print(f"  pulado {name}: {why}")
    return 0 if cases else 1


if __name__ == "__main__":
    sys.exit(main())
