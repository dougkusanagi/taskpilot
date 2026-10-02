"""Regressões da base anterior e das etapas R0–R3 do plano vigente.

Offline, sem GUI/modelos (mocks/fakes). Comportamento e invariantes, não
espelho de texto.

Roda com: uv run python -m unittest discover -s tests -v
"""

from __future__ import annotations

import json
import sys
import unittest
import unittest.mock
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))


class TestTelemetry(unittest.TestCase):
    def test_collect_env_chaves(self):
        import telemetry as tel

        env = tel.collect_env()
        for k in ("os", "cpu", "ram_total_gb", "gpu"):
            self.assertIn(k, env)

    def test_p50_p95(self):
        import telemetry as tel

        self.assertEqual(tel.p50([]), 0.0)
        self.assertEqual(tel.p95([]), 0.0)
        self.assertEqual(tel.p50([1, 2, 3]), 2.0)
        self.assertGreaterEqual(tel.p95([1, 2, 3]), 2.0)

    def test_tasks_json_20(self):
        tasks = json.loads((ROOT / "evals" / "tasks.json").read_text(encoding="utf-8"))["tasks"]
        self.assertEqual(len(tasks), 20)
        cats = {t["category"] for t in tasks}
        for c in ("notepad", "calc", "web-fixture", "visual", "recovery"):
            self.assertIn(c, cats)
        self.assertTrue(any(t.get("pilot") for t in tasks))
        self.assertTrue(any(t.get("holdout") for t in tasks))

    def test_runner_dry_run_grava_runs(self):
        from evals.runner import check_task, load_tasks, run_task

        tasks = load_tasks()
        self.assertEqual(len(tasks), 20)
        cfg = {"max_steps": 4}
        s = run_task(tasks[0], cfg, dry_run=True)
        self.assertEqual(s["result"], "dry_run_ok")
        d = ROOT / "runs" / s["run_id"]
        self.assertTrue((d / "summary.json").is_file())
        self.assertTrue((d / "run.jsonl").is_file())
        c = check_task(tasks[0], [])
        self.assertIn("pass", c)

    def test_fixture_nova_guia_reprova_repeticao(self):
        from evals.probes import check_decision, load_fixture
        from planner import PlannerDecision

        fixture = load_fixture("nova-guia-stuck")
        bad = check_decision(fixture, PlannerDecision(type="hotkey", keys="ctrl+t"))
        self.assertFalse(bad["pass"])
        good = check_decision(
            fixture,
            PlannerDecision(
                type="sequence",
                steps=[
                    {"type": "hotkey", "keys": "ctrl+l"},
                    {"type": "type_text", "text": "https://example.test"},
                    {"type": "press_key", "key": "enter"},
                ],
            ),
        )
        self.assertTrue(good["pass"])


class TestSchemasF1(unittest.TestCase):
    def test_confidence_ausente_por_padrao(self):
        from schemas import Action, Decision

        d = Decision(action=Action(type="wait", ms=100), source="planner")
        self.assertIsNone(d.confidence)

    def test_confidence_fixa_nao_e_certeza(self):
        from schemas import Action, Decision, validate_decision

        d = Decision(action=Action(type="type", text="oi"), source="planner", confidence=0.9)
        validate_decision({"type": "type", "text": "oi"}, d)
        with self.assertRaises(ValueError):
            validate_decision(
                {"type": "type", "text": "oi"},
                Decision(action=Action(type="type", text="oi"), source="planner", confidence=9.0),
            )

    def test_type_sem_text_vetado(self):
        from schemas import Action, Decision, validate_decision

        with self.assertRaises(ValueError):
            validate_decision(
                {"type": "type"}, Decision(action=Action(type="type"), source="planner")
            )

    def test_completion_exige_evidencia(self):
        from schemas import Completion

        with self.assertRaises(Exception):
            Completion(status="success", evidence_refs=[])
        c = Completion(status="success", evidence_refs=["obs1#0"])
        self.assertEqual(c.status, "success")
        b = Completion(status="blocked", evidence_refs=[])
        self.assertEqual(b.status, "blocked")

    def test_action_result_sem_repeticao_automatica(self):
        from schemas import ActionResult

        r = ActionResult(sent="unknown", confirmed=False)
        self.assertFalse(r.confirmed)
        self.assertEqual(r.sent, "unknown")


