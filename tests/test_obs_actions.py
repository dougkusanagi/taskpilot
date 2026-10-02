"""obs/actions/tools offline: multi-monitor, Unicode no type, whitelist.

Roda com: uv run python -m unittest discover -s tests -v
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from PIL import Image  # noqa: E402

import obs  # noqa: E402


class TestCropVirtualDesktop(unittest.TestCase):
    """Desktop virtual 3840x1080 com monitor secundário à ESQUERDA (x=-1920)."""

    FULL = Image.new("RGB", (3840, 1080), "white")
    VORIGIN = (-1920, 0)

    def test_janela_no_monitor_negativo(self):
        # janela em x=-1800..-800 (monitor da esquerda) -> crop nos pixels 120..1120
        crop, origin, full = obs.crop_to_rect(self.FULL, self.VORIGIN,
                                              (-1800, 100, -800, 600))
        self.assertEqual(crop.size, (1000, 500))
        self.assertEqual(origin, (-1800, 100))  # coords de tela, negativas
        self.assertEqual(full, (3840, 1080))

    def test_janela_no_primario(self):
        crop, origin, _ = obs.crop_to_rect(self.FULL, self.VORIGIN,
                                           (100, 100, 700, 500))
        self.assertEqual(crop.size, (600, 400))
        self.assertEqual(origin, (100, 100))

    def test_janela_parcialmente_fora_e_clamp(self):
        crop, origin, _ = obs.crop_to_rect(self.FULL, self.VORIGIN,
                                           (-2000, -50, -1500, 300))
        self.assertEqual(crop.size, (420, 300))  # cortado em x=-1920,y=0
        self.assertEqual(origin, (-1920, 0))

    def test_sem_rect_ou_minusculo_devolve_tudo(self):
        for rect in (None, (0, 0, 20, 20)):
            crop, origin, _ = obs.crop_to_rect(self.FULL, self.VORIGIN, rect)
            self.assertEqual(crop.size, (3840, 1080))
            self.assertEqual(origin, (-1920, 0))

    def test_roundtrip_com_visual_to_action(self):
        # Vocaela devolve 0.5,0.5 no crop -> pixel físico no monitor negativo
        from vocaela import VisualAction, visual_to_action

        crop, origin, _ = obs.crop_to_rect(self.FULL, self.VORIGIN,
                                           (-1800, 100, -800, 600))
        act = visual_to_action(VisualAction(type="click", x=0.5, y=0.5),
                               crop.size, origin)
        self.assertEqual((act.x, act.y), (-1300, 350))


class _FakeAuto:
    """pyautogui falso: grava chamadas."""
    FAILSAFE = True
    PAUSE = 0

    def __init__(self):
        self.calls: list[tuple] = []

    def __getattr__(self, name):
        def rec(*a, **k):
            self.calls.append((name, a, k))
        return rec


class TestExecute(unittest.TestCase):
    def setUp(self):
        import actions

        self.actions = actions
        self.fake = _FakeAuto()
        self._orig = (actions.pyautogui, actions._check_coords, actions._type_unicode)
        actions.pyautogui = self.fake
        actions._check_coords = lambda x, y: None
        self.typed: list[str] = []
        actions._type_unicode = self.typed.append

    def tearDown(self):
        (self.actions.pyautogui, self.actions._check_coords,
         self.actions._type_unicode) = self._orig

    def test_type_unicode_inteiro(self):
        from schemas import Action

        self.actions.execute(Action(type="type", text="Olá, não é ASCII — 100%"))
        self.assertEqual(self.typed, ["Olá, não é ASCII — 100%"])

    def test_escape_send_keys(self):
        self.assertEqual(self.actions.escape_for_send_keys("a+b^c%d~e(f){g}"),
                         "a{+}b{^}c{%}d{~}e{(}f{)}{{}g{}}")
        self.assertEqual(self.actions.escape_for_send_keys("Olá mundo"), "Olá mundo")

    def test_hotkey_presses(self):
        from schemas import Action

        d = self.actions.execute(Action(type="hotkey", key="down", presses=3))
        self.assertEqual([c[0] for c in self.fake.calls], ["hotkey"] * 3)
        self.assertEqual(d, "hotkey(down)x3")

    def test_middle_click_e_hscroll(self):
        from schemas import Action

        self.actions.execute(Action(type="middle_click", x=10, y=10))
        self.actions.execute(Action(type="scroll", text="-800", key="left"))
        self.actions.execute(Action(type="scroll", text="800", key="up"))
        names = [c[0] for c in self.fake.calls]
        self.assertIn("middleClick", names)
        self.assertIn("hscroll", names)
        self.assertIn("scroll", names)

    def test_answer_e_noop(self):
        from schemas import Action

        d = self.actions.execute(Action(type="answer", text="15"))
        self.assertEqual(d, "answer(15)")
        self.assertEqual(self.fake.calls, [])

    def test_sem_xy_levanta_valueerror(self):
        from schemas import Action

        with self.assertRaises(ValueError):
            self.actions.execute(Action(type="click"))
        with self.assertRaises(ValueError):
            self.actions.execute(Action(type="type", text=""))


class TestToolsWhitelist(unittest.TestCase):
    def setUp(self):
        import os

        import tools

        self.tools = tools
        self.opened: list[str] = []
        self._orig = (getattr(os, "startfile", None), tools.time.sleep, tools.focus_window)
        os.startfile = lambda exe, **kw: self.opened.append(exe)
        tools.time.sleep = lambda s: None
        tools.focus_window = lambda hint, timeout=2.0: True  # open foca; sem GUI

    def tearDown(self):
        import os

        startfile, self.tools.time.sleep, self.tools.focus_window = self._orig
        if startfile is None:  # Linux: startfile só existia por causa do teste
            del os.startfile
        else:
            os.startfile = startfile

    def test_whitelist_aceita_alias(self):
        for t in ("notepad.exe", "Bloco de Notas", "calc", "edge"):
            self.tools.open_app(t)
        self.assertEqual(self.opened, ["notepad", "notepad", "calc", "msedge"])

    def test_launch_fallback_popen_em_lista(self):
        import os
        import subprocess

        def boom(_, **kw):
            raise OSError("sem App Paths")
        os.startfile = boom
        calls: list = []
        orig = subprocess.Popen
        subprocess.Popen = lambda args, **k: calls.append(args)
        try:
            self.tools.open_app("msedge")
        finally:
            subprocess.Popen = orig
        # lista, nunca shell=True; navegador novo sobe com a flag de acessibilidade da página
        self.assertEqual(calls, [["msedge", "--force-renderer-accessibility"]])

    def test_navegador_recebe_flag_de_acessibilidade_e_apps_comuns_nao(self):
        calls = []
        import os
        os.startfile = lambda exe, **kw: calls.append((exe, kw))
        self.tools.open_app("chrome")
        self.tools.open_app("notepad")
        self.assertEqual(calls[0], ("chrome", {"arguments": "--force-renderer-accessibility"}))
        self.assertEqual(calls[1], ("notepad", {}))

    def test_flag_do_navegador_e_configuravel_e_pode_ser_desligada(self):
        original = self.tools.BROWSER_ARGS
        self.addCleanup(lambda: setattr(self.tools, "BROWSER_ARGS", original))
        self.tools.configure({"launch": {"browser_args": []}})
        self.assertEqual(self.tools.launch_args("chrome"), ())
        self.tools.configure({"launch": {"browser_args": ["--x", "--y"]}})
        self.assertEqual(self.tools.launch_args("msedge"), ("--x", "--y"))
        self.assertEqual(self.tools.launch_args("calc"), ())
        self.tools.configure({})  # sem seção launch: mantém o que já estava
        self.assertEqual(self.tools.launch_args("brave"), ("--x", "--y"))

    def test_fora_da_whitelist_recusa(self):
        for bad in ("powershell", "notepad & del x", 'cmd /c start "" x'):
            with self.assertRaises(ValueError):
                self.tools.open_app(bad)
        self.assertEqual(self.opened, [])

    def test_sem_teleporte_para_url(self):
        # open_url foi removido: navegar é pela UI do navegador, como um humano.
        self.assertFalse(hasattr(self.tools, "open_url"))
        with self.assertRaises(ValueError):
            self.tools.open_app("https://example.com/produto-x")

    def test_open_relata_janela_ativa(self):
        orig = self.tools.focus_window
        seen = {}
        self.tools.focus_window = lambda hint, timeout: seen.update(hint=hint, timeout=timeout) or True
        try:
            d = self.tools.open_app("chrome")
        finally:
            self.tools.focus_window = orig
        self.assertIn("(janela ativa)", d)
        self.assertEqual(seen["hint"], "chrome")
        self.assertGreaterEqual(seen["timeout"], 10)  # cold start: espera de verdade

    def test_open_relata_sem_primeiro_plano(self):
        orig = self.tools.focus_window
        self.tools.focus_window = lambda hint, timeout: False
        try:
            d = self.tools.open_app("chrome")
        finally:
            self.tools.focus_window = orig
        self.assertIn("ainda não em primeiro plano", d)


class TestVocaelaSemantica(unittest.TestCase):
    def test_presses_middle_hscroll_answer(self):
        import vocaela

        va = vocaela.parse_vocaela_output(
            '<Action>[{"action": "PRESS_KEY", "key": "down", "presses": 3}]</Action>')
        self.assertEqual((va.type, va.key, va.presses), ("key", "down", 3))
        act = vocaela.visual_to_action(va, (100, 100))
        self.assertEqual((act.type, act.presses), ("hotkey", 3))

        va = vocaela.parse_vocaela_output(
            '<Action>[{"action": "MIDDLE_CLICK", "coordinate": [0.5, 0.5]}]</Action>')
        self.assertEqual(vocaela.visual_to_action(va, (100, 100)).type, "middle_click")

        va = vocaela.parse_vocaela_output(
            '<Action>[{"action": "SCROLL", "scroll_direction": "left"}]</Action>')
        act = vocaela.visual_to_action(va, (100, 100))
        self.assertEqual((act.type, act.key), ("scroll", "left"))

        va = vocaela.parse_vocaela_output(
            '<Action>[{"action": "ANSWER", "text": "15"}]</Action>')
        act = vocaela.visual_to_action(va, (100, 100))
        self.assertEqual((act.type, act.text), ("answer", "15"))

    def test_multiplas_conta_descartadas(self):
        import vocaela

        va = vocaela.parse_vocaela_output(
            '<Action>[{"action": "click", "coordinate": [0.2, 0.3]}, '
            '{"action": "type", "text": "oi"}]</Action>')
        self.assertEqual((va.type, va.dropped), ("click", 1))
        with self.assertRaises(ValueError):
            vocaela.parse_vocaela_output("<Action>[]</Action>")


class TestSafetyHotkey(unittest.TestCase):
    def test_hotkey_vem_do_config_e_sem_esc_puro(self):
        import safety

        src = (ROOT / "safety.py").read_text(encoding="utf-8")
        self.assertNotIn('is_pressed("esc")', src)
        # sem subir thread/hook real: só a normalização
        self.assertEqual(safety.normalize_hotkey(" Ctrl+Shift+Q "), "ctrl+shift+q")
        self.assertEqual(safety.normalize_hotkey(None), "ctrl+alt+esc")
        self.assertEqual(safety.normalize_hotkey(""), "ctrl+alt+esc")


if __name__ == "__main__":
    unittest.main(verbosity=2)
