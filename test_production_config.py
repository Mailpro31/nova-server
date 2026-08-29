"""La matrice de configuration de production : ce qui est refusé, et pourquoi."""

import os
import tempfile
import unittest
from contextlib import contextmanager

import production_config


BASE = {
    "NOVA_ENV": "production",
    "NOVA_DATABASE_URL": "postgresql://nova@db.internal:5432/nova",
    "NOVA_REDIS_URL": "redis://cache.internal:6379/0",
}


@contextmanager
def environment(**overrides):
    """Un environnement complet, reconstruit à chaque cas."""
    saved = dict(os.environ)
    for key in list(os.environ):
        if key.startswith("NOVA_"):
            del os.environ[key]
    os.environ.update({key: value for key, value in overrides.items() if value is not None})
    try:
        yield
    finally:
        os.environ.clear()
        os.environ.update(saved)


class NonProductionTests(unittest.TestCase):
    def test_development_and_test_are_left_alone(self):
        # Le durcissement ne doit pas rendre le développement impraticable.
        for env in ("development", "test"):
            with environment(NOVA_ENV=env, NOVA_DB="nova.db"):
                report = production_config.check()
                self.assertTrue(report.ok, f"{env}: {report.problems}")


class DatabaseTests(unittest.TestCase):
    def test_production_refuses_sqlite(self):
        with environment(**{**BASE, "NOVA_DATABASE_URL": "sqlite:///nova.db"}):
            self.assertIn("SQLITE_REFUSED_IN_PRODUCTION", production_config.check().problems)

    def test_production_requires_an_explicit_url(self):
        with environment(NOVA_ENV="production", NOVA_REDIS_URL=BASE["NOVA_REDIS_URL"],
                         NOVA_DB="nova.db"):
            self.assertIn(
                "DATABASE_URL_REQUIRED_IN_PRODUCTION", production_config.check().problems
            )

    def test_a_malformed_url_is_refused(self):
        with environment(**{**BASE, "NOVA_DATABASE_URL": "mysql://h/db"}):
            self.assertIn("DATABASE_URL_INVALID", production_config.check().problems)


class RedisTests(unittest.TestCase):
    def test_production_requires_a_shared_counter(self):
        # Sans lui, les routes fail-closed refusent tout dès le premier appel :
        # le service serait « démarré » et inutilisable.
        with environment(NOVA_ENV="production", NOVA_DATABASE_URL=BASE["NOVA_DATABASE_URL"]):
            self.assertIn(
                "REDIS_URL_REQUIRED_IN_PRODUCTION", production_config.check().problems
            )

    def test_a_deployment_can_declare_that_it_does_not_need_one(self):
        with environment(NOVA_ENV="production", NOVA_DATABASE_URL=BASE["NOVA_DATABASE_URL"]):
            report = production_config.check(require_redis=False)
            self.assertNotIn("REDIS_URL_REQUIRED_IN_PRODUCTION", report.problems)


class OriginTests(unittest.TestCase):
    def test_a_public_origin_must_be_tls(self):
        with environment(**BASE, NOVA_SERVICE_ENDPOINT="http://nova.example.com"):
            self.assertIn(
                "NOVA_SERVICE_ENDPOINT_ORIGIN_NOT_TLS", production_config.check().problems
            )

    def test_a_public_origin_must_not_be_local(self):
        for endpoint in ("https://localhost", "https://127.0.0.1:8000", "https://[::1]"):
            with environment(**BASE, NOVA_SERVICE_ENDPOINT=endpoint):
                self.assertIn(
                    "NOVA_SERVICE_ENDPOINT_ORIGIN_NOT_PUBLIC",
                    production_config.check().problems,
                    endpoint,
                )

    def test_a_proper_public_origin_passes(self):
        with environment(**BASE, NOVA_SERVICE_ENDPOINT="https://nova.example.com"):
            self.assertTrue(production_config.check().ok)

    def test_admin_console_origins_are_checked_too(self):
        with environment(**BASE, NOVA_ADMIN_ORIGINS="https://admin.example.com,http://evil"):
            self.assertIn(
                "NOVA_ADMIN_ORIGINS_ORIGIN_NOT_TLS", production_config.check().problems
            )

    def test_a_local_bind_address_is_not_judged(self):
        # Écouter sur 127.0.0.1 derrière un reverse proxy est normal ; c'est
        # l'origine *annoncée* qui est jugée.
        with environment(**BASE, NOVA_SERVICE_ENDPOINT="https://nova.example.com",
                         NOVA_PORT="8000"):
            self.assertTrue(production_config.check().ok)


