"""Bancada com orçamento de VRAM, confirmação do alvo e regra de truncamento do grounding."""
from __future__ import annotations

import unittest
from pathlib import Path
from unittest.mock import patch

from PIL import Image

import vocaela as v
from evals import budget_bench as bb
from evals import verify_bench as vb
from evals import vram_budget as vr


def reply(content, finish="stop"):
    return {"choices": [{"finish_reason": finish, "message": {"content": content}}]}


class GroundingTruncation(unittest.TestCase):
    def test_truncated_answer_is_absent_never_a_point(self):
        with self.assertRaisesRegex(ValueError, "não visível"):
            v.grounding_content(reply('{"point_2d": [500, 500]}', "length"))
        self.assertEqual(v.grounding_content(reply('{"point_2d": [1, 2]}')), '{"point_2d": [1, 2]}')

    def test_lfm3_vl_is_a_grounding_model_with_configurable_budget(self):
        self.assertTrue(v.is_grounding_family("LFM3-VL-2B"))
        adapter = v.QwenGroundingAdapter(model="LFM3-VL-2B", max_tokens=512)
        self.assertEqual((adapter.protocol, adapter.coords, adapter.max_tokens),
                         ("p2d", "1000", 512))

    def test_build_adapters_passes_vision_max_tokens(self):
        from model_adapters import build_adapters
        cfg = {"profile": "B1", "vision": {"model": "LFM3-VL-2B", "max_tokens": 512}}
        _, vision, _ = build_adapters(cfg)
        self.assertEqual(vision.max_tokens, 512)


class VramBudget(unittest.TestCase):
    def test_budget_is_card_minus_reserve(self):
        self.assertEqual(vr.budget_mb(6144, 1536), 4608)
        for card, reserve in ((0, 0), (6144, 6144), (6144, -1)):
            with self.assertRaises(ValueError):
                vr.budget_mb(card, reserve)


class BudgetBench(unittest.TestCase):
    def test_server_runs_full_gpu_without_silent_cpu_fallback(self):
        cmd = bb.server_cmd("llama-server", Path("/m"), {"alias": "a", "gguf": "a.gguf",
                                                          "mmproj": "p.gguf",
                                                          "reasoning_budget": 256}, 9000)
        joined = " ".join(cmd)
        self.assertIn("-ngl 99", joined)
        self.assertIn("--fit off", joined)
        self.assertEqual(cmd[cmd.index("--mmproj") + 1], str(Path("/m") / "p.gguf"))
        self.assertIn("--reasoning-budget 256", joined)

    def test_oom_reason_is_short_and_specific(self):
        self.assertEqual(bb.oom_reason("...\nCUDA error: out of memory\n"), "out of memory")
        self.assertEqual(bb.oom_reason("nada"), "servidor não ficou pronto")

    def test_verify_cmd_points_to_both_servers(self):
        cmd = bb.verify_cmd({"model": "g", "verifier": "d"}, {"g": "u1", "d": "u2"}, Path("o"))
        self.assertEqual(cmd[cmd.index("--grounder-url") + 1], "u1")
        self.assertEqual(cmd[cmd.index("--verifier-url") + 1], "u2")


