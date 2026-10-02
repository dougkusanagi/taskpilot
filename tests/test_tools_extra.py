"""click_text, fill, save_as e perceive wait: decisões do loop, guardas e busca de texto OCR.

Offline: UIA/captura/OCR simulados, nenhum input físico.
"""
from __future__ import annotations

import unittest

from PIL import Image

import loop
import ocr
from planner import PlannerDecision
from schemas import Action

CFG = {"screenshot_max_width": 1024}


def words(*items):
    return {"ok": True, "backend": "fake", "ms": 1.0, "words": [
        {"text": t, "box": b, "conf": 1.0} for t, b in items]}


class FakePlanner:
    def __init__(self, dec):
        self.dec = dec

    def next_action(self, goal, window, ui_names, history, last_error="", **kw):
        return self.dec, 1.0


class Boom:
    def act_sync(self, *a, **k):
        raise AssertionError("visão não deveria ser chamada")


class TestFindText(unittest.TestCase):
    W = [{"text": "Comprar agora", "box": [59, 211, 172, 235], "conf": 1.0},
         {"text": "Veja a política de privacidade.", "box": [100, 300, 400, 320], "conf": .9},
         {"text": "Salvar", "box": [10, 10, 60, 30], "conf": .99},
         {"text": "Salvar como", "box": [10, 50, 110, 70], "conf": .99},
         {"text": "Editar", "box": [500, 100, 560, 120], "conf": 1},
         {"text": "Editar", "box": [500, 200, 560, 220], "conf": 1},
         {"text": "ruído", "box": [0, 0, 5, 5], "conf": .1}]

    def test_ignores_accents_and_case_and_cuts_the_box_for_a_phrase(self):
        r = ocr.find_text(self.W, "POLITICA de privacidade")
        self.assertEqual(r["status"], "ok")
        self.assertGreater(r["center"][0], 200)  # trecho no meio/fim da linha
        self.assertEqual(ocr.find_text(self.W, "comprar agora")["center"], [116, 223])

    def test_exact_line_beats_prefix_match(self):
        self.assertEqual(ocr.find_text(self.W, "Salvar")["center"], [35, 20])

    def test_two_places_is_ambiguous_and_never_chosen(self):
        r = ocr.find_text(self.W, "Editar")
        self.assertEqual(r["status"], "ambiguous")
        self.assertEqual(len(r["matches"]), 2)

    def test_missing_empty_and_low_confidence(self):
        self.assertEqual(ocr.find_text(self.W, "Pagar")["status"], "miss")
        self.assertEqual(ocr.find_text(self.W, "")["status"], "miss")
        self.assertEqual(ocr.find_text(self.W, "ruido")["status"], "miss")

    def test_same_spot_read_twice_is_not_ambiguity(self):
        dup = [{"text": "OK", "box": [10, 10, 40, 30], "conf": 1},
               {"text": "OK", "box": [11, 11, 41, 31], "conf": 1}]
        self.assertEqual(ocr.find_text(dup, "ok")["status"], "ok")

    def test_label_wrapped_in_two_lines_is_found_as_one_target(self):
        wrapped = [{"text": "Aceitar", "box": [100, 100, 160, 120], "conf": 1.0},
                   {"text": "todos", "box": [105, 122, 155, 142], "conf": 1.0},
                   {"text": "Rejeitar", "box": [300, 100, 370, 120], "conf": 1.0}]
        r = ocr.find_text(wrapped, "aceitar todos")
        self.assertEqual(r["status"], "ok")
        self.assertEqual(r["box"], [100, 100, 160, 142])
        self.assertEqual(ocr.find_text(wrapped, "aceitar")["status"], "ok")
        far = [{"text": "Aceitar", "box": [100, 100, 160, 120], "conf": 1.0},
               {"text": "todos", "box": [100, 300, 160, 320], "conf": 1.0}]
        self.assertEqual(ocr.find_text(far, "aceitar todos")["status"], "miss")

    def test_backend_less_environment_is_honest(self):
        original = ocr.backend
        ocr.backend = lambda: "unavailable"
        try:
            r = ocr.read_words(Image.new("RGB", (10, 10)))
        finally:
            ocr.backend = original
        self.assertFalse(r["ok"])
        self.assertEqual(r["words"], [])