class SecretTests(unittest.TestCase):
    def test_a_weak_secret_is_refused(self):
        for weak in ("changeme", "password", "test", "CHANGEME", "  secret  "):
            with environment(**BASE, NOVA_GOOGLE_CLIENT_ID="id",
                             NOVA_GOOGLE_CLIENT_SECRET=weak):
                self.assertIn(
                    "NOVA_GOOGLE_CLIENT_SECRET_WEAK",
                    production_config.check().problems,
                    weak,
                )

    def test_a_short_secret_is_refused(self):
        with environment(**BASE, NOVA_GOOGLE_CLIENT_ID="id", NOVA_GOOGLE_CLIENT_SECRET="abc123"):
            self.assertIn(
                "NOVA_GOOGLE_CLIENT_SECRET_TOO_SHORT", production_config.check().problems
            )

    def test_a_missing_secret_is_refused_when_the_provider_is_configured(self):
        with environment(**BASE, NOVA_OIDC_CLIENT_ID="id"):
            self.assertIn(
                "NOVA_OIDC_CLIENT_SECRET_MISSING", production_config.check().problems
            )

    def test_no_secret_is_required_when_its_provider_is_not_configured(self):
        # Un déploiement sans Google n'a aucune raison de porter un secret Google.
        with environment(**BASE):
            self.assertTrue(production_config.check().ok)

    def test_a_strong_secret_passes(self):
        with environment(**BASE, NOVA_GOOGLE_CLIENT_ID="id",
                         NOVA_GOOGLE_CLIENT_SECRET="G0c-9f3b21aa77de4419bc0e"):
            self.assertTrue(production_config.check().ok)

    def test_a_reference_is_resolved_before_being_judged(self):
        # `env://X` peut désigner une variable qui vaut « changeme » : c'est la
        # valeur résolue qui compte, jamais la référence.
        with environment(**BASE, NOVA_GOOGLE_CLIENT_ID="id",
                         NOVA_GOOGLE_CLIENT_SECRET="env://NOVA_UPSTREAM_SECRET",
                         NOVA_UPSTREAM_SECRET="changeme"):
            self.assertIn(
                "NOVA_GOOGLE_CLIENT_SECRET_WEAK", production_config.check().problems
            )

    def test_a_file_reference_works_end_to_end(self):
        with tempfile.TemporaryDirectory() as directory:
            path = os.path.join(directory, "google_client_secret")
            with open(path, "w", encoding="utf-8") as handle:
                handle.write("G0c-9f3b21aa77de4419bc0e\n")
            reference = "file://" + path.replace("\\", "/")
            with environment(**BASE, NOVA_GOOGLE_CLIENT_ID="id",
                             NOVA_GOOGLE_CLIENT_SECRET=reference):
                self.assertTrue(production_config.check().ok)

    def test_an_unresolvable_reference_is_refused_not_ignored(self):
        with environment(**BASE, NOVA_GOOGLE_CLIENT_ID="id",
                         NOVA_GOOGLE_CLIENT_SECRET="env://NOVA_ABSENT_SECRET"):
            problems = production_config.check().problems
            self.assertTrue(
                any(p.startswith("NOVA_GOOGLE_CLIENT_SECRET_UNRESOLVABLE") for p in problems),
                problems,
            )


class DevelopmentFlagTests(unittest.TestCase):
    def test_every_insecure_flag_is_forbidden_in_production(self):
        for flag in (
            "NOVA_CONNECTOR_ALLOW_INSECURE",
            "NOVA_CONNECTOR_RELEASE_ALLOW_INSECURE",
            "NOVA_DISCOVERY_ALLOW_INSECURE_ENDPOINT",
            "NOVA_ALLOW_LEGACY_DEPLOYMENT_TOKEN",
        ):
            with environment(**BASE, **{flag: "true"}):
                self.assertIn(
                    f"{flag}_FORBIDDEN_IN_PRODUCTION",
                    production_config.check().problems,
                    flag,
                )

    def test_a_removed_authentication_variable_is_refused(self):
        # Sa présence signale une configuration héritée qui croit encore
        # qu'un chemin retiré fonctionne.
        for removed in ("NOVA_ADMIN_TOKEN", "NOVA_DEPLOYMENT_APPLY_TOKEN"):
            with environment(**BASE, **{removed: "anything"}):
                self.assertIn(
                    f"{removed}_NO_LONGER_SUPPORTED", production_config.check().problems
                )


class TrustRootTests(unittest.TestCase):
    def test_a_release_manifest_without_a_trust_root_is_refused(self):
        with environment(**BASE, NOVA_CONNECTOR_RELEASE_MANIFEST="/srv/releases.json"):
            self.assertIn(
                "CONNECTOR_RELEASE_TRUST_ROOT_MISSING", production_config.check().problems
            )


class EnforcementTests(unittest.TestCase):
    def test_enforce_raises_with_every_problem_at_once(self):
        # Un opérateur qui corrige veut la liste, pas une découverte par
        # redémarrage.
        with environment(NOVA_ENV="production", NOVA_DATABASE_URL="sqlite:///nova.db",
                         NOVA_OIDC_CLIENT_ID="id"):
            with self.assertRaises(production_config.ConfigurationRefused) as raised:
                production_config.enforce()
            problems = raised.exception.problems
            self.assertIn("SQLITE_REFUSED_IN_PRODUCTION", problems)
            self.assertIn("REDIS_URL_REQUIRED_IN_PRODUCTION", problems)
            self.assertIn("NOVA_OIDC_CLIENT_SECRET_MISSING", problems)

    def test_a_valid_production_configuration_is_accepted(self):
        with environment(**BASE, NOVA_SERVICE_ENDPOINT="https://nova.example.com"):
            self.assertTrue(production_config.enforce().ok)

    def test_the_report_never_carries_a_secret_value(self):
        with environment(**BASE, NOVA_GOOGLE_CLIENT_ID="id",
                         NOVA_GOOGLE_CLIENT_SECRET="SECRET_TOKEN_38_leaked"):
            described = str(production_config.describe())
            self.assertNotIn("SECRET_TOKEN_38", described)


if __name__ == "__main__":
    unittest.main()
