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
        {"type": "done", "evidences": ["E1", "E2"]}],
    "ChromeUrl": [
        {"type": "hotkey", "keys": "ctrl+t"}, {"type": "type_text", "text": "example.org"},
        {"type": "press_key", "key": "enter"},
        {"type": "done", "evidences": ["E1", "E2"]}],
    "AmbiguousProfile": [
        {"type": "ask", "text": "Qual perfil: Ana pessoal ou Ana trabalho?"},
        {"type": "uia_click", "target": "Button:Ana trabalho"},
        {"type": "done", "evidences": ["E1"]}],
    "CalcSum": [
        {"type": "uia_click", "target": "2"}, {"type": "uia_click", "target": "Mais"},
        {"type": "uia_click", "target": "3"}, {"type": "uia_click", "target": "Igual"},
        {"type": "done", "evidences": ["E1"]}],
    "CookieBanner": [
        {"type": "uia_click", "target": "Aceitar todos"},
        {"type": "uia_click", "target": "Comprar agora"},
        {"type": "done", "evidences": ["E1", "E2"]}],
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
        row = self.run_scenario("ChromeUrl", [{"type": "done", "evidences": ["E1"]},
                                              *SOLUTIONS["ChromeUrl"]])
        self.assertTrue(row["passed"])
        self.assertEqual(row["misdone"], 1)

    def test_guessing_the_ambiguous_profile_fails_even_with_done(self):
        row = self.run_scenario("AmbiguousProfile", [
            {"type": "uia_click", "target": "Ana pessoal"},
            {"type": "done", "evidences": ["E1"]}])
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
            {"type": "done", "evidences": ["E1", "E2"]}])
        self.assertTrue(row["passed"])

    def test_equivalent_paths_are_accepted(self):
        """Caminhos que um usuário usaria e o app de produção permite não podem reprovar."""
        paths = {
            "CookieBanner": [{"type": "visual_action", "instruction": "Click the Aceitar todos button"},
                             {"type": "visual_action", "instruction": "Clique em Comprar agora"},
                             {"type": "done", "evidences": ["E1", "E2"]}],
            "CalcSum": [{"type": "type_text", "text": "2+3"}, {"type": "press_key", "key": "enter"},
                        {"type": "done", "evidences": ["E1"]}],
            "NotepadSave": [{"type": "type_text", "text": "ola mundo"},
                            {"type": "uia_click", "target": "Arquivo"},
                            {"type": "uia_click", "target": "Salvar"},
                            {"type": "type_text", "text": "nota.txt"},
                            {"type": "uia_click", "target": "Salvar"},
                            {"type": "done", "evidences": ["E1", "E2"]}],
            "ChromeUrl": [{"type": "uia_click", "target": "Nova guia"},
                          {"type": "type_text", "text": "https://example.org"},
                          {"type": "press_key", "key": "enter"},
                          {"type": "done", "evidences": ["E1", "E2"]}],
            "AmbiguousProfile": [{"type": "ask", "text": "Qual perfil?"},
                                 {"type": "visual_action",
                                  "instruction": "Click the Ana trabalho button"},
                                 {"type": "done", "evidences": ["E1"]}],
        }
        for name, actions in paths.items():
            with self.subTest(name):
                row = self.run_scenario(name, actions)
                self.assertTrue(row["passed"], row["errors"])
                self.assertEqual(row["wasted_actions"], 0)

    def test_navigating_in_the_current_tab_does_not_satisfy_a_new_tab_request(self):
        row = self.run_scenario("ChromeUrl", [
            {"type": "hotkey", "keys": "ctrl+l"}, {"type": "type_text", "text": "example.org"},
            {"type": "press_key", "key": "enter"},
            {"type": "done", "evidences": ["E1"]}])
        self.assertFalse(row["passed"])
        self.assertEqual(row["misdone"], 1)

    def test_clicking_an_item_that_is_not_on_screen_has_no_effect(self):
        row = self.run_scenario("NotepadSave", [{"type": "uia_click", "target": "Salvar"}])
        self.assertTrue(row["transcript"][0]["result"].startswith("no visible effect"))

    def test_prompt_is_the_production_one_and_hides_the_rules(self):
        sim = tb.NotepadSave()
        messages = tb.step_messages(sim, ["type_text(ola)"])
        self.assertEqual(messages[0]["content"], bench.PLANNER_SYSTEM)
        text = messages[1]["content"]
        self.assertIn("pendências", text)
        self.assertIn("type_text(ola)", text)
        self.assertNotIn("ev-arquivo", text)

    def test_runs_through_shared_runner_and_summarizes(self):
        with scripted_client(lambda n: SOLUTIONS[n]) as client:
            rows = bench.run_requests(client, "http://127.0.0.1/v1", "fake", tb.load_cases(), 1,
                                      SETTINGS, lambda _: None, tb.SUITE)
        self.assertEqual(sum(r["passed"] for r in rows), len(SOLUTIONS))
        extra = tb.extra_summary(rows)
        self.assertEqual(extra["Cenários concluídos"], f"{len(SOLUTIONS)}/{len(SOLUTIONS)}")
        self.assertGreater(rows[0]["tokens_per_s"], 0)


