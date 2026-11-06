"""serve.py installe le journal structuré : sans lui, les événements sortent nus."""

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent

# Le journal doit être en place AVANT `import main`, dont les avertissements de
# démarrage partent à l'import : on l'observe juste après l'import de serve.
PROBE = """
import logging, observability, serve
nova = logging.getLogger("nova")
print(type(nova.handlers[0].formatter).__name__ if nova.handlers else "none")
observability.log("ai.usage", task="voice_command", prompt_tokens=120)
"""


class ServeLoggingTests(unittest.TestCase):
    def test_importing_serve_installs_the_json_journal_on_stdout(self):
        with tempfile.TemporaryDirectory() as directory:
            env = {**os.environ, "NOVA_DB": os.path.join(directory, "serve.db"),
                   "NOVA_DOMAINS": "example.com"}
            out = subprocess.run([sys.executable, "-c", PROBE], cwd=HERE, env=env,
                                 capture_output=True, text=True, timeout=120)
        self.assertEqual(out.returncode, 0, out.stderr[-2000:])
        lines = out.stdout.splitlines()
        self.assertIn("StructuredFormatter", lines)
        usage = [json.loads(line) for line in lines if line.startswith("{") and "ai.usage" in line]
        self.assertEqual(usage[-1]["prompt_tokens"], 120)


if __name__ == "__main__":
    unittest.main()