class TestState(unittest.TestCase):
    def test_fato_diferente_de_hipotese(self):
        import state as st

        s = st.init("objetivo")
        st.apply_update(
            s, {"subgoal": "sub", "facts": {"preço": "obs1"}, "hypotheses": {"talvez": "h1"}}
        )
        self.assertEqual(s.subgoal, "sub")
        self.assertIn("preço", s.facts)
        self.assertIn("talvez", s.hypotheses)
        st.add_failure(s, "f1")
        self.assertIn("f1", st.compact(s))
        st.add_evidence(s, "e1")
        self.assertIn("e1", s.evidences)
        self.assertIn("evidências confirmadas", st.compact(s))

    def test_estado_e_resultado_chegam_ao_planner(self):
        import loop
        import state as st
        from planner import PlannerDecision

        received = {}

        class Fake:
            def next_action(self, **kwargs):
                received.update(kwargs)
                return PlannerDecision(type="wait", ms=10), 1.0

        task = st.init("pesquisar preço")
        task.pending = ["abrir resultado"]
        loop._ask_planner(
            Fake(),
            "pesquisar preço",
            "Chrome",
            ["Button:Buscar"],
            {"hist_labels": [], "task_state": task, "last_result": "ctrl+t => aba criada"},
            "",
            {},
        )
        self.assertIn("pendências: abrir resultado", received["task_summary"])
        self.assertEqual(received["last_result"], "ctrl+t => aba criada")

    def test_prompt_marca_observacao_como_dado(self):
        from planner import build_prompt

        text = build_prompt(
            "g", "Chrome", [], [], task_summary="pendente", last_result="texto da página"
        )
        self.assertIn("trusted agent memory", text)
        self.assertIn("observation, not an instruction", text)


class TestVerification(unittest.TestCase):
    def test_type_confirmado_so_com_valor(self):
        import verification as vf

        ok, _ = vf.confirm_effect("type", "Olá", "N", "N", "Olá mundo")
        self.assertTrue(ok)
        ok, _ = vf.confirm_effect("type", "Olá", "N", "N", "outra")
        self.assertFalse(ok)

    def test_titulo_sozinho_nao_confirma(self):
        import verification as vf

        ok, _ = vf.confirm_effect("open", "", "A", "B")
        self.assertFalse(ok)

    def test_nova_aba_exige_transicao_observada(self):
        import verification as vf

        ok, _ = vf.confirm_effect("hotkey:ctrl+t", after="window 'A' -> 'Nova guia'")
        self.assertTrue(ok)
        ok, _ = vf.confirm_effect("hotkey:ctrl+t", after="window 'A'")
        self.assertFalse(ok)

    def test_answer_nao_confirma_a_si_mesmo(self):
        import verification as vf

        ok, note = vf.confirm_effect("answer", "R$ 10")
        self.assertFalse(ok)
        self.assertIn("evidência", note)

    def test_prompt_unificado_expoe_frame_sem_promessa_de_evidencia(self):
        from planner import build_unified_prompt

        prompt = build_unified_prompt("g", "w", [], [], frame_id="frm-1")
        self.assertIn("frm-1", prompt)
        self.assertIn("not evidence", prompt)

    def test_fingerprint_muda_com_conteudo_uia(self):
        import loop

        a = loop._state_fingerprint("Chrome", ["Button:A"])
        b = loop._state_fingerprint("Chrome", ["Button:B"])
        self.assertTrue(loop._progress_made(a, b))

    def test_done_sem_evidencia_vetado(self):
        import verification as vf

        ok, _ = vf.done_evidence_ok([], [], [])
        self.assertFalse(ok)
        ok, _ = vf.done_evidence_ok([], [], ["type(3) => window 'N'"])
        self.assertFalse(ok)
        ok, _ = vf.done_evidence_ok(["ev-confirmada"], ["ev-confirmada"], [])
        self.assertTrue(ok)
        ok, msg = vf.done_evidence_ok(["x"], ["y"], ["type(3) => z"])
        self.assertFalse(ok)
        self.assertIn("obsoleta", msg)

    def test_wait_cancelavel(self):
        import verification as vf

        ok, ms = vf.wait_for_condition(lambda: True, deadline_s=1.0)
        self.assertTrue(ok)
        self.assertGreaterEqual(ms, 0)
        ok, _ = vf.wait_for_condition(lambda: False, deadline_s=0.05)
        self.assertFalse(ok)


