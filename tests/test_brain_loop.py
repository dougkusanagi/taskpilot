import json
import unittest

from evals.brain.fixtures import Fixture
from evals.brain.loop import run_episode
from evals.brain.verification import cheapest, expected_answer, verify_episode


class LoopTests(unittest.TestCase):
    def test_multi_turn_draft(self):
        env = Fixture("webmail")
        seen = []

        def decide(payload):
            seen.append(payload)
            if len(seen) == 1:
                return json.dumps(
                    {
                        "tool": "form_input",
                        "ref": env.current.elements[0].ref,
                        "text": "Confirmo a reunião de sexta às 14h.",
                    }
                )
            return json.dumps(
                {"tool": "done", "answer": expected_answer(env), "evidence": payload["evidence"]}
            )

        episode = run_episode("Crie rascunho", env, decide)
        self.assertEqual(episode.status, "done")
        self.assertTrue(verify_episode(env, episode)["correct"])
        self.assertIn("delta", seen[1]["history"][0]["result"])
        self.assertEqual(episode.state["sent"], [])
        episode.answer["answer"] = "Enviado!"
        self.assertFalse(verify_episode(env, episode)["correct"])

    def test_paginate_then_answer(self):
        env = Fixture("loja")

        def decide(payload):
            if not env.evidence or env.current.next:
                return json.dumps(
                    {"tool": "get_page_text", "next": env.current.next if env.evidence else ""}
                )
            return json.dumps(
                {"tool": "done", "answer": expected_answer(env), "evidence": payload["evidence"]}
            )

        episode = run_episode("Menor preço", env, decide)
        self.assertEqual(len(episode.turns), 4)
        self.assertTrue(verify_episode(env, episode)["correct"])
        self.assertEqual(cheapest(env)["price"], "R$ 12.499,00")
        episode.answer["evidence"] = episode.answer["evidence"][:1]
        self.assertFalse(verify_episode(env, episode)["correct"])

    def test_budgets_and_infrastructure(self):
        env = Fixture("loja")
        episode = run_episode("Teste", env, lambda _: "inválido", max_errors=1)
        self.assertEqual(episode.status, "error_budget")
        self.assertEqual(len(episode.turns), 2)
        episode = run_episode("Teste", env, lambda _: '{"tool":"get_page_text"}', max_steps=2)
        self.assertEqual(episode.status, "budget_exhausted")
        episode = run_episode("Teste", env, lambda _: "", timeout=0)
        self.assertEqual(episode.status, "timeout")

    def test_handoff(self):
        env = Fixture("captcha")
        episode = run_episode("Pesquisar", env, lambda _: '{"tool":"handoff","reason":"CAPTCHA"}')
        self.assertTrue(verify_episode(env, episode)["correct"])
