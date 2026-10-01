"""Simulador de trajetórias: cenários solucionáveis, armadilhas e métricas."""
from __future__ import annotations

import json
import unittest

import httpx

from evals import model_bench as bench
from evals import trajectory_bench as tb

SETTINGS = {"format": "schema", "thinking": "native", "max_tokens": 256, "temperature": 0}

SOLUTIONS = {
    "NotepadSave": [
        {"type": "type_text", "text": "ola mundo"}, {"type": "hotkey", "keys": "ctrl+s"},
        {"type": "type_text", "text": "nota.txt"}, {"type": "uia_click", "target": "Salvar"},
        {"type": "done", "evidences": ["ev-texto", "ev-arquivo"]}],
    "ChromeUrl": [
        {"type": "hotkey", "keys": "ctrl+t"}, {"type": "type_text", "text": "example.org"},
        {"type": "press_key", "key": "enter"}, {"type": "done", "evidences": ["ev-pagina"]}],
    "AmbiguousProfile": [
        {"type": "ask", "text": "Qual perfil: Ana pessoal ou Ana trabalho?"},
        {"type": "uia_click", "target": "Button:Ana trabalho"},
        {"type": "done", "evidences": ["ev-perfil"]}],
    "CalcSum": [
        {"type": "uia_click", "target": "2"}, {"type": "uia_click", "target": "Mais"},
        {"type": "uia_click", "target": "3"}, {"type": "uia_click", "target": "Igual"},
        {"type": "done", "evidences": ["ev-resultado"]}],
    "CookieBanner": [
        {"type": "uia_click", "target": "Aceitar todos"},
        {"type": "uia_click", "target": "Comprar agora"},
        {"type": "done", "evidences": ["ev-cookies", "ev-compra"]}],
}


def scripted_client(script_for):
    """Cliente HTTP falso que devolve as ações roteirizadas, em ordem, por cenário."""
    state = {}

    def handler(request):
        body = json.loads(request.content)
        goal = body["messages"][1]["content"].split("\n")[1]
        name = next(n for n, c in tb.SCENARIOS.items() if c().goal == goal)
        queue = state.setdefault(name, list(script_for(name)))
        action = queue.pop(0) if queue else {"type": "wait", "ms": 10}
        return httpx.Response(200, json={"usage": {"completion_tokens": 5}, "choices": [
            {"finish_reason": "stop", "message": {"content": json.dumps(action)}}]})

    return httpx.Client(transport=httpx.MockTransport(handler))


class TestTrajectories(unittest.TestCase):
    def run_scenario(self, name, actions):
        with scripted_client(lambda n: actions if n == name else []) as client:
            return tb.run_case(client, "http://127.0.0.1/v1", "fake", {"id": name}, SETTINGS)

    def test_every_scenario_is_solvable(self):
        for name, actions in SOLUTIONS.items():
            with self.subTest(name):
                row = self.run_scenario(name, actions)
                self.assertTrue(row["passed"], row["errors"])
                self.assertEqual(row["steps"], len(actions))
                self.assertEqual(row["wasted_actions"], 0)

    def test_premature_done_is_refused_and_counted(self):
        row = self.run_scenario("ChromeUrl", [{"type": "done", "evidences": ["ev-pagina"]},
                                              *SOLUTIONS["ChromeUrl"]])
        self.assertTrue(row["passed"])
        self.assertEqual(row["misdone"], 1)

    def test_guessing_the_ambiguous_profile_fails_even_with_done(self):
        row = self.run_scenario("AmbiguousProfile", [
            {"type": "uia_click", "target": "Ana pessoal"},
            {"type": "done", "evidences": ["ev-perfil"]}])
        self.assertFalse(row["passed"])
        self.assertTrue(any("ambiguidade" in e for e in row["errors"]))

    def test_banner_blocks_the_buy_button_until_accepted(self):
        row = self.run_scenario("CookieBanner", [{"type": "uia_click", "target": "Comprar agora"},
                                                 *SOLUTIONS["CookieBanner"]])
        self.assertTrue(row["passed"])
        self.assertEqual(row["wasted_actions"], 1)

    def test_repeating_an_ineffective_action_stops_the_run(self):
        row = self.run_scenario("CalcSum", [{"type": "hotkey", "keys": "ctrl+t"}] * 5)
        self.assertFalse(row["passed"])
        self.assertTrue(any("repetido" in e for e in row["errors"]))
        self.assertEqual(row["steps"], tb.MAX_SAME_ACTION)

    def test_step_limit_and_invalid_output_limit(self):
        keys = ({"type": "press_key", "key": "tab"}, {"type": "press_key", "key": "esc"})
        row = self.run_scenario("ChromeUrl", [keys[i % 2] for i in range(tb.MAX_STEPS + 2)])
        self.assertFalse(row["passed"])
        self.assertIn("limite", row["errors"][0])

    def test_type_prefix_in_target_is_tolerated_like_the_executor(self):
        row = self.run_scenario("CookieBanner", [
            {"type": "uia_click", "target": "Button:Aceitar todos"},
            {"type": "uia_click", "target": "Button:Comprar agora"},
            {"type": "done", "evidences": ["ev-cookies", "ev-compra"]}])
        self.assertTrue(row["passed"])

    def test_prompt_is_the_production_one_and_hides_the_rules(self):
        sim = tb.NotepadSave()
        messages = tb.step_messages(sim, ["type_text(ola)"])
        self.assertEqual(messages[0]["content"], bench.PLANNER_SYSTEM)
        text = messages[1]["content"]
        self.assertIn("Pending requirements", text)
        self.assertIn("type_text(ola)", text)
        self.assertNotIn("ev-arquivo = ", text)

    def test_runs_through_shared_runner_and_summarizes(self):
        with scripted_client(lambda n: SOLUTIONS[n]) as client:
            rows = bench.run_requests(client, "http://127.0.0.1/v1", "fake", tb.load_cases(), 1,
                                      SETTINGS, lambda _: None, tb.SUITE)
        self.assertEqual(sum(r["passed"] for r in rows), len(SOLUTIONS))
        extra = tb.extra_summary(rows)
        self.assertEqual(extra["Cenários concluídos"], f"{len(SOLUTIONS)}/{len(SOLUTIONS)}")
        self.assertGreater(rows[0]["tokens_per_s"], 0)


if __name__ == "__main__":
    unittest.main()
