"""Partes puras do harness isolado do Ubuntu (sem sessão, sem portal, sem input)."""
import unittest

from evals import ubuntu_isolated as u

LOG = """aguardando aprovação no diálogo do GNOME (compartilhar tela + controle)...
[2] PLANNER visual: Click the plus button 1041ms
[6] VERIFY  window 'A' -> 'Novo documento'
[8] PLANNER type("Olá, Ubuntu!") [viu: 'Novo documento'] 600ms
[9] VERIFY  text visible in focused field
{
  "result": "done",
  "steps": 3,
  "planner_calls": 3,
  "total_s": 20.1
}
"""


class HarnessTests(unittest.TestCase):
    def test_parse_resultado_passos_e_decisoes(self):
        r = u.parse_agent_log(LOG)
        self.assertEqual((r["result"], r["steps"]), ("done", 3))
        self.assertEqual(r["decisions"], ["visual: Click the plus button", 'type("Olá, Ubuntu!")'])
        self.assertIn("Novo documento", r["first_verify"])
        self.assertIn("text visible", r["last_verify"])

    def test_log_sem_resumo_e_crash_sao_distintos(self):
        self.assertEqual(u.parse_agent_log("Traceback (most recent call last):")["result"], "crash")
        self.assertEqual(u.parse_agent_log("")["result"], "sem_resumo")

    def test_texto_exato_exigido(self):
        self.assertTrue(u.text_ok(["", "Olá, Ubuntu!\n"]))
        self.assertFalse(u.text_ok(["Oál, Ubuntu!", "Olá, Ubuntu!!"]))
        self.assertFalse(u.text_ok([]))

    def test_tabela_tem_uma_linha_por_run(self):
        md = u.markdown_table([dict(n=1, result="done", steps=3, text_ok=True, tabs=2,
                                    decisions=["a", "b"], cause="")])
        self.assertIn("| 1 | done | 3 | sim | 2 |", md)

    def test_config_do_agente_mantem_b1_e_sem_runtime_automatico(self):
        cfg = u.agent_config(["p", "v", "d"], ["why"], 768, 6)
        self.assertEqual(cfg["profile"], "B1")
        self.assertFalse(cfg["runtime"]["auto_start"])
        self.assertEqual(cfg["vision"]["max_tokens"], 768)
        self.assertEqual(cfg["platform"]["backend"], "wayland")


if __name__ == "__main__":
    unittest.main()
