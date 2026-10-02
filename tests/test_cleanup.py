"""Limpeza pós-2-modelos: era pré-arquitetura não pode voltar.

Trava a remoção dos restos da era scorer/VLM-genérico e scripts de
experimento (E1-E3): se algum arquivo morto reaparecer ou algum import
vivo voltar a referenciá-los, a suite quebra aqui.

Roda com: uv run python -m unittest discover -s tests -v
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

# Módulos aposentados do fluxo principal (decisão 100% por IA: planner+UIA+Vocaela).
DEAD_MODULES = ("scorer.py", "vlm.py", "tests_adapters.py",
                "dbg_fix.py", "dbg_guard.py", "dbg_plan.py", "dbg_plan2.py",
                "imp.py", "run_e1.py")
# Artefatos de experimento (logs/provas manuais, nunca versionar de novo).
DEAD_ARTIFACTS = ("e1.txt", "t1.txt", "t2.txt", "overlay_proof.png",
                  "overlay_proof2.png", "overlay_proof3.png",
                  "overlay_proof4.png", "overlay_proof5.png")


class TestCleanup(unittest.TestCase):
    def test_modulos_mortos_ausentes(self):
        for name in DEAD_MODULES:
            self.assertFalse((ROOT / name).exists(),
                             f"módulo morto reapareceu: {name}")

    def test_artefatos_experimento_ausentes(self):
        for name in DEAD_ARTIFACTS:
            self.assertFalse((ROOT / name).exists(),
                             f"artefato de experimento reapareceu: {name}")

    def test_nenhum_import_vivo_de_modulo_morto(self):
        import re

        dead = ("scorer", "vlm", "tests_adapters", "dbg_fix", "dbg_guard",
                "dbg_plan", "dbg_plan2", "run_e1")
        pat = re.compile(r"^\s*(?:import|from)\s+(" + "|".join(dead) + r")\b",
                         re.MULTILINE)
        live = [p for p in ROOT.glob("*.py")]
        live += [p for p in (ROOT / "tests").glob("*.py")]
        offenders = [str(p) for p in live if pat.search(
            p.read_text(encoding="utf-8"))]
        self.assertEqual(offenders, [], f"import de módulo morto: {offenders}")

    def test_loop_sem_ramo_scorer(self):
        src = (ROOT / "loop.py").read_text(encoding="utf-8")
        self.assertNotIn('"SCORER"', src)
        self.assertNotIn("scorer_top", src)
        self.assertNotIn("scorer_ms", src)

    def test_source_type_so_fontes_reais(self):
        import schemas

        self.assertEqual(set(schemas.SourceType.__args__),
                         {"planner", "uia", "vocaela", "ocr"})

    def test_obs_sem_helpers_vlm_generico(self):
        import obs

        for name in ("downscale_for_vlm", "screenshot_b64_for_vlm",
                     "VLM_MAX_WIDTH"):
            self.assertFalse(hasattr(obs, name), f"helper morto: {name}")
        self.assertTrue(hasattr(obs, "capture_for_vision"))

    def test_sem_mojibake_nem_bom(self):
        """UTF-8 duplo (A-tilde + byte alto, 'a-circunflexo euro') e BOM ja
        entraram no prompt do planner (commit anterior a 18/09)."""
        import re

        # '\u00c3' seguido de byte alto = acento codificado 2x; 'N\u00c3O' passa.
        pat = re.compile("\u00c3[\u0080-\u00bf]|\u00e2\u20ac")
        files = list(ROOT.glob("*.py")) + list((ROOT / "tests").glob("*.py"))
        files += [ROOT / "README.md", ROOT / "AGENTS.md"]  # docs/ pode citar exemplos
        for p in files:
            raw = p.read_bytes()
            self.assertFalse(raw.startswith(b"\xef\xbb\xbf"), f"BOM em {p.name}")
            m = pat.search(raw.decode("utf-8"))
            self.assertIsNone(m, f"mojibake em {p.name}: {m and m.group(0)!r}")

    def test_gitignore_cobre_provas_de_overlay(self):
        ign = (ROOT / ".gitignore").read_text(encoding="utf-8")
        self.assertIn("overlay_proof*.png", ign.splitlines())


if __name__ == "__main__":
    unittest.main(verbosity=2)
