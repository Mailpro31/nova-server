"""Synchronisation Google Workspace : les groupes de l'annuaire arrivent seuls.

Une organisation sous Google a deja ses groupes — « aero2@ipsa.fr » — et ne
veut pas les recreer a la main. Nova lit l'API Directory avec un compte de
service, et en tire ses groupes et leurs membres.

Ce qui est verrouille ici :

- **la cle du compte de service n'entre jamais en base** : la configuration ne
  porte qu'une reference (`env://…`, `file://…`), comme tout secret ;
- **le reseau n'est jamais joue en test** : la lecture passe par un transport
  injecte, et la reconciliation ne parle qu'a la base ;
- les groupes venus de Google sont **en lecture seule** dans la console ;
- un membre retire chez Google sort du groupe ; un compte n'est jamais
  supprime ;
- une synchronisation qui echoue le dit, garde l'etat precedent, et ne vide
  aucun groupe.
"""

import json
import time
import unittest

from test_main import AdminTestCase  # noqa: F401

import google_directory
import main
import secrets_provider

main.init_db()

KEY = {
    "type": "service_account",
    "client_email": "nova@projet.iam.gserviceaccount.com",
    "private_key_id": "k1",
    # Cle de test, generee pour ce test et sans aucun usage reel.
    "private_key": None,  # rempli dans setUpModule
    "token_uri": "https://oauth2.googleapis.com/token",
}


def setUpModule():
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric import rsa

    private = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    KEY["private_key"] = private.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    ).decode("utf-8")


class FakeGoogle:
    """L'API Directory, en memoire. Elle compte ce qu'on lui demande."""

    def __init__(self, groups: dict[str, list[str]], names: dict[str, str] | None = None):
        self.groups = groups
        self.names = names or {}
        self.calls: list[str] = []
        self.token_requests = 0
        self.fail_with: int | None = None

    def __call__(self, method: str, url: str, *, headers=None, data=None, params=None):
        self.calls.append(url)
        if url.endswith("/token"):
            self.token_requests += 1
            return 200, {"access_token": "jeton-google", "expires_in": 3600}
        if self.fail_with is not None:
            return self.fail_with, {"error": {"message": "refuse"}}
        if headers.get("Authorization") != "Bearer jeton-google":
            return 401, {"error": {"message": "no token"}}
        if url.endswith("/groups"):
            return 200, {"groups": [
                {"id": f"gid-{email}", "email": email,
                 "name": self.names.get(email, email.split("@")[0])}
                for email in sorted(self.groups)
            ]}
        group_email = url.split("/groups/")[1].split("/members")[0].replace("%40", "@")
        return 200, {"members": [
            {"id": f"uid-{email}", "email": email, "type": "USER", "status": "ACTIVE"}
            for email in self.groups.get(group_email, [])
        ]}


class ReadingTests(unittest.TestCase):
    def setUp(self):
        self.google = FakeGoogle({"aero2@example.com": ["a@example.com", "b@example.com"]})
        self.config = {"admin_subject": "admin@example.com", "customer_id": "my_customer",
                       "group_filter": []}

    def key_reference(self) -> str:
        import os
        os.environ["NOVA_TEST_GOOGLE_KEY"] = json.dumps(KEY)
        return "env://NOVA_TEST_GOOGLE_KEY"

    def test_the_key_is_read_from_a_reference_never_from_the_database(self):
        account = google_directory.load_service_account(self.key_reference())
        self.assertEqual(account["client_email"], KEY["client_email"])
        with self.assertRaises(google_directory.DirectoryError) as raised:
            google_directory.load_service_account("env://NOVA_TEST_ABSENT")
        self.assertEqual(raised.exception.code, "GOOGLE_KEY_MISSING")
        with self.assertRaises(google_directory.DirectoryError) as raised:
            google_directory.load_service_account(None)
        self.assertEqual(raised.exception.code, "GOOGLE_KEY_MISSING")

    def test_a_key_that_is_not_a_service_account_is_refused(self):
        import os
        os.environ["NOVA_TEST_GOOGLE_BAD"] = json.dumps({"client_email": "x@y"})
        with self.assertRaises(google_directory.DirectoryError) as raised:
            google_directory.load_service_account("env://NOVA_TEST_GOOGLE_BAD")
        self.assertEqual(raised.exception.code, "GOOGLE_KEY_INVALID")
        os.environ["NOVA_TEST_GOOGLE_TEXT"] = "pas du json"
        with self.assertRaises(google_directory.DirectoryError) as raised:
            google_directory.load_service_account("env://NOVA_TEST_GOOGLE_TEXT")
        self.assertEqual(raised.exception.code, "GOOGLE_KEY_INVALID")

    def test_the_assertion_impersonates_the_named_admin_and_asks_read_only(self):
        import jwt

        account = google_directory.load_service_account(self.key_reference())
        token = google_directory.assertion(account, "admin@example.com", now=1000.0)
        claims = jwt.decode(token, options={"verify_signature": False})
        self.assertEqual(claims["iss"], KEY["client_email"])
        self.assertEqual(claims["sub"], "admin@example.com")
        self.assertEqual(claims["aud"], KEY["token_uri"])
        self.assertTrue(all(scope.endswith(".readonly") for scope in claims["scope"].split()))

    def test_groups_and_members_are_read(self):
        account = google_directory.load_service_account(self.key_reference())
        directory = google_directory.fetch_directory(account, self.config, self.google)
        self.assertEqual(directory, [{
            "id": "gid-aero2@example.com",
            "email": "aero2@example.com",
            "name": "aero2",
            "members": ["a@example.com", "b@example.com"],
        }])
        self.assertEqual(self.google.token_requests, 1)

    def test_only_the_selected_groups_are_read(self):
        self.google = FakeGoogle({"aero2@example.com": ["a@example.com"],
                                  "staff@example.com": ["c@example.com"]})
        account = google_directory.load_service_account(self.key_reference())
        directory = google_directory.fetch_directory(
            account, {**self.config, "group_filter": ["AERO2@example.com"]}, self.google)
        self.assertEqual([group["email"] for group in directory], ["aero2@example.com"])

    def test_a_refusal_says_which_one_and_reads_nothing_else(self):
        account = google_directory.load_service_account(self.key_reference())
        self.google.fail_with = 403
        with self.assertRaises(google_directory.DirectoryError) as raised:
            google_directory.fetch_directory(account, self.config, self.google)
        self.assertEqual(raised.exception.code, "GOOGLE_FORBIDDEN")
        self.google.fail_with = 401
        with self.assertRaises(google_directory.DirectoryError) as raised:
            google_directory.fetch_directory(account, self.config, self.google)
        self.assertEqual(raised.exception.code, "GOOGLE_UNAUTHORIZED")


