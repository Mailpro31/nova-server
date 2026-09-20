"""Trouver son organisation avec sa seule adresse e-mail.

Un etudiant ne connait pas l'adresse HTTPS de son ecole, et n'a aucune raison
de la connaitre. Il tape « prenom.nom@example.com » ; le poste prend le
domaine, lit l'enregistrement DNS `_nova.example.com`, et y trouve l'adresse
du serveur.

Ce que le serveur apporte ici :

- **un enregistrement a publier**, calcule a partir de l'adresse de service :
  la console le montre pret a coller, et personne ne l'ecrit a la main ;
- **une verification** : la console resout le DNS et dit ce qu'elle a trouve —
  rien, un autre serveur, ou la bonne adresse ;
- **une confirmation cote poste** : le serveur atteint dit s'il sert bien ce
  domaine. Sans elle, un enregistrement DNS suffirait a envoyer les postes
  d'une ecole vers le serveur de quelqu'un d'autre.

Le domaine n'est jamais divulgue en liste : on repond a la question posee
(« sers-tu example.com ? »), qui est deja publique dans le DNS.
"""

import unittest
from unittest.mock import patch

from test_main import AdminTestCase  # noqa: F401

import main

main.init_db()

ENDPOINT = "https://nova.example.com"


class RecordTests(unittest.TestCase):
    def test_the_record_is_built_from_the_service_endpoint(self):
        record = main.discovery_dns_record("example.com", ENDPOINT)
        self.assertEqual(record["name"], "_nova.example.com")
        self.assertEqual(record["type"], "TXT")
        self.assertEqual(record["value"], f"v=nova1; endpoint={ENDPOINT}")

    def test_without_an_endpoint_there_is_nothing_to_publish(self):
        self.assertIsNone(main.discovery_dns_record("example.com", ""))

    def test_a_published_record_is_read_back(self):
        self.assertEqual(
            main.read_discovery_record(['v=nova1; endpoint=https://nova.example.com']),
            "https://nova.example.com",
        )
        # Les guillemets que certains hebergeurs DNS ajoutent, et les espaces.
        self.assertEqual(
            main.read_discovery_record(['"v=nova1;endpoint=https://nova.example.com "']),
            "https://nova.example.com",
        )
        # Plusieurs enregistrements sur le meme nom : seul celui de Nova compte.
        self.assertEqual(
            main.read_discovery_record(["v=spf1 -all", "v=nova1; endpoint=" + ENDPOINT]),
            ENDPOINT,
        )

    def test_anything_else_is_not_a_nova_record(self):
        for value in ([], ["v=spf1 -all"], ["v=nova1"], ["v=nova2; endpoint=" + ENDPOINT],
                      ["v=nova1; endpoint="]):
            self.assertIsNone(main.read_discovery_record(value))


class ServesDomainTests(AdminTestCase):
    PATH = "/api/discovery/email-domain"

    def test_the_server_confirms_the_domain_it_serves(self):
        response = self.client.get(self.PATH, params={"domain": "Example.COM"})
        self.assertEqual(response.status_code, 200, response.text)
        body = response.json()
        self.assertTrue(body["serves"])
        self.assertEqual(body["domain"], "example.com")
        self.assertTrue(body["organization"]["name"])
        # Le poste recoupe l'adresse du DNS avec celle que le serveur annonce.
        self.assertIn("service_endpoint", body)

    def test_a_domain_it_does_not_serve_is_refused_without_a_list(self):
        response = self.client.get(self.PATH, params={"domain": "ailleurs.test"})
        self.assertEqual(response.status_code, 200, response.text)
        self.assertFalse(response.json()["serves"])
        self.assertNotIn("organization", response.json())
        # Aucune enumeration : la reponse ne porte jamais les autres domaines.
        self.assertNotIn("example.com", response.text)

    def test_a_missing_or_absurd_domain_is_refused(self):
        self.assertEqual(self.client.get(self.PATH).status_code, 422)
        for domain in ("", "   ", "pas un domaine", "x" * 300):
            with self.subTest(domain=domain):
                response = self.client.get(self.PATH, params={"domain": domain})
                self.assertEqual(response.status_code, 400, response.text)
                self.assertEqual(response.json()["detail"], "DOMAIN_INVALID")

    def test_no_session_is_needed(self):
        # Le poste n'a pas encore de compte : cette route precede la connexion.
        self.assertEqual(self.client.get(self.PATH, params={"domain": "example.com"}
                                         ).status_code, 200)