class TestUiaObsF2(unittest.TestCase):
    def test_resolve_ref_por_id_e_stale(self):
        import uia

        items = [{"id": 3, "name": "Sete", "type": "Button", "bounds": [0, 0, 10, 10]}]
        hit = uia.resolve_ref(items, "obs-1#3", "obs-1")
        self.assertEqual(hit["name"], "Sete")
        self.assertIsNone(uia.resolve_ref(items, "obs-2#3", "obs-1"))
        self.assertIsNone(uia.resolve_ref(items, "obs-1#9", "obs-1"))

    def test_frame_stale_e_post_state(self):
        import obs
        from schemas import FrameRef

        f = FrameRef()
        self.assertFalse(obs.frame_is_stale(f, max_age_s=60))
        f.captured_at -= 3600
        self.assertTrue(obs.frame_is_stale(f, max_age_s=5))
        obs.set_post_state("x")
        txt, valid = obs.get_post_state()
        self.assertTrue(valid and txt == "x")
        obs.invalidate_post_state("escrita")
        self.assertFalse(obs.get_post_state()[1])

    def test_monitors_lista(self):
        import obs

        mons = obs.list_monitors()
        self.assertIsInstance(mons, list)


class TestActionsSafetyF2(unittest.TestCase):
    def test_precondicao_falta_xy_ou_stale(self):
        from unittest.mock import patch

        import actions
        from schemas import Action

        msg = actions.check_preconditions(Action(type="click"))
        self.assertIn("x,y", msg)
        msg = actions.check_preconditions(Action(type="click", element_ref="obs#1"))
        self.assertIn("stale", msg)
        # tela virtual simulada: ctypes.windll só existe no Windows
        with patch("actions._virtual_screen", return_value=(0, 0, 1920, 1080)):
            msg = actions.check_preconditions(Action(type="drag", x=1, y=1))
        self.assertIn("x2", msg)

    def test_executor_unico(self):
        import safety

        safety.release_executor()
        self.assertTrue(safety.acquire_executor("t"))
        self.assertFalse(safety.acquire_executor("outro"))
        safety.release_executor()
        self.assertTrue(safety.acquire_executor("t2"))
        safety.release_executor()


