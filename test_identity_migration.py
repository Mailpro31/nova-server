"""Phase 23 — identité multi-tenant : migration, collisions, isolation.

Ces tests vivent à part parce qu'ils manipulent des **bases entières** — des
bases d'avant la migration, reconstruites à la main — là où `test_main.py`
exerce un serveur déjà démarré. Les mélanger rendrait les deux plus difficiles
à lire.
"""

import os
import sqlite3
import tempfile
import time
import unittest
import uuid
from unittest.mock import patch

TEST_DIRECTORY = tempfile.TemporaryDirectory()
# Exactement les mêmes valeurs que `test_main.py` : les deux fichiers importent
# le même module, et le premier importé fixe la configuration pour les deux.
os.environ.setdefault("NOVA_DB", os.path.join(TEST_DIRECTORY.name, "identity.db"))
os.environ.setdefault("NOVA_DOMAINS", "example.com")
os.environ.setdefault("NOVA_ORGANIZATION_NAME", "Nova Test School")
os.environ.setdefault("NOVA_ORGANIZATION_SHORT_NAME", "NTS")
os.environ.setdefault("NOVA_ENTRA_CLIENT_ID", "")

from fastapi.testclient import TestClient  # noqa: E402

import admin_cli  # noqa: E402
import main  # noqa: E402


LEGACY_SCHEMA = """
    CREATE TABLE users(
        email TEXT PRIMARY KEY,
        role TEXT DEFAULT 'student',
        cohort TEXT DEFAULT '',
        note TEXT DEFAULT '',
        disabled INTEGER DEFAULT 0,
        created_at REAL,
        last_seen REAL
    );
    CREATE TABLE tokens(
        token TEXT PRIMARY KEY, email TEXT, machine TEXT,
        active INTEGER DEFAULT 1, created_at REAL, expires_at REAL
    );
    CREATE TABLE auth_codes(
        email TEXT, code TEXT, expires_at REAL, attempts INTEGER DEFAULT 0
    );
    CREATE TABLE usage(
        email TEXT, day TEXT, transcriptions INTEGER DEFAULT 0,
        reformulations INTEGER DEFAULT 0, audio_seconds REAL DEFAULT 0,
        PRIMARY KEY (email, day)
    );
    CREATE TABLE personal_dictionary(
        id INTEGER PRIMARY KEY AUTOINCREMENT, email TEXT NOT NULL,
        term TEXT NOT NULL, replacement TEXT NOT NULL,
        source TEXT NOT NULL DEFAULT 'personal', UNIQUE(email, term)
    );
    CREATE TABLE snippets(
        id INTEGER PRIMARY KEY AUTOINCREMENT, email TEXT NOT NULL,
        trigger TEXT NOT NULL, content TEXT NOT NULL, UNIQUE(email, trigger)
    );
    CREATE TABLE formatting_rules(
        id INTEGER PRIMARY KEY AUTOINCREMENT, email TEXT,
        rule TEXT NOT NULL, shared INTEGER NOT NULL DEFAULT 0
    );
    CREATE TABLE shared_dictionary(
        id INTEGER PRIMARY KEY AUTOINCREMENT, term TEXT NOT NULL UNIQUE,
        replacement TEXT NOT NULL
    );
    CREATE TABLE server_state(key TEXT PRIMARY KEY, value TEXT NOT NULL);
    CREATE TABLE organizations(
        id TEXT PRIMARY KEY, slug TEXT, display_name TEXT NOT NULL,
        org_type TEXT NOT NULL DEFAULT 'education', entra_tenant_id TEXT,
        google_customer_id TEXT, oidc_issuer TEXT, created_at REAL
    );
    CREATE TABLE federated_identities(
        provider TEXT NOT NULL, external_subject TEXT NOT NULL,
        external_tenant_id TEXT, organization_id TEXT NOT NULL,
        email TEXT NOT NULL, verification TEXT NOT NULL DEFAULT 'transport_only',
        created_at REAL, last_seen REAL,
        PRIMARY KEY (provider, external_subject)
    );
"""


