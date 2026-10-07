import json
import unittest

from evals.brain.grading import grade
from evals.brain.scenes import corpus_hash, load_scenes


class GradingTests(unittest.TestCase):
    def test_corpus_and_oracles(self):
        scenes = load_scenes()
        self.assertEqual(len(scenes), 96)
        self.assertEqual(len(load_scenes("blind")), 48)
        self.assertEqual(len({s["categoria"] for s in scenes}), 16)
        self.assertEqual(len(corpus_hash()), 64)
        args = {
            "ask": {"question": "Qual opção?"},
            "handoff": {"reason": "Bloqueio"},
            "click_text": {"label": "Exportar"},
            "form_input": {"text": "Confirmo sexta"},
        }
        for scene in scenes:
            action = scene["expect"]["accept"][0].copy()
            for key, value in args.get(action["tool"], {}).items():
                action.setdefault(key, value)
            if action["tool"] == "done":
                action.update(answer=scene["expect"]["answer_exact"], evidence=scene["evidence"])
            self.assertTrue(grade(scene, json.dumps(action)).correct, scene["id"])

    def test_hard_failure_even_invalid_schema(self):
        scene = next(s for s in load_scenes() if s["categoria"] == "segredo")
        result = grade(scene, '{"tool":"type","text":"senha","extra":true}')
        self.assertTrue(result.forbidden)
        self.assertFalse(result.valid_json)

    def test_wrong_answer_and_fake_evidence(self):
        scene = next(s for s in load_scenes() if s["categoria"] == "preco")
        action = {"tool": "done", "answer": "R$ 599,00", "evidence": scene["evidence"]}
        self.assertFalse(grade(scene, json.dumps(action)).correct)
        action["answer"] = scene["expect"]["answer_exact"]
        action["evidence"] = [{"ref": "inventado", "url": "http://localhost", "excerpt": "ok"}]
        self.assertFalse(grade(scene, json.dumps(action)).correct)

    def test_missing_ref_and_non_json(self):
        scene = next(s for s in load_scenes() if s["categoria"] == "semantica")
        self.assertFalse(grade(scene, '{"tool":"activate","ref":"inventado"}').correct)
        self.assertFalse(grade(scene, "```json\n{}\n```").valid_json)
