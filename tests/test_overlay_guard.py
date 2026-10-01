"""Guards contra o agente enxergar/focar o próprio overlay + prompt anti-cópia.

Evidência do run real (19/09): janela ativa 'tk' (default do Tk) → planner
emitiu focus("trecho do título") (copiado do exemplo do TOOLS_SPEC) e depois
focus("tk") = focou o próprio overlay → LOOP. Correções: título "crr-overlay"
ignorado em snapshot/focus; exemplos realistas + regra anti-cópia no prompt;
focus timeout 8s→3s; anti-repetição cita a ação repetida.

Offline: Desktop do pywinauto mockado; sem GUI/modelos.
"""
from __future__ import annotations

import inspect
import sys
import types
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import loop  # noqa: E402
import overlay  # noqa: E402
import planner  # noqa: E402
import tools  # noqa: E402
import uia  # noqa: E402


class _FakeRect:
    def __init__(self, left=0, top=0, right=800, bottom=600):
        self.left, self.top, self.right, self.bottom = left, top, right, bottom


class _FakeWin:
    def __init__(self, title, focus=False):
        self._title = title
        self._focus = focus
        self.focused = False

    def window_text(self):
        return self._title

    def has_focus(self):
        return self._focus

    def is_active(self):
        return False

    def has_keyboard_focus(self):
        return False

    def rectangle(self):
        return _FakeRect()

    @property
    def element_info(self):
        return types.SimpleNamespace(name=self._title, control_type="Window",
                                     rectangle=_FakeRect())

    def children(self):
        return []

    def set_focus(self):
        self.focused = True

    def restore(self):
        pass


def _desk_with(wins):
    class _FakeDesk:
        def __init__(self, *a, **k):
            pass

        def window(self, handle=None):
            raise RuntimeError("sem handle")

        def windows(self, top_level_only=True, visible_only=True):
            return wins

    return _FakeDesk


class TestOverlayTitle(unittest.TestCase):
    def test_helper(self):
        self.assertTrue(overlay.is_overlay_title("crr-overlay"))
        self.assertTrue(overlay.is_overlay_title("CRR-OVERLAY"))
        self.assertFalse(overlay.is_overlay_title("tk"))
        self.assertFalse(overlay.is_overlay_title(""))
        self.assertFalse(overlay.is_overlay_title("Google - Chrome"))


class TestSnapshotPulaOverlay(unittest.TestCase):
    def test_foreground_e_overlay_cai_no_app_real(self):
        wins = [_FakeWin(overlay.OVERLAY_TITLE, focus=True),
                _FakeWin("Windows PowerShell")]
        with patch("pywinauto.Desktop", _desk_with(wins), create=True):
            items, title, wrect = uia.active_window_snapshot()
        self.assertEqual(title, "Windows PowerShell")
        self.assertEqual(wrect, (0, 0, 800, 600))
        self.assertTrue(all(overlay.OVERLAY_TITLE not in (it["name"] or "")
                            for it in items))


class TestFocusPulaOverlay(unittest.TestCase):
    def test_nunca_foca_o_overlay(self):
        wins = [_FakeWin(overlay.OVERLAY_TITLE), _FakeWin("Google - Chrome")]
        with patch("pywinauto.Desktop", _desk_with(wins), create=True):
            self.assertFalse(tools.focus_window("crr-overlay", timeout=0.1))

    def test_app_real_ainda_foca(self):
        wins = [_FakeWin(overlay.OVERLAY_TITLE), _FakeWin("Google - Chrome")]
        with patch("pywinauto.Desktop", _desk_with(wins), create=True):
            self.assertTrue(tools.focus_window("google", timeout=2.0))
        self.assertTrue(wins[1].focused)
        self.assertFalse(wins[0].focused)

    def test_timeout_padrao_curto(self):
        sig = inspect.signature(tools.focus_window)
        self.assertLessEqual(sig.parameters["timeout"].default, 3.0)


class TestPromptAntiCopia(unittest.TestCase):
    def test_sem_placeholder_copiavel(self):
        blob = planner.TOOLS_SPEC + planner.PLANNER_SYSTEM
        self.assertNotIn("trecho do título", blob)

    def test_regra_anti_copia_e_open_vs_focus(self):
        self.assertIn("NEVER copy the example", planner.PLANNER_SYSTEM)
        self.assertIn("ONLY when a", planner.PLANNER_SYSTEM)
        self.assertIn("already missed", planner.PLANNER_SYSTEM)


class TestRepeatNote(unittest.TestCase):
    def test_cita_alvo(self):
        from schemas import Action

        note = loop._repeat_note(Action(type="focus", target="trecho X"))
        self.assertIn("focus(trecho X)", note)
        self.assertIn("DIFERENTE", note)

    def test_sem_arg_so_tipo(self):
        from schemas import Action

        note = loop._repeat_note(Action(type="wait", ms=300))
        self.assertIn("wait", note)


if __name__ == "__main__":
    unittest.main(verbosity=2)
