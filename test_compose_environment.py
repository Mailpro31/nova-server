"""Une variable documentée pour l'école doit atteindre le conteneur.

Compose ne transmet au conteneur que les variables listées dans
`environment` ; une ligne de `.env` absente de cette liste est ignorée sans
bruit. La licence (4.1) l'a été : posée dans `.env`, elle n'arrivait jamais.
"""

import re
import unittest
from pathlib import Path

ROOT = Path(__file__).parent


class ComposeEnvironmentTests(unittest.TestCase):
    def test_every_documented_variable_reaches_the_production_containers(self):
        documented = set(re.findall(r"^([A-Z][A-Z0-9_]+)=", (
            ROOT / ".env.production.example").read_text(encoding="utf-8"), re.MULTILINE))
        compose = (ROOT / "docker-compose.production.yml").read_text(encoding="utf-8")
        missing = sorted(name for name in documented if f"${{{name}" not in compose)
        self.assertEqual(missing, [])

    def test_the_licence_reaches_the_lab_containers(self):
        compose = (ROOT / "docker-compose.yml").read_text(encoding="utf-8")
        self.assertIn('NOVA_LICENCE: "${NOVA_LICENCE:-}"', compose)


if __name__ == "__main__":
    unittest.main()
