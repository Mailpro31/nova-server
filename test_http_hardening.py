"""De vraies requêtes HTTP contre le service durci.

Ce que ces tests mesurent ne se déduit pas d'un test unitaire : en-têtes
réellement posés sur une réponse, identifiant de corrélation réellement renvoyé,
métriques réellement incrémentées, et rien de tout cela ne portant de contenu.
"""

import json
import os
import re
import tempfile
import unittest
from unittest.mock import patch

TEST_DIRECTORY = tempfile.TemporaryDirectory()
os.environ.setdefault("NOVA_DB", os.path.join(TEST_DIRECTORY.name, "hardening.db"))
os.environ.setdefault("NOVA_DOMAINS", "example.com")
os.environ.setdefault("NOVA_ORGANIZATION_NAME", "Nova Hardening Test")

from fastapi.testclient import TestClient  # noqa: E402

import main  # noqa: E402
import observability  # noqa: E402
import ratelimit  # noqa: E402

main.init_db()


class HttpHardeningTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.client = TestClient(main.app)

    def setUp(self):
        observability.METRICS.reset()
        main.reset_rate_limiter()

    # ------------------------------------------------------- corrélation

    def test_every_response_carries_a_request_id(self):
        response = self.client.get("/api/health")
        identifier = response.headers.get(observability.REQUEST_ID_HEADER)
        self.assertIsNotNone(identifier)
        self.assertRegex(identifier, r"^[A-Za-z0-9._-]{8,64}$")

    def test_a_well_formed_client_request_id_is_echoed(self):
        response = self.client.get(
            "/api/health", headers={observability.REQUEST_ID_HEADER: "trace-abc-123"}
        )
        self.assertEqual(
            response.headers[observability.REQUEST_ID_HEADER], "trace-abc-123"
        )

    def test_a_hostile_client_request_id_is_replaced(self):
        # Il ressort dans un en-tête : l'accepter tel quel en ferait un canal
        # d'injection dans les journaux de qui les lit.
        response = self.client.get(
            "/api/health",
            headers={observability.REQUEST_ID_HEADER: "<script>alert(1)</script>"},
        )
        echoed = response.headers[observability.REQUEST_ID_HEADER]
        self.assertNotIn("<", echoed)
        self.assertRegex(echoed, r"^[0-9a-f]{32}$")

    def test_a_request_id_grants_nothing(self):
        # Il ne sélectionne aucun tenant et n'authentifie personne.
        response = self.client.get(
            "/api/control/organization",
            headers={observability.REQUEST_ID_HEADER: "trace-abc-123"},
        )
        self.assertIn(response.status_code, (401, 403))

    # ------------------------------------------------- en-têtes de sécurité

    def test_security_headers_are_present_on_every_response(self):
        response = self.client.get("/api/health")
        self.assertEqual(response.headers["X-Content-Type-Options"], "nosniff")
        self.assertEqual(response.headers["Referrer-Policy"], "no-referrer")
        self.assertIn("frame-ancestors 'none'", response.headers["Content-Security-Policy"])

    def test_hsts_follows_the_declared_environment(self):
        # `NOVA_ENV` vaut « production » par defaut : un deploiement qui oublie
        # de se declarer obtient le comportement le plus prudent.
        self.assertIn("Strict-Transport-Security", self.client.get("/api/livez").headers)
        # En developpement il disparait : l'envoyer en clair n'apporte rien, et
        # peut verrouiller un poste sur un port reutilise.
        with patch.object(main, "NOVA_ENV", "development"):
            response = self.client.get("/api/livez")
        self.assertNotIn("Strict-Transport-Security", response.headers)

    def test_headers_are_present_on_an_error_response_too(self):
        response = self.client.get("/api/control/organization")
        self.assertIn(response.status_code, (401, 403))
        self.assertEqual(response.headers["X-Content-Type-Options"], "nosniff")

    # ------------------------------------------------------------ sondes

    def test_liveness_does_not_depend_on_the_database(self):
        # C'est toute la différence avec l'aptitude : un processus vivant dont
        # la base est momentanément injoignable ne doit pas être redémarré.
        def broken():
            raise RuntimeError("database is down")

        with patch.object(main, "db", broken):
            response = self.client.get("/api/livez")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {"status": "ok"})

    def test_the_historical_health_route_keeps_its_contract(self):
        # Chaque poste deploye l'interroge : en changer la forme casserait le
        # parc. Elle reste ce qu'elle etait, et `/api/livez` vient a cote.
        body = self.client.get("/api/health").json()
        for key in ("status", "domains", "auth_methods", "version"):
            self.assertIn(key, body)

    def test_readiness_reports_components_without_infrastructure_detail(self):
        response = self.client.get("/api/readyz")
        self.assertIn(response.status_code, (200, 503))
        body = response.json()
        self.assertIn("ready", body)
        self.assertIn("database", body["components"])
        self.assertIn("schema", body["components"])
        serialized = json.dumps(body)
        for forbidden in ("sqlite:", "postgresql:", "redis:", "/", "password", "@"):
            self.assertNotIn(forbidden, serialized, forbidden)

    def test_readiness_turns_false_when_the_database_is_unreachable(self):
        def broken():
            raise RuntimeError("no such table")

        with patch.object(main, "db", broken):
            response = self.client.get("/api/readyz")
        self.assertEqual(response.status_code, 503)
        self.assertFalse(response.json()["ready"])

    # --------------------------------------------------------- métriques

    def test_requests_are_counted_by_route_class(self):
        self.client.get("/api/health")
        self.client.get("/api/health")
        rendered = self.client.get("/api/metrics").text
        self.assertIn('nova_http_requests_total{route="operational",status="2xx"}', rendered)
        self.assertIn("nova_db_pool_max", rendered)
        self.assertIn("nova_rate_limiter_shared", rendered)

    def test_no_metric_label_carries_an_identifier(self):
        self.client.post("/api/auth/request", json={"email": "someone@example.com"})
        self.client.get("/api/control/organizations/org-secret-1234/members")
        rendered = self.client.get("/api/metrics").text
        self.assertNotIn("org-secret-1234", rendered)
        self.assertNotIn("someone@example.com", rendered)
        for group in re.findall(r"\{([^}]*)\}", rendered):
            for pair in group.split(","):
                self.assertIn(
                    pair.split("=", 1)[0],
                    {"route", "status", "le", "event", "outcome"},
                    rendered,
                )

    def test_metrics_carry_no_user_content(self):
        self.client.post(
            "/api/auth/verify",
            json={"email": "SECRET_EMAIL_38@example.com", "code": "SECRET_TOKEN_38"},
        )
        rendered = self.client.get("/api/metrics").text
        for sentinel in ("SECRET_EMAIL_38", "SECRET_TOKEN_38", "SECRET_TRANSCRIPT_38"):
            self.assertNotIn(sentinel, rendered)

    # ------------------------------------------------------- limitation

    def test_the_auth_route_is_really_rate_limited(self):
        quota = ratelimit.AUTH_START.limit
        codes = [
            self.client.post(
                "/api/auth/request", json={"email": "limited@example.com"}
            ).status_code
            for _ in range(quota + 3)
        ]
        self.assertIn(429, codes, "le quota doit finir par mordre")
        self.assertEqual(codes[0], 200)

    def test_the_refusal_reveals_nothing_about_the_counter(self):
        for _ in range(ratelimit.AUTH_START.limit + 2):
            response = self.client.post(
                "/api/auth/request", json={"email": "quiet@example.com"}
            )
        self.assertEqual(response.status_code, 429)
        body = response.text
        # Ni l'identité comptée, ni le quota, ni la fenêtre : un attaquant qui
        # apprend quand sa fenêtre se rouvre apprend à la contourner.
        self.assertNotIn("quiet@example.com", body)
        self.assertNotIn(str(ratelimit.AUTH_START.limit), body)
        self.assertNotIn("retry", body.lower())

    def test_two_addresses_do_not_share_a_bucket(self):
        for _ in range(ratelimit.AUTH_START.limit + 1):
            self.client.post("/api/auth/request", json={"email": "first@example.com"})
        other = self.client.post("/api/auth/request", json={"email": "second@example.com"})
        self.assertEqual(other.status_code, 200)

    def test_the_development_fallback_admits_it_shares_nothing(self):
        # Sans Redis, la protection existe mais ne franchit pas la frontière
        # d'un processus — et le service le dit au lieu de le laisser croire.
        self.assertFalse(main.rate_limiter().shared)
        self.assertIn("nova_rate_limiter_shared 0", self.client.get("/api/metrics").text)


if __name__ == "__main__":
    unittest.main()
