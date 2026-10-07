"""Plano Sandbox (docs/sandbox-test-env.md): artefatos testaveis.

Cobre o que o plano promete sem exigir Sandbox, admin ou modelos:
  - scripts/Start-Sandbox.ps1 gera .wsb valido e nao exige elevacao
  - sandbox/bootstrap.ps1 instala Python+uv e preenche HOST_IP sozinho
  - sandbox/config.sandbox.example.json aponta p/ HOST_IP:8091/8082
  - config.sandbox.json + crr.local.wsb ignorados, exemplos versionados
  - main.py aceita --config/--max-steps/--self-test (loop diario do doc)
  - salvaguardas: FAILSAFE + hotkey ctrl+alt+esc
  - docs referencia todos os artefatos (sem link quebrado)

Roda com: uv run python -m unittest discover -s tests -v
"""
from __future__ import annotations

import json
import subprocess
import sys
import unittest
import xml.etree.ElementTree as ET
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

LAUNCHER = ROOT / "scripts" / "Start-Sandbox.ps1"
INVOKER = ROOT / "scripts" / "Invoke-SandboxTest.ps1"
BOOTSTRAP = ROOT / "sandbox" / "bootstrap.ps1"
AGENT = ROOT / "sandbox" / "agent.ps1"
DOC = ROOT / "docs" / "sandbox-test-env.md"
SBX_EXAMPLE = ROOT / "sandbox" / "config.sandbox.example.json"
GITIGNORE = ROOT / ".gitignore"


def _render_template(ps1_path: Path, memory: int = 4096) -> str:
    src = ps1_path.read_text(encoding="utf-8")
    start = src.index("$Template = @'") + len("$Template = @'")
    end = src.index("'@", start)
    tpl = src[start:end].strip()
    return (tpl.replace("__REPO__", str(ROOT))
               .replace("__JOB__", str(ROOT / ".sandbox-job" / "x"))
               .replace("__MEMORY__", str(memory)))


def _render_wsb(memory: int = 4096) -> str:
    return _render_template(LAUNCHER, memory)


class TestSandboxFiles(unittest.TestCase):
    def test_arquivos_existem(self):
        for p in (LAUNCHER, INVOKER, BOOTSTRAP, AGENT, DOC, SBX_EXAMPLE):
            self.assertTrue(p.is_file(), f"faltando: {p.name}")

    def test_example_valido_e_aponta_p_host(self):
        cfg = json.loads(SBX_EXAMPLE.read_text(encoding="utf-8"))
        self.assertIn("HOST_IP", cfg["planner"]["base_url"])
        self.assertIn("8091", cfg["planner"]["base_url"])
        self.assertIn("HOST_IP", cfg["vision"]["base_url"])
        self.assertIn("8082", cfg["vision"]["base_url"])
        self.assertEqual(cfg["max_steps"], 4)  # doc: comece com 4
        self.assertEqual(cfg["screenshot_max_width"], 1024)
        self.assertEqual(cfg["stop_hotkey"], "ctrl+alt+esc")

    def test_example_carrega_via_config_py(self):
        import config as cfgmod

        cfg = cfgmod.load(SBX_EXAMPLE)  # merge com defaults, sem quebrar
        self.assertEqual(cfg["max_steps"], 4)
        self.assertEqual(cfg["stop_hotkey"], "ctrl+alt+esc")

    def test_gitignore(self):
        ign = GITIGNORE.read_text(encoding="utf-8").splitlines()
        self.assertIn("config.sandbox.json", ign)
        self.assertIn("sandbox/crr.local.wsb", ign)
        self.assertIn("sandbox/crr-agent.local.wsb", ign)
        self.assertIn(".sandbox-job/", ign)
        # o exemplo PRECISA ser versionado (bootstrap copia p/ config)
        self.assertNotIn("sandbox/config.sandbox.example.json", ign)

    def test_sem_restos_da_vm(self):
        for p in (ROOT / "scripts" / "New-TestVm.ps1",
                  ROOT / "scripts" / "Reset-TestVm.ps1",
                  ROOT / "docs" / "hyper-v-test-vm.md",
                  ROOT / "config.vm.example.json"):
            self.assertFalse(p.exists(), f"resto da VM: {p.name}")
        ign = GITIGNORE.read_text(encoding="utf-8").splitlines()
        self.assertNotIn("config.vm.json", ign)


