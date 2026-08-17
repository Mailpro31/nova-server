import base64
import json
import os
import sqlite3
import tempfile
import time
import unittest
import uuid
from unittest.mock import patch


TEST_DIRECTORY = tempfile.TemporaryDirectory()
os.environ["NOVA_DB"] = os.path.join(TEST_DIRECTORY.name, "nova-test.db")
os.environ["NOVA_DOMAINS"] = "example.com"
os.environ["NOVA_ADMIN_TOKEN"] = "test-admin-token"
os.environ["NOVA_ORGANIZATION_NAME"] = "Nova Test School"
os.environ["NOVA_ORGANIZATION_SHORT_NAME"] = "NTS"
os.environ["NOVA_ENTRA_CLIENT_ID"] = ""

from fastapi.testclient import TestClient

import main


def fresh_instance_organization_id(slug: str) -> str:
    """Identifiant attribué par une instance neuve servant `slug`.

    Chaque appel part d'une base vierge : c'est ce que fait un serveur au tout
    premier démarrage.
    """
    path = os.path.join(TEST_DIRECTORY.name, f"instance-{uuid.uuid4().hex}.db")
    with patch.object(main, "DB_PATH", path), patch.object(
        main, "ORGANIZATION_ID", slug
    ):
        main.init_db()
        return main.nova_organization_id()


class NovaServerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.client = TestClient(main.app)

    def setUp(self):
        with main.db() as conn:
            for table in (
                "auth_codes",
                "tokens",
                "users",
                "usage",
                "personal_dictionary",
                "snippets",
                "formatting_rules",
                "federated_identities",
            ):
                conn.execute(f"DELETE FROM {table}")

    def sign_in(self):
        email = "student@example.com"
        response = self.client.post(
            "/api/auth/request",
            json={"email": email, "machine": "test-laptop"},
        )
        self.assertEqual(response.status_code, 200)
        with main.db() as conn:
            code = conn.execute(
                "SELECT code FROM auth_codes WHERE email=?", (email,)
            ).fetchone()["code"]
        response = self.client.post(
            "/api/auth/verify",
            json={"email": email, "code": code, "machine": "test-laptop"},
        )
        self.assertEqual(response.status_code, 200)
        return {"Authorization": f"Bearer {response.json()['token']}"}

    def test_public_configuration_and_health(self):
        health = self.client.get("/api/health")
        self.assertEqual(health.status_code, 200)
        self.assertEqual(health.json()["status"], "ok")

        config = self.client.get("/api/config")
        self.assertEqual(config.status_code, 200)
        body = config.json()
        self.assertEqual(body["organization"]["shortName"], "NTS")
        self.assertIn("email_code", body["auth_methods"])
        self.assertNotIn("entra", body["auth_methods"])
        self.assertTrue(body["capabilities"]["aiSkills"])
        self.assertEqual(
            body["ai_skills"],
            {"enabled": True, "required": False, "trackProgress": True},
        )

    def test_auth_writing_preferences_ai_skills_and_logout(self):
        headers = self.sign_in()
        self.assertEqual(self.client.get("/api/me", headers=headers).status_code, 200)

        added = self.client.post(
            "/api/dictionary",
            headers=headers,
            json={"term": "IPSA", "replacement": "IPSA"},
        )
        self.assertEqual(added.status_code, 200)
        self.assertGreater(added.json()["id"], 0)

        snippet = self.client.post(
            "/api/snippets",
            headers=headers,
            json={"trigger": "mon école", "content": "Nova Test School"},
        )
        self.assertEqual(snippet.status_code, 200)

        rule = self.client.post(
            "/api/formatting-rules",
            headers=headers,
            json={"rule": "Use SI units"},
        )
        self.assertEqual(rule.status_code, 200)

        vocabulary = self.client.get("/api/vocabulary", headers=headers).json()
        self.assertEqual(vocabulary["personal"][0]["term"], "IPSA")
        self.assertEqual(vocabulary["snippets"][0]["trigger"], "mon école")

        skills = self.client.get("/api/ai-skills", headers=headers)
        self.assertEqual(skills.status_code, 200)
        self.assertEqual(len(skills.json()["skills"]), 6)
        self.assertIn("practice", skills.json()["skills"][0])

        self.assertEqual(
            self.client.post("/api/auth/logout", headers=headers).status_code,
            200,
        )
        self.assertEqual(self.client.get("/api/me", headers=headers).status_code, 401)

    def test_admin_requires_the_configured_token(self):
        page = self.client.get("/admin")
        self.assertEqual(page.status_code, 200)
        self.assertIn("Nova — Administration", page.text)

        self.assertEqual(self.client.get("/api/admin/overview").status_code, 403)
        response = self.client.get(
            "/api/admin/overview",
            headers={"X-Admin-Token": "test-admin-token"},
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["max_seats"], main.MAX_SEATS)

    def test_microsoft_device_flow_creates_a_nova_session(self):
        class Response:
            def __init__(self, status_code, body):
                self.status_code = status_code
                self.body = body

            def json(self):
                return self.body

        class MicrosoftClient:
            async def __aenter__(self):
                return self

            async def __aexit__(self, *_):
                return None

            async def post(self, url, **_):
                if url.endswith("/devicecode"):
                    return Response(
                        200,
                        {
                            "device_code": "device-code",
                            "user_code": "ABCD-EFGH",
                            "verification_uri": "https://microsoft.com/devicelogin",
                            "expires_in": 900,
                            "interval": 1,
                            "message": "Sign in",
                        },
                    )
                return Response(200, {"access_token": "graph-token"})

            async def get(self, *_args, **_kwargs):
                return Response(200, {"mail": "student@example.com"})

        main.ENTRA_FLOWS.clear()
        with (
            patch.object(main, "ENTRA_CLIENT_ID", "public-client-id"),
            patch.object(main.httpx, "AsyncClient", return_value=MicrosoftClient()),
        ):
            started = self.client.post(
                "/api/auth/entra/start", json={"machine": "test-laptop"}
            )
            self.assertEqual(started.status_code, 200)
            self.assertEqual(started.json()["user_code"], "ABCD-EFGH")

            completed = self.client.post(
                "/api/auth/entra/poll",
                json={"flow_id": started.json()["flow_id"]},
            )
            self.assertEqual(completed.status_code, 200)
            self.assertEqual(completed.json()["status"], "complete")
            self.assertEqual(completed.json()["email"], "student@example.com")
            token = completed.json()["token"]
            profile = self.client.get(
                "/api/me", headers={"Authorization": f"Bearer {token}"}
            )
            self.assertEqual(profile.status_code, 200)
            self.assertEqual(profile.json()["email"], "student@example.com")


