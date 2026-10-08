import json
import unittest

from pydantic import ValidationError

from evals.brain.browser_contract import TOOLS, Decision, schema
from evals.brain.grading import grade
from evals.brain.scenes import corpus_hash, load_scenes


class BrowserBenchTests(unittest.TestCase):
    def test_profile_preserves_refs_but_rejects_desktop(self):
        self.assertEqual(Decision.validate_python({"tool": "click", "ref": "o1:e1"}).ref, "o1:e1")
        for tool in ["activate", "click_text", "click_visual", "type", "read", "set_text"]:
            with self.assertRaises(ValidationError):
                Decision.validate_python({"tool": tool, "ref": "o1:e1"})
        self.assertNotIn("activate", json.dumps(schema()))
        self.assertIn("form_input", TOOLS)

    def test_new_corpus_and_expected_actions(self):
        scenes = load_scenes(corpus="browser")
        self.assertEqual(len(scenes), 96)
        self.assertEqual(len(load_scenes("blind", "browser")), 48)
        self.assertNotEqual(corpus_hash("browser"), corpus_hash("legacy"))
        defaults = {
            "ask": {"question": "Qual opção?"},
            "handoff": {"reason": "Bloqueio humano"},
            "find": {"description": "Controle solicitado"},
            "form_input": {"text": "Confirmo a reunião de sexta."},
        }
        for scene in scenes:
            self.assertTrue(scene["observation"]["url"].startswith("http://localhost:"))
            self.assertIsNone(scene["image"])
            action = scene["expect"]["accept"][0].copy()
            action.update(defaults.get(action["tool"], {}))
            if action["tool"] == "done":
                action.update(answer=scene["expect"]["answer_exact"], evidence=scene["evidence"])
            result = grade(scene, json.dumps(action), corpus="browser")
            self.assertTrue(result.correct, (scene["id"], result))

    def test_wrong_scope_and_invented_evidence(self):
        scene = next(s for s in load_scenes(corpus="browser") if s["categoria"] == "progresso")
        self.assertFalse(
            grade(scene, '{"tool":"get_page_text","scope":"old:e1"}', "browser").correct
        )
