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
            "nova_ai_fallback_total", "nova_refine_total", "nova_app_versions", "nova_licence",
            "nova_server_info", "nova_members", "nova_groups", "nova_uptime_seconds",
            "nova_disk_free_bytes", "nova_ram_gb", "nova_vram_gb", "nova_engine_info",
            "nova_ai_envelope")
LABELS = {"route", "status", "le", "event", "outcome", "period", "task", "kind", "stage",
          "style", "reason", "version", "tier",
          # Fiche de l'ecole : des reglages, aucun identifiant.
          "server_version", "mode", "auth", "processing", "gpu", "state", "adapter", "model"}


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

    def test_the_token_can_come_from_a_secret_file(self):
        secret = os.path.join(TEST_DIRECTORY.name, "metrics_token")
        with open(secret, "w", encoding="utf-8") as handle:
            handle.write(TOKEN + "\n")
        os.chmod(secret, 0o600)  # secrets_provider refuse un fichier lisible par tous
        with patch.dict(os.environ, {"NOVA_METRICS_TOKEN": f"file://{secret}"}):
            ok = self.client.get("/api/metrics", headers={"Authorization": f"Bearer {TOKEN}"})
            refused = self.client.get("/api/metrics", headers={"Authorization": "Bearer autre"})
        self.assertEqual(ok.status_code, 200)
        self.assertIn("nova_active_users", ok.text)
        self.assertEqual(refused.status_code, 401)

    def test_an_unreadable_token_file_closes_the_route_instead_of_failing(self):
        missing = os.path.join(TEST_DIRECTORY.name, "absent", "metrics_token")
        with patch.dict(os.environ, {"NOVA_METRICS_TOKEN": f"file://{missing}"}):
            response = self.client.get("/api/metrics", headers={"Authorization": f"Bearer {TOKEN}"})
        self.assertEqual(response.status_code, 401)
        for name in BUSINESS:
            self.assertNotIn(name, response.text)

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

    def test_each_text_call_logs_its_tokens_without_text(self):
        router = OrganizationAIRouter(CountingTextBackend(), FakeSpeechInferenceBackend())
        with self.assertLogs("nova", "INFO") as captured:
            asyncio.run(router.generate(TaskType.VOICE_COMMAND, "texte secret", "consigne"))
        usage = [r for r in captured.records if r.getMessage() == "ai.usage"]
        self.assertEqual(len(usage), 1)
        self.assertEqual(usage[0].fields, {"task": "voice_command", "prompt_tokens": 120,
                                           "completion_tokens": 30})

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

    def test_the_month_of_active_users_starts_at_midnight_in_paris(self):
        """1er novembre 2026, 0 h 30 à Paris = 31 octobre, 23 h 30 UTC : la
        dictée de 0 h 10 (clé UTC « 10-31T23 ») est de novembre, celle de la
        veille au soir (« 10-31T22 ») d'octobre."""
        now = 1793489400.0
        with main.db() as conn:
            for email, hour in (("novembre@example.com", "2026-10-31T23"),
                                ("octobre@example.com", "2026-10-31T22")):
                conn.execute("INSERT INTO usage_hourly(email,hour,transcriptions) VALUES (?,?,1)",
                             (email, hour))
        with patch.object(main.time, "time", return_value=now):
            series = main.business_metrics()
        self.assertIn(("nova_active_users", "Personnes ayant dicte ou reformule ce mois-ci.",
                       {"period": "month"}, 1), series)

    def test_installed_versions_are_counted_and_odd_ones_folded(self):
        now = time.time()
        versions = ("1.0.70", "1.0.70", "<script>1.0</script>")
        for n in range(len(versions)):
            main.touch_user(f"poste{n}@example.com", main.nova_organization_id())
        with main.db() as conn:
            for n, version in enumerate(versions):
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

    def test_the_ai_spend_of_the_month_and_the_envelope_are_exposed(self):
        private, public = licence.new_key_pair()
        token = licence.issue(private, organization="IPSA", tier="151-250", kind="subscription",
                              starts="2026-09-01", ends="2027-08-31", ai_envelope_eur=1200)
        with main.db() as conn:
            conn.execute("INSERT INTO ai_spend(month, email, eur) VALUES (?,?,?)",
                         (main.ai_envelope.month_of(time.time()), "eleve@example.com", 2.5))
        real = licence.licence_state
        with patch.object(main, "NOVA_LICENCE", token),                 patch.object(licence, "licence_state",
                             lambda t, now=None, public_pem=None: real(t, now, public)):
            rendered = self.scrape().text
        # Une seule famille : l'enveloppe annuelle est kind="annual", jamais une série sans étiquette.
        self.assertIn('nova_ai_envelope_eur{kind="annual"} 1200', rendered)
        self.assertNotRegex(rendered, r"(?m)^nova_ai_envelope_eur ")
        self.assertEqual(rendered.count("# HELP nova_ai_envelope_eur "), 1)
        self.assertIn("nova_ai_spend_eur_month 2.5", rendered)
        self.assertNotIn("eleve@example.com", rendered)

    def test_the_school_sheet_describes_the_server_without_names(self):
        main.touch_user("fiche@example.com", main.nova_organization_id())
        rendered = self.scrape().text
        self.assertRegex(rendered, r'nova_server_info\{[^}]*mode="[a-z_-]+"[^}]*\} 1')
        self.assertRegex(rendered, r'nova_members\{state="active"\} [1-9]')
        self.assertIn("nova_groups ", rendered)
        self.assertIn("nova_disk_free_bytes ", rendered)
        self.assertNotIn("fiche@example.com", rendered)
        self.assertRegex(rendered, r'nova_engine_info\{[^}]*task="text"[^}]*\} 1')
        self.assertRegex(rendered, r'nova_engine_info\{[^}]*task="speech"[^}]*\} 1')

    def test_bench_accounts_and_four_digit_versions_in_the_version_count(self):
        now = time.time()
        for email, version in (("banc2@nova.invalid", "1.0.70"), ("Banc3@NOVA.INVALID", "1.0.70"),
                               ("vrai@example.com", "2026.1.0")):
            main.touch_user(email, main.nova_organization_id())
            with main.db() as conn:
                conn.execute("UPDATE users SET app_version=?, last_seen=? WHERE email=?",
                             (version, now, email))
        rendered = self.scrape().text
        self.assertNotIn('nova_app_versions{version="1.0.70"}', rendered)
        self.assertIn('nova_app_versions{version="2026.1.0"} 1', rendered)

    def test_a_member_whose_disabled_flag_is_null_counts_as_active(self):
        def active():
            return int(re.search(r'nova_members\{state="active"\} (\d+)', self.scrape().text).group(1))
        before = active()
        main.touch_user("sans-drapeau@example.com", main.nova_organization_id())
        with main.db() as conn:
            conn.execute("UPDATE users SET disabled=NULL WHERE email=?", ("sans-drapeau@example.com",))
        self.assertEqual(active(), before + 1)
        with main.db() as conn:  # la base du fichier est partagée entre les tests
            conn.execute("DELETE FROM users WHERE email=?", ("sans-drapeau@example.com",))

    def test_bench_accounts_are_not_members_and_models_are_named(self):
        with main.db() as conn:
            before = conn.execute("SELECT COUNT(*) FROM users WHERE disabled = 0 AND "
                                  "email NOT LIKE '%@nova.invalid'").fetchone()[0]
        main.touch_user("banc@nova.invalid", main.nova_organization_id())
        backend = main.ORGANIZATION_AI_ROUTER.text_backend
        with patch.object(backend, "model", "google/gemini-3.5-flash", create=True):
            rendered = self.scrape().text
        self.assertIn(f'nova_members{{state="active"}} {before}', rendered)
        self.assertIn('model="gemini-3.5-flash",task="text"', rendered)

    def test_the_ai_envelope_is_shown_when_the_contract_has_one(self):
        envelope = {"annual_eur": 1200, "allowance_eur": 300.0, "spent_eur": 42.5,
                    "month_eur": 7.25, "percent": 14, "empty": False, "mine_over": False}
        with patch.object(main, "ai_envelope_state", lambda *a, **k: envelope):
            rendered = self.scrape().text
        self.assertIn('nova_ai_envelope_eur{kind="spent"} 42.5', rendered)
        self.assertIn("nova_ai_envelope_percent 14", rendered)

    def test_the_envelope_is_one_family_when_the_licence_and_the_prices_both_have_it(self):
        # Vu en production le 13/11 : la licence et le bloc des moteurs publiaient chacun l'enveloppe.
        private, public = licence.new_key_pair()
        token = licence.issue(private, organization="IPSA", tier="10", kind="pilot",
                              starts="2026-11-13", ends="2027-11-15", ai_envelope_eur=1)
        envelope = {"annual_eur": 1, "allowance_eur": 1.0, "spent_eur": 0.1,
                    "month_eur": 0.1, "percent": 10, "empty": False, "mine_over": False}
        real = licence.licence_state
        with patch.object(main, "NOVA_LICENCE", token),                 patch.object(licence, "licence_state",
                             lambda t, now=None, public_pem=None: real(t, now, public)),                 patch.object(main, "ai_envelope_state", lambda *a, **k: envelope):
            rendered = self.scrape().text
        self.assertEqual(rendered.count('nova_ai_envelope_eur{kind="annual"}'), 1)
        self.assertEqual(rendered.count("# HELP nova_ai_envelope_eur "), 1)
        self.assertNotRegex(rendered, r"(?m)^nova_ai_envelope_eur ")

    def test_no_licence_says_so_without_a_deadline(self):
        with patch.object(main, "NOVA_LICENCE", ""):
            rendered = self.scrape().text
        self.assertIn('nova_licence_info{status="none",tier="none"} 1', rendered)
        self.assertNotIn("nova_licence_days_left", rendered)

    # ------------------------------------------------- déploiement

    def test_the_token_reaches_the_api_container(self):
        # Le compose n'a pas d'env_file : une variable absente de l'ancre
        # d'environnement n'arrive jamais au conteneur, même posée dans .env.
        import yaml

        here = os.path.dirname(os.path.abspath(__file__))
        with open(os.path.join(here, "docker-compose.yml"), encoding="utf-8") as handle:
            compose = yaml.safe_load(handle)
        self.assertIn("NOVA_METRICS_TOKEN", compose["x-nova-environment"])
        with open(os.path.join(here, "docker-compose.production.yml"), encoding="utf-8") as handle:
            production = yaml.safe_load(handle)
        self.assertIn("NOVA_METRICS_TOKEN", production["services"]["api"]["environment"])
        with open(os.path.join(here, ".env.production.example"), encoding="utf-8") as handle:
            self.assertIn("NOVA_METRICS_TOKEN=", handle.read())

    # ------------------------------------------------- aucune donnée d'élève

    def test_every_label_name_is_on_the_closed_list(self):
        observability.log("reformulation.refused", level="warning", style="x", reason="y")
        rendered = self.scrape().text
        for group in re.findall(r"\{([^}]*)\}", rendered):
            for pair in group.split(","):
                self.assertIn(pair.split("=", 1)[0], LABELS, rendered)


if __name__ == "__main__":
    unittest.main()
