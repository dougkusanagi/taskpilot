import json
import unittest

from evals.brain import agent_contract
from evals.brain.browser_contract import TOOLS


class AgentContractTests(unittest.TestCase):
    def test_same_tools_as_browser_contract(self):
        names = {m.model_fields["tool"].annotation.__args__[0] for m in agent_contract.models}
        self.assertEqual(names | {"done"}, TOOLS)

    def test_done_cites_refs_not_copies(self):
        ok = json.dumps({"tool": "done", "answer": "x", "evidence": ["r:a1b2c3d4"]})
        self.assertEqual(agent_contract.Decision.validate_json(ok).evidence, ["r:a1b2c3d4"])
        for bad in (
            {"tool": "done", "answer": "x", "evidence": []},
            {
                "tool": "done",
                "answer": "x",
                "evidence": [{"ref": "r:a", "url": "u", "excerpt": "e"}],
            },
            {"tool": "done", "answer": "x"},
        ):
            with self.assertRaises(ValueError):
                agent_contract.Decision.validate_json(json.dumps(bad))

    def test_schema_exposes_a_string_list_for_constrained_decoding(self):
        schema = json.dumps(agent_contract.schema())
        self.assertIn('"minItems": 1', schema)


if __name__ == "__main__":
    unittest.main()