class TestProfilesF4(unittest.TestCase):
    def test_todos_perfis_conhecidos(self):
        import config as cfgmod

        for pid in ("B0", "B1", "D1", "D2", "U1", "U2", "G1", "E1", "E2"):
            self.assertIn(pid, cfgmod.PROFILES)
        self.assertEqual(cfgmod.profile_of({})["name"], "B1")  # default atual
        self.assertEqual(cfgmod.profile_of({"profile": "B0"})["name"], "B0")
        self.assertEqual(cfgmod.profile_of({"profile": "U1"})["mode"], "unified")

    def test_unified_alias_sem_duplicar(self):
        import model_adapters as ma

        p = {"mode": "unified", "planner": "M", "vision": "M"}
        a, b = ma.unified_endpoints("http://x:1/v1", "http://y:2/v1", p)
        self.assertEqual(a, b)
        d = {"mode": "dual", "planner": "A", "vision": "B"}
        a, b = ma.unified_endpoints("http://x:1/v1", "http://y:2/v1", d)
        self.assertNotEqual(a, b)

    def test_orcamento_6gb(self):
        import model_adapters as ma

        ok, _ = ma.vram_budget_ok(None, "U2")
        self.assertTrue(ok)
        ok, msg = ma.vram_budget_ok(3000, "U2")
        self.assertFalse(ok)
        self.assertIn("insuficiente", msg)

    def test_server_unified_um_processo(self):
        import server

        with unittest.mock.patch.object(server, "_endpoint_alive", return_value={"models": ["m"]}):
            out = server.ensure_servers_for_profile(
                ("http://127.0.0.1:1/v1", "http://127.0.0.1:1/v1"),
                {},
                {"mode": "unified", "planner": "M", "vision": "M", "name": "U1"},
            )
        self.assertEqual(out, {"planner": None, "vision": None})

    def test_cli_unified_usa_um_endpoint(self):
        import server

        profile = {"mode": "unified", "planner": "M", "vision": "M"}
        urls = server.cli_base_urls("127.0.0.1", profile)
        self.assertEqual(urls[0], urls[1])
        dual = server.cli_base_urls("127.0.0.1", {"mode": "dual", "planner": "A", "vision": "B"})
        self.assertNotEqual(dual[0], dual[1])

    def test_gpu_status_sem_presumir(self):
        import server

        gs = server.gpu_status({"runtime": {"ngl": 2}})
        self.assertIn("warn", gs)

    def test_b1_seletor_gguf(self):
        import server

        self.assertEqual(server.planner_gguf_for({})["file"], "MiniCPM5-1B-Q4_K_M.gguf")
        self.assertEqual(
            server.planner_gguf_for({"planner": {"model": "MiniCPM5-2B"}})["file"],
            "MiniCPM5-2B-Q4_K_M.gguf",
        )
        self.assertIn("openbmb/MiniCPM5-2B-GGUF", server.PLANNER_2B_GGUF["url"])
        # desconhecido = default seguro 1B
        self.assertEqual(
            server.planner_gguf_for({"planner": {"model": "Xyz-9B"}})["file"],
            "MiniCPM5-1B-Q4_K_M.gguf",
        )

    def test_u1_seleciona_qwenvl_sem_fallback(self):
        import server

        picked = server.planner_gguf_for({"planner": {"model": "Qwen3-VL-2B-Instruct"}})
        self.assertEqual(picked["file"], "Qwen3-VL-2B-Instruct-Q4_K_M.gguf")
        self.assertIn("Qwen3-VL-2B-Instruct-GGUF", picked["url"])

    def test_unified_recusa_endpoint_de_outro_modelo(self):
        import server

        with unittest.mock.patch.object(
            server, "_endpoint_alive", return_value={"models": ["MiniCPM5-2B-Q4_K_M.gguf"]}
        ):
            with self.assertRaises(RuntimeError) as cm:
                server.ensure_servers_for_profile(
                    ("http://127.0.0.1:1/v1", "http://127.0.0.1:1/v1"),
                    {},
                    {
                        "mode": "unified",
                        "planner": "Qwen3-VL-2B-Instruct",
                        "vision": "Qwen3-VL-2B-Instruct",
                        "name": "U1",
                    },
                )
        self.assertIn("nao Qwen3-VL-2B-Instruct", str(cm.exception))

    def test_b1_assets_baixa_2b_sem_1b(self):
        import tempfile
        from unittest.mock import patch

        import server

        with tempfile.TemporaryDirectory() as td:
            with (
                patch.object(server, "MODELS_DIR", Path(td)),
                patch.object(server, "LLAMA_EXE", Path(td) / "s.exe"),
                patch.object(server, "_download") as dl,
                patch.object(server.sys, "platform", "win32"),
            ):
                (Path(td) / "s.exe").write_bytes(b"x")
                dl.side_effect = lambda url, dest, **k: dest.write_bytes(b"g")
                cfg = {"planner": {"model": "MiniCPM5-2B"}}
                assets = server.ensure_assets(progress=lambda *a: None, cfg=cfg)
                self.assertTrue(str(assets["planner_gguf"]).endswith("MiniCPM5-2B-Q4_K_M.gguf"))
                baixados = [c.args[1].name for c in dl.call_args_list]
                self.assertIn("MiniCPM5-2B-Q4_K_M.gguf", baixados)
                self.assertNotIn("MiniCPM5-1B-Q4_K_M.gguf", baixados)

    def test_apply_profile_b1_e_rollback(self):
        import config as cfgmod

        cfg = cfgmod.load("nao-existe.json")
        cfgmod.apply_profile(cfg, "B1")
        self.assertEqual(cfg["profile"], "B1")
        self.assertEqual(cfg["planner"]["model"], "MiniCPM5-2B")
        self.assertEqual(cfg["vision"]["model"], "Vocaela-2-500M-1024R2")
        with self.assertRaises(ValueError):
            cfgmod.apply_profile(cfg, "Z9")
        cfgmod.apply_profile(cfg, "B0")  # rollback
        self.assertEqual(cfg["planner"]["model"], "MiniCPM5-1B")


class TestSkillsF5(unittest.TestCase):
    def test_catalogo_e_quatro_skills(self):
        import skills as sk

        names = {s["name"] for s in sk.list_skills()}
        for n in ("computer-use", "browser-gui", "text-editing", "blender-cli"):
            self.assertIn(n, names)
        self.assertIn("blender-cli", sk.catalog_text())

    def test_ausente_erro_honesto(self):
        import skills as sk

        with self.assertRaises(ValueError):
            sk.load_skill("nao-existe")

    def test_cli_receita_invalida_vetada(self):
        import skills as sk

        with self.assertRaises(ValueError):
            sk.run_cli_skill("blender-cli", {"recipe": "nave-espacial"}, timeout_s=5)
        with self.assertRaises(ValueError):
            sk.run_cli_skill("computer-use", {"recipe": "cubo"}, timeout_s=5)

    def test_cli_piloto_grava_artefatos(self):
        import subprocess
        import tempfile

        with tempfile.TemporaryDirectory() as td:
            import skills as _sk

            orig = _sk.ROOT
            try:
                _sk.ROOT = Path(td)
                cwd = Path(td) / "blender-jobs"
                # aponta o executor p/ raiz temporária via monkeypatch leve:
                # executa o script piloto diretamente (argv, sem shell).
                import subprocess

                script = ROOT / "skills" / "blender-cli" / "scripts" / "cubo.py"
                cwd.mkdir(parents=True, exist_ok=True)
                p = subprocess.run(
                    [sys.executable, str(script), "--tamanho=2"],
                    cwd=str(cwd),
                    capture_output=True,
                    text=True,
                    timeout=30,
                )
                self.assertEqual(p.returncode, 0)
                self.assertTrue((cwd / "cubo-t2.blend").is_file())
                self.assertTrue((cwd / "cubo-t2.png").is_file())
            finally:
                _sk.ROOT = orig


