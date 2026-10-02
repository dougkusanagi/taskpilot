"""Exportador de SFT e modo --record: só passos úteis de runs concluídos."""
from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from evals import export_sft as ex


def row(**kw):
    base = {"run_id": "r1", "run_result": "done", "step": 1,
            "messages": [{"role": "system", "content": "S"}, {"role": "user", "content": "U"}],
            "response": '{"type":"hotkey","keys":"ctrl+t"}',
            "outcome": {"did": "hotkey(ctrl+t)", "verify": "janela mudou", "confirm": "ok"}}
    base.update(kw)
    return base


class TestUsefulStep(unittest.TestCase):
    def test_keeps_effective_steps_of_finished_runs(self):
        self.assertTrue(ex.useful_step(row()))

    def test_drops_failed_runs_ineffective_and_refused_steps(self):
        self.assertFalse(ex.useful_step(row(run_result="max_steps")))
        self.assertFalse(ex.useful_step(row(outcome={"did": "x", "verify": "no visible effect"})))
        self.assertFalse(ex.useful_step(row(outcome={"did": "acao recusada"})))
        self.assertFalse(ex.useful_step(row(outcome=None)))
        self.assertFalse(ex.useful_step(row(response="")))


class TestPlannerExport(unittest.TestCase):
    def test_reads_run_files_and_builds_chat_examples(self):
        with tempfile.TemporaryDirectory() as temp:
            run = Path(temp) / "run-a"
            run.mkdir()
            lines = [row(step=1), row(step=2, outcome={"did": "x", "verify": "no visible effect"}),
                     row(step=3, run_result="stuck")]
            (run / "sft.jsonl").write_text("\n".join(json.dumps(r) for r in lines),
                                           encoding="utf-8")
            rows = ex.planner_rows(Path(temp))
        self.assertEqual(len(rows), 1)
        self.assertEqual([m["role"] for m in rows[0]["messages"]], ["system", "user", "assistant"])
        self.assertEqual(rows[0]["meta"]["step"], 1)


class TestGroundExport(unittest.TestCase):
    def test_points_to_the_box_center_and_null_for_absent_targets(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "cases.json"
            path.write_text(json.dumps({"cases": [
                {"id": "a", "category": "c", "image": "images/a.png", "instruction": "Click OK",
                 "bbox": [0.2, 0.4, 0.4, 0.6]},
                {"id": "b", "category": "c", "image": "images/a.png", "instruction": "Click X",
                 "bbox": None}]}), encoding="utf-8")
            rows = ex.ground_rows(path)
        self.assertEqual(json.loads(rows[0]["messages"][2]["content"]), {"x": 0.3, "y": 0.5})
        self.assertEqual(json.loads(rows[1]["messages"][2]["content"]), {"x": None, "y": None})
        self.assertIn("Click OK", rows[0]["messages"][1]["content"][0]["text"])


if __name__ == "__main__":
    unittest.main()
