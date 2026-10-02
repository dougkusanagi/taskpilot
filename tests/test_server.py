"""Runtime próprio dos modelos (server.py + loop._ensure_local_servers).

Tudo offline: _endpoint_alive/_port_in_use/ensure_assets/_spawn_one são
mockados; nenhum download ou llama-server é tocado.

Roda com: uv run python -m unittest discover -s tests -v
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import config as cfgmod  # noqa: E402
import server  # noqa: E402


class TestNeedsLocalServe(unittest.TestCase):
    def test_localhost_8091_8082(self):
        self.assertTrue(server.needs_local_serve(
            ("http://127.0.0.1:8091/v1", "http://127.0.0.1:8082/v1")))

    def test_localhost_sem_porta_ainda_e_local(self):
        self.assertTrue(server.needs_local_serve(
            ("http://localhost:8091/v1", "http://127.0.0.1:9999/v1")))

    def test_remoto_nao_sobe_nada(self):
        self.assertFalse(server.needs_local_serve(
            ("http://192.168.1.10:8091/v1", "http://192.168.1.10:8082/v1")))

    def test_misto_nao_sobe(self):
        self.assertFalse(server.needs_local_serve(
            ("http://127.0.0.1:8091/v1", "http://192.168.1.10:8082/v1")))

    def test_vazio_nao_sobe(self):
        self.assertFalse(server.needs_local_serve(("", "")))


class TestSplitHostPort(unittest.TestCase):
    def test_padrao(self):
        self.assertEqual(server._split_host_port("http://127.0.0.1:8091/v1"),
                         ("127.0.0.1", 8091))

    def test_sem_scheme(self):
        host, port = server._split_host_port("127.0.0.1:8082")
        self.assertEqual((host, port), ("127.0.0.1", 8082))


class TestServerArgs(unittest.TestCase):
    def _cfg(self, **kw):
        rt = {"host": "127.0.0.1", "ngl": 0, "threads": 4, "ctx": 4096}
        rt.update(kw)
        return {"runtime": rt}

    def test_planner_tem_jinja_sem_mmproj(self):
        args = server._server_args("planner", Path("p.gguf"), None, 8091, self._cfg())
        self.assertIn("--jinja", args)
        self.assertNotIn("--mmproj", args)
        self.assertIn("8091", args)

    def test_vision_tem_mmproj_sem_jinja(self):
        args = server._server_args("vision", Path("v.gguf"), Path("m.gguf"),
                                   8082, self._cfg())
        self.assertIn("--mmproj", args)
        self.assertNotIn("--jinja", args)

    def test_threads_zero_vira_default(self):
        args = server._server_args("planner", Path("p.gguf"), None, 8091,
                                   self._cfg(threads=0))
        i = args.index("-t") + 1
        self.assertEqual(int(args[i]), server.DEFAULT_THREADS)


class TestEnsureServers(unittest.TestCase):
    URLS = ("http://127.0.0.1:8091/v1", "http://127.0.0.1:8082/v1")

    def test_reusa_endpoint_vivo_sem_baixar(self):
        with patch.object(server, "_endpoint_alive",
                          return_value={"models": ["m"]}), \
             patch.object(server, "ensure_assets") as assets, \
             patch.object(server, "_spawn_one") as spawn:
            out = server.ensure_servers(self.URLS, {}, progress=lambda *a: None)
        self.assertEqual(out, {"planner": None, "vision": None})
        assets.assert_not_called()
        spawn.assert_not_called()

    def test_porta_ocupada_sem_endpoint_e_erro_honesto(self):
        with patch.object(server, "_endpoint_alive", return_value=None), \
             patch.object(server, "_port_in_use", return_value=True):
            with self.assertRaises(RuntimeError) as cm:
                server.ensure_servers(self.URLS, {}, progress=lambda *a: None)
        self.assertIn("ocupada", str(cm.exception))

    def test_porta_livre_baixa_e_sobe(self):
        sentinel = object()
        with patch.object(server, "_endpoint_alive", return_value=None), \
             patch.object(server, "_port_in_use", return_value=False), \
             patch.object(server, "ensure_assets",
                          return_value={"exe": "e"}) as assets, \
             patch.object(server, "_spawn_one",
                          return_value=sentinel) as spawn:
            out = server.ensure_servers(self.URLS, {}, progress=lambda *a: None)
        assets.assert_called_once()
        self.assertEqual(spawn.call_count, 2)
        self.assertIs(out["planner"], sentinel)
        self.assertIs(out["vision"], sentinel)

    def test_stop_servers_so_mata_os_nossos(self):
        killed: list[str] = []

        class FakeProc:
            def __init__(self, name):
                self.name = name

            def terminate(self):
                killed.append(self.name)

            def wait(self, timeout=None):
                pass

        server.stop_servers({"planner": None, "vision": FakeProc("vision")})
        self.assertEqual(killed, ["vision"])


class TestWaitAlive(unittest.TestCase):
    class FakeProc:
        def __init__(self, rc=None):
            self._rc = rc
            self.killed = False

        def poll(self):
            return self._rc

        def kill(self):
            self.killed = True

    def test_responde_na_terceira_tentativa_sem_matar(self):
        proc = self.FakeProc(rc=None)
        calls = {"n": 0}

        def fake_alive(base_url, timeout_s=5.0):
            calls["n"] += 1
            return {"models": ["m"]} if calls["n"] >= 3 else None

        with patch.object(server, "_endpoint_alive", side_effect=fake_alive):
            alive = server._wait_alive("planner", "http://x/v1", proc,
                                       Path("f.log"), timeout_s=60, poll_s=0.01,
                                       progress=lambda *a: None)
        self.assertEqual(alive, {"models": ["m"]})
        self.assertEqual(calls["n"], 3)
        self.assertFalse(proc.killed)

    def test_saida_precoce_mostra_causa_sem_matar(self):
        proc = self.FakeProc(rc=1)
        with patch.object(server, "_endpoint_alive", return_value=None), \
             patch.object(server, "_tail", return_value="GGUF corrompido"):
            with self.assertRaises(RuntimeError) as cm:
                server._wait_alive("planner", "http://x/v1", proc,
                                   Path("f.log"), timeout_s=60, poll_s=0.01,
                                   progress=lambda *a: None)
        self.assertIn("saiu cedo", str(cm.exception))
        self.assertIn("GGUF corrompido", str(cm.exception))
        self.assertFalse(proc.killed)  # já morto: nada a matar

    def test_timeout_mata_e_diz_o_deadline(self):
        proc = self.FakeProc(rc=None)
        with patch.object(server, "_endpoint_alive", return_value=None):
            with self.assertRaises(RuntimeError) as cm:
                server._wait_alive("vision", "http://x/v1", proc,
                                   Path("f.log"), timeout_s=0.05, poll_s=0.01,
                                   progress=lambda *a: None)
        self.assertIn("não respondeu", str(cm.exception))
        self.assertIn("startup_timeout_s", str(cm.exception))
        self.assertTrue(proc.killed)


class TestLoopEnsureLocal(unittest.TestCase):
    URLS = ("http://127.0.0.1:8091/v1", "http://127.0.0.1:8082/v1")

    def test_auto_start_false_nao_faz_nada(self):
        from loop import _ensure_local_servers

        cfg = {"runtime": {"auto_start": False},
               "planner": {"base_url": self.URLS[0]},
               "vision": {"base_url": self.URLS[1]}}
        with patch.object(server, "ensure_servers") as es:
            out = _ensure_local_servers(cfg)
        self.assertEqual(out, {})
        es.assert_not_called()

    def test_url_remota_nao_baixa(self):
        from loop import _ensure_local_servers

        cfg = {"runtime": {"auto_start": True},
               "planner": {"base_url": "http://192.168.1.10:8091/v1"},
               "vision": {"base_url": "http://192.168.1.10:8082/v1"}}
        with patch.object(server, "ensure_servers") as es:
            out = _ensure_local_servers(cfg)
        self.assertEqual(out, {})
        es.assert_not_called()

    def test_erro_do_runtime_vira_runtimeerror(self):
        from loop import _ensure_local_servers

        cfg = {"runtime": {"auto_start": True},
               "planner": {"base_url": "http://127.0.0.1:8091/v1"},
               "vision": {"base_url": "http://127.0.0.1:8082/v1"}}
        with patch.object(server, "ensure_servers",
                          side_effect=RuntimeError("porta ocupada")):
            with self.assertRaises(RuntimeError):
                _ensure_local_servers(cfg)


class TestRuntimeDefaults(unittest.TestCase):
    def test_config_traz_runtime(self):
        cfg = cfgmod.load("nao-existe.json")
        rt = cfg.get("runtime", {})
        self.assertTrue(rt.get("auto_start", False))
        self.assertEqual(rt.get("host"), "127.0.0.1")
        self.assertIn("ctx", rt)


if __name__ == "__main__":
    unittest.main(verbosity=2)


class TestBackendsAndMemoryFlags(unittest.TestCase):
    def cfg(self, **runtime):
        return {"runtime": runtime}

    def test_urls_per_backend(self):
        cpu = server.llama_urls("cpu")
        self.assertEqual(len(cpu), 1)
        self.assertTrue(cpu[0].endswith("bin-win-cpu-x64.zip"))
        self.assertTrue(server.llama_urls("vulkan")[0].endswith("bin-win-vulkan-x64.zip"))
        cuda = server.llama_urls("cuda")
        self.assertEqual(len(cuda), 2)  # binário + cudart
        self.assertIn("cudart", cuda[1])
        with self.assertRaises(ValueError):
            server.llama_urls("metal")

    def test_gpu_backend_offloads_by_default_but_respects_explicit_ngl(self):
        args = server._server_args("planner", Path("p.gguf"), None, 8091,
                                   self.cfg(backend="vulkan"))
        self.assertEqual(args[args.index("-ngl") + 1], "99")
        args = server._server_args("planner", Path("p.gguf"), None, 8091,
                                   self.cfg(backend="vulkan", ngl=20))
        self.assertEqual(args[args.index("-ngl") + 1], "20")
        args = server._server_args("planner", Path("p.gguf"), None, 8091, self.cfg())
        self.assertEqual(args[args.index("-ngl") + 1], "0")

    def test_memory_flags_are_opt_in(self):
        base = server._server_args("vision", Path("v.gguf"), Path("m.gguf"), 8082, self.cfg())
        for flag in ("-np", "-ctk", "--no-mmproj-offload"):
            self.assertNotIn(flag, base)
        args = server._server_args("vision", Path("v.gguf"), Path("m.gguf"), 8082,
                                   self.cfg(parallel=1, kv_cache="q8_0", mmproj_offload=False))
        self.assertEqual(args[args.index("-np") + 1], "1")
        self.assertEqual(args[args.index("-ctk") + 1], "q8_0")
        self.assertEqual(args[args.index("-ctv") + 1], "q8_0")
        self.assertEqual(args[args.index("-fa") + 1], "on")
        self.assertIn("--no-mmproj-offload", args)
        with self.assertRaises(ValueError):
            server._server_args("planner", Path("p.gguf"), None, 8091, self.cfg(kv_cache="q2"))


class TestRegistryAndProfiles(unittest.TestCase):
    def test_registry_resolves_model_names_loosely(self):
        for name in ("MAI-UI-2B", "mai_ui_2b", " Mai-UI-2B "):
            self.assertIs(server.registry_entry(name), server.GGUF_REGISTRY["mai-ui-2b"])
        self.assertIsNotNone(server.registry_entry("Qwen3-4B-Instruct-2507"))
        self.assertIsNone(server.registry_entry("MiniCPM5-2B"))

    def test_every_registry_file_has_a_public_url_and_a_distinct_name(self):
        names = set()
        for key, entry in server.GGUF_REGISTRY.items():
            for part in ("gguf", "mmproj"):
                if part in entry:
                    self.assertTrue(entry[part]["url"].startswith("https://huggingface.co/"), key)
                    self.assertTrue(entry[part]["file"].endswith(".gguf"), key)
                    self.assertNotIn(entry[part]["file"], names)
                    names.add(entry[part]["file"])

    def downloads(self, cfg):
        got = []
        with patch.object(server.sys, "platform", "win32"), \
                patch.object(server, "_download", lambda url, dest, **k: got.append(dest.name)), \
                patch.object(Path, "exists", lambda self: self.name == "llama-server.exe"):
            server.ensure_assets(progress=lambda *_: None, cfg=cfg)
        return got

    def test_dual_profile_downloads_planner_and_vision_with_projector(self):
        cfg = cfgmod.apply_profile({"runtime": {}}, "P1")
        got = self.downloads(cfg)
        self.assertEqual(sorted(got), sorted([
            "Qwen3-4B-Instruct-2507-Q4_K_M.gguf", "MAI-UI-2B.Q5_K_S.gguf",
            "MAI-UI-2B.mmproj-f16.gguf"]))

    def test_unified_profile_downloads_one_model_and_its_projector(self):
        got = self.downloads(cfgmod.apply_profile({"runtime": {}}, "U3"))
        self.assertEqual(sorted(got), sorted([
            "Qwen3-VL-4B-Instruct-Q4_K_M.gguf", "mmproj-Qwen3-VL-4B-Instruct-F16.gguf"]))

    def test_legacy_profiles_still_pick_their_old_files(self):
        got = self.downloads(cfgmod.apply_profile({"runtime": {}}, "B1"))
        self.assertIn("MiniCPM5-2B-Q4_K_M.gguf", got)
        self.assertIn("Vocaela-2-500M-1024R2-Q8_0.gguf", got)

    def test_profile_presets_apply_memory_flags_features_and_protocol(self):
        cfg = cfgmod.apply_profile({}, "P1")
        self.assertEqual(cfg["planner"]["features"], ["dynschema", "fewshot", "plan"])
        self.assertEqual(cfg["vision"]["protocol"], "pyauto")
        rt = cfg["runtime"]
        self.assertEqual((rt["kv_cache"], rt["mmproj_offload"], rt["parallel"], rt["ctx"]),
                         ("q8_0", False, 1, 4096))
        args = server._server_args("vision", Path("v.gguf"), Path("m.gguf"), 8082, cfg)
        for flag in ("--no-mmproj-offload", "-np", "-ctk", "-fa"):
            self.assertIn(flag, args)
        self.assertEqual(args[args.index("-ngl") + 1], "99")  # backend cuda => offload

    def test_preset_is_copied_not_shared_between_runs(self):
        a = cfgmod.apply_profile({}, "U3")
        a["planner"]["features"].append("tools")
        b = cfgmod.apply_profile({}, "U3")
        self.assertEqual(b["planner"]["features"], ["dynschema", "fewshot", "plan"])

    def test_legacy_profiles_have_no_preset(self):
        cfg = cfgmod.apply_profile({}, "B1")
        self.assertNotIn("features", cfg["planner"])
        self.assertNotIn("backend", cfg.get("runtime", {}))


class TestPlatformGuard(unittest.TestCase):
    def test_non_windows_fails_before_downloading_anything(self):
        calls = []
        with patch.object(server.sys, "platform", "linux"), \
                patch.object(server, "_download", lambda *a, **k: calls.append(a)):
            with self.assertRaisesRegex(RuntimeError, "só funcionam no Windows"):
                server.ensure_assets(progress=lambda *_: None, cfg={})
        self.assertEqual(calls, [])
