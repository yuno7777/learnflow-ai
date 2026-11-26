import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]


class DevBootstrapTests(unittest.TestCase):
    def test_init_is_portable_and_preserves_existing_configuration(self):
        with tempfile.TemporaryDirectory(prefix="learnflow bootstrap ") as directory:
            root = Path(directory)
            script = root / "scripts" / "dev.py"
            script.parent.mkdir()
            script.write_bytes((ROOT / "scripts" / "dev.py").read_bytes())
            (root / ".env.example").write_bytes((ROOT / ".env.example").read_bytes())

            cmd = [sys.executable, str(script), "init"]
            result = subprocess.run(cmd, cwd=root.parent, check=True, capture_output=True, text=True)
            self.assertIn("Created .env", result.stdout)

            config = root / ".env"
            generated = config.read_text(encoding="utf-8")
            secrets = re.findall(
                r"^(?:JWT_SECRET|WEBHOOK_SIGNING_SECRET|N8N_ENCRYPTION_KEY)=([a-f0-9]{64})$",
                generated,
                re.MULTILINE,
            )
            self.assertEqual(len(set(secrets)), 3)
            self.assertTrue(all(secret not in result.stdout for secret in secrets))
            self.assertIn("LLM_PROVIDER=mock", generated)
            if os.name == "posix":
                self.assertEqual(config.stat().st_mode & 0o077, 0)

            config.write_text("API_PORT=8012\n", encoding="utf-8")
            subprocess.run(cmd, cwd=root.parent, check=True, capture_output=True, text=True)
            self.assertEqual(config.read_text(encoding="utf-8"), "API_PORT=8012\n")
