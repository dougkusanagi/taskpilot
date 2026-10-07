"""Runtime próprio no Linux: descoberta do llama-server, download de fallback e flags.

Offline: nada de rede nem de GPU. Os binários são scripts de shell falsos em diretórios
temporários, por isso os testes que executam um "binário" só rodam fora do Windows.
"""
import stat
import sys
import tarfile
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import server

POSIX = sys.platform != "win32"


def fake_binary(path: Path, ok: bool = True) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    body = "#!/bin/sh\necho 'version: fake'\n" if ok else (
        "#!/bin/sh\necho 'error while loading shared libraries: libcudart.so.12' >&2\nexit 127\n")
    path.write_text(body)
    path.chmod(path.stat().st_mode | stat.S_IXUSR)
    return path


class TestUrlsAndClassification(unittest.TestCase):
    def test_linux_urls_sao_tar_gz_e_cuda_traz_cudart(self):
        with patch.object(server.sys, "platform", "linux"):
            cpu = server.llama_urls("cpu")
            vk = server.llama_urls("vulkan")
            cuda = server.llama_urls("cuda")
        self.assertEqual(len(cpu), 1)
        self.assertTrue(cpu[0].endswith("bin-ubuntu-x64.tar.gz"))
        self.assertTrue(vk[0].endswith("bin-ubuntu-vulkan-x64.tar.gz"))
        self.assertEqual(len(cuda), 2)
        self.assertIn("ubuntu-cuda-12.8-x64", cuda[0])
        self.assertTrue(cuda[1].startswith("https://github.com/ggml-org/llama.cpp/releases/"))
        self.assertIn("cudart", cuda[1])

    def test_windows_continua_zip(self):
        with patch.object(server.sys, "platform", "win32"):
            self.assertTrue(server.llama_urls("cpu")[0].endswith("bin-win-cpu-x64.zip"))

    def test_classifica_pastas_do_lmstudio(self):
        c = server._classify_lmstudio
        self.assertEqual(c("llama.cpp-linux-x86_64-nvidia-cuda12-avx2-2.51.0"),
                         ("cuda12", (2, 51, 0)))
        self.assertEqual(c("llama.cpp-linux-x86_64-nvidia-cuda-avx2-2.41.0"), ("cuda", (2, 41, 0)))
        self.assertEqual(c("llama.cpp-linux-x86_64-vulkan-avx2-2.51.0"), ("vulkan", (2, 51, 0)))
        self.assertEqual(c("llama.cpp-linux-x86_64-avx2-2.51.0"), ("cpu", (2, 51, 0)))
        self.assertIsNone(c("vendor"))
        self.assertIsNone(c("llama.cpp-windows-x86_64-avx2-1.0.0"))


@unittest.skipUnless(POSIX, "scripts de shell falsos")
class TestDiscovery(unittest.TestCase):
    def setUp(self):
        self._td = tempfile.TemporaryDirectory()
        self.root = Path(self._td.name)
        self.addCleanup(self._td.cleanup)

    def mk(self, name: str, ok: bool = True) -> Path:
        return fake_binary(self.root / name / "llama-server", ok)

    def test_cuda_prefere_cuda12_e_a_versao_mais_nova(self):
        self.mk("llama.cpp-linux-x86_64-nvidia-cuda-avx2-2.51.0")
        old = self.mk("llama.cpp-linux-x86_64-nvidia-cuda12-avx2-2.49.0")
        new = self.mk("llama.cpp-linux-x86_64-nvidia-cuda12-avx2-2.51.0")
        self.mk("llama.cpp-linux-x86_64-vulkan-avx2-2.51.0")
        got = server._lmstudio_candidates("cuda", root=self.root)
        self.assertEqual(got[0], new)
        self.assertEqual(got[1], old)
        self.assertEqual(len(got), 3)  # cuda11 por último; vulkan/cpu nunca servem a "cuda"

    def test_vulkan_e_cpu_so_pegam_o_proprio_tipo(self):
        vk = self.mk("llama.cpp-linux-x86_64-vulkan-avx2-2.51.0")
        cpu = self.mk("llama.cpp-linux-x86_64-avx2-2.51.0")
        self.assertEqual(server._lmstudio_candidates("vulkan", root=self.root), [vk])
        self.assertEqual(server._lmstudio_candidates("cpu", root=self.root), [cpu])

    def test_pasta_sem_binario_e_raiz_inexistente_nao_quebram(self):
        (self.root / "llama.cpp-linux-x86_64-avx2-9.9.9").mkdir()
        self.assertEqual(server._lmstudio_candidates("cpu", root=self.root), [])
        self.assertEqual(server._lmstudio_candidates("cpu", root=self.root / "nao-existe"), [])

    def test_env_inclui_libs_ao_lado_e_o_vendor_cuda12(self):
        exe = self.mk("backends/llama.cpp-linux-x86_64-nvidia-cuda12-avx2-2.51.0")
        vendor = self.root / "backends" / "vendor" / "linux-llama-cuda12-vendor-v1"
        vendor.mkdir(parents=True)
        env = server._runtime_env(exe)
        dirs = env["LD_LIBRARY_PATH"].split(":")
        self.assertEqual(dirs[0], str(exe.parent))
        self.assertIn(str(vendor), dirs)

    def test_env_no_windows_e_none(self):
        with patch.object(server.sys, "platform", "win32"):
            self.assertIsNone(server._runtime_env(Path("x/llama-server.exe")))

    def test_probe_distingue_binario_que_roda_do_que_nao_carrega_libs(self):
        good = self.mk("a")
        bad = self.mk("b", ok=False)
        self.assertEqual(server._probe_binary(good), "")
        self.assertIn("libcudart", server._probe_binary(bad))
        self.assertTrue(server._probe_binary(self.root / "nao-existe"))


