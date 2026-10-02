"""done cita IDs curtos (E1, E2…) das evidências confirmadas; o verificador resolve p/ o texto."""
from __future__ import annotations

import unittest

import state as statemod
import verification as vf


class TestEvidenceIds(unittest.TestCase):
    EVS = ["texto digitado: ola", "arquivo salvo: nota.txt", "janela Salvar fechada"]

    def test_compact_shows_stable_absolute_ids(self):
        s = statemod.init("g")
        for e in self.EVS:
            statemod.add_evidence(s, e)
        text = statemod.compact(s)
        self.assertIn("E1=texto digitado: ola", text)
        self.assertIn("E3=janela Salvar fechada", text)

    def test_ids_stay_absolute_when_only_the_last_five_are_shown(self):
        s = statemod.init("g")
        for i in range(1, 8):
            statemod.add_evidence(s, f"fato {i}")
        text = statemod.compact(s)
        self.assertIn("E3=fato 3", text)
        self.assertIn("E7=fato 7", text)
        self.assertNotIn("E2=", text)

    def test_resolves_ids_case_and_space_insensitively(self):
        for ref, want in (("E2", self.EVS[1]), ("e1", self.EVS[0]), (" E3 ", self.EVS[2])):
            self.assertEqual(vf.resolve_evidence_ref(ref, self.EVS), want)
        self.assertEqual(vf.resolve_evidence_ref("E9", self.EVS), "E9")  # fora do intervalo
        self.assertEqual(vf.resolve_evidence_ref("texto livre", self.EVS), "texto livre")

    def test_done_accepts_ids_and_exact_text_but_not_made_up_references(self):
        self.assertTrue(vf.done_evidence_ok(["E1", "E2"], self.EVS)[0])
        self.assertTrue(vf.done_evidence_ok([self.EVS[0]], self.EVS)[0])  # compat: texto exato
        ok, msg = vf.done_evidence_ok(["E9"], self.EVS)
        self.assertFalse(ok)
        self.assertIn("obsoleta/ausente", msg)
        self.assertFalse(vf.done_evidence_ok([], self.EVS)[0])
        self.assertFalse(vf.done_evidence_ok(["E1"], [])[0])  # sem evidência confirmada


class TestPlannerContract(unittest.TestCase):
    def test_prompt_teaches_done_with_evidence_ids(self):
        import planner

        self.assertIn('"evidences":["E1","E2"]', planner.PLANNER_SYSTEM)
        self.assertIn("evidence IDs", planner.PLANNER_SYSTEM)
        self.assertIn('"evidences":["E1"]', planner.build_system(("fewshot",)))

    def test_fewshot_only_shows_fill_when_the_tool_is_offered(self):
        import planner

        self.assertNotIn('"fill"', planner.build_system(("fewshot",)))
        both = planner.build_system(("fewshot", "tools"))
        self.assertIn('"fill"', both)
        self.assertIn("click_text", both)

    def test_baseline_has_no_optional_tools(self):
        import planner

        base = planner.build_system(())
        for name in ("click_text", "save_as", "wait:<texto>"):
            self.assertNotIn(name, base)

    @staticmethod
    def by_type(schema):
        return {v["properties"]["type"]["enum"][0]: v for v in schema["anyOf"]}

    def test_dynamic_schema_has_one_strict_variant_per_action(self):
        import planner

        schema = planner.planner_json_schema(names=["Button:Salvar", "Edit:Editor=oi"],
                                             evidence_ids=["E1"], apps=["chrome"],
                                             extra_types=False, allow_skill=False)
        by = self.by_type(schema)
        self.assertEqual(by["uia_click"]["properties"]["target"]["enum"], ["Editor", "Salvar"])
        self.assertEqual(by["done"]["properties"]["evidences"]["items"]["enum"], ["E1"])
        self.assertEqual(by["done"]["properties"]["evidences"]["minItems"], 1)
        self.assertEqual(by["open_app"]["properties"]["app"]["enum"], ["chrome"])
        self.assertIn("pattern", by["hotkey"]["properties"]["keys"])
        self.assertIn("enter", by["press_key"]["properties"]["key"]["enum"])
        for name in ("click_text", "fill", "save_as", "use_skill"):
            self.assertNotIn(name, by)
        for variant in schema["anyOf"]:  # nenhum campo fora dos da própria ação
            self.assertFalse(variant["additionalProperties"])
            self.assertIn("type", variant["required"])

    def test_done_disappears_without_confirmed_evidence_and_click_without_names(self):
        import planner

        by = self.by_type(planner.planner_json_schema(names=[], evidence_ids=[],
                                                      extra_types=False))
        self.assertNotIn("done", by)
        self.assertNotIn("uia_click", by)
        self.assertIn("visual_action", by)

    def test_optional_tools_and_skills_only_when_offered(self):
        import planner

        by = self.by_type(planner.planner_json_schema(names=["Edit:Page"], evidence_ids=["E1"],
                                                      extra_types=True, allow_skill=True))
        self.assertEqual(by["fill"]["required"], ["type", "target", "text"])
        self.assertEqual(by["fill"]["properties"]["target"]["enum"], ["Page"])
        for name in ("click_text", "save_as", "use_skill"):
            self.assertIn(name, by)

    def test_why_is_required_only_when_requested(self):
        import planner

        self.assertEqual(planner.planner_json_schema()["required"], ["type"])
        sch = planner.planner_json_schema(why=True)
        self.assertEqual(sch["required"], ["why", "type"])
        self.assertEqual(next(iter(sch["properties"])), "why")  # antes da ação
        typed = planner.planner_json_schema(why=True, names=["Button:Ok"], evidence_ids=["E1"])
        for variant in typed["anyOf"]:
            self.assertEqual(variant["required"][0], "why")
            self.assertEqual(next(iter(variant["properties"])), "why")

    def test_unknown_feature_fails_early(self):
        import planner

        with self.assertRaises(ValueError):
            planner.MiniCPMPlanner(features=("magia",))

    def test_recipes_follow_the_active_window_only(self):
        import recipes

        self.assertIn("ctrl+s", recipes.recipes_for("Sem título - Bloco de Notas"))
        self.assertIn("ctrl+t", recipes.recipes_for("Google - Google Chrome"))
        self.assertEqual(recipes.recipes_for("Planilha de custos"), "")
        self.assertLessEqual(len(recipes.recipes_for("Salvar como - Notepad").splitlines()), 2)


if __name__ == "__main__":
    unittest.main()


class TestRequirementsInState(unittest.TestCase):
    def test_requirements_appear_in_the_compact_summary_before_progress(self):
        s = statemod.init("Digite e salve")
        s.requirements = ["digitar o texto", "salvar como nota.txt"]
        statemod.add_evidence(s, "texto digitado")
        text = statemod.compact(s)
        self.assertIn("requisitos do pedido: (1) digitar o texto; (2) salvar como nota.txt", text)
        self.assertLess(text.index("requisitos"), text.index("evidências"))

    def test_without_requirements_the_summary_is_unchanged(self):
        self.assertNotIn("requisitos", statemod.compact(statemod.init("g")))
