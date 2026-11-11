"""Le panel de l'éditeur (5.3 à 5.5) : des fichiers, vérifiés ici.

Les tableaux de bord ne citent que des séries que le serveur produit vraiment,
l'agent des écoles reste coupé par défaut et ne lit que le service `api`, et
le panel n'écoute que sur la machine.
"""

import json
import re
import unittest
from pathlib import Path

ROOT = Path(__file__).parent
DASHBOARDS = sorted((ROOT / "panel/grafana/dashboards").glob("*.json"))
SOURCES = (ROOT / "observability.py").read_text(encoding="utf-8") + (
    ROOT / "main.py").read_text(encoding="utf-8")
SUFFIXES = re.compile(r"_(bucket|sum|count)$")


def expressions(board: dict) -> list[str]:
    return [target["expr"] for panel in board["panels"] for target in panel["targets"]]


class DashboardTests(unittest.TestCase):
    def test_six_dashboards_with_distinct_ids(self):
        # Les cinq de 5.5, plus la fiche école (11/11).
        boards = [json.loads(path.read_text(encoding="utf-8")) for path in DASHBOARDS]
        self.assertEqual(len(boards), 6)
        self.assertEqual(len({board["uid"] for board in boards}), 6)

    def test_every_series_is_one_the_server_produces(self):
        for path in DASHBOARDS:
            for expr in expressions(json.loads(path.read_text(encoding="utf-8"))):
                for name in re.findall(r"\bnova_[a-z_]+", expr):
                    family = SUFFIXES.sub("", name)
                    with self.subTest(path.name, series=name):
                        self.assertIn(family, SOURCES)

    def test_alerts_only_name_produced_series(self):
        alerts = (ROOT / "panel/prometheus/alerts.yml").read_text(encoding="utf-8")
        for name in set(re.findall(r"\bnova_[a-z_]+", alerts)):
            with self.subTest(name):
                self.assertIn(name, SOURCES)


class AgentTests(unittest.TestCase):
    def test_the_agent_is_off_unless_the_contract_opens_it(self):
        compose = (ROOT / "docker-compose.yml").read_text(encoding="utf-8")
        block = compose[compose.index("\n  alloy:"):compose.index("\n  whisper:")]
        self.assertIn('profiles: ["panel"]', block)
        self.assertIn("docker.sock:/var/run/docker.sock:ro", block)

    def test_the_agent_reads_only_the_api_logs_and_only_warnings(self):
        config = (ROOT / "deploy/alloy/config.alloy").read_text(encoding="utf-8")
        self.assertRegex(config, r'regex\s*=\s*"api"\s*\n\s*action\s*=\s*"keep"')
        self.assertIn('expression = "^(debug|info)$"', config)
        self.assertIn('expression = "^[^{]"', config)


class PanelTests(unittest.TestCase):
    def test_the_panel_listens_on_the_machine_only(self):
        compose = (ROOT / "panel/docker-compose.yml").read_text(encoding="utf-8")
        ports = re.findall(r'- "([^"]+:\d+:\d+)"', compose)
        self.assertEqual(len(ports), 3)
        for port in ports:
            self.assertTrue(port.startswith("127.0.0.1:"), port)

    def test_no_password_is_written_in_the_panel(self):
        compose = (ROOT / "panel/docker-compose.yml").read_text(encoding="utf-8")
        self.assertIn("${GRAFANA_ADMIN_PASSWORD:?", compose)
        self.assertIn('GF_AUTH_ANONYMOUS_ENABLED: "false"', compose)


if __name__ == "__main__":
    unittest.main()