@unittest.skipUnless(POSIX, "scripts de shell falsos")
class TestEnsureLlamaLinux(unittest.TestCase):
    def setUp(self):
        self._td = tempfile.TemporaryDirectory()
        self.root = Path(self._td.name)
        self.addCleanup(self._td.cleanup)
        bin_dir = self.root / "bin"
        for target, value in (("MODELS_DIR", self.root), ("BIN_DIR", bin_dir),
                              ("LLAMA_BIN", bin_dir / "llama-server"),
                              ("BACKEND_MARK", bin_dir / ".backend"),
                              ("_LMSTUDIO_BACKENDS", self.root / "lmstudio")):
            p = patch.object(server, target, value)
            p.start()
            self.addCleanup(p.stop)

    def test_caminho_explicito_vence_e_precisa_executar(self):
        exe = fake_binary(self.root / "meu" / "llama-server")
        got = server._ensure_llama_linux({"runtime": {"llama_server": str(exe)}},
                                         progress=lambda *_: None)
        self.assertEqual(got, (exe, "cpu"))
        with self.assertRaisesRegex(RuntimeError, "inexistente"):
            server._ensure_llama_linux({"runtime": {"llama_server": str(self.root / "x")}},
                                       progress=lambda *_: None)
        bad = fake_binary(self.root / "ruim" / "llama-server", ok=False)
        with self.assertRaisesRegex(RuntimeError, "não executa"):
            server._ensure_llama_linux({"runtime": {"llama_server": str(bad)}},
                                       progress=lambda *_: None)

    def test_reusa_o_do_lmstudio_sem_baixar_nada(self):
        exe = fake_binary(self.root / "lmstudio" / "llama.cpp-linux-x86_64-avx2-2.51.0"
                          / "llama-server")
        with patch.object(server, "_download") as dl, patch.dict("os.environ", {}, clear=False):
            got = server._ensure_llama_linux({"runtime": {"backend": "cpu"}},
                                             progress=lambda *_: None)
        self.assertEqual(got, (exe, "cpu"))
        dl.assert_not_called()

    def test_candidato_que_nao_carrega_e_pulado_para_o_proximo(self):
        broken = fake_binary(self.root / "lmstudio" / "llama.cpp-linux-x86_64-nvidia-cuda12-avx2"
                             "-2.51.0" / "llama-server", ok=False)
        fine = fake_binary(self.root / "lmstudio" / "llama.cpp-linux-x86_64-nvidia-cuda12-avx2"
                           "-2.50.0" / "llama-server")
        notes = []
        got = server._ensure_llama_linux({"runtime": {"backend": "cuda"}}, progress=notes.append)
        self.assertEqual(got, (fine, "cuda"))
        self.assertTrue(any(str(broken) in n and "libcudart" in n for n in notes))

    def test_sem_nada_local_baixa_o_release_e_extrai(self):
        archive = self.root / "src.tar.gz"
        pkg = self.root / "pkg" / "llama-b1"
        fake_binary(pkg / "llama-server")
        (pkg / "libggml.so").write_text("x")
        with tarfile.open(archive, "w:gz") as tf:
            tf.add(pkg, arcname="llama-b1")

        def fake_download(url, dest, **_k):
            dest.write_bytes(archive.read_bytes())
            return dest

        with patch.object(server, "_download", side_effect=fake_download) as dl, \
                patch.object(server.shutil, "which", return_value=None):
            exe, backend = server._ensure_llama_linux({"runtime": {"backend": "cpu"}},
                                                      progress=lambda *_: None)
        self.assertEqual(backend, "cpu")
        self.assertEqual(exe, server.LLAMA_BIN)
        self.assertTrue((server.BIN_DIR / "libggml.so").exists())  # libs ao lado do binário
        self.assertEqual(server.BACKEND_MARK.read_text().strip(), "cpu")
        self.assertIn("ubuntu-x64.tar.gz", dl.call_args.args[0])

    def test_backend_invalido_falha_alto(self):
        with self.assertRaises(ValueError):
            server._ensure_llama_linux({"runtime": {"backend": "metal"}}, progress=print)


@unittest.skipUnless(POSIX, "scripts de shell falsos")
class TestEnsureAssetsLinux(unittest.TestCase):
    def test_nao_recusa_mais_no_linux_e_devolve_o_binario_resolvido(self):
        with tempfile.TemporaryDirectory() as td:
            td = Path(td)
            exe = fake_binary(td / "llama-server")
            with patch.object(server, "MODELS_DIR", td), \
                    patch.object(server, "_download",
                                 lambda url, dest, **k: dest.write_bytes(b"g")), \
                    patch.object(server.sys, "platform", "linux"), \
                    patch.object(server, "_ensure_llama_linux", return_value=(exe, "cuda")):
                assets = server.ensure_assets(progress=lambda *_: None, cfg={})
            self.assertEqual(assets["exe"], exe)
            self.assertEqual(server._RESOLVED["backend"], "cuda")

    def test_server_args_usa_o_binario_resolvido(self):
        args = server._server_args("planner", Path("p.gguf"), None, 8091, {"runtime": {}},
                                   exe=Path("/opt/llama/llama-server"))
        self.assertEqual(args[0], "/opt/llama/llama-server")

    def test_gpu_status_le_o_backend_resolvido(self):
        with patch.dict(server._RESOLVED, {"exe": Path("x"), "backend": "cuda"}):
            self.assertFalse(server.gpu_status({"runtime": {"ngl": 99}})["cpu_only_binary"])
        with patch.dict(server._RESOLVED, {"exe": Path("x"), "backend": "cpu"}):
            self.assertTrue(server.gpu_status({"runtime": {"ngl": 99}})["warn"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