class TestLoopF3F6(unittest.TestCase):
    def _patch_snapshot(self, items, title, wrect):
        import loop

        orig = loop.active_window_snapshot
        loop.active_window_snapshot = lambda: (items, title, wrect)
        self.addCleanup(lambda: setattr(loop, "active_window_snapshot", orig))

    def test_done_com_evidencia_obsoleta_vetado(self):
        import loop
        from planner import PlannerDecision

        class Fake:
            def next_action(
                self, goal, window, ui_names, history, last_error="", skills_catalog=""
            ):
                return PlannerDecision(type="done", evidences=["fantasma"]), 1.0

        import state as st

        self._patch_snapshot([], "Edge", None)
        ctx = {"hist_labels": ["type(x) => window 'Edge'"], "task_state": st.init("g")}
        with self.assertRaises(RuntimeError) as cm:
            loop.decide("g", 1, ctx, {}, planner=Fake(), vocaela=object())
        self.assertIn("obsoleta", str(cm.exception))

    def test_sequence_valida_e_invalida(self):
        import loop
        from planner import PlannerDecision

        ok = PlannerDecision(
            type="sequence",
            steps=[
                {"type": "hotkey", "keys": "ctrl+l"},
                {"type": "type_text", "text": "https://www.amazon.com"},
                {"type": "press_key", "key": "enter"},
            ],
        )
        acts = loop._sequence_to_actions(ok)
        self.assertEqual([a.type for a in acts], ["hotkey", "type", "hotkey"])
        bad = PlannerDecision(type="sequence", steps=[{"type": "hotkey", "keys": "ctrl+l"}] * 4)
        with self.assertRaises(RuntimeError):
            loop._sequence_to_actions(bad)
        bad2 = PlannerDecision(type="sequence", steps=[{"type": "uia_click", "target": "x"}])
        with self.assertRaises(RuntimeError):
            loop._sequence_to_actions(bad2)
        # press_key misturado com keys (probe 19/09): rejeita com dica.
        mixed = PlannerDecision(
            type="sequence", steps=[{"type": "press_key", "keys": "ctrl+l", "key": "enter"}]
        )
        with self.assertRaises(RuntimeError) as cm:
            loop._sequence_to_actions(mixed)
        self.assertIn("hotkey", str(cm.exception))
        # alias inequívoco: hotkey com só "key" vira combinação.
        alias = PlannerDecision(type="sequence", steps=[{"type": "hotkey", "key": "ctrl+l"}])
        self.assertEqual(loop._sequence_to_actions(alias)[0].key, "ctrl+l")

    def test_use_skill_gui_carrega_e_repergunta(self):
        import loop
        from planner import PlannerDecision

        calls = []

        class Fake:
            def next_action(
                self,
                goal,
                window,
                ui_names,
                history,
                last_error="",
                skills_catalog="",
                skill_context="",
            ):
                calls.append(skill_context)
                if len(calls) == 1:
                    return PlannerDecision(type="use_skill", skill="computer-use", args={}), 1.0
                return PlannerDecision(type="hotkey", keys="ctrl+l"), 1.0

        import state as st

        self._patch_snapshot([], "Edge", None)
        ctx = {"hist_labels": [], "task_state": st.init("g")}
        dec, tm = loop.decide("g", 1, ctx, {}, planner=Fake(), vocaela=object())
        # GUI nunca vira wait(0) executável: re-query age com a skill.
        self.assertEqual(dec.action.type, "hotkey")
        self.assertEqual(tm.get("skill_loaded"), "computer-use")
        self.assertIn("computer-use", ctx["skill_docs"])
        self.assertTrue(calls[1])  # 2ª pergunta levou o contexto da skill

    def test_use_skill_gui_reativar_vetado(self):
        import loop
        from planner import PlannerDecision

        class Fake:
            def next_action(
                self,
                goal,
                window,
                ui_names,
                history,
                last_error="",
                skills_catalog="",
                skill_context="",
            ):
                return PlannerDecision(type="use_skill", skill="computer-use", args={}), 1.0

        import state as st

        self._patch_snapshot([], "Edge", None)
        ctx = {"hist_labels": [], "task_state": st.init("g")}
        with self.assertRaises(RuntimeError) as cm:
            loop.decide("g", 1, ctx, {}, planner=Fake(), vocaela=object())
        self.assertIn("já está ativa", str(cm.exception))

    def test_http_pool_reusa_cliente(self):
        import http_pool as pool

        a = pool.client_for("http://127.0.0.1:9/v1", 5)
        b = pool.client_for("http://127.0.0.1:9/v1", 5)
        self.assertIs(a, b)
        self.assertIn("requests", list(pool.stats().values())[0])


