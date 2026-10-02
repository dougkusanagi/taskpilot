"""Parser de grounding tolerante (formato/escala) e zoom em duas etapas."""
from __future__ import annotations

import unittest

from PIL import Image

import vocaela as v


class TestParsePointAny(unittest.TestCase):
    def pt(self, text, size=(1000, 500), coords="auto"):
        a = v.parse_point_any(text, size, coords)
        return a.x, a.y

    def test_unit_scale_and_common_formats(self):
        for text in ('{"x": 0.25, "y": 0.5}', '{"point_2d": [0.25, 0.5]}',
                     '{"coordinate": [0.25, 0.5]}', "<point>0.25, 0.5</point>",
                     "click(0.25, 0.5)", "pyautogui.click(x=0.25, y=0.5)", "[0.25, 0.5]",
                     '```json\n{"x": 0.25, "y": 0.5}\n```'):
            with self.subTest(text=text):
                self.assertEqual(self.pt(text), (0.25, 0.5))

    def test_thousand_and_pixel_scales_in_auto_mode(self):
        self.assertEqual(self.pt('{"x": 250, "y": 500}'), (0.25, 0.5))  # 0..1000
        self.assertEqual(self.pt('{"x": 1250, "y": 250}', size=(2000, 500)), (0.625, 0.5))  # px

    def test_explicit_modes_do_not_guess(self):
        with self.assertRaises(ValueError):
            v.parse_point_any('{"x": 250, "y": 500}', (1000, 500), "unit")
        self.assertEqual(self.pt('{"x": 250, "y": 250}', coords="pixel"), (0.25, 0.5))
        with self.assertRaises(ValueError):
            v.parse_point_any('{"x": 250, "y": 250}', None, "pixel")

    def test_absent_and_garbage_are_honest_errors(self):
        with self.assertRaisesRegex(ValueError, "não visível"):
            v.parse_point_any('{"x": null, "y": null}')
        with self.assertRaisesRegex(ValueError, "não visível"):
            v.parse_point_any("The element is not visible")
        with self.assertRaisesRegex(ValueError, "sem ponto"):
            v.parse_point_any("não sei")
        with self.assertRaises(ValueError):
            v.parse_point_any('{"x": 5000, "y": 9000}', (1000, 500))

    def test_unknown_mode_is_rejected(self):
        with self.assertRaises(ValueError):
            v.parse_point_any("[0.1, 0.1]", coords="metros")


class TestZoom(unittest.TestCase):
    def test_crop_is_centered_clamped_and_never_smaller_than_min(self):
        self.assertEqual(v.crop_around((1920, 1080), (0.5, 0.5), 0.35, 320), (624, 351, 1296, 729))
        x0, y0, x1, y1 = v.crop_around((1920, 1080), (0.0, 0.0), 0.1, 320)
        self.assertEqual((x0, y0, x1 - x0, y1 - y0), (0, 0, 320, 320))
        x0, y0, x1, y1 = v.crop_around((1920, 1080), (1.0, 1.0), 0.35, 320)
        self.assertEqual((x1, y1), (1920, 1080))
        self.assertEqual(v.crop_around((200, 100), (0.5, 0.5), 0.35, 320), (0, 0, 200, 100))

    def test_second_pass_refines_and_maps_back_to_full_image(self):
        image = Image.new("RGB", (2000, 1000))
        calls = []

        def ground(img, instruction):
            calls.append(img.size)
            # 1ª: ponto grosso em (0.50, 0.50); 2ª (recorte): centro do recorte
            return '{"x": 0.5, "y": 0.5}'

        va, meta = v.zoom_ground(ground, image, "Click OK", frac=0.4, coords="unit")
        self.assertEqual(calls, [(2000, 1000), (800, 400)])
        self.assertEqual((va.x, va.y), (0.5, 0.5))
        self.assertEqual(meta["zoom_status"], "ok")

    def test_refinement_moves_the_point_inside_the_crop(self):
        image = Image.new("RGB", (2000, 1000))
        answers = iter(['{"x": 0.5, "y": 0.5}', '{"x": 0.75, "y": 0.25}'])
        va, _ = v.zoom_ground(lambda *_: next(answers), image, "Click OK", 0.4, "unit")
        # recorte 800x400 centrado em (1000,500): x0=600,y0=300 -> 600+0.75*800=1200, 300+0.25*400=400
        self.assertEqual((va.x, va.y), (0.6, 0.4))

    def test_unconfirmed_zoom_keeps_first_or_rejects_on_request(self):
        image = Image.new("RGB", (2000, 1000))

        def ground(img, instruction):
            if img.size == image.size:
                return '{"x": 0.3, "y": 0.3}'
            return '{"x": null, "y": null}'

        va, meta = v.zoom_ground(ground, image, "Click OK", coords="unit")
        self.assertEqual((va.x, va.y, meta["zoom_status"]), (0.3, 0.3, "unconfirmed"))
        with self.assertRaisesRegex(ValueError, "não confirmado"):
            v.zoom_ground(ground, image, "Click OK", coords="unit", unconfirmed="reject")

    def test_absent_target_in_first_pass_propagates(self):
        with self.assertRaisesRegex(ValueError, "não visível"):
            v.zoom_ground(lambda *_: '{"x": null, "y": null}', Image.new("RGB", (400, 400)), "x")