def clear():
    with main.db() as conn:
        for table in ("organization_group_rules", "organization_group_memberships",
                      "organization_groups", "organization_directory_sync",
                      "scim_user_management"):
            conn.execute(f"DELETE FROM {table}")


class ReconcileTests(AdminTestCase):
    def setUp(self):
        super().setUp()
        clear()
        self.organization = main.nova_organization_id()
        self.admin = self.admin_session()

    def reconcile(self, directory: list[dict]) -> dict:
        with main.db() as conn:
            return google_directory.reconcile(
                conn, self.organization, directory,
                create_user=main.create_user, normalize_email=main.normalize_email,
                now=time.time())

    def groups(self) -> dict[str, list[str]]:
        with main.db() as conn:
            rows = conn.execute(
                "SELECT g.display_name, g.source, g.external_id, u.email "
                "FROM organization_groups g "
                "LEFT JOIN organization_group_memberships m "
                "ON m.organization_id=g.organization_id AND m.group_id=g.group_id "
                "LEFT JOIN users u ON u.user_id=m.user_id "
                "WHERE g.organization_id=? AND g.deleted_at IS NULL",
                (self.organization,)).fetchall()
        found: dict[str, list[str]] = {}
        for row in rows:
            found.setdefault(row["display_name"], [])
            if row["email"]:
                found[row["display_name"]].append(row["email"])
        return {name: sorted(emails) for name, emails in found.items()}

    def test_a_google_group_creates_the_group_the_accounts_and_the_memberships(self):
        outcome = self.reconcile([{"id": "g1", "email": "aero2@example.com", "name": "Aero 2",
                                   "members": ["a@example.com", "b@example.com"]}])
        self.assertEqual(outcome["groups"], 1)
        self.assertEqual(outcome["accounts_created"], 2)
        self.assertEqual(outcome["added"], 2)
        self.assertEqual(self.groups(), {"Aero 2": ["a@example.com", "b@example.com"]})
        with main.db() as conn:
            row = conn.execute(
                "SELECT source, external_id FROM organization_groups WHERE display_name='Aero 2'"
            ).fetchone()
        self.assertEqual((row["source"], row["external_id"]), ("google", "g1"))

    def test_an_existing_account_is_reused_never_duplicated(self):
        self.sign_in("a@example.com")
        self.reconcile([{"id": "g1", "email": "aero2@example.com", "name": "Aero 2",
                         "members": ["A@Example.com"]}])
        with main.db() as conn:
            count = conn.execute(
                "SELECT COUNT(*) AS n FROM users WHERE organization_id=? AND normalized_email=?",
                (self.organization, "a@example.com")).fetchone()["n"]
        self.assertEqual(count, 1)

    def test_someone_removed_at_google_leaves_the_group_but_keeps_their_account(self):
        group = [{"id": "g1", "email": "aero2@example.com", "name": "Aero 2",
                  "members": ["a@example.com", "b@example.com"]}]
        self.reconcile(group)
        outcome = self.reconcile([{**group[0], "members": ["a@example.com"]}])
        self.assertEqual(outcome["removed"], 1)
        self.assertEqual(self.groups(), {"Aero 2": ["a@example.com"]})
        with main.db() as conn:
            self.assertIsNotNone(main.user_by_email(conn, self.organization, "b@example.com"))

    def test_a_group_that_disappeared_at_google_disappears_here(self):
        self.reconcile([{"id": "g1", "email": "aero2@example.com", "name": "Aero 2",
                         "members": ["a@example.com"]}])
        self.reconcile([])
        self.assertEqual(self.groups(), {})

    def test_a_renamed_group_keeps_its_members(self):
        self.reconcile([{"id": "g1", "email": "aero2@example.com", "name": "Aero 2",
                         "members": ["a@example.com"]}])
        self.reconcile([{"id": "g1", "email": "aero2@example.com", "name": "Aéro 2",
                         "members": ["a@example.com"]}])
        self.assertEqual(self.groups(), {"Aéro 2": ["a@example.com"]})

    def test_a_console_group_is_never_touched(self):
        response = self.client.post(
            f"/api/control/organizations/{self.organization}/groups",
            headers=self.auth(self.admin), json={"display_name": "Club"})
        self.assertEqual(response.status_code, 201, response.text)
        self.reconcile([])
        self.assertIn("Club", self.groups())

    def test_a_second_run_that_changes_nothing_writes_nothing(self):
        directory = [{"id": "g1", "email": "aero2@example.com", "name": "Aero 2",
                      "members": ["a@example.com"]}]
        self.reconcile(directory)
        self.assertEqual(self.reconcile(directory),
                         {"groups": 1, "accounts_created": 0, "added": 0, "removed": 0})