class TestSandboxScripts(unittest.TestCase):
    def test_launcher_sem_admin_e_com_template(self):
        src = LAUNCHER.read_text(encoding="utf-8")
        self.assertNotIn("#Requires -RunAsAdministrator", src)
        self.assertIn("SupportsShouldProcess", src)  # -WhatIf
        for needle in ("crr.local.wsb", "MappedFolder", "SandboxFolder",
                       "wsb start", "wsb exec", "ExistingLogin",
                       "bootstrap.ps1", "MemoryInMB",
                       "__REPO__", "__MEMORY__", "8091", "8082",
                       "-OpenModelPorts"):
            self.assertIn(needle, src, f"trecho ausente: {needle}")

    def test_wsb_template_e_xml_valido(self):
        xml = _render_wsb()
        root = ET.fromstring(xml)
        self.assertEqual(root.tag, "Configuration")
        host = root.find("./MappedFolders/MappedFolder/HostFolder")
        self.assertEqual(host.text, str(ROOT))
        self.assertIsNone(root.find("./LogonCommand"))
        mem = root.find("./MemoryInMB").text
        self.assertEqual(mem, "4096")

    def test_bootstrap_instala_e_configura(self):
        src = BOOTSTRAP.read_text(encoding="utf-8")
        for needle in ("winget", "Python.Python.3.12", "astral-sh.uv",
                       "uv sync", "Get-NetRoute", "HOST_IP",
                       "config.sandbox.example.json", "config.sandbox.json",
                       "--self-test", "--max-steps 4",
                       "UV_PROJECT_ENVIRONMENT", "UV_CACHE_DIR"):
            self.assertIn(needle, src, f"trecho ausente: {needle}")
        # venv fora do repo mapeado: um `uv sync` em C:\crr sobrescreveria
        # o .venv do host. LogonCommand nao dispara (AGENTS.md) -> nem citar.
        self.assertNotIn("LogonCommand", src)

    def test_powershell_parse_sem_executar(self):
        """Parser oficial do PS: erro de sintaxe falha aqui, sem Sandbox."""
        for ps1 in (LAUNCHER, INVOKER, BOOTSTRAP, AGENT):
            cmd = (
                "$errs=$null; $toks=$null; "
                "[void][System.Management.Automation.Language.Parser]::"
                f"ParseFile('{ps1}', [ref]$toks, [ref]$errs); "
                "$errs.Count"
            )
            try:
                out = subprocess.run(
                    ["powershell", "-NoProfile", "-Command", cmd],
                    capture_output=True, text=True, timeout=30)
            except (FileNotFoundError, subprocess.SubprocessError) as e:
                self.skipTest(f"powershell indisponivel: {e}")
            self.assertEqual(out.returncode, 0, out.stderr[:500])
            self.assertEqual(out.stdout.strip(), "0",
                             f"erro de sintaxe em {ps1.name}: "
                             f"{out.stdout}{out.stderr[:500]}")