class TestRunReal1909(unittest.TestCase):
    """Regressão do run real 19/09: focus('Google') em loop, sem abrir aba.

    O planner copiou o exemplo do spec e focus_window casava qualquer aba
    do Chrome pelo sufixo '- Google Chrome'. Trava os três fixes.
    """

    def _patch_snapshot(self, items, title, wrect):
        import loop

        orig = loop.active_window_snapshot
        loop.active_window_snapshot = lambda: (items, title, wrect)
        self.addCleanup(lambda: setattr(loop, "active_window_snapshot", orig))

    def test_browser_want(self):
        import loop

        self.assertEqual(loop._browser_want("abra o chrome e busque x")[0], "chrome")
        self.assertEqual(loop._browser_want("abra o edge")[0], "msedge")
        self.assertEqual(loop._browser_want("no brave, crie um cubo")[0], "brave")
        self.assertIsNone(loop._browser_want("abra o notepad"))

    def test_bootstrap_browser_step0_abre(self):
        from unittest.mock import patch

        import loop
        import tools

        with patch.object(tools, "focus_window", return_value=False):
            boot = loop._app_bootstrap("abra o chrome e pesquise x", 0, "windows powershell")
            self.assertIsNotNone(boot)
            self.assertEqual((boot.type, boot.target), ("open", "chrome"))
        # janela certa ativa: mão p/ os modelos
        self.assertIsNone(loop._app_bootstrap("abra o chrome", 2, "amazon - google chrome"))

    def test_focus_estrito_nao_casa_sufixo(self):
        import tools

        self.assertEqual(tools._page_part("atelier — editor - google chrome"), "atelier — editor")
        self.assertEqual(
            tools._match_score("atelier — editor - google chrome", "google"), -1
        )  # só sufixo: miss
        self.assertEqual(tools._match_score("google - google chrome", "google"), 3)
        self.assertGreaterEqual(tools._match_score("atelier - google chrome", "chrome"), 0)

    def test_antipia_google_recusado(self):
        import loop
        from planner import PlannerDecision

        class Fake:
            def next_action(self, goal, window, ui_names, history, last_error="", **kw):
                return PlannerDecision(type="focus_window", target="Google"), 1.0

        import state as st

        self._patch_snapshot([], "Atelier - Google Chrome", None)
        ctx = {
            "hist_labels": ["opened chrome => window 'Atelier'"],
            "task_state": st.init("abra o chrome e pesquise rtx"),
        }
        with self.assertRaises(RuntimeError) as cm:
            loop.decide(
                "abra o chrome e pesquise rtx", 3, ctx, {}, planner=Fake(), vocaela=object()
            )
        self.assertIn("EXEMPLO", str(cm.exception))

    def test_google_genuino_passa(self):
        import loop
        from planner import PlannerDecision

        class Fake:
            def next_action(self, goal, window, ui_names, history, last_error="", **kw):
                return PlannerDecision(type="focus_window", target="Google"), 1.0

        import state as st

        self._patch_snapshot([], "Google - Google Chrome", None)
        ctx = {
            "hist_labels": ["opened chrome => window 'Google'"],
            "task_state": st.init("pesquise no google o preço"),
        }
        dec, _ = loop.decide(
            "pesquise no google o preço", 3, ctx, {}, planner=Fake(), vocaela=object()
        )
        self.assertEqual(dec.action.type, "focus")


