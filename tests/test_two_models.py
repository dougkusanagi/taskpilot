"""Testes da arquitetura de 2 modelos (offline + mock HTTP, sem GUI).

- Planner: prompt compacto sem screenshot, JSON tolerante, veto a coordenadas.
- Vocaela: parser do formato oficial <Action>[...]</Action>, normalização 0..1.
- Integração: clientes OpenAI-compatible contra servidores mock locais.
- UIA resolve por nome (sem scorer no fluxo principal).

Roda com: .\\.venv\\Scripts\\python.exe -m unittest discover -s tests -v
"""
from __future__ import annotations

import json
import sys
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from PIL import Image  # noqa: E402

import config as cfgmod  # noqa: E402
import planner  # noqa: E402
import vocaela  # noqa: E402


# --- helpers mock ------------------------------------------------------------
def _make_server(reply_text: str):
    seen: list[dict] = []

    class H(BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def do_GET(self):
            if self.path.endswith("/models"):
                body = json.dumps({"data": [{"id": "mock"}]}).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
            else:
                self.send_response(404)
                self.end_headers()

        def do_POST(self):
            n = int(self.headers.get("Content-Length", 0))
            seen.append(json.loads(self.rfile.read(n) or b"{}"))
            body = json.dumps({"choices": [{"message": {"content": reply_text}}]}).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

    srv = ThreadingHTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv, seen


# --- planner -----------------------------------------------------------------
class TestPlannerPrompt(unittest.TestCase):
    def test_compacto_sem_screenshot(self):
        p = planner.build_prompt("Abra o notepad", "Bloco de Notas",
                                 ["Arquivo", "Editar"], ["open_app(notepad)"])
        self.assertIn("Abra o notepad", p)
        self.assertIn("Bloco de Notas", p)
        self.assertIn("Arquivo", p)
        self.assertIn("open_app(notepad)", p)
        for banned in ("base64", "image_url", "data:image"):
            self.assertNotIn(banned, p)

    def test_historico_limitado_a_5(self):
        p = planner.build_prompt("g", "w", ["a"], [f"a{i}" for i in range(10)])
        self.assertNotIn("a0", p.split("Recent actions:")[1])
        self.assertIn("a9", p)

    def test_system_proibe_coordenadas(self):
        sys = planner.PLANNER_SYSTEM
        self.assertIn("NEVER output coordinates", sys)
        self.assertIn("visual_action", sys)
        self.assertIn("uia_click", sys)

    def test_proibe_url_profunda_inventada(self):
        blob = planner.TOOLS_SPEC + planner.PLANNER_SYSTEM
        self.assertNotIn("open_url", blob)
        self.assertIn("NEVER type a deep/product URL you guessed", planner.PLANNER_SYSTEM)
        self.assertIn("like a human, through the", planner.PLANNER_SYSTEM)

    def test_answer_no_spec_e_no_schema(self):
        self.assertIn("answer", planner.TOOLS_SPEC)
        self.assertIn("answer", planner.planner_json_schema()["properties"]["type"]["enum"])
        dec = planner.PlannerDecision.model_validate({"type": "answer",
                                                      "text": "R$ 12.499"})
        self.assertEqual(dec.text, "R$ 12.499")


class TestPlannerJson(unittest.TestCase):
    def test_limpo(self):
        d = planner.extract_json('{"type": "done"}')
        self.assertEqual(d, {"type": "done"})

    def test_fenced(self):
        d = planner.extract_json('```json\n{"type":"open_app","app":"notepad"}\n```')
        self.assertEqual(d["app"], "notepad")

    def test_ruido(self):
        d = planner.extract_json('Sure! {"type": "wait", "ms": 500} ok')
        self.assertEqual(d["ms"], 500)

    def test_invalido(self):
        with self.assertRaises(ValueError):
            planner.extract_json("olá, clique ali")

    def test_veta_coords(self):
        dec = planner.PlannerDecision.model_validate({"type": "visual_action",
                                                      "instruction": "Click X"})
        with self.assertRaises(ValueError):
            dec.assert_no_coords({"type": "click", "x": 100, "y": 200})

    def test_decision_valida(self):
        dec = planner.PlannerDecision.model_validate({"type": "type_text",
                                                      "text": "Olá"})
        self.assertEqual(dec.text, "Olá")

    def test_tipo_desconhecido_rejeitado(self):
        with self.assertRaises(Exception):
            planner.PlannerDecision.model_validate({"type": "abra_tudo"})


class TestPlannerClient(unittest.TestCase):
    def test_roundtrip_sem_imagem(self):
        srv, seen = _make_server('{"type": "open_app", "app": "notepad"}')
        try:
            base = f"http://127.0.0.1:{srv.server_port}/v1"
            pl = planner.MiniCPMPlanner(base_url=base, model="MiniCPM5-1B")
            self.assertTrue(pl.check()["ok"])
            dec, ms = pl.next_action("goal", "win", ["a"], [])
            self.assertEqual(dec.type, "open_app")
            self.assertEqual(dec.app, "notepad")
            self.assertGreaterEqual(ms, 0)
            payload = seen[0]
            blob = json.dumps(payload)
            self.assertNotIn("image_url", blob)
            self.assertNotIn("base64", blob)
            self.assertIn("MiniCPM5-1B", payload["model"])
            self.assertLessEqual(payload["temperature"], 0.2)
        finally:
            srv.shutdown()
            srv.server_close()

    def test_qwenvl_unificado_recebe_screenshot(self):
        from unittest.mock import patch

        srv, seen = _make_server('{"type": "hotkey", "keys": "ctrl+l"}')
        try:
            base = f"http://127.0.0.1:{srv.server_port}/v1"
            pl = planner.QwenVLPlanner(
                base_url=base, model="Qwen3-VL-2B-Instruct")
            with patch("obs.capture_for_vision", return_value=(
                    Image.new("RGB", (32, 24), "white"), (0, 0), (32, 24))):
                dec, _ = pl.next_action("abra amazon", "Chrome", [], [])
            self.assertEqual(dec.type, "hotkey")
            payload = seen[0]
            content = payload["messages"][1]["content"]
            self.assertEqual(content[0]["type"], "text")
            self.assertEqual(content[1]["type"], "image_url")
            self.assertTrue(content[1]["image_url"]["url"].startswith(
                "data:image/jpeg;base64,"))
            self.assertEqual(payload["model"], "Qwen3-VL-2B-Instruct")
        finally:
            srv.shutdown()
            srv.server_close()


# --- vocaela -----------------------------------------------------------------
class TestVocaelaParse(unittest.TestCase):
    def test_formato_oficial_click(self):
        va = vocaela.parse_vocaela_output(
            '<Action>[{"action": "CLICK", "coordinate": [0.1, 0.5]}]</Action>')
        self.assertEqual((va.type, va.x, va.y), ("click", 0.1, 0.5))

    def test_minusculo_e_multiplas_usa_primeira(self):
        va = vocaela.parse_vocaela_output(
            '<Action>[{"action": "click", "coordinate": [0.2, 0.3]}, '
            '{"action": "type", "text": "oi"}]</Action>')
        self.assertEqual(va.type, "click")

    def test_type(self):
        va = vocaela.parse_vocaela_output(
            '<Action>[{"action": "TYPE", "text": "hello"}]</Action>')
        self.assertEqual((va.type, va.text), ("type", "hello"))

    def test_drag(self):
        va = vocaela.parse_vocaela_output(
            '<Action>[{"action": "DRAG", "coordinate": [0.1, 0.2], '
            '"coordinate2": [0.3, 0.4]}]</Action>')
        self.assertEqual((va.type, va.x2, va.y2), ("drag", 0.3, 0.4))

    def test_scroll_hotkey_presskey(self):
        va = vocaela.parse_vocaela_output(
            '<Action>[{"action": "SCROLL", "scroll_direction": "up"}]</Action>')
        self.assertEqual((va.type, va.scroll_direction), ("scroll", "up"))
        va = vocaela.parse_vocaela_output(
            '<Action>[{"action": "HOTKEY", "hotkeys": ["ctrl", "c"]}]</Action>')
        self.assertEqual((va.type, va.key), ("hotkey", "ctrl+c"))
        va = vocaela.parse_vocaela_output(
            '<Action>[{"action": "PRESS_KEY", "key": "enter"}]</Action>')
        self.assertEqual((va.type, va.key), ("key", "enter"))

    def test_bare_json_sem_tags(self):
        va = vocaela.parse_vocaela_output('{"action": "click", "coordinate": [0.5, 0.5]}')
        self.assertEqual(va.type, "click")

    def test_fora_de_range_rejeita(self):
        with self.assertRaises(ValueError):
            vocaela.parse_vocaela_output(
                '<Action>[{"action": "CLICK", "coordinate": [53, 91]}]</Action>')

    def test_lixo_rejeita(self):
        with self.assertRaises(ValueError):
            vocaela.parse_vocaela_output("não achei nada na tela")

    def test_acao_desconhecida_rejeita(self):
        with self.assertRaises(ValueError):
            vocaela.parse_vocaela_output(
                '<Action>[{"action": "TELEPORT", "coordinate": [0.1, 0.1]}]</Action>')


class TestVocaelaAdapter(unittest.TestCase):
    def test_prep_mantem_aspect_e_teto(self):
        img = Image.new("RGB", (1920, 1080), "white")
        b64, (w, h) = vocaela._prep_image(img, max_long_edge=1024)
        self.assertEqual((w, h), (1024, 576))
        self.assertTrue(b64)

    def test_prep_nao_amplia(self):
        img = Image.new("RGB", (800, 600), "white")
        _, (w, h) = vocaela._prep_image(img, max_long_edge=1024)
        self.assertEqual((w, h), (800, 600))

    def test_system_message_oficial_no_request(self):
        reply = '<Action>[{"action": "CLICK", "coordinate": [0.5, 0.25]}]</Action>'
        srv, seen = _make_server(reply)
        try:
            base = f"http://127.0.0.1:{srv.server_port}/v1"
            ad = vocaela.VocaelaAdapter(base_url=base)
            self.assertTrue(ad.check()["ok"])
            va, ms = ad.act_sync(Image.new("RGB", (100, 100), "white"),
                                 "Click the address bar")
            self.assertEqual((va.type, va.x, va.y), ("click", 0.5, 0.25))
            payload = seen[0]
            msgs = payload["messages"]
            self.assertIn("navigate the computer screen", msgs[0]["content"])
            user_blob = json.dumps(msgs[1])
            self.assertIn("image_url", user_blob)
            self.assertIn("Click the address bar", user_blob)
        finally:
            srv.shutdown()
            srv.server_close()

    def test_visual_to_action_com_origin(self):
        va = vocaela.VisualAction(type="click", x=0.5, y=0.5)
        act = vocaela.visual_to_action(va, (1000, 500), (100, 200))
        self.assertEqual((act.x, act.y), (600, 450))


# --- config ------------------------------------------------------------------
class TestConfig(unittest.TestCase):
    def test_defaults_2_modelos(self):
        cfg = cfgmod.load("nao-existe.json")
        self.assertEqual(cfg["profile"], "B1")
        self.assertEqual(cfg["planner"]["model"], "MiniCPM5-2B")
        self.assertEqual(cfg["vision"]["model"], "Vocaela-2-500M-1024R2")
        self.assertIn("8091", cfg["planner"]["base_url"])
        self.assertIn("8082", cfg["vision"]["base_url"])
        self.assertEqual(cfg["screenshot_max_width"], 1024)

    def test_migra_legado(self):
        import tempfile

        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "c.json"
            p.write_text(json.dumps({"base_url": "http://127.0.0.1:1234/v1",
                                     "vision_model": "MAI-UI-2B",
                                     "scorer_threshold": 0.8}), encoding="utf-8")
            cfg = cfgmod.load(p)
            self.assertIn("1234", cfg["planner"]["base_url"])
            self.assertIn("1234", cfg["vision"]["base_url"])
            self.assertNotIn("scorer_threshold", cfg)


# --- UIA resolve --------------------------------------------------------------
class TestResolveUia(unittest.TestCase):
    ITEMS = [
        {"id": 0, "name": "Sete", "type": "Button", "bounds": [10, 10, 50, 50]},
        {"id": 1, "name": "Minimizar Calculadora", "type": "Button",
         "bounds": [10, 10, 50, 50]},
        {"id": 2, "name": "Calculadora", "type": "Window",
         "bounds": [0, 0, 400, 400]},
    ]

    def test_exact(self):
        from loop import _resolve_uia

        a, note = _resolve_uia(self.ITEMS, "Sete", (0, 0, 400, 400),
                               state_title="Calculadora")
        self.assertIsNotNone(a)
        self.assertEqual(note, "")
        self.assertEqual((a.x, a.y), (30, 30))

    def test_pula_chrome_e_janela(self):
        from loop import _resolve_uia

        self.assertEqual(_resolve_uia(self.ITEMS, "Minimizar", (0, 0, 400, 400)), (None, ""))
        self.assertEqual(_resolve_uia(self.ITEMS, "Calculadora", (0, 0, 400, 400),
                                      state_title="Calculadora"), (None, ""))

    def test_miss_fora_da_janela(self):
        from loop import _resolve_uia

        self.assertEqual(_resolve_uia(self.ITEMS, "Sete", (500, 500, 900, 900)), (None, ""))


class TestPlannerSchema(unittest.TestCase):
    def test_response_format_no_payload(self):
        srv, seen = _make_server('{"type": "done"}')
        try:
            base = f"http://127.0.0.1:{srv.server_port}/v1"
            pl = planner.MiniCPMPlanner(base_url=base)
            dec, _ = pl.next_action("g", "w", ["a"], [])
            self.assertEqual(dec.type, "done")
            rf = seen[0].get("response_format", {})
            self.assertEqual(rf.get("type"), "json_schema")
            schema = rf["json_schema"]["schema"]
            self.assertIn("done", schema["properties"]["type"]["enum"])
            self.assertFalse(schema.get("additionalProperties", True))
            self.assertNotIn("x", schema["properties"])
            self.assertNotIn("y", schema["properties"])
        finally:
            srv.shutdown()
            srv.server_close()

    def test_model_validate_recusa_coords(self):
        with self.assertRaises(Exception):
            planner.PlannerDecision.model_validate(
                {"type": "uia_click", "target": "7", "x": 10, "y": 20})


class TestFormatUiNames(unittest.TestCase):
    def test_tipo_nome_e_interativos_primeiro(self):
        from loop import format_ui_names

        items = [
            {"name": "texto corrido", "type": "Text", "bounds": [0, 0, 10, 10]},
            {"name": "7", "type": "Button", "bounds": [0, 0, 10, 10]},
            {"name": "Pesquisar", "type": "Edit", "bounds": [0, 0, 10, 10]},
            {"name": "", "type": "Button", "bounds": [0, 0, 10, 10]},
        ]
        names = format_ui_names(items)
        self.assertEqual(names[0], "Button:7")
        self.assertEqual(names[1], "Edit:Pesquisar")
        self.assertTrue(names[-1].startswith("Text:"))
        self.assertEqual(len(names), 3)


class TestVocaelaHistory(unittest.TestCase):
    def test_historico_vai_no_prompt(self):
        reply = '<Action>[{"action": "CLICK", "coordinate": [0.5, 0.25]}]</Action>'
        srv, seen = _make_server(reply)
        try:
            base = f"http://127.0.0.1:{srv.server_port}/v1"
            ad = vocaela.VocaelaAdapter(base_url=base)
            va, _ = ad.act_sync(Image.new("RGB", (50, 50), "white"),
                                "Click X", history=["visual a -> click", "type oi"])
            self.assertEqual(va.type, "click")
            user_blob = json.dumps(seen[0]["messages"][1])
            self.assertIn("Action history sequence", user_blob)
            self.assertIn("Click X", user_blob)
        finally:
            srv.shutdown()
            srv.server_close()

    def test_sem_historico_sem_secao(self):
        reply = '<Action>[{"action": "CLICK", "coordinate": [0.5, 0.25]}]</Action>'
        srv, seen = _make_server(reply)
        try:
            base = f"http://127.0.0.1:{srv.server_port}/v1"
            ad = vocaela.VocaelaAdapter(base_url=base)
            ad.act_sync(Image.new("RGB", (50, 50), "white"), "Click X")
            user_blob = json.dumps(seen[0]["messages"][1])
            self.assertNotIn("Action history sequence", user_blob)
        finally:
            srv.shutdown()
            srv.server_close()


class TestQwenGrounding(unittest.TestCase):
    def test_parse_json_xy(self):
        va = vocaela.parse_qwen_grounding('{"x": 0.5, "y": 0.25}')
        self.assertEqual((va.type, va.x, va.y), ("click", 0.5, 0.25))

    def test_parse_coordinate_array(self):
        va = vocaela.parse_qwen_grounding(
            '{"coordinate": [0.2, 0.8]}')
        self.assertEqual((va.x, va.y), (0.2, 0.8))

    def test_parse_lista_pura(self):
        va = vocaela.parse_qwen_grounding('[0.1, 0.9]')
        self.assertEqual((va.x, va.y), (0.1, 0.9))

    def test_alvo_ausente_erro_honesto(self):
        with self.assertRaises(ValueError):
            vocaela.parse_qwen_grounding('{"x": null, "y": null}')

    def test_fora_de_range_rejeita(self):
        with self.assertRaises(ValueError):
            vocaela.parse_qwen_grounding('{"x": 53, "y": 0.5}')

    def test_adapter_envia_grounding_json(self):
        srv, seen = _make_server('{"x": 0.4, "y": 0.6}')
        try:
            base = f"http://127.0.0.1:{srv.server_port}/v1"
            ad = vocaela.QwenGroundingAdapter(base_url=base, protocol="json")  # protocolo histórico
            va, _ = ad.act_sync(Image.new("RGB", (50, 50), "white"),
                                "Click the Continue shopping button")
            self.assertEqual((va.type, va.x, va.y), ("click", 0.4, 0.6))
            payload = seen[0]
            blob = json.dumps(payload["messages"])
            self.assertIn("grounding", blob.lower())
            self.assertNotIn("<Action>", blob)
            self.assertIn("Continue shopping", blob)
        finally:
            srv.shutdown()
            srv.server_close()

    def test_unified_system_tem_regras_explicitas(self):
        # Run U1 20/09: "same as textual" era ignorado; regra precisa estar
        # escrita por extenso no system unificado.
        self.assertIn("NEVER emit open_app", planner.UNIFIED_SYSTEM)
        self.assertIn("Continue shopping", planner.UNIFIED_SYSTEM)
        self.assertIn("0..1 in THAT frame", planner.UNIFIED_SYSTEM)

    def test_build_adapters_qwen_usa_grounding(self):
        from model_adapters import build_adapters

        cfg = {"profile": "U1",
               "planner": {"model": "Qwen3-VL-2B-Instruct"},
               "vision": {"model": "Qwen3-VL-2B-Instruct"},
               "screenshot_max_width": 1024}
        p, v, prof = build_adapters(cfg)
        self.assertEqual(type(p).__name__, "QwenVLPlanner")
        self.assertEqual(type(v).__name__, "QwenGroundingAdapter")
        cfg2 = {"profile": "B1",
                "planner": {"model": "MiniCPM5-2B"},
                "vision": {"model": "Vocaela-2-500M-1024R2"},
                "screenshot_max_width": 1024}
        _, v2, _ = build_adapters(cfg2)
        self.assertEqual(type(v2).__name__, "VocaelaAdapter")


if __name__ == "__main__":
    unittest.main(verbosity=2)