def legacy_database(extra_sql: str = "") -> str:
    """Une base telle qu'elle existait avant la Phase 23 : e-mail en clé primaire."""
    path = os.path.join(TEST_DIRECTORY.name, f"legacy-{uuid.uuid4().hex}.db")
    conn = sqlite3.connect(path)
    conn.executescript(LEGACY_SCHEMA + extra_sql)
    conn.commit()
    conn.close()
    return path


class EmailNormalizationTests(unittest.TestCase):
    """La normalisation doit rester ennuyeuse. C'est sa qualité principale."""

    def test_case_and_spaces_are_ignored(self):
        for raw in ("Alex@Example.com", "  alex@example.com  ", "ALEX@EXAMPLE.COM"):
            self.assertEqual(main.normalize_email(raw), "alex@example.com")

    def test_provider_specific_rules_are_not_applied(self):
        """Supprimer les points ou couper au `+alias` fusionnerait des comptes
        distincts. Ces règles appartiennent à un opérateur, pas à Nova, et ne
        valent pas dans un domaine d'entreprise."""
        self.assertEqual(
            main.normalize_email("prenom.nom@example.com"), "prenom.nom@example.com")
        self.assertEqual(
            main.normalize_email("alex+nova@example.com"), "alex+nova@example.com")
        self.assertNotEqual(
            main.normalize_email("prenom.nom@example.com"),
            main.normalize_email("prenomnom@example.com"))

    def test_no_linguistic_folding(self):
        # `casefold()` replierait « ß » en « ss » et changerait le destinataire.
        self.assertEqual(main.normalize_email("STRASSE@example.com"),
                         "strasse@example.com")
        self.assertNotEqual(main.normalize_email("straße@example.com"),
                            "strasse@example.com")