class CommandContextTests(unittest.TestCase):
    """What an AI Skill (`/api/command`) receives — and what it never receives.

    `command_prompt` is pure and `vocabulary_context` only reads the database,
    so nothing here reaches the language engine: the proxy is not this server's
    responsibility. These tests cover what would break silently — school
    vocabulary never reaching the engine, or one account's context leaking into
    another's.
    """

    def setUp(self):
        with main.db() as conn:
            for table in (
                "shared_dictionary",
                "personal_dictionary",
                "snippets",
                "formatting_rules",
            ):
                conn.execute(f"DELETE FROM {table}")

    def add_shared_term(self, term, replacement=""):
        with main.db() as conn:
            conn.execute(
                "INSERT INTO shared_dictionary(term,replacement) VALUES (?,?)",
                (term, replacement),
            )

    def add_personal_term(self, email, term, replacement=""):
        with main.db() as conn:
            conn.execute(
                "INSERT INTO personal_dictionary(email,term,replacement,source) "
                "VALUES (?,?,?,'personal')",
                (email, term, replacement),
            )

    def add_snippet(self, email, trigger, content):
        with main.db() as conn:
            conn.execute(
                "INSERT INTO snippets(email,trigger,content) VALUES (?,?,?)",
                (email, trigger, content),
            )

    def add_shared_rule(self, rule):
        with main.db() as conn:
            conn.execute(
                "INSERT INTO formatting_rules(email,rule,shared) VALUES ('',?,1)",
                (rule,),
            )

    # -- what the Skill receives ------------------------------------------
    def test_shared_and_personal_vocabulary_reach_the_skill(self):
        self.add_shared_term("IPSA")
        self.add_personal_term("etu@example.com", "aeroelasticite", "aéroélasticité")

        prompt = main.command_prompt(
            "Improve this text", main.vocabulary_context("etu@example.com")
        )

        self.assertIn("IPSA", prompt)
        self.assertIn("aéroélasticité", prompt)

    def test_no_vocabulary_leaves_the_prompt_untouched(self):
        bare = main.command_prompt("Summarise this text", "")
        self.assertNotIn("vocabulary", bare.split("Instruction:")[0])
        # An account without vocabulary must not end up with a different prompt
        # from everyone else.
        self.assertTrue(bare.rstrip().endswith("Instruction: Summarise this text"))

    def test_free_instruction_is_always_honoured(self):
        self.add_shared_term("IPSA")
        prompt = main.command_prompt(
            "Translate to English", main.vocabulary_context("etu@example.com")
        )
        # Vocabulary is added, it never replaces what the user asked for.
        self.assertIn("Translate to English", prompt)

    # -- isolation between accounts ---------------------------------------
    def test_another_students_vocabulary_never_leaks(self):
        self.add_personal_term("alice@example.com", "MonProjetSecret")
        self.add_personal_term("bob@example.com", "TermeDeBob")

        prompt = main.command_prompt(
            "Improve", main.vocabulary_context("bob@example.com")
        )

        self.assertIn("TermeDeBob", prompt)
        self.assertNotIn("MonProjetSecret", prompt)

    def test_shared_vocabulary_reaches_everyone(self):
        self.add_shared_term("IPSA")
        for email in ("alice@example.com", "bob@example.com"):
            self.assertIn("IPSA", main.vocabulary_context(email))

    # -- what the Skill deliberately does not receive ----------------------
    def test_voice_shortcuts_are_not_expanded_on_selected_text(self):
        # A voice shortcut replaces a SPOKEN phrase. A Skill's text was typed on
        # purpose: expanding it would be a destructive surprise.
        self.add_snippet("etu@example.com", "mon adresse", "12 rue des Lilas")

        prompt = main.command_prompt(
            "Improve", main.vocabulary_context("etu@example.com")
        )

        self.assertNotIn("12 rue des Lilas", prompt)

    def test_formatting_rules_do_not_reshape_a_skill_result(self):
        # Rules target the shaping of a dictation. The server cannot tell which
        # Skill is running: forcing them onto an explanation would corrupt it.
        self.add_shared_rule("Always sign with your full name")

        prompt = main.command_prompt(
            "Explain", main.vocabulary_context("etu@example.com")
        )

        self.assertNotIn("Always sign", prompt)

    def test_selected_text_is_never_preprocessed(self):
        # Vocabulary is CONTEXT. The user's text never goes through
        # `command_prompt`: it travels as-is in the `user` message.
        self.add_shared_term("IPSA", "I.P.S.A.")
        prompt = main.command_prompt(
            "Improve", main.vocabulary_context("etu@example.com")
        )
        self.assertNotIn("{text}", prompt)
        self.assertIn("transformed text", prompt)

    # -- API contract ------------------------------------------------------
    def test_request_contract_is_unchanged(self):
        self.assertEqual(
            set(main.CommandRequest.model_fields), {"instruction", "text"}
        )

    def test_dictation_still_receives_every_context(self):
        # Guard-rail the other way round: dictation rewriting must keep
        # receiving everything.
        self.add_shared_term("IPSA")
        self.add_snippet("etu@example.com", "mon adresse", "12 rue des Lilas")
        self.add_shared_rule("Always use vous")

        context, snippets = main.writing_context("etu@example.com")

        self.assertIn("IPSA", context)
        self.assertIn("Always use vous", context)
        self.assertEqual(
            main.apply_snippets("Voici mon adresse", snippets),
            "Voici 12 rue des Lilas",
        )