class TestAskEPicker(unittest.TestCase):
    """Human-in-the-loop (ask) + seletor de perfil sem adivinhação."""

    def _patch_snapshot(self, items, title, wrect):
        import loop

        orig = loop.active_window_snapshot
        loop.active_window_snapshot = lambda: (items, title, wrect)
        self.addCleanup(lambda: setattr(loop, "active_window_snapshot", orig))

    def test_sequence_com_open_app_vetado(self):
        # Regressão 19/09 22:58: ramo sequence inalcançável executava o
        # placeholder wait(0); open_app dentro de sequence deve vetar.
        import loop
        from planner import PlannerDecision

        class Fake:
            def next_action(self, goal, window, ui_names, history, last_error="", **kw):
                return PlannerDecision(
                    type="sequence",
                    steps=[{"type": "open_app", "app": "chrome", "target": "Google Chrome"}],
                ), 1.0

        import state as st

        self._patch_snapshot([], "Google Chrome", None)
        ctx = {
            "hist_labels": ["opened chrome => window 'Google Chrome'"],
            "task_state": st.init("abra o chrome e abra amazon"),
        }
        with self.assertRaises(RuntimeError) as cm:
            loop.decide("abra o chrome e abra amazon", 2, ctx, {}, planner=Fake(), vocaela=object())
        self.assertIn("proibido", str(cm.exception))

    def test_bootstrap_foca_existente_antes_de_abrir(self):
        from unittest.mock import patch

        import loop
        import tools

        with patch.object(tools, "focus_window", return_value=True) as fw:
            boot = loop._app_bootstrap("abra o chrome e pesquise x", 0, "windows powershell")
            self.assertEqual((boot.type, boot.ms), ("wait", 300))
            fw.assert_called_once_with("chrome", timeout=2.0)
        with patch.object(tools, "focus_window", return_value=False) as fw:
            boot = loop._app_bootstrap("abra o chrome e pesquise x", 0, "windows powershell")
            self.assertEqual((boot.type, boot.target), ("open", "chrome"))
            fw.assert_called_once_with("chrome", timeout=2.0)

    def test_detect_picker(self):
        import loop

        names = ["Button:Seu Chrome", "Static:Quem está usando o Chrome?", "Button:Modo visitante"]
        self.assertTrue(loop.detect_chrome_picker("Google Chrome", names))
        self.assertFalse(loop.detect_chrome_picker("Amazon - Google Chrome", ["Edit:Pesquisar"]))
        self.assertFalse(loop.detect_chrome_picker("Bloco de Notas", names))

    def test_picker_com_preferencia_clica_lembrado(self):
        from unittest.mock import patch

        import loop
        import state as st

        items = [
            {"id": 0, "name": "Seu Chrome", "type": "Button", "bounds": [10, 10, 100, 100]},
            {
                "id": 1,
                "name": "Quem está usando o Chrome?",
                "type": "Text",
                "bounds": [10, 200, 400, 240],
            },
        ]
        self._patch_snapshot(items, "Google Chrome", (0, 0, 800, 600))

        class Fake:
            def next_action(self, *a, **k):
                raise AssertionError("planner não deveria ser chamado")

        with patch("prefs.get", return_value="Seu Chrome"):
            dec, tm = loop.decide(
                "abra o chrome",
                2,
                {"hist_labels": [], "task_state": st.init("abra o chrome")},
                {},
                planner=Fake(),
                vocaela=object(),
            )
        self.assertEqual(dec.action.type, "click")
        self.assertIn("lembrado", dec.reason)

    def test_picker_sem_preferencia_pergunta_sem_chamar_modelo(self):
        from unittest.mock import patch

        import loop
        import state as st

        items = [
            {"id": 0, "name": "Doug", "type": "Button", "bounds": [10, 10, 100, 100]},
            {"id": 1, "name": "Modo visitante", "type": "Button", "bounds": [10, 200, 400, 240]},
        ]
        self._patch_snapshot(items, "Google Chrome", (0, 0, 800, 600))

        class Fake:
            def next_action(self, *a, **k):
                raise AssertionError("planner nao deveria ser chamado")

        with patch("prefs.get", return_value=""):
            dec, tm = loop.decide(
                "abra o chrome",
                2,
                {"hist_labels": [], "task_state": st.init("abra o chrome")},
                {},
                planner=Fake(),
                vocaela=object(),
            )
        self.assertTrue(tm["picker"])
        self.assertEqual(dec.action.type, "ask")
        self.assertIn("Doug", dec.action.text or "")

    def test_url_sem_ctrl_l_e_recusada(self):
        import loop
        import state as st
        from planner import PlannerDecision

        class Fake:
            def next_action(self, *a, **k):
                return PlannerDecision(
                    type="sequence",
                    steps=[
                        {"type": "hotkey", "keys": "ctrl+t"},
                        {"type": "type_text", "text": "https://amazon.com"},
                        {"type": "press_key", "key": "enter"},
                    ],
                ), 1.0

        self._patch_snapshot([], "Nova guia - Google Chrome", None)
        with self.assertRaises(RuntimeError) as cm:
            loop.decide(
                "abra o chrome e abra amazon",
                2,
                {"hist_labels": [], "task_state": st.init("abra amazon")},
                {},
                planner=Fake(),
                vocaela=object(),
            )
        self.assertIn("ctrl+l", str(cm.exception))

    def test_ask_vira_action_e_nao_conta_p_done(self):
        import loop
        from planner import PlannerDecision

        class Fake:
            def next_action(self, goal, window, ui_names, history, last_error="", **kw):
                return PlannerDecision(type="ask", text="qual perfil devo usar?"), 1.0

        import state as st

        self._patch_snapshot([], "Google Chrome", None)
        ctx = {
            "hist_labels": ["opened chrome => window 'Google Chrome'"],
            "task_state": st.init("abra o chrome"),
        }
        dec, _ = loop.decide("abra o chrome", 2, ctx, {}, planner=Fake(), vocaela=object())
        self.assertEqual(dec.action.type, "ask")
        self.assertIn("perfil", dec.action.text or "")
        self.assertFalse(loop.done_allowed(["ask(x) => humano: y"]))

    def test_do_ask_dry_run_e_sem_humano(self):
        import builtins

        import loop

        self.assertIn("dry_run", loop.do_ask("qual?", {}, dry_run=True))
        orig = builtins.input
        builtins.input = lambda *a: (_ for _ in ()).throw(EOFError())
        try:
            self.assertIn("sem resposta", loop.do_ask("qual?", {}))
        finally:
            builtins.input = orig

    def test_prefs_roundtrip_e_contexto(self):
        import tempfile
        from unittest.mock import patch

        import prefs

        with tempfile.TemporaryDirectory() as td:
            with patch.object(prefs, "PATH", Path(td) / "p.json"):
                self.assertEqual(prefs.context_line(), "")
                prefs.remember("browser_profile", "Seu Chrome")
                self.assertEqual(prefs.get("browser_profile"), "Seu Chrome")
                self.assertIn("Seu Chrome", prefs.context_line())