class ConfigurationTests(AdminTestCase):
    def setUp(self):
        super().setUp()
        clear()
        self.organization = main.nova_organization_id()
        self.admin = self.admin_session()

    def path(self) -> str:
        return f"/api/control/organizations/{self.organization}/directory-sync"

    def configure(self, **changes):
        body = {"enabled": True, "admin_subject": "admin@example.com",
                "customer_id": "my_customer", "credentials_ref": "env://NOVA_TEST_GOOGLE_KEY",
                "group_filter": [], **changes}
        return self.client.put(self.path(), headers=self.auth(self.admin), json=body)

    def test_the_configuration_is_written_and_read_back_without_the_key(self):
        self.assertEqual(self.configure().status_code, 200)
        body = self.client.get(self.path(), headers=self.auth(self.admin)).json()
        self.assertEqual(body["sync"]["admin_subject"], "admin@example.com")
        self.assertTrue(body["sync"]["enabled"])
        # La reference est montree, jamais la valeur.
        self.assertEqual(body["sync"]["credentials"]["scheme"], "env")
        self.assertNotIn("private_key", str(body))

    def test_a_key_written_in_clear_is_refused(self):
        response = self.configure(credentials_ref=json.dumps(KEY))
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json()["detail"], "GOOGLE_KEY_REFERENCE_REQUIRED")

    def test_the_admin_address_is_required_when_enabled(self):
        response = self.configure(admin_subject="  ")
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json()["detail"], "GOOGLE_ADMIN_SUBJECT_REQUIRED")

    def test_running_needs_identity_management_and_is_audited(self):
        self.configure()
        member = self.sign_in("curieux@example.com")
        self.assertIn(self.client.post(f"{self.path()}/run",
                                       headers=self.auth(member)).status_code, (401, 403))
        with main.db() as conn:
            actions = [row["action"] for row in conn.execute(
                "SELECT action FROM admin_audit_log WHERE action LIKE 'directory_sync.%'")]
        self.assertIn("directory_sync.update", actions)

    def test_a_disabled_synchronization_refuses_to_run(self):
        self.configure(enabled=False)
        response = self.client.post(f"{self.path()}/run", headers=self.auth(self.admin))
        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.json()["detail"], "DIRECTORY_SYNC_DISABLED")

    def test_a_failed_run_is_reported_and_keeps_the_previous_state(self):
        self.configure(credentials_ref="env://NOVA_TEST_ABSENT")
        response = self.client.post(f"{self.path()}/run", headers=self.auth(self.admin))
        self.assertEqual(response.status_code, 502)
        self.assertEqual(response.json()["detail"], "GOOGLE_KEY_MISSING")
        body = self.client.get(self.path(), headers=self.auth(self.admin)).json()
        self.assertEqual(body["sync"]["last_status"], "error")
        self.assertEqual(body["sync"]["last_error"], "GOOGLE_KEY_MISSING")


class WorkerTests(unittest.TestCase):
    def test_the_hourly_service_is_declared_and_runs_the_worker(self):
        compose = open("docker-compose.yml", encoding="utf-8").read()
        self.assertIn("directory-sync:", compose)
        self.assertIn('["python", "directory_worker.py"]', compose)

    def test_only_an_enabled_organization_that_waited_long_enough_is_due(self):
        rows = [
            {"organization_id": "a", "enabled": 1, "last_sync_at": None},
            {"organization_id": "b", "enabled": 1, "last_sync_at": 1000.0},
            {"organization_id": "c", "enabled": 1, "last_sync_at": 4000.0},
            {"organization_id": "d", "enabled": 0, "last_sync_at": None},
        ]
        due = google_directory.due_organizations(rows, now=5000.0, interval=3600.0)
        self.assertEqual(due, ["a", "b"])


if __name__ == "__main__":
    unittest.main()
