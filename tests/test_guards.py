"""Veto de ambiguidade pelo pedido: nunca escolher entre itens que o usuário não distinguiu."""
from __future__ import annotations

import unittest

from guards import goal_ambiguity as amb


class TestGoalAmbiguity(unittest.TestCase):
    PROFILES = ["Ana pessoal", "Ana trabalho"]

    def test_profiles_the_user_did_not_tell_apart_are_ambiguous(self):
        self.assertEqual(amb("Abra o perfil de Ana", "Ana pessoal", self.PROFILES),
                         ["Ana trabalho"])
        self.assertEqual(amb("Open Ana's profile", "Ana trabalho", self.PROFILES), ["Ana pessoal"])

    def test_full_name_in_the_request_is_not_ambiguous(self):
        self.assertEqual(amb("Abra o perfil de Ana trabalho", "Ana trabalho", self.PROFILES), [])
        self.assertEqual(amb("Abra Ana pessoal", "Ana pessoal", self.PROFILES), [])

    def test_unrelated_target_or_unmentioned_names_do_not_trigger(self):
        self.assertEqual(amb("Calcule 2 + 3", "Mais", ["1", "2", "3", "Mais", "Igual"]), [])
        self.assertEqual(amb("Aceite os cookies", "Aceitar todos",
                             ["Aceitar todos", "Rejeitar", "Comprar agora"]), [])
        self.assertEqual(amb("Abra uma nova aba", "Nova guia", ["Nova guia", "Página"]), [])

    def test_same_leading_word_in_different_files_is_flagged(self):
        names = ["Relatório 2023", "Relatório 2024"]
        self.assertEqual(amb("Abra o relatório", "Relatório 2024", names), ["Relatório 2023"])

    def test_single_candidate_or_identical_names_are_left_to_the_uia_resolver(self):
        self.assertEqual(amb("Abra o perfil de Ana", "Ana pessoal", ["Ana pessoal"]), [])
        self.assertEqual(amb("Abra o perfil de Ana", "Ana pessoal",
                             ["Ana pessoal", "Ana pessoal"]), [])

    def test_accents_case_and_stopwords_are_ignored(self):
        self.assertEqual(amb("ABRA O PERFIL DA ANA", "Ana Pessoal", ["Ana Pessoal", "ANA TRABALHO"]),
                         ["ANA TRABALHO"])


class TestGoalConflict(unittest.TestCase):
    PROFILES = ["Ana pessoal", "Ana trabalho"]

    def test_target_that_contradicts_the_clarified_request(self):
        from guards import goal_conflict
        goal = "Abra o perfil de Ana Ana trabalho"
        self.assertEqual(goal_conflict(goal, "Ana pessoal", self.PROFILES), ["Ana trabalho"])
        self.assertEqual(goal_conflict(goal, "Ana trabalho", self.PROFILES), [])

    def test_without_clarification_there_is_no_conflict_only_ambiguity(self):
        from guards import goal_conflict
        self.assertEqual(goal_conflict("Abra o perfil de Ana", "Ana pessoal", self.PROFILES), [])


if __name__ == "__main__":
    unittest.main()