class MigrationTests(unittest.TestCase):
    """D'une base à clé primaire e-mail vers une identité stable."""

    def migrate(self, path):
        with patch.object(main, "DB_PATH", path):
            main.init_db()

    def read(self, path, query, params=()):
        conn = sqlite3.connect(path)
        conn.row_factory = sqlite3.Row
        try:
            return conn.execute(query, params).fetchall()
        finally:
            conn.close()

    def test_an_existing_account_keeps_its_identity(self):
        """Le point le plus important : un `user_id` déjà attribué — celui
        d'une personne réelle — n'est jamais régénéré."""
        path = legacy_database("""
            ALTER TABLE users ADD COLUMN user_id TEXT;
            ALTER TABLE users ADD COLUMN organization_id TEXT;
            INSERT INTO users(email, role, cohort, created_at, user_id, organization_id)
                VALUES ('connu@example.com', 'teacher', 'AERO2', 1.0,
                        'identite-stable', 'org-historique');
        """)
        self.migrate(path)
        rows = self.read(path, "SELECT * FROM users WHERE email='connu@example.com'")
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["user_id"], "identite-stable")
        self.assertEqual(rows[0]["organization_id"], "org-historique")
        # Et rien du profil n'est perdu.
        self.assertEqual(rows[0]["role"], "teacher")
        self.assertEqual(rows[0]["cohort"], "AERO2")
        self.assertEqual(rows[0]["normalized_email"], "connu@example.com")

    def test_an_account_without_identity_receives_one(self):
        path = legacy_database(
            "INSERT INTO users(email, created_at) VALUES ('ancien@example.com', 1.0);")
        self.migrate(path)
        rows = self.read(path, "SELECT user_id FROM users")
        self.assertEqual(len(rows), 1)
        self.assertTrue(rows[0]["user_id"])

    def test_the_identifier_is_not_derived_from_the_email(self):
        """Un identifiant dérivé de l'adresse ne serait pas stable : changer
        d'adresse changerait l'identité, ce que toute cette phase interdit."""
        path = legacy_database(
            "INSERT INTO users(email, created_at) VALUES ('alex@example.com', 1.0);")
        self.migrate(path)
        user_id = self.read(path, "SELECT user_id FROM users")[0]["user_id"]
        for form in ("alex@example.com", "alex", "example.com"):
            self.assertNotIn(form, user_id)

    def test_a_second_start_changes_nothing(self):
        path = legacy_database("""
            INSERT INTO users(email, created_at) VALUES ('stable@example.com', 1.0);
        """)
        self.migrate(path)
        before = self.read(path, "SELECT * FROM users")[0]
        self.migrate(path)
        self.migrate(path)
        after = self.read(path, "SELECT * FROM users")
        self.assertEqual(len(after), 1)
        self.assertEqual(after[0]["user_id"], before["user_id"])
        self.assertEqual(after[0]["created_at"], before["created_at"])

    def test_related_rows_follow_the_account(self):
        path = legacy_database("""
            INSERT INTO users(email, created_at) VALUES ('lie@example.com', 1.0);
            INSERT INTO tokens(token, email, machine, active, created_at)
                VALUES ('jeton', 'lie@example.com', 'poste-1', 1, 1.0);
            INSERT INTO usage(email, day, transcriptions) VALUES ('lie@example.com','2026-01-01',7);
            INSERT INTO personal_dictionary(email, term, replacement)
                VALUES ('lie@example.com', 'IPSA', 'IPSA');
            INSERT INTO snippets(email, trigger, content)
                VALUES ('lie@example.com', ':sig', 'Cordialement');
            INSERT INTO formatting_rules(email, rule, shared)
                VALUES ('lie@example.com', 'Pas de familiarité', 0);
        """)
        self.migrate(path)
        user_id = self.read(path, "SELECT user_id FROM users")[0]["user_id"]
        for table in ("tokens", "usage", "personal_dictionary", "snippets",
                      "formatting_rules"):
            rows = self.read(path, f"SELECT user_id FROM {table}")
            self.assertEqual(len(rows), 1, table)
            self.assertEqual(rows[0]["user_id"], user_id, table)

    def test_a_federated_identity_survives_untouched(self):
        path = legacy_database("""
            INSERT INTO users(email, created_at) VALUES ('federe@example.com', 1.0);
            INSERT INTO federated_identities(provider, external_subject,
                external_tenant_id, organization_id, email, verification,
                created_at, last_seen)
                VALUES ('microsoft_entra', 'oid-connu', 'tenant-1', 'org-1',
                        'federe@example.com', 'verified', 1.0, 2.0);
        """)
        self.migrate(path)
        rows = self.read(path, "SELECT * FROM federated_identities")
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["external_subject"], "oid-connu")
        self.assertEqual(rows[0]["verification"], "verified")
        # Elle pointe maintenant vers l'identité, pas seulement vers l'adresse.
        user_id = self.read(path, "SELECT user_id FROM users")[0]["user_id"]
        self.assertEqual(rows[0]["user_id"], user_id)

    def test_a_valid_session_is_not_lost(self):
        """Migrer ne doit pas déconnecter tout le monde."""
        path = legacy_database(f"""
            INSERT INTO users(email, created_at) VALUES ('session@example.com', 1.0);
            INSERT INTO tokens(token, email, machine, active, created_at, expires_at)
                VALUES ('jeton-en-clair', 'session@example.com', 'poste-1', 1, 1.0,
                        {time.time() + 3600});
        """)
        self.migrate(path)
        with patch.object(main, "DB_PATH", path):
            client = TestClient(main.app)
            response = client.get(
                "/api/me", headers={"Authorization": "Bearer jeton-en-clair"})
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["email"], "session@example.com")

    def test_the_security_role_survives(self):
        path = legacy_database("""
            ALTER TABLE users ADD COLUMN security_role TEXT;
            INSERT INTO users(email, created_at, security_role)
                VALUES ('chef@example.com', 1.0, 'organization_admin');
        """)
        self.migrate(path)
        rows = self.read(path, "SELECT security_role FROM users")
        self.assertEqual(rows[0]["security_role"], "organization_admin")


