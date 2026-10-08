import json
import shutil
import subprocess
import unittest
from pathlib import Path

ROOT = Path(__file__).parents[1]


class ExtensionTests(unittest.TestCase):
    def test_optional_permissions_without_default_host_access(self):
        manifest = json.loads((ROOT / "extension/manifest.json").read_text())
        self.assertEqual(manifest["manifest_version"], 3)
        self.assertNotIn("host_permissions", manifest)
        self.assertIn("http://*/*", manifest["optional_host_permissions"])
        self.assertNotIn("externally_connectable", manifest)
        self.assertNotIn("content_scripts", manifest)

    @unittest.skipUnless(shutil.which("node"), "Node ausente para N0 JavaScript")
    def test_javascript_policy_invariants_and_syntax(self):
        process = subprocess.run(
            ["node", str(ROOT / "tests/browser_policy.mjs")], capture_output=True, text=True
        )
        self.assertEqual(process.returncode, 0, process.stderr)
        files = list((ROOT / "extension").glob("*.js"))
        files.append(ROOT / "evals/browser/driver.mjs")
        for file in files:
            process = subprocess.run(["node", "--check", str(file)], capture_output=True, text=True)
            self.assertEqual(process.returncode, 0, process.stderr)