class TestNewToolsInSimulator(unittest.TestCase):
    def run_scenario(self, name, actions, features=()):
        with scripted_client(lambda n: actions if n == name else []) as client:
            return tb.make_run_case(features)(client, "http://127.0.0.1/v1", "fake",
                                              {"id": name}, SETTINGS)

    def test_fill_and_save_as_shorten_the_notepad_scenario(self):
        row = self.run_scenario("NotepadSave", [
            {"type": "fill", "target": "Editor", "text": "ola mundo"},
            {"type": "save_as", "text": "nota.txt"},
            {"type": "done", "evidences": ["E1", "E2"]}])
        self.assertTrue(row["passed"], row["errors"])
        self.assertEqual(row["steps"], 3)

    def test_save_as_with_another_name_does_not_satisfy_the_request(self):
        row = self.run_scenario("NotepadSave", [
            {"type": "fill", "target": "Editor", "text": "ola mundo"},
            {"type": "save_as", "text": "outro.txt"},
            {"type": "done", "evidences": ["E1"]}])
        self.assertFalse(row["passed"])
        self.assertIn("pedido era nota.txt", row["transcript"][1]["result"])

    def test_click_text_is_the_same_target_as_a_visual_click(self):
        row = self.run_scenario("CookieBanner", [
            {"type": "click_text", "text": "Aceitar todos"},
            {"type": "click_text", "text": "Comprar agora"},
            {"type": "done", "evidences": ["E1", "E2"]}])
        self.assertTrue(row["passed"], row["errors"])

    def test_fill_in_the_address_bar_and_wait_perception(self):
        row = self.run_scenario("ChromeUrl", [
            {"type": "uia_click", "target": "Nova guia"},
            {"type": "fill", "target": "Barra de endereço", "text": "example.org"},
            {"type": "press_key", "key": "enter"},
            {"type": "perceive", "perception": "wait:Example Domain"},
            {"type": "done", "evidences": ["E1", "E2"]}])
        self.assertTrue(row["passed"], row["errors"])
        self.assertIn("apareceu", row["transcript"][3]["result"])
        self.assertEqual(row["wasted_actions"], 0)

    def test_wait_for_something_that_never_shows_says_so(self):
        row = self.run_scenario("CalcSum", [{"type": "perceive", "perception": "wait:Concluído"}])
        self.assertIn("NÃO apareceu", row["transcript"][0]["result"])

    def test_done_must_cite_ids_not_internal_keys(self):
        row = self.run_scenario("ChromeUrl", [
            {"type": "hotkey", "keys": "ctrl+t"}, {"type": "type_text", "text": "example.org"},
            {"type": "press_key", "key": "enter"},
            {"type": "done", "evidences": ["ev-aba", "ev-pagina"]},
            {"type": "done", "evidences": ["E1", "E2"]}])
        self.assertTrue(row["passed"])
        self.assertEqual(row["misdone"], 1)

    def test_prompt_shows_confirmed_evidence_with_ids_and_human_pending_items(self):
        sim = tb.NotepadSave()
        text = tb.step_messages(sim, [])[1]["content"]
        self.assertIn("pendências: digitar o texto no editor; salvar o arquivo como nota.txt", text)
        sim.evidence["ev-texto"] = "texto digitado"
        text = tb.step_messages(sim, [])[1]["content"]
        self.assertIn("evidências confirmadas: E1=texto digitado", text)

    def test_features_change_system_prompt_recipes_and_schema(self):
        sim = tb.NotepadSave()
        base = tb.step_messages(sim, [], ())
        full = tb.step_messages(sim, [], ("fewshot", "recipes"))
        self.assertNotIn("App notes", base[1]["content"])
        self.assertIn("App notes", full[1]["content"])
        self.assertIn("Examples of the format only", full[0]["content"])
        payloads = []

        def handler(request):
            payloads.append(json.loads(request.content))
            return httpx.Response(200, json={"usage": {}, "choices": [
                {"finish_reason": "stop", "message": {"content": '{"type":"wait","ms":10}'}}]})

        with httpx.Client(transport=httpx.MockTransport(handler)) as client:
            tb.make_run_case(("dynschema",))(client, "http://x/v1", "fake",
                                             {"id": "NotepadSave"}, SETTINGS)
        schema = payloads[0]["response_format"]["json_schema"]["schema"]
        self.assertIn("anyOf", schema)
        self.assertEqual(sorted(schema["anyOf"][0]["properties"]["target"]["enum"]),
                         ["Arquivo", "Editor"])  # só o que está na tela, sem o tipo


if __name__ == "__main__":
    unittest.main()
