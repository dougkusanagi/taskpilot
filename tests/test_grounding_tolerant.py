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
    def test_defaults_keep_historical_behaviour(self):
        a = v.QwenGroundingAdapter()
        self.assertEqual((a.zoom, a.coords), (False, "unit"))

    def test_invalid_coords_mode_fails_early(self):
        with self.assertRaises(ValueError):
            v.QwenGroundingAdapter(coords="foo")


if __name__ == "__main__":
    unittest.main()
