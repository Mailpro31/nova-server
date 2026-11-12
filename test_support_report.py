"""Le rapport « Signaler un problème » : journalisé en warning, sans contenu."""

import io
import json
import os
import tempfile
import unittest

TEST_DIRECTORY = tempfile.TemporaryDirectory()
os.environ.setdefault("NOVA_DB", os.path.join(TEST_DIRECTORY.name, "support.db"))
os.environ.setdefault("NOVA_DOMAINS", "example.com")
os.environ.setdefault("NOVA_ORGANIZATION_NAME", "Nova Support Test")

from fastapi.testclient import TestClient  # noqa: E402

import main  # noqa: E402
import observability  # noqa: E402

main.init_db()

DICTATED = "mon texte dicté très privé"
REPORT = {
    "app_version": "1.0.70",
    "os": "Windows 11",
    "memory_mb": 16000,
    "engines": {"whisper": "ready"},
    "last_error": "errors.model_load",
    "reported_at": "2026-11-12T10:00:00Z",
    "description": "plante, écrivez à eleve@ecole.fr",
}


class SupportReportTests(unittest.TestCase):
    def setUp(self):
        main.reset_rate_limiter()
        self.client = TestClient(main.app)
        self.stream = io.StringIO()
        observability.configure_logging("INFO", self.stream)
        main.REQUIRE_AUTH, self._auth = False, main.REQUIRE_AUTH

    def tearDown(self):
        main.REQUIRE_AUTH = self._auth

    def lines(self):
        return [json.loads(x) for x in self.stream.getvalue().splitlines() if x]

    def test_logged_as_warning_without_email_or_dictation(self):
        body = dict(REPORT, text=DICTATED, transcript=DICTATED, history=[DICTATED])
        response = self.client.post("/api/support/report", json=body)
        self.assertEqual(response.status_code, 200)
        raw = self.stream.getvalue()
        entry = [x for x in self.lines() if x["event"] == "support.report"][0]
        self.assertEqual(entry["level"], "warning")
        self.assertEqual(entry["last_error"], "errors.model_load")
        self.assertNotIn("eleve@ecole.fr", raw)
        self.assertIn("<email>", entry["description"])
        self.assertNotIn(DICTATED, raw)
        self.assertNotIn("dev", entry.values())  # identifiant opaque, pas l'identité

    def test_free_text_in_code_fields_is_stripped(self):
        body = dict(REPORT, last_error="Bonjour@moi.fr: " + DICTATED)
        self.client.post("/api/support/report", json=body)
        self.assertNotIn("@", self.stream.getvalue().split("last_error")[1].split(",")[0])

    def test_description_is_capped(self):
        body = dict(REPORT, description="x" * 501)
        self.assertEqual(self.client.post("/api/support/report", json=body).status_code, 422)

    def test_requires_authentication(self):
        main.REQUIRE_AUTH = True
        self.assertEqual(self.client.post("/api/support/report", json=REPORT).status_code, 401)

    def test_rate_limited(self):
        codes = [self.client.post("/api/support/report", json=REPORT).status_code for _ in range(6)]
        self.assertEqual(codes[:5], [200] * 5)
        self.assertEqual(codes[5], 429)


if __name__ == "__main__":
    unittest.main()