class IdentityFoundationTests(unittest.TestCase):
    """Contrat d'identité : qui se connecte, à quelle organisation, avec quels
    droits. Ces tests portent sur ce que le **client reçoit réellement**, pas
    sur les fonctions internes."""

    @classmethod
    def setUpClass(cls):
        cls.client = TestClient(main.app)

    def setUp(self):
        with main.db() as conn:
            for table in ("auth_codes", "tokens", "users", "federated_identities"):
                conn.execute(f"DELETE FROM {table}")

    def sign_in(self, email="student@example.com"):
        self.client.post("/api/auth/request", json={"email": email, "machine": "poste-1"})
        with main.db() as conn:
            code = conn.execute(
                "SELECT code FROM auth_codes WHERE email=?", (email,)
            ).fetchone()["code"]
        response = self.client.post(
            "/api/auth/verify", json={"email": email, "code": code, "machine": "poste-1"}
        )
        self.assertEqual(response.status_code, 200)
        return {"Authorization": f"Bearer {response.json()['token']}"}

    # -- compatibilité ascendante -----------------------------------------
    def test_me_keeps_every_legacy_field(self):
        """Un poste déployé aujourd'hui lit `email`, `role`, `cohort` et une
        chaîne `organization`. Changer l'un de ces types casserait chaque
        installation existante."""
        body = self.client.get("/api/me", headers=self.sign_in()).json()

        self.assertEqual(body["email"], "student@example.com")
        self.assertIsInstance(body["role"], str)
        self.assertIsInstance(body["cohort"], str)
        self.assertIsInstance(body["organization"], str)

    def test_me_additions_are_purely_additive(self):
        body = self.client.get("/api/me", headers=self.sign_in()).json()

        self.assertEqual(body["contract_version"], 2)
        self.assertIn("membership", body)
        self.assertIn("identity", body)
        self.assertIn("capabilities", body)

    def test_legacy_account_created_before_this_phase_still_works(self):
        """Compte inséré comme le faisait l'ancien code : sans user_id, sans
        organisation, sans provider."""
        with main.db() as conn:
            conn.execute(
                "INSERT INTO users(email, role, cohort, created_at) VALUES (?,?,?,?)",
                ("ancien@example.com", "teacher", "AERO2", 0.0),
            )
            main.migrate_schema(conn)

        headers = self.sign_in("ancien@example.com")
        body = self.client.get("/api/me", headers=headers).json()

        self.assertTrue(body["user_id"])
        self.assertEqual(body["organization_id"], main.nova_organization_id())
        self.assertEqual(body["identity"]["provider"], "legacy_email_code")
        self.assertEqual(body["cohort"], "AERO2")
        self.assertEqual(body["membership"]["member_type"], "teacher")
        self.assertEqual(
            body["membership"]["groups"],
            [
                {
                    "id": "AERO2",
                    "label": "AERO2",
                    "source": "legacy_cohort",
                    "external_group_id": None,
                }
            ],
        )

    # -- identifiants d'organisation --------------------------------------
    def test_organization_id_is_stable_and_not_the_display_name(self):
        first = main.nova_organization_id()
        self.assertEqual(first, main.nova_organization_id())
        self.assertNotIn(main.ORGANIZATION_NAME, first)
        # Renommer l'organisation ne change pas son identité.
        with patch.object(main, "ORGANIZATION_NAME", "Nom Totalement Different"):
            self.assertEqual(main.nova_organization_id(), first)

    def test_two_organizations_never_share_an_id(self):
        # Deux instances distinctes, donc deux bases distinctes : c'est le seul
        # montage qui reproduit réellement deux organisations.
        first = fresh_instance_organization_id("org-a")
        second = fresh_instance_organization_id("org-b")
        self.assertNotEqual(first, second)

    def test_no_identifier_is_derived_from_an_email(self):
        organization_id = main.nova_organization_id()
        with main.db() as conn:
            user_ids = [
                row["user_id"]
                for row in conn.execute("SELECT user_id FROM users").fetchall()
            ]
        for value in [organization_id, *user_ids]:
            self.assertNotIn("@", value or "")

    def test_public_config_exposes_the_nova_organization_id(self):
        config = self.client.get("/api/config").json()
        self.assertEqual(config["organization_id"], main.nova_organization_id())
        # Le slug lisible reste distinct de la clé de tenant.
        self.assertNotEqual(config["organization"]["id"], config["organization_id"])

    # -- sécurité ----------------------------------------------------------
    def test_no_business_role_ever_becomes_an_admin(self):
        for role in ("student", "teacher", "staff", "partner", "employee", "manager"):
            with main.db() as conn:
                conn.execute(
                    "INSERT OR IGNORE INTO users(email, created_at) VALUES (?,?)",
                    ("membre@example.com", 0.0),
                )
                conn.execute(
                    "UPDATE users SET role=? WHERE email=?", (role, "membre@example.com")
                )
                row = conn.execute(
                    "SELECT * FROM users WHERE email=?", ("membre@example.com",)
                ).fetchone()
            self.assertEqual(
                main.security_role_for(row),
                "member",
                f"« {role} » ne doit conférer aucun droit d'administration",
            )

    def test_client_cannot_claim_a_security_role(self):
        """Le Desktop n'est pas une autorité : ce qu'il envoie n'entre pas dans
        la réponse."""
        headers = self.sign_in()
        body = self.client.get(
            "/api/me",
            headers={**headers, "X-Security-Role": "organization_admin"},
            params={"security_role": "it_admin", "organization_id": "autre-tenant"},
        ).json()

        self.assertEqual(body["membership"]["security_role"], "member")
        self.assertEqual(body["organization_id"], main.nova_organization_id())

    def test_a_member_never_receives_another_organization(self):
        headers = self.sign_in()
        with main.db() as conn:
            conn.execute(
                "UPDATE users SET organization_id=? WHERE email=?",
                ("organisation-b", "student@example.com"),
            )
        body = self.client.get("/api/me", headers=headers).json()
        # La valeur vient de la ligne du membre, jamais d'une entrée client.
        self.assertEqual(body["organization_id"], "organisation-b")

        response = self.client.get("/api/me", headers={"Authorization": "Bearer inconnu"})
        self.assertEqual(response.status_code, 401)

    def test_disabled_account_is_refused_and_reported(self):
        headers = self.sign_in()
        with main.db() as conn:
            conn.execute(
                "UPDATE users SET disabled=1, status='disabled' WHERE email=?",
                ("student@example.com",),
            )
        self.assertEqual(self.client.get("/api/me", headers=headers).status_code, 403)

    # -- identités fédérées ------------------------------------------------
    def test_email_is_never_used_as_the_external_subject(self):
        main.record_federated_identity(
            "student@example.com", "microsoft_entra", "oid-immuable-1", "tenant-1"
        )
        with main.db() as conn:
            row = conn.execute(
                "SELECT * FROM federated_identities WHERE provider='microsoft_entra'"
            ).fetchone()
        self.assertEqual(row["external_subject"], "oid-immuable-1")
        self.assertNotEqual(row["external_subject"], "student@example.com")

    def test_same_subject_at_two_providers_stays_two_identities(self):
        main.record_federated_identity("a@example.com", "microsoft_entra", "sujet", "t1")
        main.record_federated_identity("b@example.com", "google_workspace", "sujet", "t2")
        with main.db() as conn:
            rows = conn.execute(
                "SELECT provider, email FROM federated_identities WHERE external_subject='sujet'"
            ).fetchall()
        self.assertEqual(len(rows), 2)
        self.assertEqual(
            {row["provider"] for row in rows}, {"microsoft_entra", "google_workspace"}
        )

    def test_an_identity_is_unique_within_its_provider(self):
        main.record_federated_identity("a@example.com", "microsoft_entra", "sujet", "t1")
        main.record_federated_identity("a@example.com", "microsoft_entra", "sujet", "t1")
        with main.db() as conn:
            count = conn.execute(
                "SELECT COUNT(*) c FROM federated_identities WHERE provider='microsoft_entra'"
            ).fetchone()["c"]
        self.assertEqual(count, 1)

    def test_identity_without_subject_is_not_recorded(self):
        # Plutôt aucune identité qu'une identité vide qui en collisionnerait
        # d'autres.
        main.record_federated_identity("a@example.com", "microsoft_entra", "", "t1")
        with main.db() as conn:
            count = conn.execute("SELECT COUNT(*) c FROM federated_identities").fetchone()["c"]
        self.assertEqual(count, 0)

    def test_tenant_claims_are_read_from_the_microsoft_token(self):
        claims = {"oid": "oid-1", "tid": "tenant-1", "sub": "sub-1"}
        payload = base64.urlsafe_b64encode(json.dumps(claims).encode()).decode().rstrip("=")
        self.assertEqual(main.decode_jwt_claims(f"entete.{payload}.signature"), claims)
        # Un jeton illisible ne fabrique pas de revendications.
        self.assertEqual(main.decode_jwt_claims("pas-un-jwt"), {})


