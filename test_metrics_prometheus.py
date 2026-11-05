"""Chiffres du serveur pour Prometheus (point 5.1).

`/api/metrics` est joignable depuis Internet sur un serveur d'école : sans
`NOVA_METRICS_TOKEN`, la route garde exactement ses séries techniques et
n'expose AUCUNE série métier (actifs, jetons, refus, versions, licence). Avec
le jeton, les séries métier apparaissent — des comptes, jamais un nom, une
adresse, un identifiant ni un texte d'élève.
"""

import asyncio
import os
import re
import tempfile
import time
import unittest
from unittest.mock import patch

TEST_DIRECTORY = tempfile.TemporaryDirectory()
os.environ.setdefault("NOVA_DB", os.path.join(TEST_DIRECTORY.name, "metrics.db"))
os.environ.setdefault("NOVA_DOMAINS", "example.com")
os.environ.setdefault("NOVA_ORGANIZATION_NAME", "Nova Metrics Test")

from fastapi.testclient import TestClient  # noqa: E402

import ai_runtime  # noqa: E402
import licence  # noqa: E402
import main  # noqa: E402
import observability  # noqa: E402
from ai_runtime import (  # noqa: E402
    FakeSpeechInferenceBackend, OrganizationAIRouter, TaskType, TextInferenceResult,
)

main.init_db()

TOKEN = "jeton-de-test-prometheus"
BUSINESS = ("nova_active_users", "nova_ai_tokens_total", "nova_stage_seconds",
            "nova_rewrite_refused_total", "nova_command_refused_total",
            "nova_ai_fallback_total", "nova_refine_total", "nova_app_versions", "nova_licence")
LABELS = {"route", "status", "le", "event", "outcome", "period", "task", "kind", "stage",
          "style", "reason", "version", "tier"}


class CountingTextBackend:
    adapter_name = "counting_text"

    async def generate(self, request):
        return TextInferenceResult(
            text="ok", finish_reason="stop", latency_ms=0,
            usage={"prompt_tokens": 120, "completion_tokens": 30, "total_tokens": 150},
            backend={"adapter": self.adapter_name})


class MetricsPrometheusTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.client = TestClient(main.app)

    def setUp(self):
        observability.METRICS.reset()
        main.reset_rate_limiter()
        with main.db() as conn:
            conn.execute("DELETE FROM usage_hourly")
            conn.execute("UPDATE users SET app_version=NULL")

    def scrape(self, token=TOKEN):
        headers = {"Authorization": f"Bearer {token}"} if token else {}
        with patch.dict(os.environ, {"NOVA_METRICS_TOKEN": TOKEN}):
            return self.client.get("/api/metrics", headers=headers)

    # --------------------------------------------------------- le jeton

    def test_without_a_configured_token_no_business_series_is_exposed(self):
        observability.log("reformulation.refused", level="warning",
                          style="nova_style_email", reason="content-dropped")
        with patch.dict(os.environ, {"NOVA_METRICS_TOKEN": ""}):
            response = self.client.get("/api/metrics")
        self.assertEqual(response.status_code, 200)
        self.assertIn("nova_http_requests_total", response.text)
        for name in BUSINESS:
            self.assertNotIn(name, response.text)

    def test_a_wrong_token_is_refused(self):
        self.assertEqual(self.scrape("mauvais-jeton").status_code, 401)

    def test_a_missing_token_is_refused_once_one_is_configured(self):
        self.assertEqual(self.scrape(None).status_code, 401)

    def test_the_right_token_opens_the_business_series(self):
        response = self.scrape()
        self.assertEqual(response.status_code, 200)
        self.assertIn('nova_active_users{period="month"}', response.text)
        self.assertIn("nova_licence_info", response.text)

    # --------------------------------------------------- refus, replis

    def test_refusals_are_counted_by_style_and_reason(self):
        for _ in range(2):
            observability.log("reformulation.refused", level="warning",
                              style="nova_style_email", reason="content-dropped")
        observability.log("command.refused", level="warning", reason="number-changed", kind="rewrite")
        rendered = self.scrape().text
        self.assertIn('nova_rewrite_refused_total{reason="content-dropped",style="nova_style_email"} 2',
                      rendered)
        self.assertIn('nova_command_refused_total{kind="rewrite",reason="number-changed"} 1', rendered)

    def test_a_personal_style_never_becomes_a_label(self):
        observability.log("reformulation.refused", level="warning",
                          style="perso-3f9c2a-cours-de-marie", reason="content-added")
        rendered = self.scrape().text
        self.assertNotIn("perso-3f9c2a", rendered)
        self.assertIn('style="custom"', rendered)

    def test_an_unexpected_reason_value_is_folded(self):
        observability.log("ai.text_fallback", level="warning", reason="Mot de passe: hunter2")
        rendered = self.scrape().text
        self.assertNotIn("hunter2", rendered)
        self.assertIn('nova_ai_fallback_total{reason="other"} 1', rendered)

    def test_fallbacks_and_refine_outcomes_are_counted(self):
        observability.log("ai.text_fallback", level="warning", reason="primary_set_aside")
        observability.log("transcription.refine_failed", level="warning", reason="AI_RUNTIME_TIMEOUT")
        observability.log("transcription.refine_rejected", level="warning")
        rendered = self.scrape().text
        self.assertIn('nova_ai_fallback_total{reason="primary_set_aside"} 1', rendered)
        self.assertIn('nova_refine_total{outcome="failed"} 1', rendered)
        self.assertIn('nova_refine_total{outcome="rejected"} 1', rendered)

    # ------------------------------------------- jetons et latences, ai_runtime

    def test_tokens_and_the_formatting_stage_are_measured_in_the_router(self):
        router = OrganizationAIRouter(CountingTextBackend(), FakeSpeechInferenceBackend())
        asyncio.run(router.generate(TaskType.STYLE_TRANSFORM, "texte", "consigne"))
        rendered = self.scrape().text
        self.assertIn('nova_ai_tokens_total{kind="prompt",task="style_transform"} 120', rendered)
        self.assertIn('nova_ai_tokens_total{kind="completion",task="style_transform"} 30', rendered)
        self.assertIn('nova_stage_seconds_count{stage="mise_en_forme"} 1', rendered)

    def test_the_transcription_stage_is_measured(self):
        router = OrganizationAIRouter(CountingTextBackend(), FakeSpeechInferenceBackend())
        asyncio.run(router.transcribe(TaskType.TRANSCRIPTION_REALTIME, b"RIFF", "a.wav"))
        self.assertIn('nova_stage_seconds_count{stage="transcription"} 1', self.scrape().text)

    def test_the_refine_stage_is_measured(self):
        import httpx

        refiner = ai_runtime.AudioRefineBackend(
            "http://refine:8000/v1", "m", token="t",
            transport=httpx.MockTransport(lambda request: httpx.Response(
                200, json={"choices": [{"message": {"content": "Relu."}}]})))
        asyncio.run(refiner.refine(b"RIFF", "wav", "relu", []))
        self.assertIn('nova_stage_seconds_count{stage="relecture"} 1', self.scrape().text)

    # --------------------------------------------- actifs, versions, licence

    def test_active_users_of_the_month_are_a_count_without_address(self):
        month = time.strftime("%Y-%m")
        with main.db() as conn:
            for email, hour in (("eleve-a@example.com", f"{month}-01T09"),
                                ("eleve-a@example.com", f"{month}-02T10"),
                                ("eleve-b@example.com", f"{month}-03T11"),
                                ("ancien@example.com", "2001-01-01T09")):
                conn.execute("INSERT INTO usage_hourly(email,hour,transcriptions) VALUES (?,?,1)",
                             (email, hour))
        rendered = self.scrape().text
        self.assertIn('nova_active_users{period="month"} 2', rendered)
        self.assertNotIn("example.com", rendered)

    def test_installed_versions_are_counted_and_odd_ones_folded(self):
        now = time.time()
        with main.db() as conn:
            for n, version in enumerate(("1.0.70", "1.0.70", "<script>1.0</script>")):
                main.touch_user(f"poste{n}@example.com", main.nova_organization_id())
                conn.execute("UPDATE users SET app_version=?, last_seen=? WHERE email=?",
                             (version, now, f"poste{n}@example.com"))
        rendered = self.scrape().text
        self.assertIn('nova_app_versions{version="1.0.70"} 2', rendered)
        self.assertIn('nova_app_versions{version="other"} 1', rendered)
        self.assertNotIn("<script>", rendered)

    def test_the_licence_tier_and_days_left_are_exposed(self):
        private, public = licence.new_key_pair()
        ends = time.strftime("%Y-%m-%d", time.gmtime(time.time() + 40 * 86400))
        token = licence.issue(private, organization="IPSA", tier="151-250", kind="subscription",
                              starts="2026-09-01", ends=ends)
        real = licence.licence_state
        with patch.object(main, "NOVA_LICENCE", token), \
                patch.object(licence, "licence_state",
                             lambda t, now=None, public_pem=None: real(t, now, public)):
            rendered = self.scrape().text
        self.assertIn('nova_licence_info{status="valid",tier="151-250"} 1', rendered)
        self.assertRegex(rendered, r"nova_licence_days_left 4[01]\b")
        self.assertNotIn("IPSA", rendered)

    def test_no_licence_says_so_without_a_deadline(self):
        with patch.object(main, "NOVA_LICENCE", ""):
            rendered = self.scrape().text
        self.assertIn('nova_licence_info{status="none",tier="none"} 1', rendered)
        self.assertNotIn("nova_licence_days_left", rendered)

    # ------------------------------------------------- aucune donnée d'élève

    def test_every_label_name_is_on_the_closed_list(self):
        observability.log("reformulation.refused", level="warning", style="x", reason="y")
        rendered = self.scrape().text
        for group in re.findall(r"\{([^}]*)\}", rendered):
            for pair in group.split(","):
                self.assertIn(pair.split("=", 1)[0], LABELS, rendered)


if __name__ == "__main__":
    unittest.main()