class ConsoleCheckTests(AdminTestCase):
    def setUp(self):
        super().setUp()
        self.admin = self.admin_session()
        self.organization = main.nova_organization_id()
        with main.db() as conn:
            conn.execute("UPDATE organizations SET service_endpoint=? WHERE id=?",
                         (ENDPOINT, self.organization))

    def path(self) -> str:
        return f"/api/control/organizations/{self.organization}/discovery/dns"

    def check(self, found, headers=None):
        def resolver(name: str) -> list[str]:
            self.resolved = name
            return found

        with patch.object(main, "resolve_txt_record", resolver):
            return self.client.post(self.path(), headers=headers or self.auth(self.admin),
                                    json={"domain": "example.com"})

    def test_the_console_shows_the_record_to_publish(self):
        response = self.client.get(self.path(), headers=self.auth(self.admin))
        self.assertEqual(response.status_code, 200, response.text)
        body = response.json()
        self.assertEqual(body["service_endpoint"], ENDPOINT)
        [record] = body["records"]
        self.assertEqual(record["name"], "_nova.example.com")
        self.assertEqual(record["value"], f"v=nova1; endpoint={ENDPOINT}")

    def test_a_published_record_is_reported_as_matching(self):
        response = self.check([f"v=nova1; endpoint={ENDPOINT}"])
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["status"], "match")
        self.assertEqual(self.resolved, "_nova.example.com")

    def test_a_record_pointing_elsewhere_is_reported(self):
        response = self.check(["v=nova1; endpoint=https://nova.ailleurs.test"])
        self.assertEqual(response.json()["status"], "mismatch")
        self.assertEqual(response.json()["found_endpoint"], "https://nova.ailleurs.test")

    def test_no_record_at_all_is_reported(self):
        self.assertEqual(self.check([]).json()["status"], "missing")

    def test_a_dns_failure_is_reported_as_such_not_as_an_absence(self):
        def failing(name: str) -> list[str]:
            raise main.DiscoveryLookupError("DNS_UNAVAILABLE")

        with patch.object(main, "resolve_txt_record", failing):
            response = self.client.post(self.path(), headers=self.auth(self.admin),
                                        json={"domain": "example.com"})
        self.assertEqual(response.status_code, 502, response.text)
        self.assertEqual(response.json()["detail"], "DNS_UNAVAILABLE")

    def test_only_a_domain_of_this_organization_is_checked(self):
        response = self.client.post(self.path(), headers=self.auth(self.admin),
                                    json={"domain": "ailleurs.test"})
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json()["detail"], "DOMAIN_NOT_SERVED")

    def test_checking_needs_an_administrator(self):
        member = self.sign_in("curieux@example.com")
        self.assertIn(self.check([], headers=self.auth(member)).status_code, (401, 403))


class DocumentationTests(unittest.TestCase):
    def test_the_https_deployment_guide_exists_and_says_what_matters(self):
        from pathlib import Path

        guide = (Path(__file__).resolve().parent / "docs" / "deployment"
                 / "https-and-email-discovery.md").read_text(encoding="utf-8").lower()
        for topic in ("tunnel", "nova_trusted_proxies", "nova_service_endpoint",
                      "nova_admin_origins", "_nova.", "entra"):
            self.assertIn(topic, guide)


if __name__ == "__main__":
    unittest.main()