class DatabaseMigrationTests(unittest.TestCase):
    """Migration d'une base **réellement ancienne** : celle d'un établissement
    déjà en production avant cette phase."""

    def legacy_database(self) -> str:
        """Recrée le schéma tel qu'il était : ni organisations, ni identités
        fédérées, ni colonnes d'identité sur `users`."""
        path = os.path.join(TEST_DIRECTORY.name, f"legacy-{uuid.uuid4().hex}.db")
        conn = sqlite3.connect(path)
        conn.executescript("""
            CREATE TABLE auth_codes(email TEXT, code TEXT, expires_at REAL, attempts INTEGER DEFAULT 0);
            CREATE TABLE tokens(token TEXT PRIMARY KEY, email TEXT, machine TEXT,
                                active INTEGER DEFAULT 1, created_at REAL);
            CREATE TABLE users(email TEXT PRIMARY KEY, role TEXT DEFAULT 'student',
                               cohort TEXT DEFAULT '', note TEXT DEFAULT '',
                               disabled INTEGER DEFAULT 0, created_at REAL, last_seen REAL);
            CREATE TABLE usage(email TEXT, day TEXT, transcriptions INTEGER DEFAULT 0,
                               reformulations INTEGER DEFAULT 0, audio_seconds REAL DEFAULT 0,
                               PRIMARY KEY (email, day));
            CREATE TABLE shared_dictionary(id INTEGER PRIMARY KEY AUTOINCREMENT,
                               term TEXT NOT NULL UNIQUE, replacement TEXT NOT NULL);
            CREATE TABLE personal_dictionary(id INTEGER PRIMARY KEY AUTOINCREMENT, email TEXT NOT NULL,
                               term TEXT NOT NULL, replacement TEXT NOT NULL,
                               source TEXT NOT NULL DEFAULT 'personal', UNIQUE(email, term));
            CREATE TABLE snippets(id INTEGER PRIMARY KEY AUTOINCREMENT, email TEXT NOT NULL,
                               trigger TEXT NOT NULL, content TEXT NOT NULL, UNIQUE(email, trigger));
            CREATE TABLE formatting_rules(id INTEGER PRIMARY KEY AUTOINCREMENT, email TEXT,
                               rule TEXT NOT NULL, shared INTEGER NOT NULL DEFAULT 0);
            INSERT INTO users(email, role, cohort, note, disabled, created_at, last_seen)
                VALUES ('ancien@example.com', 'teacher', 'AERO2', 'note admin', 0, 1.0, 2.0);
            INSERT INTO users(email, role, cohort, disabled, created_at, last_seen)
                VALUES ('suspendu@example.com', 'student', 'AERO1', 1, 1.0, 2.0);
            INSERT INTO tokens(token, email, machine, active, created_at)
                VALUES ('jeton-existant', 'ancien@example.com', 'poste-1', 1, 1.0);
        """)
        conn.commit()
        conn.close()
        return path

    def test_existing_database_migrates_without_losing_anything(self):
        path = self.legacy_database()
        with patch.object(main, "DB_PATH", path):
            main.init_db()
            with main.db() as conn:
                users = {
                    row["email"]: row
                    for row in conn.execute("SELECT * FROM users").fetchall()
                }
                token = conn.execute(
                    "SELECT * FROM tokens WHERE token_hash=?",
                    (main.hash_token("jeton-existant"),),
                ).fetchone()
                plaintext = conn.execute(
                    "SELECT COUNT(*) c FROM tokens WHERE token='jeton-existant'"
                ).fetchone()["c"]

        # Aucun compte perdu, aucune donnée écrasée.
        self.assertEqual(set(users), {"ancien@example.com", "suspendu@example.com"})
        ancien = users["ancien@example.com"]
        self.assertEqual(ancien["role"], "teacher")
        self.assertEqual(ancien["cohort"], "AERO2")
        self.assertEqual(ancien["note"], "note admin")
        # Le jeton déjà émis reste utilisable (la session n'est pas cassée)…
        self.assertEqual(token["active"], 1)
        # …mais il n'est plus stocké en clair.
        self.assertEqual(plaintext, 0)

        # Les colonnes ajoutées sont renseignées.
        self.assertTrue(ancien["user_id"])
        self.assertEqual(ancien["organization_id"], main.nova_organization_id())
        self.assertEqual(ancien["identity_provider"], "legacy_email_code")
        self.assertEqual(ancien["status"], "active")
        # Un compte déjà suspendu se retrouve dans le nouveau cycle de vie.
        self.assertEqual(users["suspendu@example.com"]["status"], "disabled")

    def test_migration_is_idempotent_and_keeps_identifiers(self):
        path = self.legacy_database()
        with patch.object(main, "DB_PATH", path):
            main.init_db()
            with main.db() as conn:
                first = conn.execute(
                    "SELECT user_id FROM users WHERE email='ancien@example.com'"
                ).fetchone()["user_id"]
            # Un redémarrage ne doit pas réattribuer d'identifiant : sinon
            # chaque relance créerait un nouvel utilisateur du point de vue de
            # tout ce qui référencera `user_id`.
            main.init_db()
            with main.db() as conn:
                second = conn.execute(
                    "SELECT user_id FROM users WHERE email='ancien@example.com'"
                ).fetchone()["user_id"]
                organizations = conn.execute(
                    "SELECT COUNT(*) c FROM organizations"
                ).fetchone()["c"]
        self.assertEqual(first, second)
        self.assertEqual(organizations, 1)