class TestAdapterConfig(unittest.TestCase):
    def test_protocol_is_derived_from_the_model_family(self):
        for model, want in (("Qwen3-VL-2B-Instruct", "p2d"), ("qwen3-vl-4b-instruct", "p2d"),
                            ("MAI-UI-2B", "pyauto"), ("gui-owl-1.5-2b-instruct", "pyauto"),
                            ("Qwen3.5-4B", "json"), ("Vocaela-2", "json")):
            self.assertEqual(v.default_protocol(model), want, model)
        self.assertEqual(v.QwenGroundingAdapter().protocol, "p2d")  # modelo padrão: Qwen3-VL-2B

    def test_scale_follows_the_protocol_and_can_be_forced_back(self):
        self.assertEqual(v.QwenGroundingAdapter(model="Qwen3.5-4B").coords, "unit")
        # p2d e pyauto: os modelos respondem em 0..1000 (medido), não adivinhar em "auto"
        self.assertEqual(v.QwenGroundingAdapter(model="MAI-UI-2B").coords, "1000")
        self.assertEqual(v.QwenGroundingAdapter(model="Qwen3-VL-2B").coords, "1000")
        old = v.QwenGroundingAdapter(protocol="json", coords="unit")  # volta ao histórico
        self.assertEqual((old.protocol, old.coords, old.zoom), ("json", "unit", False))

    def test_invalid_choices_fail_early(self):
        with self.assertRaises(ValueError):
            v.QwenGroundingAdapter(coords="foo")
        with self.assertRaises(ValueError):
            v.QwenGroundingAdapter(protocol="xml")

    def test_families(self):
        for name in ("Qwen3-VL-2B", "qwen3.5-4b", "GUI-Owl-1.5", "mai-ui-2b"):
            self.assertTrue(v.is_grounding_family(name), name)
        for name in ("Vocaela-2-500M", "MiniCPM5-2B"):
            self.assertFalse(v.is_grounding_family(name), name)

    def test_each_protocol_builds_its_own_prompt_and_parses_its_own_answer(self):
        import base64
        import io
        import json as _json

        sent = []

        def fake_post(base, path, payload, timeout, retries=0):
            sent.append(payload)
            answers = {"json": '{"x": 0.5, "y": 0.25}', "p2d": '{"point_2d": [500, 250]}',
                       "pyauto": "click(500, 250)"}
            return ({"choices": [{"message": {"content": answers[self.proto]}}]}, 1.0)

        import http_pool
        original = http_pool.post_json
        http_pool.post_json = fake_post
        buf = io.BytesIO()
        Image.new("RGB", (1024, 512)).save(buf, format="JPEG")
        image = Image.open(io.BytesIO(buf.getvalue()))
        try:
            for self.proto in v.PROTOCOLS:
                adapter = v.QwenGroundingAdapter(model="x-vl", protocol=self.proto)
                va, _ms = adapter.act_sync(image, "Click OK")
                self.assertEqual((va.x, va.y), (0.5, 0.25), self.proto)
                system = sent[-1]["messages"][0]["content"]
                self.assertEqual(system, v.GROUNDING_PROMPTS[self.proto][0])
        finally:
            http_pool.post_json = original
        del base64, _json


if __name__ == "__main__":
    unittest.main()
