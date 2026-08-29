"""Observabilité : ce qu'elle expose, et surtout ce qu'elle n'expose jamais."""

import io
import json
import logging
import os
import re
import tempfile
import unittest

import observability


class RequestIdTests(unittest.TestCase):
    def test_a_well_formed_client_identifier_is_kept(self):
        self.assertEqual(observability.accept_request_id("abc-123_XYZ"), "abc-123_XYZ")

    def test_a_hostile_or_malformed_identifier_is_replaced(self):
        # Il traverse les journaux et l'en-tête de réponse : l'accepter tel
        # quel en ferait un canal d'injection commode.
        for raw in (
            None, "", "short", "a" * 200, "with space", "with\nnewline",
            "<script>alert(1)</script>", "id;DROP TABLE users",
            "../../etc/passwd", "\x00binary",
        ):
            generated = observability.accept_request_id(raw)
            self.assertRegex(generated, r"^[0-9a-f]{32}$", f"input {raw!r}")

    def test_two_generated_identifiers_differ(self):
        self.assertNotEqual(
            observability.accept_request_id(None), observability.accept_request_id(None)
        )


class RouteClassTests(unittest.TestCase):
    def test_identifiers_never_leak_into_the_route_class(self):
        # C'est la garantie de cardinalité : mille organisations ne doivent pas
        # produire mille séries temporelles.
        for path, expected in (
            ("/scim/v2/Users/8f14e45f", "scim"),
            ("/api/control/organizations/org-abc123/deployment", "deployment"),
            ("/api/control/organizations/org-abc123/provisioning", "provisioning"),
            ("/api/control/organizations/org-abc123/members", "control"),
            ("/api/control/connectors/c-999/revoke", "connector"),
            ("/api/auth/verify", "auth"),
            ("/api/admin/step-up/start", "admin"),
            ("/api/readyz", "operational"),
            ("/nowhere", "other"),
        ):
            label = observability.route_class(path)
            self.assertEqual(label, expected, path)
            self.assertNotIn("org-abc123", label)
            self.assertNotIn("8f14e45f", label)


class StructuredLogTests(unittest.TestCase):
    def setUp(self):
        self.stream = io.StringIO()
        observability.configure_logging("INFO", self.stream)
        observability.set_request_id("correlation-1234")
        self.addCleanup(observability.set_request_id, None)
        self.addCleanup(logging.getLogger("nova").handlers.clear)

    def lines(self):
        return [json.loads(line) for line in self.stream.getvalue().splitlines() if line]

    def test_an_event_is_one_json_line_with_its_correlation_id(self):
        observability.log("scim.user.created", route="scim", status=201)
        entry = self.lines()[-1]
        self.assertEqual(entry["event"], "scim.user.created")
        self.assertEqual(entry["level"], "info")
        self.assertEqual(entry["request_id"], "correlation-1234")
        self.assertEqual(entry["route"], "scim")
        self.assertIn("timestamp", entry)

    def test_forbidden_fields_are_redacted_not_truncated(self):
        # Le champ est nommé, sa valeur ne l'est pas : un opérateur voit qu'il
        # y avait quelque chose, sans que la chose sorte.
        observability.log(
            "auth.attempt",
            token="SECRET_TOKEN_38",
            password="SECRET_TOKEN_38",
            transcript="SECRET_TRANSCRIPT_38",
            prompt="SECRET_PROMPT_38",
            email="SECRET_EMAIL_38",
            authorization="Bearer SECRET_TOKEN_38",
            route="auth",
        )
        raw = self.stream.getvalue()
        for sentinel in (
            "SECRET_TOKEN_38", "SECRET_TRANSCRIPT_38",
            "SECRET_PROMPT_38", "SECRET_EMAIL_38",
        ):
            self.assertNotIn(sentinel, raw, sentinel)
        entry = self.lines()[-1]
        self.assertEqual(entry["token"], "<redacted>")
        self.assertEqual(entry["transcript"], "<redacted>")
        self.assertEqual(entry["route"], "auth", "les champs sûrs restent lisibles")


