"""Le refus de demarrer sur une configuration de base inadaptee.

Directement lie au portage : la politique cible est « plan de controle
centralise de production = PostgreSQL ». Le reste des controles de production
viendra plus tard ; celui-ci appartient a cette continuation.
"""

import os
import subprocess
import sys
import tempfile
import textwrap
import unittest


HERE = os.path.dirname(os.path.abspath(__file__))


def check_in_subprocess(environment: dict) -> dict:
    """`main` lit `NOVA_ENV` a l'import : chaque cas demande un processus neuf."""
    program = textwrap.dedent(
        """
        import json, os, sys
        sys.path.insert(0, sys.argv[1])
        import main

        try:
            main.check_production_database()
            print(json.dumps({"refused": None, "url": main.database_url()}))
        except main.ProductionConfigurationError as error:
            print(json.dumps({"refused": error.code}))
        """
    )
    with tempfile.TemporaryDirectory() as directory:
        script = os.path.join(directory, "check.py")
        with open(script, "w", encoding="utf-8") as handle:
            handle.write(program)
        merged = dict(os.environ)
        for key in ("NOVA_ENV", "NOVA_DATABASE_URL", "NOVA_DB"):
            merged.pop(key, None)
        merged.update(environment)
        merged.setdefault("NOVA_DOMAINS", "example.com")
        merged.setdefault("NOVA_ORGANIZATION_NAME", "Prod Check")
        completed = subprocess.run(
            [sys.executable, script, HERE],
            cwd=HERE,
            env=merged,
            capture_output=True,
            text=True,
            timeout=300,
        )
    if completed.returncode != 0:
        raise AssertionError(completed.stderr[-3000:])
    import json

    return json.loads(completed.stdout.strip().splitlines()[-1])


class ProductionDatabasePolicyTests(unittest.TestCase):
    def test_production_refuses_sqlite(self):
        result = check_in_subprocess(
            {"NOVA_ENV": "production", "NOVA_DATABASE_URL": "sqlite:///nova.db"}
        )
        self.assertEqual(result["refused"], "SQLITE_REFUSED_IN_PRODUCTION")

    def test_production_requires_an_explicit_url(self):
        # Sans cela, un oubli de configuration se rabattrait sur `NOVA_DB` et
        # passerait pour un choix.
        result = check_in_subprocess({"NOVA_ENV": "production", "NOVA_DB": "nova.db"})
        self.assertEqual(result["refused"], "DATABASE_URL_REQUIRED_IN_PRODUCTION")

    def test_production_accepts_postgresql(self):
        result = check_in_subprocess(
            {
                "NOVA_ENV": "production",
                "NOVA_DATABASE_URL": "postgresql://user@host:5432/nova",
            }
        )
        self.assertIsNone(result["refused"])

    def test_development_still_runs_on_sqlite(self):
        # Le durcissement ne doit pas rendre le developpement impraticable.
        for environment in (
            {"NOVA_ENV": "development", "NOVA_DB": "nova-dev.db"},
            {"NOVA_ENV": "test", "NOVA_DB": "nova-test.db"},
        ):
            result = check_in_subprocess(environment)
            self.assertIsNone(result["refused"], environment)
            self.assertTrue(result["url"].startswith("sqlite:"), result["url"])


if __name__ == "__main__":
    unittest.main()