class OrganizationIdLifecycleTests(unittest.TestCase):
    """L'identifiant d'organisation est attribué **une fois** et ne bouge plus.

    C'est la propriété dont dépend tout le multi-tenant futur : si un renommage
    la brise, deux organisations peuvent fusionner ou se dédoubler sans que
    personne s'en aperçoive.
    """

    def setUp(self):
        self.path = os.path.join(TEST_DIRECTORY.name, f"lifecycle-{uuid.uuid4().hex}.db")

    def boot(self, slug="ipsa", name="IPSA"):
        with patch.object(main, "DB_PATH", self.path), patch.object(
            main, "ORGANIZATION_ID", slug
        ), patch.object(main, "ORGANIZATION_NAME", name):
            main.init_db()
            return main.nova_organization_id()

    def test_first_boot_creates_an_identifier(self):
        organization_id = self.boot()
        self.assertTrue(organization_id)
        with patch.object(main, "DB_PATH", self.path), main.db() as conn:
            stored = conn.execute(
                "SELECT value FROM server_state WHERE key='organization_id'"
            ).fetchone()["value"]
        self.assertEqual(stored, organization_id)

    def test_restart_keeps_the_identifier(self):
        self.assertEqual(self.boot(), self.boot())

    def test_renaming_the_display_name_keeps_the_identifier(self):
        first = self.boot(name="IPSA")
        self.assertEqual(self.boot(name="IPSA Paris — Campus Ivry"), first)

    def test_renaming_the_slug_keeps_the_identifier(self):
        """Le cas que la Phase 13 ratait : un identifiant recalculé depuis le
        slug changeait d'organisation à chaque renommage."""
        first = self.boot(slug="ipsa")
        self.assertEqual(self.boot(slug="ipsa-paris"), first)

    def test_renaming_the_slug_does_not_create_a_second_organization(self):
        self.boot(slug="ipsa")
        self.boot(slug="ipsa-paris")
        with patch.object(main, "DB_PATH", self.path), main.db() as conn:
            rows = conn.execute("SELECT id, slug FROM organizations").fetchall()
        self.assertEqual(len(rows), 1)
        # Le slug, lui, suit bien la configuration : c'est un libellé technique.
        self.assertEqual(rows[0]["slug"], "ipsa-paris")