class VerifyBench(unittest.TestCase):
    def rows(self):
        hit = {"bbox": [0, 0, 1, 1], "decision": {"x": .5, "y": .5}, "hit": True}
        miss = {"bbox": [0, 0, .1, .1], "decision": {"x": .5, "y": .5}, "hit": False}
        absent = {"bbox": None, "decision": {"x": .5, "y": .5}, "hit": False}
        said_absent = {"bbox": None, "decision": None, "hit": False, "p_verify": None}
        return [dict(hit, p_verify=.9), dict(hit, p_verify=.2), dict(miss, p_verify=.3),
                dict(absent, p_verify=.1), said_absent]

    def test_independent_cases_are_used_instead_of_calibration_set(self):
        import json
        import tempfile
        from types import SimpleNamespace
        from unittest.mock import MagicMock

        with tempfile.TemporaryDirectory() as tmp:
            image = Path(tmp) / "new-screen.png"
            Image.new("RGB", (100, 100)).save(image)
            cases = Path(tmp) / "cases.json"
            cases.write_text(json.dumps({"cases": [{"id": "held-out", "image": str(image),
                "instruction": "Click OK", "bbox": [.4, .4, .6, .6]}]}))
            args = SimpleNamespace(cases=cases, timeout=1, reps=1, grounder_model="test",
                                   max_tokens=512, protocol="p2d", grounder_url="http://g/v1",
                                   verifier_url="http://d/v1")
            response = MagicMock()
            response.json.return_value = reply('{"point_2d": [500, 500]}')
            with patch.object(vb.httpx, "Client") as client, \
                    patch.object(vb, "ask_verifier", return_value=.8):
                client.return_value.__enter__.return_value.post.return_value = response
                result = vb.run(args)
            self.assertEqual([r["case_id"] for r in result["rows"]], ["held-out"])
            self.assertEqual(result["gates"][0]["hits"], 1)

    def test_gate_counts_wrong_clicks_on_absent_and_off_target(self):
        g0 = vb.gate(self.rows(), 0.0)
        self.assertEqual((g0["hits"], g0["wrong_clicks"], g0["hits_blocked"]), (2, 2, 0))
        g = vb.gate(self.rows(), 0.25)
        self.assertEqual((g["hits"], g["wrong_clicks"], g["hits_blocked"]), (1, 1, 1))

    def test_crop_is_centered_and_clamped(self):
        img = Image.new("RGB", (1280, 800))
        self.assertEqual(v.verify_crop(img, 0.0, 0.0).size, (384, 384))

    def test_question_strips_the_action_verb(self):
        self.assertEqual(v.target_text("Click the trash can icon"), "the trash can icon")
        self.assertIn("the trash can icon?", v.verify_question("Click the trash can icon"))


class TargetVerifierGate(unittest.TestCase):
    img = Image.new("RGB", (1280, 800))

    def post(self, p):
        class R:
            def raise_for_status(self):
                pass

            def json(self):
                return {"answers": {"q": {"noul": p}}}
        return lambda url, **k: R()

    def test_invalid_probability_blocks_click(self):
        for probability in (float("nan"), float("inf"), -0.1, 1.1, True, "0.9", None):
            with self.subTest(probability=probability):
                with patch.object(v.httpx, "post", self.post(probability)):
                    with self.assertRaisesRegex(ValueError, "verificador falhou"):
                        v.TargetVerifier("http://x/v1").confirm(self.img, .5, .5, "Click OK")

    def test_low_probability_is_not_visible(self):
        with patch.object(v.httpx, "post", self.post(0.1)):
            with self.assertRaisesRegex(ValueError, "não visível"):
                v.TargetVerifier("http://x/v1", 0.2).confirm(self.img, .5, .5, "Click OK")
            self.assertEqual(v.TargetVerifier("http://x/v1", 0.05).confirm(
                self.img, .5, .5, "Click OK"), 0.1)

    def test_verifier_failure_never_clicks(self):
        def boom(url, **k):
            raise OSError("caiu")
        with patch.object(v.httpx, "post", boom):
            with self.assertRaisesRegex(ValueError, "verificador falhou"):
                v.TargetVerifier("http://x/v1").confirm(self.img, .5, .5, "Click OK")

    def test_invalid_threshold_is_rejected(self):
        with self.assertRaises(ValueError):
            v.TargetVerifier("http://x/v1", 1.5)

    def test_adapter_asks_the_verifier_before_returning_a_point(self):
        seen = []

        class Ver:
            def confirm(self, image, x, y, instruction):
                seen.append((x, y, instruction))
                raise ValueError("alvo não visível p/ o grounding: verificador não confirmou")
        a = v.QwenGroundingAdapter(model="LFM3-VL-2B", verifier=Ver())
        with patch.object(a, "_ask", lambda *_: '{"point_2d": [500, 250]}'):
            with self.assertRaisesRegex(ValueError, "não visível"):
                a.act_sync(self.img, "Click OK")
        self.assertEqual(len(seen), 1)

    def test_build_adapters_wires_the_verifier(self):
        from model_adapters import build_adapters
        cfg = {"profile": "B1", "vision": {"model": "LFM3-VL-2B", "verifier": {
            "base_url": "http://127.0.0.1:8193/v1", "threshold": 0.3}}}
        _, vision, _ = build_adapters(cfg)
        self.assertEqual(vision.verifier.threshold, 0.3)


if __name__ == "__main__":
    unittest.main()
