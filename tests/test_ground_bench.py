"""Bateria visual: pontuação, contrato de produção e ausência de vazamento do gabarito."""
from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import httpx
from PIL import Image

from evals import ground_bench as gb
from evals import model_bench as bench
from vocaela import QWEN_GROUNDING_SYSTEM

BOX = [0.4, 0.4, 0.6, 0.5]


def reply(content, finish="stop"):
    return {"model": "fake", "usage": {"completion_tokens": 10},
            "choices": [{"finish_reason": finish, "message": {"content": content}}]}


def case(bbox=BOX, image="images/x.png"):
    return {"id": "c1", "category": "t", "image": image, "bbox": bbox,
            "instruction": "Click the OK button", "viewport": [100, 100]}


class TestGroundBench(unittest.TestCase):
    def ev(self, content, bbox=BOX, finish="stop"):
        return gb.evaluate_response(case(bbox), reply(content, finish), 5)

    def test_point_inside_box_passes_and_outside_fails_with_distance(self):
        self.assertTrue(self.ev('{"x":0.5,"y":0.45}')["passed"])
        miss = self.ev('{"x":0.9,"y":0.9}')
        self.assertFalse(miss["passed"])
        self.assertEqual(miss["error_kind"], "semantic")
        self.assertGreater(miss["miss_distance"], .3)

    def test_edges_count_as_inside(self):
        self.assertTrue(self.ev('{"x":0.4,"y":0.5}')["passed"])

    def test_absent_target_requires_null_and_flags_false_click(self):
        self.assertTrue(self.ev('{"x":null,"y":null}', bbox=None)["passed"])
        row = self.ev('{"x":0.5,"y":0.5}', bbox=None)
        self.assertFalse(row["passed"])
        self.assertTrue(row["false_click"])

    def test_existing_target_declared_absent_fails(self):
        row = self.ev('{"x":null,"y":null}')
        self.assertFalse(row["passed"])
        self.assertTrue(row["said_absent"])
        self.assertEqual(row["error_kind"], "semantic")

    def test_thousand_scale_is_rejected_but_diagnosed(self):
        row = self.ev('{"x":500,"y":450}')
        self.assertEqual(row["error_kind"], "format")
        self.assertTrue(row["hit_if_scale_1000"])
        self.assertFalse(row["passed"])

    def test_garbage_and_truncation_are_failures(self):
        self.assertEqual(self.ev("não sei")["error_kind"], "format")
        self.assertEqual(self.ev('{"x":0.5,"y":0.45}', finish="length")["error_kind"],
                         "truncated")
        self.assertFalse(self.ev('{"x":0.5,"y":0.45}', finish="length")["passed"])

    def test_extra_summary_counts_false_clicks_and_scale(self):
        rows = [self.ev('{"x":0.5,"y":0.5}', bbox=None), self.ev('{"x":null,"y":null}', bbox=None),
                self.ev('{"x":500,"y":450}'), self.ev('{"x":0.5,"y":0.45}')]
        extra = gb.extra_summary(rows)
        self.assertEqual(extra["Cliques falsos (alvo inexistente)"], 1)
        self.assertEqual(extra["Alvos ausentes tratados como ausentes"], "1/2")
        self.assertEqual(extra["Alvos existentes localizados"], "1/2")

    def test_messages_use_production_prompt_and_never_leak_the_box(self):
        with tempfile.TemporaryDirectory() as temp:
            (Path(temp) / "images").mkdir()
            Image.new("RGB", (200, 100), "white").save(Path(temp) / "images/x.png")
            with patch.object(gb, "GROUND", Path(temp)), patch.dict(gb._IMAGES, clear=True):
                messages = gb.make_messages(case())
        self.assertEqual(messages[0]["content"], QWEN_GROUNDING_SYSTEM)
        text, image = messages[1]["content"]
        self.assertIn("Click the OK button", text["text"])
        self.assertTrue(image["image_url"]["url"].startswith("data:image/jpeg;base64,"))
        self.assertNotIn("0.4", json.dumps(messages).replace("data:image", ""))

    def test_quick_has_one_case_per_category_plus_an_absent(self):
        cases = [{"id": f"{c}{i}", "category": c, "bbox": None if i == 1 else BOX}
                 for c in "ab" for i in range(3)]
        quick = gb.quick_cases(cases)
        self.assertEqual({q["category"] for q in quick}, {"a", "b"})
        self.assertTrue(any(q["bbox"] is None for q in quick))

    def test_suite_runs_through_the_shared_runner_without_schema(self):
        payloads = []

        def handler(request):
            payloads.append(json.loads(request.content))
            return httpx.Response(200, json=reply('{"x":0.5,"y":0.45}'))

        with tempfile.TemporaryDirectory() as temp:
            (Path(temp) / "images").mkdir()
            Image.new("RGB", (200, 100), "white").save(Path(temp) / "images/x.png")
            with patch.object(gb, "GROUND", Path(temp)), patch.dict(gb._IMAGES, clear=True), \
                    httpx.Client(transport=httpx.MockTransport(handler)) as client:
                rows = bench.run_requests(client, "http://127.0.0.1/v1", "fake", [case()], 1,
                                          {"format": "prompt", "temperature": 0,
                                           "max_tokens": 64, "thinking": "off"},
                                          lambda _: None, gb.SUITE)
        self.assertTrue(rows[0]["passed"])
        self.assertNotIn("response_format", payloads[0])
        self.assertEqual(payloads[0]["reasoning_effort"], "none")
        self.assertGreater(rows[0]["tokens_per_s"], 0)

    def test_committed_specs_are_well_formed(self):
        specs = json.loads((gb.GROUND.parent / "ground_specs.json").read_text(encoding="utf-8"))
        ids = [p["id"] for p in specs["pages"]]
        self.assertEqual(len(ids), len(set(ids)))
        for page in specs["pages"]:
            self.assertTrue(page.get("url") or (gb.GROUND / page["file"]).is_file(), page["id"])
            for target in page["targets"]:
                self.assertTrue(target.get("absent") or target.get("selector")
                                or target.get("js"), (page["id"], target))


if __name__ == "__main__":
    unittest.main()