class TestSandboxAgent(unittest.TestCase):
    def test_host_runner_sem_admin_e_protocolo(self):
        src = INVOKER.read_text(encoding="utf-8")
        self.assertNotIn("#Requires -RunAsAdministrator", src)
        self.assertIn("SupportsShouldProcess", src)
        for needle in ("command.ps1", "done.marker", "started.marker",
                       "exitcode.txt", "stdout.log", "stderr.log",
                       "TimeoutSec", "-Bootstrap", "-KeepOpen", "-NoThrow",
                       "wsb start", '"connect"', "wsb exec", "wsb stop",
                       "ExistingLogin", "/d /c start powershell.exe",
                       "crr-agent.local.wsb", "-JobTimeoutSec",
                       "MappedFolder", "agent.ps1"):
            self.assertIn(needle, src, f"trecho ausente: {needle}")

    def test_wsb_agente_tem_duas_pastas(self):
        root = ET.fromstring(_render_template(INVOKER))
        folders = root.findall("./MappedFolders/MappedFolder")
        self.assertEqual(len(folders), 2)
        sandboxes = [f.find("SandboxFolder").text for f in folders]
        self.assertIn("C:\\crr", sandboxes)
        self.assertIn("C:\\job", sandboxes)
        self.assertIsNone(root.find("./LogonCommand"))
        src = INVOKER.read_text(encoding="utf-8")
        self.assertIn("C:\\crr\\sandbox\\agent.ps1", src)
        self.assertIn("-JobDir C:\\job", src)

    def test_agent_inbox_outbox_e_timeout(self):
        src = AGENT.read_text(encoding="utf-8")
        for needle in ("command.ps1", "done.marker", "started.marker",
                       "exitcode.txt", "stdout.log", "stderr.log",
                       "-Bootstrap", "bootstrap.ps1", "HasExited", "124",
                       "LASTEXITCODE", "run-wrapper", "ErrorActionPreference",
                       "PollTimeoutSec", "JobTimeoutSec", "125"):
            self.assertIn(needle, src, f"trecho ausente: {needle}")
        self.assertNotIn("LogonCommand", src)
        # bootstrap falhando ainda grava done.marker (host nao ve so timeout)
        self.assertIn("catch", src.rsplit("bootstrap.ps1", 1)[1][:400])

    def test_docs_cobre_agente(self):
        doc = DOC.read_text(encoding="utf-8")
        for needle in ("Invoke-SandboxTest.ps1", "agent.ps1",
                       "done.marker", "command.ps1", "exitcode.txt"):
            self.assertIn(needle, doc, f"docs sem: {needle}")

    def test_agents_md_memoria(self):
        agents = ROOT / "AGENTS.md"
        self.assertTrue(agents.is_file(), "AGENTS.md sumiu da raiz")
        doc = agents.read_text(encoding="utf-8")
        for needle in ("Invoke-SandboxTest.ps1", "Start-Sandbox.ps1",
                       "LASTEXITCODE", "8091", "8082",
                       "uv run python -m unittest discover -s tests",
                       "FAILSAFE", "sandbox-test-env.md"):
            self.assertIn(needle, doc, f"AGENTS.md sem: {needle}")


class TestSandboxCliESalvaguardas(unittest.TestCase):
    def test_main_help_tem_flags_do_loop_diario(self):
        out = subprocess.run(
            [sys.executable, "main.py", "--help"],
            capture_output=True, text=True, timeout=60, cwd=ROOT)
        self.assertEqual(out.returncode, 0, out.stderr[:500])
        for flag in ("--config", "--self-test", "--platform"):
            self.assertIn(flag, out.stdout)

    def test_salvaguardas_no_codigo(self):
        import actions
        import safety

        self.assertTrue(actions.pyautogui.FAILSAFE)
        self.assertEqual(safety.HOTKEY, "ctrl+alt+esc")

    def test_docs_referencia_tudo(self):
        doc = DOC.read_text(encoding="utf-8")
        for needle in ("Start-Sandbox.ps1", "bootstrap.ps1",
                       "config.sandbox.example.json", "config.sandbox.json",
                       "crr.local.wsb", "--self-test", "--max-steps 4",
                       "--config config.sandbox.json",
                       "8091", "8082", "FAILSAFE", "ctrl+alt+esc"):
            self.assertIn(needle, doc, f"docs sem: {needle}")


if __name__ == "__main__":
    unittest.main(verbosity=2)