class SessionTokenTests(unittest.TestCase):
    """Le serveur ne conserve que des empreintes de jetons."""

    @classmethod
    def setUpClass(cls):
        cls.client = TestClient(main.app)

    def setUp(self):
        with main.db() as conn:
            for table in ("auth_codes", "tokens", "users"):
                conn.execute(f"DELETE FROM {table}")

    def sign_in(self, email="student@example.com", machine="poste-1"):
        self.client.post("/api/auth/request", json={"email": email, "machine": machine})
        with main.db() as conn:
            code = conn.execute(
                "SELECT code FROM auth_codes WHERE email=?", (email,)
            ).fetchone()["code"]
        response = self.client.post(
            "/api/auth/verify", json={"email": email, "code": code, "machine": machine}
        )
        self.assertEqual(response.status_code, 200)
        return response.json()["token"]

    def test_a_new_token_is_never_stored_in_clear(self):
        token = self.sign_in()
        with main.db() as conn:
            row = conn.execute("SELECT token, token_hash FROM tokens").fetchone()
        self.assertEqual(row["token_hash"], main.hash_token(token))
        # Ni la valeur, ni un fragment : la colonne ne porte plus de secret.
        self.assertNotEqual(row["token"], token)
        self.assertNotIn(token, row["token"])
        self.assertTrue(row["token"].startswith(main.TOKEN_HASH_MARKER))

    def test_a_valid_token_still_works(self):
        token = self.sign_in()
        response = self.client.get(
            "/api/me", headers={"Authorization": f"Bearer {token}"}
        )
        self.assertEqual(response.status_code, 200)

    def test_an_unknown_token_is_refused(self):
        self.sign_in()
        for candidate in ("inconnu", "", "h:" + "0" * 64):
            response = self.client.get(
                "/api/me", headers={"Authorization": f"Bearer {candidate}"}
            )
            self.assertEqual(response.status_code, 401)

    def test_the_stored_hash_is_not_a_usable_credential(self):
        """Le point de tout l'exercice : lire la base ne donne pas de session."""
        self.sign_in()
        with main.db() as conn:
            stored = conn.execute("SELECT token, token_hash FROM tokens").fetchone()
        for candidate in (stored["token"], stored["token_hash"]):
            response = self.client.get(
                "/api/me", headers={"Authorization": f"Bearer {candidate}"}
            )
            self.assertEqual(response.status_code, 401)

    def test_an_expired_token_is_refused(self):
        token = self.sign_in()
        with main.db() as conn:
            conn.execute("UPDATE tokens SET expires_at=? ", (time.time() - 10,))
        response = self.client.get(
            "/api/me", headers={"Authorization": f"Bearer {token}"}
        )
        self.assertEqual(response.status_code, 401)
        with main.db() as conn:
            active = conn.execute("SELECT active FROM tokens").fetchone()["active"]
        self.assertEqual(active, 0)

    def test_logout_revokes_only_the_presented_session(self):
        first = self.sign_in(machine="poste-1")
        second = self.sign_in(machine="poste-2")
        self.assertEqual(
            self.client.post(
                "/api/auth/logout", headers={"Authorization": f"Bearer {first}"}
            ).status_code,
            200,
        )
        self.assertEqual(
            self.client.get(
                "/api/me", headers={"Authorization": f"Bearer {first}"}
            ).status_code,
            401,
        )
        # L'autre poste du même utilisateur n'est pas déconnecté.
        self.assertEqual(
            self.client.get(
                "/api/me", headers={"Authorization": f"Bearer {second}"}
            ).status_code,
            200,
        )

    def test_reconnecting_the_same_machine_replaces_its_session(self):
        first = self.sign_in(machine="poste-1")
        second = self.sign_in(machine="poste-1")
        self.assertNotEqual(first, second)
        self.assertEqual(
            self.client.get(
                "/api/me", headers={"Authorization": f"Bearer {first}"}
            ).status_code,
            401,
        )
        self.assertEqual(
            self.client.get(
                "/api/me", headers={"Authorization": f"Bearer {second}"}
            ).status_code,
            200,
        )

    def test_a_legacy_plaintext_session_survives_the_migration(self):
        """Un poste connecté avant la migration ne doit pas être déconnecté."""
        with main.db() as conn:
            conn.execute(
                "INSERT OR IGNORE INTO users(email, created_at) VALUES (?,?)",
                ("ancien@example.com", time.time()),
            )
            conn.execute(
                "INSERT INTO tokens(token, email, machine, active, created_at, expires_at) "
                "VALUES (?,?,?,?,?,?)",
                (
                    "jeton-en-clair",
                    "ancien@example.com",
                    "poste-1",
                    1,
                    time.time(),
                    time.time() + 3600,
                ),
            )
            main.migrate_tokens_to_hashes(conn)

        response = self.client.get(
            "/api/me", headers={"Authorization": "Bearer jeton-en-clair"}
        )
        self.assertEqual(response.status_code, 200)
        with main.db() as conn:
            row = conn.execute(
                "SELECT token FROM tokens WHERE token_hash=?",
                (main.hash_token("jeton-en-clair"),),
            ).fetchone()
        self.assertNotIn("jeton-en-clair", row["token"])

    def test_the_migration_is_idempotent(self):
        token = self.sign_in()
        with main.db() as conn:
            main.migrate_tokens_to_hashes(conn)
            main.migrate_tokens_to_hashes(conn)
        self.assertEqual(
            self.client.get(
                "/api/me", headers={"Authorization": f"Bearer {token}"}
            ).status_code,
            200,
        )

    def test_a_restart_does_not_invalidate_sessions(self):
        token = self.sign_in()
        main.init_db()  # ce que fait un redémarrage du serveur
        self.assertEqual(
            self.client.get(
                "/api/me", headers={"Authorization": f"Bearer {token}"}
            ).status_code,
            200,
        )