class MetricsTests(unittest.TestCase):
    def setUp(self):
        self.metrics = observability.Metrics()

    def test_requests_and_latency_are_counted_by_route_class(self):
        self.metrics.observe_request("scim", 201, 0.01)
        self.metrics.observe_request("scim", 200, 0.02)
        self.metrics.observe_request("auth", 429, 0.001)
        rendered = self.metrics.render()
        self.assertIn('nova_http_requests_total{route="scim",status="2xx"} 2', rendered)
        self.assertIn('nova_http_requests_total{route="auth",status="4xx"} 1', rendered)
        self.assertIn('nova_http_latency_seconds_count{route="scim"} 2', rendered)

    def test_server_errors_are_counted_separately(self):
        self.metrics.observe_request("control", 500, 0.5)
        self.assertIn('nova_http_errors_total{route="control"} 1', self.metrics.render())

    def test_operational_events_are_countable_with_bounded_labels(self):
        self.metrics.count("scim.user.created")
        self.metrics.count("scim.user.created")
        self.metrics.count("connector.sync", "failed")
        rendered = self.metrics.render()
        self.assertIn('nova_events_total{event="scim.user.created",outcome="ok"} 2', rendered)
        self.assertIn('nova_events_total{event="connector.sync",outcome="failed"} 1', rendered)

    def test_no_label_ever_carries_an_identifier(self):
        self.metrics.observe_request("scim", 200, 0.01)
        self.metrics.count("deployment.verify")
        rendered = self.metrics.render({"db_pool_idle": 3})
        labels = re.findall(r'\{([^}]*)\}', rendered)
        for group in labels:
            for pair in group.split(","):
                name = pair.split("=", 1)[0]
                self.assertIn(
                    name,
                    {"route", "status", "le", "event", "outcome"},
                    f"étiquette non bornée: {name}",
                )

    def test_gauges_are_rendered(self):
        rendered = self.metrics.render({"db_pool_leased": 2, "rate_limiter_shared": 1})
        self.assertIn("nova_db_pool_leased 2", rendered)
        self.assertIn("nova_rate_limiter_shared 1", rendered)


class SecurityHeaderTests(unittest.TestCase):
    def test_production_adds_hsts_and_development_does_not(self):
        production = observability.security_headers(production=True)
        development = observability.security_headers(production=False)
        self.assertIn("Strict-Transport-Security", production)
        self.assertNotIn("Strict-Transport-Security", development)

    def test_the_api_policy_forbids_framing_and_loading(self):
        headers = observability.security_headers(production=True)
        policy = headers["Content-Security-Policy"]
        self.assertIn("frame-ancestors 'none'", policy)
        self.assertIn("default-src 'none'", policy)
        self.assertEqual(headers["X-Content-Type-Options"], "nosniff")
        self.assertEqual(headers["Referrer-Policy"], "no-referrer")

    def test_no_unsafe_directive_was_added_to_make_something_pass(self):
        policy = observability.security_headers(production=True)["Content-Security-Policy"]
        self.assertNotIn("unsafe-inline", policy)
        self.assertNotIn("unsafe-eval", policy)
        self.assertNotIn("*", policy)


class TrustedProxyTests(unittest.TestCase):
    def test_a_forwarded_header_from_an_untrusted_peer_is_ignored(self):
        # Sans cela, toute limite par adresse serait contournable en une ligne.
        address = observability.client_address(
            "203.0.113.9", "1.2.3.4", frozenset({"10.0.0.1"})
        )
        self.assertEqual(address, "203.0.113.9")

    def test_a_forwarded_header_from_a_trusted_proxy_is_used(self):
        address = observability.client_address(
            "10.0.0.1", "198.51.100.7", frozenset({"10.0.0.1"})
        )
        self.assertEqual(address, "198.51.100.7")

    def test_the_last_hop_wins_because_the_client_controls_the_first(self):
        address = observability.client_address(
            "10.0.0.1", "1.2.3.4, 5.6.7.8, 198.51.100.7", frozenset({"10.0.0.1"})
        )
        self.assertEqual(address, "198.51.100.7")

    def test_no_proxy_is_trusted_by_default(self):
        empty = observability.trusted_proxies_from_environment("")
        self.assertEqual(empty, frozenset())
        self.assertEqual(
            observability.client_address("10.0.0.1", "1.2.3.4", empty), "10.0.0.1"
        )

    def test_the_proxy_list_is_explicit_addresses_only(self):
        parsed = observability.trusted_proxies_from_environment(" 10.0.0.1, 10.0.0.2 ")
        self.assertEqual(parsed, frozenset({"10.0.0.1", "10.0.0.2"}))


class ReadinessTests(unittest.TestCase):
    def test_ready_only_when_every_component_answers(self):
        result = observability.readiness({"database": lambda: True, "redis": lambda: True})
        self.assertTrue(result["ready"])
        self.assertEqual(result["components"], {"database": "ok", "redis": "ok"})

    def test_one_unavailable_component_makes_the_service_not_ready(self):
        result = observability.readiness({"database": lambda: True, "redis": lambda: False})
        self.assertFalse(result["ready"])
        self.assertEqual(result["components"]["redis"], "unavailable")

    def test_a_probe_that_raises_is_an_error_not_a_crash(self):
        def broken():
            raise RuntimeError("postgresql://user:pass@host/db is unreachable")

        result = observability.readiness({"database": broken})
        self.assertFalse(result["ready"])
        self.assertEqual(result["components"]["database"], "error")
        # Le détail de l'exception ne ressort pas : il portait une URL complète.
        self.assertNotIn("postgresql", json.dumps(result))

    def test_the_response_carries_no_infrastructure_detail(self):
        result = observability.readiness({"database": lambda: True})
        serialized = json.dumps(result)
        for forbidden in ("postgresql://", "redis://", "password", "@", "/run/secrets"):
            self.assertNotIn(forbidden, serialized, forbidden)


if __name__ == "__main__":
    unittest.main()