class TestClickText(unittest.TestCase):
    def test_maps_the_ocr_center_to_physical_coordinates(self):
        img = Image.new("RGB", (400, 200))
        dec = PlannerDecision(type="click_text", text="Comprar agora")
        tm = {}
        out = loop._click_text_decision(
            dec, img, (1000, 500), words(("Comprar agora", [100, 50, 200, 90])), tm)
        self.assertEqual(out.source, "ocr")
        self.assertEqual((out.action.type, out.action.x, out.action.y), ("click", 1150, 570))
        self.assertEqual(tm["ocr"]["backend"], "fake")

    def test_ambiguous_missing_and_unavailable_raise_honest_errors(self):
        img = Image.new("RGB", (400, 200))
        dec = PlannerDecision(type="click_text", text="Editar")
        two = words(("Editar", [10, 10, 50, 30]), ("Editar", [10, 100, 50, 120]))
        with self.assertRaisesRegex(RuntimeError, "ambíguo"):
            loop._click_text_decision(dec, img, (0, 0), two, {})
        with self.assertRaisesRegex(RuntimeError, "não está legível"):
            loop._click_text_decision(dec, img, (0, 0), words(("Outro", [1, 1, 9, 9])), {})
        with self.assertRaisesRegex(RuntimeError, "OCR indisponível"):
            loop._click_text_decision(dec, img, (0, 0), {"ok": False, "reason": "sem backend"}, {})
        with self.assertRaisesRegex(RuntimeError, "precisa de text"):
            loop._click_text_decision(PlannerDecision(type="click_text"), img, (0, 0), two, {})

    def test_stale_frame_guard_covers_ocr_clicks(self):
        dec = loop.Decision(action=Action(type="click", x=1, y=1), source="ocr")
        original = loop._foreground_title
        loop._foreground_title = lambda: "Outra janela"
        try:
            note = loop._visual_stale_note(dec, {"visual_title": "Loja"})
        finally:
            loop._foreground_title = original
        self.assertIn("obsoleto", note)


class TestDecideTools(unittest.TestCase):
    def setUp(self):
        self._orig = loop.active_window_snapshot
        self.addCleanup(lambda: setattr(loop, "active_window_snapshot", self._orig))

    def snapshot(self, items, title="Formulário - Chrome"):
        loop.active_window_snapshot = lambda: (items, title, None)

    def decide(self, dec, items=(), title="Formulário - Chrome"):
        self.snapshot(list(items), title)
        return loop.decide("preencha o formulário", 3, {"hist_labels": []}, CFG,
                           planner=FakePlanner(dec), vocaela=Boom())

    FIELD = {"id": 0, "name": "Nome", "type": "Edit", "bounds": [10, 10, 110, 40], "context": ""}

    def test_fill_expands_to_click_select_all_type_with_focus_guard(self):
        dec, _ = self.decide(PlannerDecision(type="fill", target="Nome", text="Ana"), [self.FIELD])
        self.assertEqual(dec.kind, "sequence")
        self.assertEqual([s.type for s in dec.steps], ["click", "hotkey", "type"])
        self.assertEqual((dec.steps[0].x, dec.steps[0].y), (60, 25))
        self.assertEqual(dec.steps[1].key, "ctrl+a")
        self.assertEqual(dec.steps[2].text, "Ana")
        self.assertEqual(dec.sequence_guard, "fill_focus")

    def test_fill_refuses_unknown_field_and_empty_text(self):
        with self.assertRaisesRegex(RuntimeError, "não está na árvore"):
            self.decide(PlannerDecision(type="fill", target="Sobrenome", text="x"), [self.FIELD])
        with self.assertRaisesRegex(RuntimeError, "precisa de text"):
            self.decide(PlannerDecision(type="fill", target="Nome", text=""), [self.FIELD])

    def test_save_as_builds_ctrl_s_wait_type_enter_with_dialog_guard(self):
        dec, _ = self.decide(PlannerDecision(type="save_as", text="nota.txt"), title="Bloco")
        self.assertEqual([s.type for s in dec.steps], ["hotkey", "wait", "type", "hotkey"])
        self.assertEqual((dec.steps[0].key, dec.steps[2].text, dec.steps[3].key),
                         ("ctrl+s", "nota.txt", "enter"))
        self.assertEqual(dec.sequence_guard, "save_dialog")

    def test_save_as_rejects_paths_and_reserved_characters(self):
        for bad in ("", "..", "a/b.txt", "C:\\x.txt", "x?.txt", "a" * 101):
            with self.subTest(bad=bad), self.assertRaisesRegex(RuntimeError, "inválido"):
                self.decide(PlannerDecision(type="save_as", text=bad))

    def test_click_text_goes_through_ocr_not_vision(self):
        self.snapshot([])
        orig = (loop.capture_for_vision, ocr.read_words)
        loop.capture_for_vision = lambda max_long_edge=1024: (
            Image.new("RGB", (200, 100)), (50, 60), (1920, 1080))
        ocr.read_words = lambda img, lang="pt": words(("Comprar agora", [20, 10, 120, 30]))
        self.addCleanup(lambda: (setattr(loop, "capture_for_vision", orig[0]),
                                 setattr(ocr, "read_words", orig[1])))
        dec, tm = loop.decide("compre", 3, {"hist_labels": []}, CFG,
                              planner=FakePlanner(PlannerDecision(type="click_text",
                                                                  text="Comprar agora")),
                              vocaela=Boom())
        self.assertEqual((dec.source, dec.action.x, dec.action.y), ("ocr", 120, 80))
        self.assertGreater(tm["screenshot_ms"], -1)