class TestSequenceRecipe(unittest.TestCase):
    """Run 19/09 23:12: 1B inventou ctrl+alt+n p/ nova aba (correta: ctrl+t)."""

    def test_receita_tem_ctrl_t(self):
        import planner

        blob = planner.PLANNER_SYSTEM + planner.TOOLS_SPEC
        self.assertIn("ctrl+t", blob)
        self.assertIn("ctrl+l", blob)

    def test_steps_label(self):
        import loop
        from schemas import Action

        self.assertEqual(
            loop._steps_label(
                [{"type": "hotkey", "keys": "ctrl+t"}, {"type": "type_text", "text": "amazon"}]
            ),
            "hotkey(ctrl+t)+type_text(amazon)",
        )
        self.assertEqual(
            loop._steps_label([Action(type="hotkey", key="ctrl+l"), Action(type="wait", ms=500)]),
            "hotkey(ctrl+l)+wait(500)",
        )

    def test_verify_action_resume_combinacoes(self):
        import loop
        from schemas import Action, Decision

        dec = Decision(
            action=Action(type="hotkey", key="ctrl+alt+n"),
            source="planner",
            kind="sequence",
            steps=[Action(type="hotkey", key="ctrl+alt+n")],
        )
        va = loop._verify_action(dec)
        self.assertEqual((va.type, va.key), ("hotkey", "ctrl+alt+n"))
        plain = Decision(action=Action(type="type", text="oi"), source="planner")
        self.assertIs(loop._verify_action(plain).type, "type")

    def test_observe_hotkey_sem_efeito_aponta_receita(self):
        import loop
        from schemas import Action

        o = loop.observe(Action(type="hotkey", key="ctrl+alt+n"), "Aba X", "Aba X")
        self.assertIn("no visible effect", o)
        self.assertIn("ctrl+t", o)
        o2 = loop.observe(Action(type="hotkey", key="ctrl+t"), "Aba X", "Nova guia")
        self.assertNotIn("no visible effect", o2)

    def test_repeat_note_cita_conteudo_da_sequence(self):
        import loop
        from schemas import Action, Decision

        dec = Decision(
            action=Action(type="hotkey", key="ctrl+alt+n"),
            source="planner",
            kind="sequence",
            steps=[Action(type="hotkey", key="ctrl+alt+n")],
        )
        note = loop._repeat_note(dec.action, dec)
        self.assertIn("ctrl+alt+n", note)
        self.assertIn("mude as TECLAS", note)


if __name__ == "__main__":
    unittest.main(verbosity=2)