class MigrationCollisionTests(unittest.TestCase):
    """Une migration qui devine est une migration qui perd des données."""

    def migrate(self, path):
        with patch.object(main, "DB_PATH", path):
            main.init_db()

    def test_two_accounts_with_the_same_address_stop_the_migration(self):
        # L'ancienne clé primaire était sensible à la casse : deux lignes
        # pouvaient coexister et désigner la même adresse.
        path = legacy_database("""
            INSERT INTO users(email, created_at) VALUES ('alex@example.com', 1.0);
            INSERT INTO users(email, created_at) VALUES ('Alex@Example.com', 2.0);
        """)
        with self.assertRaises(main.UserIdentityCollision) as raised:
            self.migrate(path)
        message = str(raised.exception)
        # Le diagnostic doit permettre de retrouver la ligne sans recopier
        # l'adresse en clair dans les journaux d'exploitation.
        self.assertNotIn("alex@example.com", message.lower())
        self.assertIn("empreinte=", message)

    def test_nothing_is_written_when_the_migration_stops(self):
        path = legacy_database("""
            INSERT INTO users(email, created_at) VALUES ('alex@example.com', 1.0);
            INSERT INTO users(email, created_at) VALUES ('ALEX@example.com', 2.0);
        """)
        with self.assertRaises(main.UserIdentityCollision):
            self.migrate(path)
        conn = sqlite3.connect(path)
        try:
            columns = {r[1] for r in conn.execute("PRAGMA table_info(users)")}
            remaining = conn.execute("SELECT COUNT(*) FROM users").fetchone()[0]
        finally:
            conn.close()
        # Ni table de travail laissée derrière, ni compte disparu.
        self.assertNotIn("normalized_email", columns)
        self.assertEqual(remaining, 2)

    def test_a_duplicate_identifier_stops_the_migration(self):
        path = legacy_database("""
            ALTER TABLE users ADD COLUMN user_id TEXT;
            INSERT INTO users(email, created_at, user_id)
                VALUES ('un@example.com', 1.0, 'meme-identite');
            INSERT INTO users(email, created_at, user_id)
                VALUES ('deux@example.com', 2.0, 'meme-identite');
        """)
        with self.assertRaises(main.UserIdentityCollision):
            self.migrate(path)

    def test_an_account_without_an_address_stops_the_migration(self):
        path = legacy_database(
            "INSERT INTO users(email, created_at) VALUES ('', 1.0);")
        with self.assertRaises(main.UserIdentityCollision):
            self.migrate(path)

    def test_the_same_address_in_two_organizations_is_accepted(self):
        """Le but même de la phase : ce sont deux personnes."""
        path = legacy_database("""
            ALTER TABLE users ADD COLUMN organization_id TEXT;
            INSERT INTO users(email, created_at, organization_id)
                VALUES ('alex@example.com', 1.0, 'org-a');
        """)
        self.migrate(path)
        conn = sqlite3.connect(path)
        conn.row_factory = sqlite3.Row
        try:
            main_org = conn.execute(
                "SELECT organization_id FROM users").fetchone()["organization_id"]
            conn.execute(
                "INSERT INTO users(user_id, organization_id, email, normalized_email, "
                "created_at) VALUES (?,?,?,?,?)",
                ("autre-identite", "org-b", "alex@example.com", "alex@example.com", 2.0))
            conn.commit()
            rows = conn.execute(
                "SELECT organization_id, user_id FROM users "
                "WHERE normalized_email='alex@example.com'").fetchall()
        finally:
            conn.close()
        self.assertEqual(main_org, "org-a")
        self.assertEqual(len(rows), 2)
        self.assertEqual(len({r["user_id"] for r in rows}), 2)

    def test_the_same_address_twice_in_one_organization_is_refused(self):
        path = legacy_database("")
        self.migrate(path)
        conn = sqlite3.connect(path)
        try:
            conn.execute(
                "INSERT INTO users(user_id, organization_id, email, normalized_email) "
                "VALUES ('a','org-a','alex@example.com','alex@example.com')")
            with self.assertRaises(sqlite3.IntegrityError):
                conn.execute(
                    "INSERT INTO users(user_id, organization_id, email, normalized_email) "
                    "VALUES ('b','org-a','Alex@Example.com','alex@example.com')")
        finally:
            conn.close()