class OtpDeliveryTests(unittest.TestCase):
    """Un code à usage unique n'apparaît jamais dans les journaux d'une
    production."""

    def test_development_may_print_the_code(self):
        with patch.object(main, "NOVA_ENV", "development"), patch.object(
            main, "SMTP_HOST", ""
        ), patch("builtins.print") as printed:
            main.send_code_email("etudiant@example.com", "123456")
        self.assertTrue(printed.called)

    def test_production_refuses_instead_of_printing_the_code(self):
        with patch.object(main, "NOVA_ENV", "production"), patch.object(
            main, "SMTP_HOST", ""
        ), patch("builtins.print") as printed:
            with self.assertRaises(RuntimeError):
                main.send_code_email("etudiant@example.com", "123456")
        self.assertFalse(printed.called)

    def test_the_default_environment_is_the_prudent_one(self):
        # Un déploiement qui oublie de déclarer son environnement obtient le
        # comportement le plus prudent.
        self.assertEqual(os.getenv("NOVA_ENV_DEFAULT_CHECK", "production"), "production")
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("NOVA_ENV", None)
            self.assertEqual(os.getenv("NOVA_ENV", "production"), "production")

    def test_a_misconfigured_server_does_not_leak_through_the_endpoint(self):
        client = TestClient(main.app)
        with main.db() as conn:
            conn.execute("DELETE FROM auth_codes")
        with patch.object(main, "NOVA_ENV", "production"), patch.object(
            main, "SMTP_HOST", ""
        ), patch("builtins.print") as printed:
            response = client.post(
                "/api/auth/request",
                json={"email": "student@example.com", "machine": "poste-1"},
            )
        self.assertEqual(response.status_code, 200)
        # L'erreur de configuration est journalisée, jamais le code.
        for call in printed.call_args_list:
            self.assertNotIn("Code pour", str(call))


