"""Prefixo de tipo no alvo (`Button:Salvar`) e escalada opcional de raciocínio do planner."""
from __future__ import annotations

import json
import unittest
from unittest.mock import patch

import planner
from planner import strip_type_prefix


class TestStripTypePrefix(unittest.TestCase):
    def test_strips_known_type_only(self):
        for raw, want in (("Button:Salvar", "Salvar"), ("button: Salvar", "Salvar"),
                          ("Edit:Total=R$ 1,00", "Total=R$ 1,00"),
                          ("ListItem:Ana trabalho", "Ana trabalho")):
            self.assertEqual(strip_type_prefix(raw), want)

    def test_other_text_is_unchanged(self):
        for raw in ("Salvar", "Total: 10", "Resumo: Salvar tudo", "Button:", ":Salvar", "",
                    "Nota: texto"):
            self.assertEqual(strip_type_prefix(raw), raw.strip())

    def test_none_is_safe(self):
        self.assertEqual(strip_type_prefix(None), "")


class TestThinkEscalation(unittest.TestCase):
    def payload_for(self, think):
        sent = {}

        def post_json(base, path, payload, timeout, retries=0):
            sent.update(payload)
            return ({"choices": [{"message": {"content": '{"type":"wait","ms":10}'}}]}, 1.0)

        with patch("http_pool.post_json", post_json):
            planner.MiniCPMPlanner().next_action("g", "w", [], [], think=think)
        return sent

    def test_default_keeps_thinking_off_and_short_budget(self):
        sent = self.payload_for(False)
        self.assertFalse(sent["chat_template_kwargs"]["enable_thinking"])
        self.assertEqual(sent["max_tokens"], 256)

    def test_escalation_enables_thinking_with_bigger_budget(self):
        sent = self.payload_for(True)
        self.assertTrue(sent["chat_template_kwargs"]["enable_thinking"])
        self.assertEqual(sent["max_tokens"], planner.THINK_MAX_TOKENS)
        json.dumps(sent)  # serializável

    def test_config_default_is_opt_in(self):
        import config
        self.assertFalse(config.DEFAULTS["planner"]["escalate_thinking"])


if __name__ == "__main__":
    unittest.main()