class SameAddressInTwoOrganizationsTests(unittest.TestCase):
    """Deux personnes, une même adresse, aucune contamination.

    Le serveur sert une organisation ; la seconde est créée directement en base,
    comme le ferait un Control Plane. Ce qui est vérifié ici, c'est le **modèle
    de données** : c'est lui qui, jusqu'à cette phase, rendait la chose
    impossible.
    """

    ORGANIZATION_B = "organisation-b"
    EMAIL = "alex@example.com"

    @classmethod
    def setUpClass(cls):
        cls.client = TestClient(main.app)

    def setUp(self):
        self.organization_a = main.nova_organization_id()
        with main.db() as conn:
            conn.execute(
                "DELETE FROM users WHERE normalized_email=?",
                (main.normalize_email(self.EMAIL),))
            conn.execute(
                "INSERT OR REPLACE INTO organizations(id, slug, display_name, "
                "org_type, status, created_at) VALUES (?,?,?,?,?,?)",
                (self.ORGANIZATION_B, "org-b", "Organisation B", "education",
                 "active", time.time()))
            self.user_a = main.create_user(conn, self.organization_a, self.EMAIL)
            self.user_b = main.create_user(conn, self.ORGANIZATION_B, self.EMAIL)

    def tearDown(self):
        with main.db() as conn:
            conn.execute("DELETE FROM users WHERE user_id IN (?,?)",
                         (self.user_a, self.user_b))
            conn.execute("DELETE FROM organizations WHERE id=?", (self.ORGANIZATION_B,))

    def account(self, user_id):
        with main.db() as conn:
            return main.user_by_id(conn, user_id)

    def test_the_two_accounts_are_distinct(self):
        self.assertNotEqual(self.user_a, self.user_b)
        self.assertEqual(self.account(self.user_a)["organization_id"], self.organization_a)
        self.assertEqual(self.account(self.user_b)["organization_id"], self.ORGANIZATION_B)

    def test_a_lookup_never_crosses_the_boundary(self):
        with main.db() as conn:
            found_a = main.user_by_email(conn, self.organization_a, self.EMAIL)
            found_b = main.user_by_email(conn, self.ORGANIZATION_B, "ALEX@Example.com")
        self.assertEqual(found_a["user_id"], self.user_a)
        self.assertEqual(found_b["user_id"], self.user_b)

    def test_a_lookup_without_an_organization_is_impossible(self):
        # La signature elle-même refuse : c'est le défaut qu'on ne veut pas
        # pouvoir réintroduire par distraction.
        with main.db() as conn:
            with self.assertRaises(ValueError):
                main.user_by_email(conn, "", self.EMAIL)

    def test_a_security_role_does_not_contaminate_the_other_tenant(self):
        main.set_security_role(self.EMAIL, main.SECURITY_ROLE_ORGANIZATION_ADMIN,
                               self.organization_a)
        self.assertEqual(main.security_role_for(self.account(self.user_a)),
                         main.SECURITY_ROLE_ORGANIZATION_ADMIN)
        self.assertEqual(main.security_role_for(self.account(self.user_b)),
                         main.SECURITY_ROLE_MEMBER)

    def test_the_operator_cli_acts_on_one_organization_only(self):
        self.assertEqual(
            admin_cli.run(["grant", self.EMAIL, "it_admin",
                           "--organization", self.ORGANIZATION_B]), 0)
        self.assertEqual(main.security_role_for(self.account(self.user_b)),
                         main.SECURITY_ROLE_IT_ADMIN)
        self.assertEqual(main.security_role_for(self.account(self.user_a)),
                         main.SECURITY_ROLE_MEMBER)

    def test_disabling_one_account_leaves_the_other_alone(self):
        with main.db() as conn:
            conn.execute("UPDATE users SET disabled=1 WHERE user_id=?", (self.user_a,))
        self.assertTrue(self.account(self.user_a)["disabled"])
        self.assertFalse(self.account(self.user_b)["disabled"])

    def test_sessions_are_distinct_and_revoked_separately(self):
        session_a = main.issue_token(self.EMAIL, "poste-a", self.organization_a)
        session_b = main.issue_token(self.EMAIL, "poste-b", self.ORGANIZATION_B)
        self.assertNotEqual(session_a["token"], session_b["token"])
        with main.db() as conn:
            owners = conn.execute(
                "SELECT user_id, organization_id FROM tokens WHERE active=1 "
                "AND user_id IN (?,?)", (self.user_a, self.user_b)).fetchall()
        self.assertEqual({row["user_id"] for row in owners},
                         {self.user_a, self.user_b})

        # Révoquer les appareils de A ne touche pas B.
        with main.db() as conn:
            conn.execute("UPDATE tokens SET active=0 WHERE user_id=?", (self.user_a,))
            still_active = conn.execute(
                "SELECT COUNT(*) c FROM tokens WHERE active=1 AND user_id=?",
                (self.user_b,)).fetchone()["c"]
        self.assertEqual(still_active, 1)

    def test_deleting_one_account_leaves_the_other_alone(self):
        with main.db() as conn:
            conn.execute("DELETE FROM users WHERE user_id=?", (self.user_a,))
        self.assertIsNone(self.account(self.user_a))
        self.assertIsNotNone(self.account(self.user_b))


