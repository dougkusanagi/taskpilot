import unittest

from pydantic import ValidationError

from evals.brain.contract import Decision, Element, Evidence, Observation, validate_context


class ContractTests(unittest.TestCase):
    def test_variants_and_extra(self):
        action = Decision.validate_python({"tool": "activate", "ref": "o1:e1"})
        self.assertEqual(action.ref, "o1:e1")
        for raw in (
            {"tool": "activate"},
            {"tool": "activate", "ref": "e1", "x": 0},
            {"tool": "shell", "text": "pwd"},
            {"tool": "done", "answer": "ok", "evidence": []},
        ):
            with self.assertRaises(ValidationError):
                Decision.validate_python(raw)

    def test_refs_and_evidence(self):
        obs = Observation(
            id="o1",
            url="http://localhost",
            elements=[Element(ref="o1:e1", role="Edit", name="campo")],
        )
        action = Decision.validate_python({"tool": "read", "ref": "o0:e1"})
        with self.assertRaises(ValueError):
            validate_context(action, obs, [])
        ev = Evidence(ref="r1", url=obs.url, excerpt="valor lido")
        done = Decision.validate_python(
            {"tool": "done", "answer": "ok", "evidence": [ev.model_dump()]}
        )
        with self.assertRaises(ValueError):
            validate_context(done, obs, [])
        validate_context(done, obs, [ev])
        self.assertIn("UNTRUSTED", obs.render())
