import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from evals.brain.runtime import launch


class RuntimeMatrixTests(unittest.TestCase):
    def test_explicit_text_only_and_context(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            binary, gguf = root / "llama-server", root / "model.gguf"
            binary.write_bytes(b"installed")
            gguf.write_bytes(b"gguf")
            with (
                patch("evals.brain.runtime._lmstudio_candidates", return_value=[binary]),
                patch("evals.brain.runtime._runtime_env", return_value=None),
                patch("evals.brain.runtime.subprocess.Popen") as spawn,
                patch("evals.brain.runtime.VramMonitor") as monitor,
                patch("evals.brain.runtime.signal.signal"),
            ):
                process = spawn.return_value
                process.pid, process.returncode = 432, 0
                process.poll.return_value = 0
                monitor.return_value.__enter__.return_value.peak_mib = 1200
                launch("teste", gguf, None, root / "run", context=4096, ngl=30, ubatch=256)
                command = spawn.call_args.args[0]
                self.assertEqual(command[command.index("-ngl") + 1], "30")
                self.assertEqual(command[command.index("-ub") + 1], "256")
                self.assertIn("--no-mmproj", command)
                self.assertNotIn("--mmproj", command)
                self.assertEqual(command[command.index("-c") + 1], "4096")
                meta = json.loads((root / "run/runtime.json").read_text())
                self.assertIsNone(meta["mmproj"])
                self.assertEqual(meta["peak_vram_mib"], 1200)

    def test_invalid_matrix_or_missing_files_never_download(self):
        with self.assertRaises(ValueError):
            launch("teste", Path("missing"), None, Path("runs"), context=100)
        with self.assertRaises(ValueError):
            launch("teste", Path("missing"), None, Path("runs"), ngl=200)
        with self.assertRaises(ValueError):
            launch("teste", Path("missing"), None, Path("runs"), ubatch=64)
        with patch("evals.brain.runtime.subprocess.Popen") as spawn:
            with self.assertRaises(ValueError):
                launch("teste", Path("missing"), None, Path("runs"))
            spawn.assert_not_called()