class EmailChangeTests(unittest.TestCase):
    """Ce que la clé primaire globale rendait impossible."""

    def setUp(self):
        self.organization_id = main.nova_organization_id()
        with main.db() as conn:
            for address in ("avant@example.com", "apres@example.com"):
                conn.execute("DELETE FROM users WHERE normalized_email=?", (address,))
            self.user_id = main.create_user(conn, self.organization_id, "avant@example.com")

    def tearDown(self):
        with main.db() as conn:
            conn.execute("DELETE FROM users WHERE user_id=?", (self.user_id,))

    def test_the_identity_does_not_move_when_the_address_does(self):
        with main.db() as conn:
            main.update_user_email(conn, self.user_id, "apres@example.com")
            account = main.user_by_id(conn, self.user_id)
        self.assertEqual(account["user_id"], self.user_id)
        self.assertEqual(account["email"], "apres@example.com")
        self.assertEqual(account["normalized_email"], "apres@example.com")

    def test_the_account_is_found_under_its_new_address_only(self):
        with main.db() as conn:
            main.update_user_email(conn, self.user_id, "apres@example.com")
            self.assertIsNotNone(
                main.user_by_email(conn, self.organization_id, "apres@example.com"))
            self.assertIsNone(
                main.user_by_email(conn, self.organization_id, "avant@example.com"))

    def test_sessions_and_federated_identity_stay_attached(self):
        session = main.issue_token("avant@example.com", "poste", self.organization_id)
        with main.db() as conn:
            conn.execute(
                "INSERT INTO federated_identities(provider, external_subject, "
                "organization_id, email, user_id, external_issuer, created_at) "
                "VALUES (?,?,?,?,?,?,?)",
                ("microsoft_entra", "oid-stable", self.organization_id,
                 "avant@example.com", self.user_id, "", time.time()))
            main.update_user_email(conn, self.user_id, "apres@example.com")
            still_bound = conn.execute(
                "SELECT COUNT(*) c FROM tokens WHERE user_id=? AND active=1",
                (self.user_id,)).fetchone()["c"]
            identity = conn.execute(
                "SELECT user_id FROM federated_identities WHERE external_subject=?",
                ("oid-stable",)).fetchone()
        self.assertTrue(session["token"])
        self.assertEqual(still_bound, 1)
        self.assertEqual(identity["user_id"], self.user_id)
        with main.db() as conn:
            conn.execute("DELETE FROM federated_identities WHERE external_subject=?",
                         ("oid-stable",))

    def test_the_security_role_is_unaffected(self):
        main.set_security_role("avant@example.com", main.SECURITY_ROLE_IT_ADMIN,
                               self.organization_id)
        with main.db() as conn:
            main.update_user_email(conn, self.user_id, "apres@example.com")
            account = main.user_by_id(conn, self.user_id)
        self.assertEqual(main.security_role_for(account), main.SECURITY_ROLE_IT_ADMIN)


if __name__ == "__main__":
    unittest.main()