class TestSequenceGuards(unittest.TestCase):
    def guard(self, name, prim, title="Documento", expected="Documento", focus=""):
        return loop._sequence_step_guard(name, prim, expected, lambda: title, lambda: focus)

    def test_save_dialog_only_types_when_the_dialog_opened(self):
        type_ = Action(type="type", text="nota.txt")
        self.assertIn("não abriu", self.guard("save_dialog", type_, title="Documento - Bloco"))
        self.assertEqual(self.guard("save_dialog", type_, title="Salvar como"), "")
        self.assertEqual(self.guard("save_dialog", type_, title="Save As"), "")
        self.assertEqual(self.guard("save_dialog", Action(type="wait", ms=900),
                                    title="Documento"), "")

    def test_fill_only_selects_all_when_an_editable_field_has_focus(self):
        select = Action(type="hotkey", key="ctrl+a")
        self.assertIn("nada foi selecionado", self.guard("fill_focus", select, focus="Button"))
        self.assertIn("desconhecido", self.guard("fill_focus", select, focus=""))
        for ok in ("Edit", "Document", "ComboBox"):
            self.assertEqual(self.guard("fill_focus", select, focus=ok), "")
        self.assertEqual(self.guard("fill_focus", Action(type="type", text="x"), focus="Button"), "")

    def test_default_guard_still_stops_type_after_focus_change(self):
        type_ = Action(type="type", text="x")
        self.assertIn("foco mudou", self.guard("", type_, title="Modal", expected="Documento"))
        self.assertEqual(self.guard("", type_, title="Documento", expected="Documento"), "")


class TestPerceiveWait(unittest.TestCase):
    def run_wait(self, shown_after, wait_s=2.0):
        calls = {"n": 0}
        clock = {"t": 0.0}

        def snap():
            calls["n"] += 1
            names = [{"name": "Salvo com sucesso", "type": "Text", "bounds": [0, 0, 9, 9]}] \
                if calls["n"] > shown_after else []
            return names, "Bloco", None

        def sleep(s):
            clock["t"] += s

        tm = {}
        facts = loop._run_perception("wait:salvo com SUCESSO", {}, {"perception": {"wait_s": wait_s}},
                                     tm, snapshot_fn=snap, sleep_fn=sleep,
                                     clock_fn=lambda: clock["t"])
        return facts, calls["n"]

    def test_reports_when_the_text_appears(self):
        facts, polls = self.run_wait(shown_after=2)
        self.assertIn("apareceu", facts)
        self.assertEqual(polls, 3)

    def test_reports_timeout_and_tells_the_model_not_to_repeat(self):
        facts, _ = self.run_wait(shown_after=99)
        self.assertIn("NÃO apareceu", facts)
        self.assertIn("não repita wait", facts)

    def test_spec_parsing(self):
        self.assertEqual(loop._parse_perception("wait:Concluído"), ("wait", "Concluído"))
        with self.assertRaises(RuntimeError):
            loop._parse_perception("wait:")


if __name__ == "__main__":
    unittest.main()