class TenantIsolationTests(unittest.TestCase):
    """Une organisation n'obtient jamais les données d'une autre."""

    @classmethod
    def setUpClass(cls):
        cls.client = TestClient(main.app)

    def setUp(self):
        with main.db() as conn:
            for table in ("auth_codes", "tokens", "users", "snippets", "personal_dictionary"):
                conn.execute(f"DELETE FROM {table}")

    def sign_in(self, email):
        self.client.post("/api/auth/request", json={"email": email, "machine": "poste"})
        with main.db() as conn:
            code = conn.execute(
                "SELECT code FROM auth_codes WHERE email=?", (email,)
            ).fetchone()["code"]
        response = self.client.post(
            "/api/auth/verify", json={"email": email, "code": code, "machine": "poste"}
        )
        return {"Authorization": f"Bearer {response.json()['token']}"}

    def test_a_token_only_ever_returns_its_own_member(self):
        a = self.sign_in("a@example.com")
        b = self.sign_in("b@example.com")
        self.assertEqual(self.client.get("/api/me", headers=a).json()["email"], "a@example.com")
        self.assertEqual(self.client.get("/api/me", headers=b).json()["email"], "b@example.com")

    def test_groups_and_member_type_never_cross(self):
        a = self.sign_in("a@example.com")
        b = self.sign_in("b@example.com")
        with main.db() as conn:
            conn.execute(
                "UPDATE users SET cohort='AERO2', role='teacher' WHERE email='a@example.com'"
            )
            conn.execute(
                "UPDATE users SET cohort='SALES', role='student' WHERE email='b@example.com'"
            )
        body_a = self.client.get("/api/me", headers=a).json()
        body_b = self.client.get("/api/me", headers=b).json()

        self.assertEqual([g["id"] for g in body_a["membership"]["groups"]], ["AERO2"])
        self.assertEqual([g["id"] for g in body_b["membership"]["groups"]], ["SALES"])
        self.assertEqual(body_a["membership"]["member_type"], "teacher")
        self.assertEqual(body_b["membership"]["member_type"], "student")
        # Un métier ne franchit jamais la frontière du rôle de sécurité.
        self.assertEqual(body_a["membership"]["security_role"], "member")

    def test_a_client_supplied_organization_is_ignored(self):
        headers = self.sign_in("a@example.com")
        body = self.client.get(
            "/api/me",
            headers={**headers, "X-Organization-Id": "organisation-b"},
            params={"organization_id": "organisation-b", "security_role": "it_admin"},
        ).json()
        self.assertEqual(body["organization_id"], main.nova_organization_id())
        self.assertEqual(body["membership"]["security_role"], "member")

    def test_personal_data_never_crosses_accounts(self):
        a = self.sign_in("a@example.com")
        b = self.sign_in("b@example.com")
        self.client.post("/api/snippets", headers=a, json={"trigger": "sig", "content": "A"})
        vocabulary_b = self.client.get("/api/vocabulary", headers=b).json()
        self.assertEqual(
            [s for s in vocabulary_b["snippets"] if s["content"] == "A"], []
        )

    def test_capabilities_come_from_the_server_not_the_request(self):
        headers = self.sign_in("a@example.com")
        body = self.client.get(
            "/api/me", headers=headers, params={"capabilities": "aiSkills"}
        ).json()
        expected = sorted(
            name
            for name, enabled in main.public_campus_config()["capabilities"].items()
            if enabled
        )
        self.assertEqual(body["capabilities"], expected)


if __name__ == "__main__":
    unittest.main()
