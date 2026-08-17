import asyncio
import base64
import contextlib
import hashlib
import hmac
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

import jwt
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi import HTTPException
from fastapi.testclient import TestClient
from pathlib import Path

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


class EntraPkceTests(unittest.TestCase):
    """SSO Microsoft moderne : Authorization Code + PKCE.

    Aucun test ne touche le réseau. Les jetons sont signés localement par une
    paire de clés de test, et le JWKS est injecté dans le cache — ce que fait
    Microsoft en production, sans en dépendre ici.
    """

    TENANT_A = "11111111-1111-4111-8111-111111111111"
    TENANT_B = "22222222-2222-4222-8222-222222222222"
    CLIENT_ID = "aaaaaaaa-bbbb-4ccc-8ddd-eeeeeeeeeeee"
    ORGANIZATION_B = "org-b-uuid"

    @classmethod
    def setUpClass(cls):
        cls.client = TestClient(main.app)
        cls.private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        cls.other_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)

    def setUp(self):
        with main.db() as conn:
            for table in ("auth_codes", "tokens", "users", "federated_identities"):
                conn.execute(f"DELETE FROM {table}")
            conn.execute("DELETE FROM organizations WHERE id=?", (self.ORGANIZATION_B,))
            # Le tenant A est rattaché à l'organisation servie par l'instance.
            conn.execute(
                "UPDATE organizations SET entra_tenant_id=? WHERE id=?",
                (self.TENANT_A, main.nova_organization_id()),
            )
            # Le tenant B appartient à une autre organisation.
            conn.execute(
                "INSERT OR REPLACE INTO organizations(id, slug, display_name, org_type, "
                "entra_tenant_id, created_at) VALUES (?,?,?,?,?,?)",
                (self.ORGANIZATION_B, "org-b", "Organisation B", "business",
                 self.TENANT_B, time.time()),
            )
        main.SSO_FLOWS.clear()
        main.JWKS_CACHE.clear()
        self.seed_jwks(self.TENANT_A)
        self.seed_jwks(self.TENANT_B)

    # -- outillage ---------------------------------------------------------

    def jwk_for(self, key, kid: str) -> dict:
        jwk = json.loads(
            jwt.algorithms.RSAAlgorithm.to_jwk(key.public_key())
        )
        jwk.update({"kid": kid, "use": "sig", "alg": "RS256"})
        return jwk

    def seed_jwks(self, tenant_id: str, kid: str = "test-key-1", key=None):
        uri = main.PROVIDERS["microsoft_entra"]["jwks_uri"]({"tid": tenant_id})
        main.JWKS_CACHE[uri] = {
            "jwks": {"keys": [self.jwk_for(key or self.private_key, kid)]},
            "fetched_at": time.time(),
        }

    def id_token(self, key=None, kid="test-key-1", **overrides) -> str:
        now = int(time.time())
        claims = {
            "iss": f"https://login.microsoftonline.com/{self.TENANT_A}/v2.0",
            "aud": self.CLIENT_ID,
            "exp": now + 600,
            "iat": now,
            "nbf": now - 10,
            "sub": "sub-application-specifique",
            "oid": "oid-immuable-1",
            "tid": self.TENANT_A,
            "nonce": "nonce-attendu-0123456789",
            "preferred_username": "etudiant@example.com",
        }
        claims.update(overrides)
        for empty in [k for k, v in claims.items() if v is None]:
            claims.pop(empty)
        return jwt.encode(claims, key or self.private_key, algorithm="RS256",
                          headers={"kid": kid})

    def validate(self, token: str, nonce: str = "nonce-attendu-0123456789"):
        with patch.object(main, "ENTRA_CLIENT_ID", self.CLIENT_ID):
            return asyncio.run(main.validate_entra_id_token(token, nonce))

    # -- validation du jeton d'identité ------------------------------------

    def test_a_correctly_signed_token_is_accepted(self):
        claims = self.validate(self.id_token())
        self.assertEqual(claims["tid"], self.TENANT_A)
        self.assertEqual(claims["oid"], "oid-immuable-1")

    def test_a_token_signed_by_another_key_is_refused(self):
        with self.assertRaises(HTTPException) as raised:
            self.validate(self.id_token(key=self.other_key))
        self.assertEqual(raised.exception.detail, "ID_TOKEN_INVALID")

    def test_an_unknown_kid_triggers_a_controlled_refresh(self):
        """Une clé inconnue peut être une rotation, pas une attaque : on
        recharge une fois, depuis l'URL dérivée du tenant."""
        rotated = self.jwk_for(self.private_key, "kid-apres-rotation")
        calls = []

        async def fake_fetch(jwks_uri):
            calls.append(jwks_uri)
            return {"keys": [rotated]}

        # Le cache ne contient que l'ancienne clé.
        self.seed_jwks(self.TENANT_A, kid="ancienne-cle")
        with patch.object(main, "fetch_jwks", fake_fetch):
            claims = self.validate(self.id_token(kid="kid-apres-rotation"))
        self.assertEqual(claims["oid"], "oid-immuable-1")
        self.assertEqual(len(calls), 1)
        self.assertIn(self.TENANT_A, calls[0])

    def test_an_unknown_kid_that_survives_a_refresh_is_refused(self):
        async def fake_fetch(jwks_uri):
            return {"keys": []}

        with patch.object(main, "fetch_jwks", fake_fetch):
            with self.assertRaises(HTTPException):
                self.validate(self.id_token(kid="kid-inexistant"))

    def test_a_wrong_issuer_is_refused(self):
        with self.assertRaises(HTTPException):
            self.validate(self.id_token(iss="https://accounts.example.com/v2.0"))

    def test_a_wrong_audience_is_refused(self):
        with self.assertRaises(HTTPException):
            self.validate(self.id_token(aud="une-autre-application"))

    def test_an_expired_token_is_refused(self):
        past = int(time.time()) - 10_000
        with self.assertRaises(HTTPException):
            self.validate(self.id_token(exp=past, iat=past - 100, nbf=past - 100))

    def test_a_token_not_yet_valid_is_refused(self):
        future = int(time.time()) + 10_000
        with self.assertRaises(HTTPException):
            self.validate(self.id_token(nbf=future, iat=future))

    def test_a_wrong_or_missing_nonce_is_refused(self):
        with self.assertRaises(HTTPException):
            self.validate(self.id_token(nonce="autre-nonce"))
        with self.assertRaises(HTTPException):
            self.validate(self.id_token(nonce=None))
        # Un nonce attendu vide n'ouvre rien non plus.
        with self.assertRaises(HTTPException):
            self.validate(self.id_token(), nonce="")

    def test_a_token_without_tenant_or_subject_is_refused(self):
        with self.assertRaises(HTTPException):
            self.validate(self.id_token(tid=None))
        with self.assertRaises(HTTPException):
            self.validate(self.id_token(oid=None))

    def test_a_symmetric_algorithm_is_refused(self):
        """Confusion d'algorithme : le jeton est signé en HMAC avec la clé
        **publique**, que l'attaquant connaît. Seul un algorithme imposé côté
        serveur ferme cette porte."""
        public_pem = self.private_key.public_key().public_bytes(
            serialization.Encoding.PEM,
            serialization.PublicFormat.SubjectPublicKeyInfo,
        )
        now = int(time.time())
        header = {"alg": "HS256", "typ": "JWT", "kid": "test-key-1"}
        payload = {
            "iss": f"https://login.microsoftonline.com/{self.TENANT_A}/v2.0",
            "aud": self.CLIENT_ID,
            "exp": now + 600,
            "iat": now,
            "sub": "s",
            "oid": "oid-immuable-1",
            "tid": self.TENANT_A,
            "nonce": "nonce-attendu-0123456789",
        }

        def segment(value: dict) -> bytes:
            return base64.urlsafe_b64encode(
                json.dumps(value, separators=(",", ":")).encode()
            ).rstrip(b"=")

        signing_input = segment(header) + b"." + segment(payload)
        signature = base64.urlsafe_b64encode(
            hmac.new(public_pem, signing_input, hashlib.sha256).digest()
        ).rstrip(b"=")
        forged = (signing_input + b"." + signature).decode()

        with self.assertRaises(HTTPException):
            self.validate(forged)

    def test_an_unsigned_token_is_refused(self):
        now = int(time.time())
        forged = jwt.encode(
            {"iss": "x", "aud": self.CLIENT_ID, "exp": now + 600, "sub": "s"},
            key="",
            algorithm="none",
            headers={"kid": "test-key-1"},
        )
        with self.assertRaises(HTTPException):
            self.validate(forged)

    def test_the_key_location_never_comes_from_the_token(self):
        # `jku` est un champ d'en-tête que l'attaquant contrôle : il ne doit
        # jouer aucun rôle. L'URL est dérivée du tenant, point.
        uri = main.PROVIDERS["microsoft_entra"]["jwks_uri"]({"tid": self.TENANT_A})
        self.assertIn(self.TENANT_A, uri)
        self.assertTrue(uri.startswith("https://login.microsoftonline.com/"))

    # -- démarrage du flux -------------------------------------------------

    def start_flow(self, redirect_uri="http://127.0.0.1:53125/callback"):
        with patch.object(main, "ENTRA_CLIENT_ID", self.CLIENT_ID):
            return self.client.post(
                "/api/auth/entra/pkce/start",
                json={
                    "code_challenge": "a" * 43,
                    "nonce": "nonce-attendu-0123456789",
                    "redirect_uri": redirect_uri,
                    "machine": "poste-1",
                },
            )

    def test_the_authorization_url_carries_pkce_and_no_secret(self):
        body = self.start_flow().json()
        url = body["authorization_url"]
        self.assertIn("code_challenge_method=S256", url)
        self.assertIn("response_type=code", url)
        self.assertIn(f"client_id={self.CLIENT_ID}", url)
        self.assertNotIn("client_secret", url)
        self.assertIn("organizations", url)
        self.assertTrue(body["flow_id"])

    def test_only_a_loopback_redirect_is_accepted(self):
        for rejected in (
            "http://192.168.1.20:53125/callback",
            "http://attaquant.example.com/callback",
            "http://localhost:53125/callback",
            "https://127.0.0.1:53125/callback",
            "http://127.0.0.1/callback",
        ):
            response = self.start_flow(rejected)
            self.assertEqual(response.status_code, 400, rejected)
            self.assertEqual(response.json()["detail"], "REDIRECT_URI_INVALID")

    def test_sign_in_is_unavailable_without_configuration(self):
        with patch.object(main, "ENTRA_CLIENT_ID", ""):
            response = self.client.post(
                "/api/auth/entra/pkce/start",
                json={
                    "code_challenge": "a" * 43,
                    "nonce": "n" * 16,
                    "redirect_uri": "http://127.0.0.1:53125/callback",
                },
            )
        self.assertEqual(response.status_code, 503)

    def test_availability_requires_both_client_and_tenant_mapping(self):
        with patch.object(main, "ENTRA_CLIENT_ID", self.CLIENT_ID):
            body = self.client.get("/api/auth/entra/pkce/available").json()
        self.assertTrue(body["available"])
        self.assertIn("microsoft_entra", body["providers"])
        # Le mode hérité reste toujours proposé.
        self.assertIn("legacy_email_code", body["providers"])

        with main.db() as conn:
            conn.execute(
                "UPDATE organizations SET entra_tenant_id=NULL WHERE id=?",
                (main.nova_organization_id(),),
            )
        with patch.object(main, "ENTRA_CLIENT_ID", self.CLIENT_ID):
            body = self.client.get("/api/auth/entra/pkce/available").json()
        self.assertFalse(body["available"])
        self.assertNotIn("microsoft_entra", body["providers"])

    # -- échange -----------------------------------------------------------

    def exchange(self, flow_id, token=None, redirect_uri="http://127.0.0.1:53125/callback",
                 code="code-microsoft"):
        async def fake_redeem(provider, body):
            return {"id_token": token if token is not None else self.id_token()}

        with patch.object(main, "ENTRA_CLIENT_ID", self.CLIENT_ID), patch.object(
            main, "redeem_authorization_code", fake_redeem
        ):
            return self.client.post(
                "/api/auth/entra/pkce/exchange",
                json={
                    "flow_id": flow_id,
                    "code": code,
                    "code_verifier": "v" * 64,
                    "redirect_uri": redirect_uri,
                },
            )

    def test_a_complete_sign_in_issues_a_nova_session(self):
        flow_id = self.start_flow().json()["flow_id"]
        response = self.exchange(flow_id)
        self.assertEqual(response.status_code, 200, response.text)
        body = response.json()
        self.assertEqual(body["email"], "etudiant@example.com")
        # Le poste ne reçoit qu'une session Nova : aucun jeton Microsoft.
        self.assertNotIn("id_token", body)
        self.assertNotIn("access_token", body)
        self.assertNotIn("refresh_token", body)
        # …et cette session fonctionne.
        me = self.client.get(
            "/api/me", headers={"Authorization": f"Bearer {body['token']}"}
        )
        self.assertEqual(me.status_code, 200)

    def test_the_identity_becomes_verified(self):
        flow_id = self.start_flow().json()["flow_id"]
        self.exchange(flow_id)
        with main.db() as conn:
            row = conn.execute(
                "SELECT verification, external_subject, external_tenant_id "
                "FROM federated_identities WHERE provider='microsoft_entra'"
            ).fetchone()
        self.assertEqual(row["verification"], "verified")
        self.assertEqual(row["external_subject"], "oid-immuable-1")
        self.assertEqual(row["external_tenant_id"], self.TENANT_A)
        # Le sujet n'est jamais l'adresse.
        self.assertNotIn("@", row["external_subject"])

    def test_a_flow_is_single_use(self):
        flow_id = self.start_flow().json()["flow_id"]
        self.assertEqual(self.exchange(flow_id).status_code, 200)
        # Rejouer le même flux — code capté, tentative de rejeu — échoue.
        replayed = self.exchange(flow_id)
        self.assertEqual(replayed.status_code, 400)
        self.assertEqual(replayed.json()["detail"], "AUTH_TIMEOUT")

    def test_an_unknown_flow_is_refused(self):
        self.assertEqual(self.exchange("flux-inconnu").status_code, 400)

    def test_an_expired_flow_is_refused(self):
        flow_id = self.start_flow().json()["flow_id"]
        main.SSO_FLOWS[flow_id]["expires_at"] = time.time() - 1
        response = self.exchange(flow_id)
        self.assertEqual(response.json()["detail"], "AUTH_TIMEOUT")

    def test_a_changed_redirect_uri_is_refused(self):
        flow_id = self.start_flow().json()["flow_id"]
        response = self.exchange(
            flow_id, redirect_uri="http://127.0.0.1:60000/callback"
        )
        self.assertEqual(response.json()["detail"], "REDIRECT_URI_INVALID")

    def test_a_replayed_nonce_from_an_older_flow_is_refused(self):
        first = self.start_flow().json()["flow_id"]
        self.exchange(first)
        # Nouvelle tentative avec un nonce différent : un jeton émis pour la
        # première ne doit pas la satisfaire.
        with patch.object(main, "ENTRA_CLIENT_ID", self.CLIENT_ID):
            second = self.client.post(
                "/api/auth/entra/pkce/start",
                json={
                    "code_challenge": "b" * 43,
                    "nonce": "nonce-de-la-seconde-tentative",
                    "redirect_uri": "http://127.0.0.1:53125/callback",
                    "machine": "poste-1",
                },
            ).json()["flow_id"]
        response = self.exchange(second, token=self.id_token())
        self.assertEqual(response.status_code, 401)
        self.assertEqual(response.json()["detail"], "ID_TOKEN_INVALID")

    def test_a_failed_token_exchange_is_reported_without_detail(self):
        flow_id = self.start_flow().json()["flow_id"]

        async def failing(provider, body):
            raise main.auth_error(400, "TOKEN_EXCHANGE_FAILED")

        with patch.object(main, "ENTRA_CLIENT_ID", self.CLIENT_ID), patch.object(
            main, "redeem_authorization_code", failing
        ):
            response = self.client.post(
                "/api/auth/entra/pkce/exchange",
                json={
                    "flow_id": flow_id,
                    "code": "code-perime",
                    "code_verifier": "v" * 64,
                    "redirect_uri": "http://127.0.0.1:53125/callback",
                },
            )
        self.assertEqual(response.json()["detail"], "TOKEN_EXCHANGE_FAILED")

    # -- tenants -----------------------------------------------------------

    def test_an_unmapped_tenant_is_denied(self):
        flow_id = self.start_flow().json()["flow_id"]
        unknown = "99999999-9999-4999-8999-999999999999"
        self.seed_jwks(unknown)
        token = self.id_token(
            tid=unknown,
            iss=f"https://login.microsoftonline.com/{unknown}/v2.0",
        )
        response = self.exchange(flow_id, token=token)
        self.assertEqual(response.status_code, 403)
        self.assertEqual(response.json()["detail"], "TENANT_NOT_ALLOWED")

    def test_a_tenant_belonging_to_another_organization_cannot_sign_in_here(self):
        """Le tenant B est connu de Nova, mais rattaché à une autre
        organisation : il n'obtient pas de session sur cette instance."""
        flow_id = self.start_flow().json()["flow_id"]
        token = self.id_token(
            tid=self.TENANT_B,
            iss=f"https://login.microsoftonline.com/{self.TENANT_B}/v2.0",
            oid="oid-du-tenant-b",
            preferred_username="employe@example.com",
        )
        response = self.exchange(flow_id, token=token)
        # L'identité est valide, l'organisation ne correspond pas.
        self.assertEqual(response.status_code, 403)

    def test_the_same_subject_in_two_tenants_is_not_confused(self):
        flow_id = self.start_flow().json()["flow_id"]
        self.exchange(flow_id)
        with main.db() as conn:
            conn.execute(
                "INSERT INTO federated_identities(provider, external_subject, "
                "external_tenant_id, organization_id, email, verification, created_at, "
                "last_seen) VALUES (?,?,?,?,?,?,?,?)",
                ("microsoft_entra", "oid-immuable-1-bis", self.TENANT_B,
                 self.ORGANIZATION_B, "homonyme@example.com", "verified",
                 time.time(), time.time()),
            )
            rows = conn.execute(
                "SELECT organization_id, email FROM federated_identities "
                "ORDER BY organization_id"
            ).fetchall()
        self.assertEqual(len(rows), 2)
        self.assertNotEqual(rows[0]["organization_id"], rows[1]["organization_id"])

    def test_a_client_supplied_organization_is_ignored(self):
        flow_id = self.start_flow().json()["flow_id"]

        async def fake_redeem(provider, body):
            return {"id_token": self.id_token()}

        with patch.object(main, "ENTRA_CLIENT_ID", self.CLIENT_ID), patch.object(
            main, "redeem_authorization_code", fake_redeem
        ):
            response = self.client.post(
                "/api/auth/entra/pkce/exchange",
                json={
                    "flow_id": flow_id,
                    "code": "code-microsoft",
                    "code_verifier": "v" * 64,
                    "redirect_uri": "http://127.0.0.1:53125/callback",
                    "organization_id": self.ORGANIZATION_B,
                    "security_role": "organization_admin",
                },
            )
        token = response.json()["token"]
        me = self.client.get(
            "/api/me", headers={"Authorization": f"Bearer {token}"}
        ).json()
        self.assertEqual(me["organization_id"], main.nova_organization_id())
        self.assertEqual(me["membership"]["security_role"], "member")

    # -- rattachement de compte -------------------------------------------

    def test_a_known_federated_identity_finds_its_account(self):
        flow_id = self.start_flow().json()["flow_id"]
        self.exchange(flow_id)
        second = self.start_flow().json()["flow_id"]
        # Même sujet, adresse changée côté Microsoft : le compte est retrouvé
        # par le sujet immuable, pas par l'adresse.
        response = self.exchange(
            second, token=self.id_token(preferred_username="nouvelle@example.com")
        )
        self.assertEqual(response.json()["email"], "etudiant@example.com")

    def test_a_legacy_account_is_linked_under_controlled_conditions(self):
        with main.db() as conn:
            conn.execute(
                "INSERT INTO users(email, role, cohort, created_at, organization_id) "
                "VALUES (?,?,?,?,?)",
                ("etudiant@example.com", "student", "AERO2", time.time(),
                 main.nova_organization_id()),
            )
        flow_id = self.start_flow().json()["flow_id"]
        response = self.exchange(flow_id)
        self.assertEqual(response.status_code, 200)
        me = self.client.get(
            "/api/me", headers={"Authorization": f"Bearer {response.json()['token']}"}
        ).json()
        # Le compte historique est conservé, cohorte comprise.
        self.assertEqual(me["cohort"], "AERO2")
        self.assertEqual(me["membership"]["member_type"], "student")
        self.assertEqual(me["identity"]["provider"], "microsoft_entra")

    def test_a_conflicting_identity_blocks_the_link(self):
        """Une autre identité Microsoft occupe déjà ce compte : on refuse plutôt
        que de rattacher deux personnes au même compte."""
        with main.db() as conn:
            conn.execute(
                "INSERT INTO users(email, created_at, organization_id) VALUES (?,?,?)",
                ("etudiant@example.com", time.time(), main.nova_organization_id()),
            )
            conn.execute(
                "INSERT INTO federated_identities(provider, external_subject, "
                "external_tenant_id, organization_id, email, verification, created_at, "
                "last_seen) VALUES (?,?,?,?,?,?,?,?)",
                ("microsoft_entra", "un-autre-oid", self.TENANT_A,
                 main.nova_organization_id(), "etudiant@example.com", "verified",
                 time.time(), time.time()),
            )
        flow_id = self.start_flow().json()["flow_id"]
        response = self.exchange(flow_id)
        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.json()["detail"], "IDENTITY_CONFLICT")

    def test_an_account_of_another_organization_is_never_taken_over(self):
        with main.db() as conn:
            conn.execute(
                "INSERT INTO users(email, created_at, organization_id) VALUES (?,?,?)",
                ("etudiant@example.com", time.time(), self.ORGANIZATION_B),
            )
        flow_id = self.start_flow().json()["flow_id"]
        response = self.exchange(flow_id)
        self.assertEqual(response.status_code, 403)
        self.assertEqual(response.json()["detail"], "ORGANIZATION_MISMATCH")

    def test_a_disabled_account_cannot_sign_in_through_sso(self):
        with main.db() as conn:
            conn.execute(
                "INSERT INTO users(email, created_at, organization_id, disabled) "
                "VALUES (?,?,?,1)",
                ("etudiant@example.com", time.time(), main.nova_organization_id()),
            )
        flow_id = self.start_flow().json()["flow_id"]
        response = self.exchange(flow_id)
        self.assertEqual(response.status_code, 403)
        self.assertEqual(response.json()["detail"], "ACCOUNT_DISABLED")

    def test_a_deprovisioned_account_cannot_sign_in_through_sso(self):
        with main.db() as conn:
            conn.execute(
                "INSERT INTO users(email, created_at, organization_id, status) "
                "VALUES (?,?,?,'deprovisioned')",
                ("etudiant@example.com", time.time(), main.nova_organization_id()),
            )
        flow_id = self.start_flow().json()["flow_id"]
        self.assertEqual(self.exchange(flow_id).status_code, 403)

    def test_a_member_outside_the_admission_policy_is_refused(self):
        flow_id = self.start_flow().json()["flow_id"]
        token = self.id_token(preferred_username="externe@autre-domaine.com")
        response = self.exchange(flow_id, token=token)
        self.assertEqual(response.status_code, 403)
        self.assertEqual(response.json()["detail"], "MEMBERSHIP_NOT_FOUND")

    def test_microsoft_sign_in_never_grants_administration(self):
        flow_id = self.start_flow().json()["flow_id"]
        token = self.id_token(
            roles=["GlobalAdministrator"], jobTitle="Manager", wids=["admin"]
        )
        response = self.exchange(flow_id, token=token)
        me = self.client.get(
            "/api/me", headers={"Authorization": f"Bearer {response.json()['token']}"}
        ).json()
        self.assertEqual(me["membership"]["security_role"], "member")

    # -- cohabitation avec l'existant --------------------------------------

    def test_the_legacy_email_code_still_works_alongside_sso(self):
        email = "student@example.com"
        self.client.post("/api/auth/request", json={"email": email, "machine": "p"})
        with main.db() as conn:
            code = conn.execute(
                "SELECT code FROM auth_codes WHERE email=?", (email,)
            ).fetchone()["code"]
        response = self.client.post(
            "/api/auth/verify", json={"email": email, "code": code, "machine": "p"}
        )
        self.assertEqual(response.status_code, 200)
        me = self.client.get(
            "/api/me", headers={"Authorization": f"Bearer {response.json()['token']}"}
        ).json()
        self.assertEqual(me["identity"]["provider"], "legacy_email_code")

    def test_device_code_identities_stay_transport_only(self):
        """Le Device Code hérité ne produit pas d'identité vérifiée : il n'a
        jamais contrôlé la signature du jeton."""
        main.record_federated_identity(
            "ancien@example.com", "microsoft_entra", "oid-device-code", self.TENANT_A
        )
        with main.db() as conn:
            row = conn.execute(
                "SELECT verification FROM federated_identities WHERE external_subject=?",
                ("oid-device-code",),
            ).fetchone()
        self.assertEqual(row["verification"], "transport_only")

    def test_no_secret_is_ever_sent_to_microsoft(self):
        """Client public : la requête d'échange ne porte aucun secret client,
        et c'est le `code_verifier` qui prouve l'origine de la demande."""
        captured = {}

        class FakeResponse:
            status_code = 200

            def json(self):
                return {"id_token": "peu-importe"}

        class FakeClient:
            async def __aenter__(self):
                return self

            async def __aexit__(self, *args):
                return False

            async def post(self, url, data=None, auth=None):
                captured["url"] = url
                captured["data"] = data
                captured["auth"] = auth
                return FakeResponse()

        body = main.EntraPkceExchangeRequest(
            flow_id="f",
            code="code-microsoft",
            code_verifier="v" * 64,
            redirect_uri="http://127.0.0.1:53125/callback",
        )
        with patch.object(main, "ENTRA_CLIENT_ID", self.CLIENT_ID), patch.object(
            main.httpx, "AsyncClient", lambda **kwargs: FakeClient()
        ):
            asyncio.run(main.redeem_authorization_code("microsoft_entra", body))

        self.assertNotIn("client_secret", captured["data"])
        self.assertEqual(captured["data"]["code_verifier"], "v" * 64)
        self.assertEqual(captured["data"]["grant_type"], "authorization_code")
        self.assertTrue(captured["url"].startswith("https://login.microsoftonline.com/"))


class GoogleWorkspaceTests(unittest.TestCase):
    """SSO Google Workspace, sur le même moteur que Microsoft.

    Aucun test ne touche le réseau : les jetons sont signés par une paire de
    clés locale et le JWKS est injecté dans le cache partagé.
    """

    CLIENT_ID = "111111111111-abcdefghij.apps.googleusercontent.com"
    CLIENT_SECRET = "GOCSPX-secret-de-test-non-confidentiel"
    HOSTED_DOMAIN = "example.com"
    OTHER_DOMAIN = "autre-workspace.com"
    ORGANIZATION_OTHER = "org-google-b"

    @classmethod
    def setUpClass(cls):
        cls.client = TestClient(main.app)
        cls.private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        cls.other_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)

    def setUp(self):
        with main.db() as conn:
            for table in ("auth_codes", "tokens", "users", "federated_identities"):
                conn.execute(f"DELETE FROM {table}")
            conn.execute("DELETE FROM organizations WHERE id=?", (self.ORGANIZATION_OTHER,))
            conn.execute(
                "UPDATE organizations SET google_hosted_domain=? WHERE id=?",
                (self.HOSTED_DOMAIN, main.nova_organization_id()),
            )
            conn.execute(
                "INSERT OR REPLACE INTO organizations(id, slug, display_name, org_type, "
                "google_hosted_domain, created_at) VALUES (?,?,?,?,?,?)",
                (self.ORGANIZATION_OTHER, "org-google-b", "Organisation B", "business",
                 self.OTHER_DOMAIN, time.time()),
            )
        main.SSO_FLOWS.clear()
        main.JWKS_CACHE.clear()
        self.seed_jwks()

    def jwk_for(self, key, kid):
        jwk = json.loads(jwt.algorithms.RSAAlgorithm.to_jwk(key.public_key()))
        jwk.update({"kid": kid, "use": "sig", "alg": "RS256"})
        return jwk

    def seed_jwks(self, kid="google-key-1", key=None):
        uri = main.PROVIDERS["google_workspace"]["jwks_uri"]({})
        main.JWKS_CACHE[uri] = {
            "jwks": {"keys": [self.jwk_for(key or self.private_key, kid)]},
            "fetched_at": time.time(),
        }

    def id_token(self, key=None, kid="google-key-1", **overrides):
        now = int(time.time())
        claims = {
            "iss": "https://accounts.google.com",
            "aud": self.CLIENT_ID,
            "exp": now + 600,
            "iat": now,
            "sub": "104729184756310928475",
            "hd": self.HOSTED_DOMAIN,
            "email": "etudiant@example.com",
            "nonce": "nonce-google-0123456789",
        }
        claims.update(overrides)
        for empty in [k for k, v in claims.items() if v is None]:
            claims.pop(empty)
        return jwt.encode(claims, key or self.private_key, algorithm="RS256",
                          headers={"kid": kid})

    def google_config(self):
        return (
            patch.object(main, "GOOGLE_CLIENT_ID", self.CLIENT_ID),
            patch.object(main, "GOOGLE_CLIENT_SECRET", self.CLIENT_SECRET),
        )

    def validate(self, token, nonce="nonce-google-0123456789"):
        a, b = self.google_config()
        with a, b:
            return asyncio.run(main.validate_id_token("google_workspace", token, nonce))

    def start_flow(self, redirect_uri="http://127.0.0.1:53200/callback"):
        a, b = self.google_config()
        with a, b:
            return self.client.post(
                "/api/auth/sso/google_workspace/start",
                json={
                    "code_challenge": "g" * 43,
                    "nonce": "nonce-google-0123456789",
                    "redirect_uri": redirect_uri,
                    "machine": "poste-1",
                },
            )

    def exchange(self, flow_id, token=None,
                 redirect_uri="http://127.0.0.1:53200/callback",
                 provider="google_workspace"):
        async def fake_redeem(prov, body):
            return {"id_token": token if token is not None else self.id_token()}

        a, b = self.google_config()
        with a, b, patch.object(main, "redeem_authorization_code", fake_redeem):
            return self.client.post(
                f"/api/auth/sso/{provider}/exchange",
                json={
                    "flow_id": flow_id,
                    "code": "code-google",
                    "code_verifier": "v" * 64,
                    "redirect_uri": redirect_uri,
                },
            )

    def test_a_correctly_signed_google_token_is_accepted(self):
        claims = self.validate(self.id_token())
        self.assertEqual(claims["hd"], self.HOSTED_DOMAIN)
        self.assertTrue(claims["sub"])

    def test_a_token_signed_by_another_key_is_refused(self):
        with self.assertRaises(HTTPException) as raised:
            self.validate(self.id_token(key=self.other_key))
        self.assertEqual(raised.exception.detail, "ID_TOKEN_INVALID")

    def test_a_wrong_issuer_is_refused(self):
        for issuer in ("https://accounts.google.example.com",
                       "https://login.microsoftonline.com/x/v2.0",
                       "accounts.google.com.evil.test"):
            with self.assertRaises(HTTPException):
                self.validate(self.id_token(iss=issuer))

    def test_a_wrong_audience_is_refused(self):
        with self.assertRaises(HTTPException):
            self.validate(self.id_token(aud="autre.apps.googleusercontent.com"))

    def test_an_expired_token_is_refused(self):
        past = int(time.time()) - 10000
        with self.assertRaises(HTTPException):
            self.validate(self.id_token(exp=past, iat=past - 100))

    def test_a_wrong_or_missing_nonce_is_refused(self):
        with self.assertRaises(HTTPException):
            self.validate(self.id_token(nonce="autre-nonce"))
        with self.assertRaises(HTTPException):
            self.validate(self.id_token(nonce=None))

    def test_a_token_without_subject_is_refused(self):
        with self.assertRaises(HTTPException):
            self.validate(self.id_token(sub=None))

    def test_a_symmetric_algorithm_is_refused(self):
        now = int(time.time())
        header = {"alg": "HS256", "typ": "JWT", "kid": "google-key-1"}
        payload = {"iss": "https://accounts.google.com", "aud": self.CLIENT_ID,
                   "exp": now + 600, "iat": now, "sub": "s",
                   "hd": self.HOSTED_DOMAIN, "nonce": "nonce-google-0123456789"}

        def segment(value):
            return base64.urlsafe_b64encode(
                json.dumps(value, separators=(",", ":")).encode()).rstrip(b"=")

        public_pem = self.private_key.public_key().public_bytes(
            serialization.Encoding.PEM,
            serialization.PublicFormat.SubjectPublicKeyInfo)
        signing_input = segment(header) + b"." + segment(payload)
        signature = base64.urlsafe_b64encode(
            hmac.new(public_pem, signing_input, hashlib.sha256).digest()).rstrip(b"=")
        with self.assertRaises(HTTPException):
            self.validate((signing_input + b"." + signature).decode())

    def test_an_unknown_kid_triggers_a_controlled_refresh(self):
        rotated = self.jwk_for(self.private_key, "google-key-rotated")
        calls = []

        async def fake_fetch(jwks_uri):
            calls.append(jwks_uri)
            return {"keys": [rotated]}

        self.seed_jwks(kid="google-key-ancienne")
        with patch.object(main, "fetch_jwks", fake_fetch):
            claims = self.validate(self.id_token(kid="google-key-rotated"))
        self.assertTrue(claims["sub"])
        self.assertEqual(len(calls), 1)
        self.assertIn("googleapis.com", calls[0])

    def test_the_key_location_comes_from_the_provider_not_the_token(self):
        uri = main.PROVIDERS["google_workspace"]["jwks_uri"]({"jku": "https://evil.test/keys"})
        self.assertTrue(uri.startswith("https://www.googleapis.com/"))

    def test_a_personal_gmail_account_is_refused(self):
        """Un compte Google personnel ne porte aucun `hd` : il n'appartient a
        aucune organisation, donc il n'entre nulle part."""
        flow_id = self.start_flow().json()["flow_id"]
        token = self.id_token(hd=None, email="quelquun@gmail.com")
        response = self.exchange(flow_id, token=token)
        self.assertEqual(response.status_code, 403)
        self.assertEqual(response.json()["detail"], "TENANT_NOT_ALLOWED")

    def test_an_unmapped_workspace_is_refused(self):
        flow_id = self.start_flow().json()["flow_id"]
        response = self.exchange(flow_id, token=self.id_token(hd="workspace-inconnu.com"))
        self.assertEqual(response.json()["detail"], "TENANT_NOT_ALLOWED")

    def test_a_workspace_of_another_organization_cannot_sign_in_here(self):
        flow_id = self.start_flow().json()["flow_id"]
        token = self.id_token(hd=self.OTHER_DOMAIN, email="employe@example.com")
        response = self.exchange(flow_id, token=token)
        self.assertEqual(response.status_code, 403)
        self.assertEqual(response.json()["detail"], "TENANT_NOT_ALLOWED")

    def test_a_mapped_workspace_signs_in(self):
        flow_id = self.start_flow().json()["flow_id"]
        response = self.exchange(flow_id)
        self.assertEqual(response.status_code, 200, response.text)
        body = response.json()
        self.assertEqual(body["email"], "etudiant@example.com")
        for leaked in ("id_token", "access_token", "refresh_token"):
            self.assertNotIn(leaked, body)

    def test_the_identity_is_verified_and_keyed_on_sub(self):
        flow_id = self.start_flow().json()["flow_id"]
        self.exchange(flow_id)
        with main.db() as conn:
            row = conn.execute(
                "SELECT * FROM federated_identities WHERE provider='google_workspace'"
            ).fetchone()
        self.assertEqual(row["verification"], "verified")
        self.assertEqual(row["external_subject"], "104729184756310928475")
        self.assertEqual(row["external_tenant_id"], self.HOSTED_DOMAIN)
        self.assertNotIn("@", row["external_subject"])

    def test_google_sign_in_never_grants_administration(self):
        flow_id = self.start_flow().json()["flow_id"]
        response = self.exchange(flow_id)
        me = self.client.get(
            "/api/me",
            headers={"Authorization": f"Bearer {response.json()['token']}"}).json()
        self.assertEqual(me["membership"]["security_role"], "member")
        self.assertEqual(me["identity"]["provider"], "google_workspace")

    def test_a_client_supplied_organization_or_role_is_ignored(self):
        flow_id = self.start_flow().json()["flow_id"]

        async def fake_redeem(prov, body):
            return {"id_token": self.id_token()}

        a, b = self.google_config()
        with a, b, patch.object(main, "redeem_authorization_code", fake_redeem):
            response = self.client.post(
                "/api/auth/sso/google_workspace/exchange",
                json={"flow_id": flow_id, "code": "c", "code_verifier": "v" * 64,
                      "redirect_uri": "http://127.0.0.1:53200/callback",
                      "organization_id": self.ORGANIZATION_OTHER,
                      "security_role": "it_admin"})
        me = self.client.get(
            "/api/me",
            headers={"Authorization": f"Bearer {response.json()['token']}"}).json()
        self.assertEqual(me["organization_id"], main.nova_organization_id())
        self.assertEqual(me["membership"]["security_role"], "member")

    def test_the_authorization_url_is_google_and_carries_pkce(self):
        body = self.start_flow().json()
        url = body["authorization_url"]
        self.assertTrue(url.startswith("https://accounts.google.com/o/oauth2/v2/auth?"))
        self.assertIn("code_challenge_method=S256", url)
        self.assertIn("response_type=code", url)
        self.assertNotIn("client_secret", url)
        self.assertNotIn(self.CLIENT_SECRET, url)

    def test_only_a_loopback_redirect_is_accepted(self):
        for rejected in ("http://192.168.1.20:53200/callback",
                         "http://localhost:53200/callback",
                         "https://127.0.0.1:53200/callback"):
            self.assertEqual(self.start_flow(rejected).status_code, 400)

    def test_google_needs_both_client_id_and_secret(self):
        with patch.object(main, "GOOGLE_CLIENT_ID", self.CLIENT_ID), patch.object(
            main, "GOOGLE_CLIENT_SECRET", ""
        ):
            response = self.client.post(
                "/api/auth/sso/google_workspace/start",
                json={"code_challenge": "g" * 43, "nonce": "n" * 20,
                      "redirect_uri": "http://127.0.0.1:53200/callback"})
        self.assertEqual(response.status_code, 503)
        self.assertEqual(response.json()["detail"], "PROVIDER_NOT_CONFIGURED")

    def test_google_is_unavailable_without_a_mapped_domain(self):
        with main.db() as conn:
            conn.execute(
                "UPDATE organizations SET google_hosted_domain=NULL WHERE id=?",
                (main.nova_organization_id(),))
        a, b = self.google_config()
        with a, b:
            providers = self.client.get("/api/auth/providers").json()["providers"]
        self.assertNotIn("google_workspace", providers)

    def test_the_exchange_sends_the_google_client_secret_server_side(self):
        """Google exige le secret de son client Desktop a l'echange. Il ne
        quitte pas le serveur : le poste ne le voit jamais."""
        captured = {}

        class FakeResponse:
            status_code = 200

            def json(self):
                return {"id_token": "peu-importe"}

        class FakeClient:
            async def __aenter__(self):
                return self

            async def __aexit__(self, *a):
                return False

            async def post(self, url, data=None, auth=None):
                captured["url"] = url
                captured["data"] = data
                captured["auth"] = auth
                return FakeResponse()

        body = main.SsoExchangeRequest(
            flow_id="f", code="c", code_verifier="v" * 64,
            redirect_uri="http://127.0.0.1:53200/callback")
        a, b = self.google_config()
        with a, b, patch.object(main.httpx, "AsyncClient", lambda **k: FakeClient()):
            asyncio.run(main.redeem_authorization_code("google_workspace", body))

        self.assertEqual(captured["data"]["client_secret"], self.CLIENT_SECRET)
        self.assertEqual(captured["data"]["code_verifier"], "v" * 64)
        self.assertTrue(captured["url"].startswith("https://oauth2.googleapis.com/"))

    def test_microsoft_exchange_still_sends_no_secret(self):
        captured = {}

        class FakeResponse:
            status_code = 200

            def json(self):
                return {"id_token": "peu-importe"}

        class FakeClient:
            async def __aenter__(self):
                return self

            async def __aexit__(self, *a):
                return False

            async def post(self, url, data=None, auth=None):
                captured["data"] = data
                return FakeResponse()

        body = main.SsoExchangeRequest(
            flow_id="f", code="c", code_verifier="v" * 64,
            redirect_uri="http://127.0.0.1:53200/callback")
        with patch.object(main, "ENTRA_CLIENT_ID", "app-microsoft"), patch.object(
            main.httpx, "AsyncClient", lambda **k: FakeClient()
        ):
            asyncio.run(main.redeem_authorization_code("microsoft_entra", body))
        self.assertNotIn("client_secret", captured["data"])


class MultiProviderTests(unittest.TestCase):
    """Ce qui doit rester separe quand deux fournisseurs coexistent."""

    GOOGLE_CLIENT_ID = "222222222222-zzz.apps.googleusercontent.com"
    GOOGLE_CLIENT_SECRET = "GOCSPX-autre-secret-de-test"
    ENTRA_CLIENT_ID = "cccccccc-dddd-4eee-8fff-aaaaaaaaaaaa"
    TENANT = "33333333-3333-4333-8333-333333333333"
    DOMAIN = "example.com"

    @classmethod
    def setUpClass(cls):
        cls.client = TestClient(main.app)
        cls.key = rsa.generate_private_key(public_exponent=65537, key_size=2048)

    def setUp(self):
        with main.db() as conn:
            for table in ("auth_codes", "tokens", "users", "federated_identities"):
                conn.execute(f"DELETE FROM {table}")
            conn.execute(
                "UPDATE organizations SET entra_tenant_id=?, google_hosted_domain=? "
                "WHERE id=?",
                (self.TENANT, self.DOMAIN, main.nova_organization_id()))
        main.SSO_FLOWS.clear()
        main.JWKS_CACHE.clear()
        for provider, seed in (("microsoft_entra", {"tid": self.TENANT}),
                               ("google_workspace", {})):
            uri = main.PROVIDERS[provider]["jwks_uri"](seed)
            jwk = json.loads(jwt.algorithms.RSAAlgorithm.to_jwk(self.key.public_key()))
            jwk.update({"kid": "k1", "use": "sig", "alg": "RS256"})
            main.JWKS_CACHE[uri] = {"jwks": {"keys": [jwk]}, "fetched_at": time.time()}

    def config(self):
        return (patch.object(main, "GOOGLE_CLIENT_ID", self.GOOGLE_CLIENT_ID),
                patch.object(main, "GOOGLE_CLIENT_SECRET", self.GOOGLE_CLIENT_SECRET),
                patch.object(main, "ENTRA_CLIENT_ID", self.ENTRA_CLIENT_ID))

    def token(self, provider, subject, email, nonce):
        now = int(time.time())
        if provider == "microsoft_entra":
            claims = {"iss": f"https://login.microsoftonline.com/{self.TENANT}/v2.0",
                      "aud": self.ENTRA_CLIENT_ID, "exp": now + 600, "iat": now,
                      "sub": "sub-app", "oid": subject, "tid": self.TENANT,
                      "preferred_username": email, "nonce": nonce}
        else:
            claims = {"iss": "https://accounts.google.com",
                      "aud": self.GOOGLE_CLIENT_ID, "exp": now + 600, "iat": now,
                      "sub": subject, "hd": self.DOMAIN, "email": email,
                      "nonce": nonce}
        return jwt.encode(claims, self.key, algorithm="RS256", headers={"kid": "k1"})

    def sign_in(self, provider, subject, email, port=53300):
        nonce = f"nonce-{provider}-0123456789"
        a, b, c = self.config()
        with a, b, c:
            start = self.client.post(
                f"/api/auth/sso/{provider}/start",
                json={"code_challenge": "x" * 43, "nonce": nonce,
                      "redirect_uri": f"http://127.0.0.1:{port}/callback",
                      "machine": "poste"}).json()

            async def fake_redeem(prov, body):
                return {"id_token": self.token(provider, subject, email, nonce)}

            with patch.object(main, "redeem_authorization_code", fake_redeem):
                return self.client.post(
                    f"/api/auth/sso/{provider}/exchange",
                    json={"flow_id": start["flow_id"], "code": "c",
                          "code_verifier": "v" * 64,
                          "redirect_uri": f"http://127.0.0.1:{port}/callback"})

    def test_the_same_email_at_two_providers_is_not_merged_blindly(self):
        """Deux identites, un seul compte — et surtout deux lignes distinctes :
        rien ne fusionne parce que l'adresse coincide."""
        first = self.sign_in("microsoft_entra", "oid-microsoft", "personne@example.com")
        self.assertEqual(first.status_code, 200, first.text)
        second = self.sign_in("google_workspace", "sub-google", "personne@example.com",
                              port=53301)
        self.assertEqual(second.status_code, 200, second.text)

        with main.db() as conn:
            identities = conn.execute(
                "SELECT provider, external_subject FROM federated_identities "
                "ORDER BY provider").fetchall()
            users = conn.execute("SELECT COUNT(*) c FROM users").fetchone()["c"]
        self.assertEqual([r["provider"] for r in identities],
                         ["google_workspace", "microsoft_entra"])
        self.assertEqual(len({r["external_subject"] for r in identities}), 2)
        self.assertEqual(users, 1)

    def test_the_same_subject_at_two_providers_is_two_identities(self):
        shared = "sujet-identique-partout"
        self.assertEqual(
            self.sign_in("microsoft_entra", shared, "a@example.com").status_code, 200)
        self.assertEqual(
            self.sign_in("google_workspace", shared, "a@example.com", 53302).status_code,
            200)
        with main.db() as conn:
            rows = conn.execute(
                "SELECT provider FROM federated_identities WHERE external_subject=?",
                (shared,)).fetchall()
        self.assertEqual(len(rows), 2)
        self.assertEqual({r["provider"] for r in rows},
                         {"microsoft_entra", "google_workspace"})

    def test_a_google_flow_cannot_be_exchanged_as_microsoft(self):
        """Isolation des tentatives : un flux appartient a son fournisseur."""
        a, b, c = self.config()
        with a, b, c:
            start = self.client.post(
                "/api/auth/sso/google_workspace/start",
                json={"code_challenge": "x" * 43, "nonce": "n" * 20,
                      "redirect_uri": "http://127.0.0.1:53310/callback"}).json()

            async def fake_redeem(prov, body):
                return {"id_token": "peu-importe"}

            with patch.object(main, "redeem_authorization_code", fake_redeem):
                response = self.client.post(
                    "/api/auth/sso/microsoft_entra/exchange",
                    json={"flow_id": start["flow_id"], "code": "c",
                          "code_verifier": "v" * 64,
                          "redirect_uri": "http://127.0.0.1:53310/callback"})
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json()["detail"], "STATE_MISMATCH")

    def test_a_microsoft_flow_cannot_be_exchanged_as_google(self):
        a, b, c = self.config()
        with a, b, c:
            start = self.client.post(
                "/api/auth/sso/microsoft_entra/start",
                json={"code_challenge": "x" * 43, "nonce": "n" * 20,
                      "redirect_uri": "http://127.0.0.1:53311/callback"}).json()

            async def fake_redeem(prov, body):
                return {"id_token": "peu-importe"}

            with patch.object(main, "redeem_authorization_code", fake_redeem):
                response = self.client.post(
                    "/api/auth/sso/google_workspace/exchange",
                    json={"flow_id": start["flow_id"], "code": "c",
                          "code_verifier": "v" * 64,
                          "redirect_uri": "http://127.0.0.1:53311/callback"})
        self.assertEqual(response.json()["detail"], "STATE_MISMATCH")

    def test_both_providers_are_announced_when_both_are_configured(self):
        a, b, c = self.config()
        with a, b, c:
            providers = self.client.get("/api/auth/providers").json()["providers"]
        self.assertEqual(providers,
                         ["microsoft_entra", "google_workspace", "legacy_email_code"])
        with a, b, c:
            legacy = self.client.get("/api/auth/entra/pkce/available").json()
        self.assertTrue(legacy["available"])
        self.assertIn("google_workspace", legacy["providers"])

    def test_an_unknown_provider_is_refused(self):
        response = self.client.post(
            "/api/auth/sso/okta/start",
            json={"code_challenge": "x" * 43, "nonce": "n" * 20,
                  "redirect_uri": "http://127.0.0.1:53312/callback"})
        self.assertEqual(response.status_code, 404)
        self.assertEqual(response.json()["detail"], "PROVIDER_UNKNOWN")

    def test_the_legacy_email_code_still_works_beside_both(self):
        email = "student@example.com"
        self.client.post("/api/auth/request", json={"email": email, "machine": "p"})
        with main.db() as conn:
            code = conn.execute(
                "SELECT code FROM auth_codes WHERE email=?", (email,)).fetchone()["code"]
        response = self.client.post(
            "/api/auth/verify", json={"email": email, "code": code, "machine": "p"})
        self.assertEqual(response.status_code, 200)
        me = self.client.get(
            "/api/me",
            headers={"Authorization": f"Bearer {response.json()['token']}"}).json()
        self.assertEqual(me["identity"]["provider"], "legacy_email_code")


class OidcIssuerPolicyTests(unittest.TestCase):
    """Politique d'URL d'émetteur : la vraie nouveauté de l'adaptateur OIDC.

    Un émetteur configurable transforme le serveur en client HTTP dirigé par la
    configuration. Ces tests décrivent ce qu'il refuse de contacter.
    """

    def allowed(self, issuer, allow_private=False):
        return main.issuer_url_is_allowed(issuer, allow_private)

    def test_a_public_https_issuer_is_accepted(self):
        ok, reason = self.allowed("https://accounts.google.com")
        self.assertTrue(ok, reason)

    def test_plain_http_is_refused(self):
        ok, reason = self.allowed("http://idp.example.com")
        self.assertFalse(ok)
        self.assertEqual(reason, "scheme_not_https")

    def test_exotic_schemes_are_refused(self):
        for issuer in ("file:///etc/passwd", "ftp://idp.example.com",
                       "gopher://idp.example.com"):
            ok, _ = self.allowed(issuer)
            self.assertFalse(ok, issuer)

    def test_credentials_in_the_url_are_refused(self):
        ok, reason = self.allowed("https://user:pass@idp.example.com")
        self.assertFalse(ok)
        self.assertEqual(reason, "credentials_in_url")

    def test_a_query_or_fragment_is_refused(self):
        # Un émetteur OIDC est une base, pas une requête.
        for issuer in ("https://idp.example.com?a=b", "https://idp.example.com#x"):
            ok, reason = self.allowed(issuer)
            self.assertFalse(ok, issuer)
            self.assertEqual(reason, "not_a_base_url")

    def test_loopback_is_refused_in_public_mode(self):
        for issuer in ("https://127.0.0.1", "https://127.0.0.1:8443",
                       "https://[::1]"):
            ok, reason = self.allowed(issuer)
            self.assertFalse(ok, issuer)
            self.assertEqual(reason, "internal_address")

    def test_the_cloud_metadata_service_is_refused(self):
        # 169.254.169.254 : la cible SSRF classique dans un cloud.
        ok, reason = self.allowed("https://169.254.169.254")
        self.assertFalse(ok)
        self.assertEqual(reason, "internal_address")

    def test_private_networks_are_refused_by_default(self):
        for issuer in ("https://10.0.0.5", "https://192.168.1.20",
                       "https://172.16.4.4"):
            ok, reason = self.allowed(issuer)
            self.assertFalse(ok, issuer)
            self.assertEqual(reason, "internal_address")

    def test_a_private_issuer_is_allowed_only_when_opted_in(self):
        """Un Keycloak auto-hébergé reste possible — mais c'est une décision
        d'exploitation, prise côté serveur."""
        self.assertFalse(self.allowed("https://10.0.0.5")[0])
        self.assertTrue(self.allowed("https://10.0.0.5", allow_private=True)[0])

    def test_the_desktop_cannot_switch_the_private_mode(self):
        # La politique n'est lisible que côté serveur : rien dans l'API ne
        # l'expose, donc rien ne permet de la basculer à distance.
        client = TestClient(main.app)
        for path in ("/api/config", "/api/health", "/api/auth/providers"):
            body = client.get(path).text
            self.assertNotIn("ALLOW_PRIVATE", body)
            self.assertNotIn("allow_private", body)

    def test_an_unresolvable_host_is_refused(self):
        ok, reason = self.allowed("https://hote-qui-n-existe-pas.invalid")
        self.assertFalse(ok)
        self.assertEqual(reason, "unresolvable_host")


class OidcDiscoveryTests(unittest.TestCase):
    """Découverte `.well-known/openid-configuration`, sans réseau."""

    ISSUER = "https://idp.example.com"

    def setUp(self):
        main.OIDC_METADATA_CACHE.clear()

    def metadata(self, **overrides):
        document = {
            "issuer": self.ISSUER,
            "authorization_endpoint": f"{self.ISSUER}/authorize",
            "token_endpoint": f"{self.ISSUER}/token",
            "jwks_uri": f"{self.ISSUER}/jwks",
        }
        document.update(overrides)
        return {k: v for k, v in document.items() if v is not None}

    def fetch(self, document, status=200, issuer=None, allow_private=True):
        class FakeResponse:
            status_code = status

            def json(self):
                if document is None:
                    raise ValueError("malformed")
                return document

        class FakeClient:
            async def __aenter__(self):
                return self

            async def __aexit__(self, *a):
                return False

            async def get(self, url):
                return FakeResponse()

        # La politique d'URL a sa propre classe de tests : ici on vérifie le
        # document, pas la destination. L'hôte d'exemple ne résout pas.
        with patch.object(
            main, "issuer_url_is_allowed", lambda *a, **k: (True, "ok")
        ), patch.object(main, "OIDC_ALLOW_PRIVATE_ISSUER", allow_private), patch.object(
            main.httpx, "AsyncClient", lambda **k: FakeClient()
        ):
            return asyncio.run(main.fetch_oidc_metadata(issuer or self.ISSUER))

    def test_a_valid_document_is_accepted(self):
        metadata = self.fetch(self.metadata())
        self.assertEqual(metadata["issuer"], self.ISSUER)
        self.assertTrue(metadata["jwks_uri"].startswith("https://"))

    def test_an_issuer_mismatch_is_refused(self):
        with self.assertRaises(HTTPException) as raised:
            self.fetch(self.metadata(issuer="https://autre-idp.example.com"))
        self.assertEqual(raised.exception.detail, "OIDC_ISSUER_MISMATCH")

    def test_a_prefix_match_is_not_enough(self):
        # Comparaison exacte : un émetteur voisin ne passe pas.
        with self.assertRaises(HTTPException):
            self.fetch(self.metadata(issuer=self.ISSUER + ".evil.test"))

    def test_missing_endpoints_are_refused(self):
        for field in ("authorization_endpoint", "token_endpoint", "jwks_uri"):
            with self.assertRaises(HTTPException) as raised:
                self.fetch(self.metadata(**{field: None}))
            self.assertEqual(raised.exception.detail, "OIDC_DISCOVERY_FAILED", field)

    def test_non_https_endpoints_are_refused(self):
        with self.assertRaises(HTTPException):
            self.fetch(self.metadata(token_endpoint="http://idp.example.com/token"))

    def test_a_malformed_document_is_refused(self):
        with self.assertRaises(HTTPException) as raised:
            self.fetch(None)
        self.assertEqual(raised.exception.detail, "OIDC_DISCOVERY_FAILED")

    def test_a_failing_discovery_is_reported(self):
        with self.assertRaises(HTTPException) as raised:
            self.fetch(self.metadata(), status=404)
        self.assertEqual(raised.exception.detail, "OIDC_DISCOVERY_FAILED")

    def test_a_rejected_issuer_never_reaches_the_network(self):
        called = []

        class FakeClient:
            async def __aenter__(self):
                return self

            async def __aexit__(self, *a):
                return False

            async def get(self, url):
                called.append(url)
                raise AssertionError("le réseau ne doit pas être contacté")

        with patch.object(main, "OIDC_ALLOW_PRIVATE_ISSUER", False), patch.object(
            main.httpx, "AsyncClient", lambda **k: FakeClient()
        ):
            with self.assertRaises(HTTPException) as raised:
                asyncio.run(main.fetch_oidc_metadata("https://169.254.169.254"))
        self.assertEqual(raised.exception.detail, "PROVIDER_CONFIG_INVALID")
        self.assertEqual(called, [])

    def test_the_document_is_cached(self):
        calls = []

        async def fake_fetch(issuer):
            calls.append(issuer)
            return self.metadata()

        with patch.object(main, "fetch_oidc_metadata", fake_fetch):
            asyncio.run(main.oidc_metadata(self.ISSUER))
            asyncio.run(main.oidc_metadata(self.ISSUER))
        self.assertEqual(len(calls), 1)


class OidcSignInTests(unittest.TestCase):
    """Connexion OIDC de bout en bout, sur le moteur partagé."""

    ISSUER = "https://idp.example.com"
    OTHER_ISSUER = "https://autre-idp.example.com"
    CLIENT_ID = "nova-organization"

    @classmethod
    def setUpClass(cls):
        cls.client = TestClient(main.app)
        cls.key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        cls.other_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)

    def setUp(self):
        with main.db() as conn:
            for table in ("auth_codes", "tokens", "users", "federated_identities"):
                conn.execute(f"DELETE FROM {table}")
            conn.execute(
                "UPDATE organizations SET oidc_issuer=? WHERE id=?",
                (self.ISSUER, main.nova_organization_id()),
            )
        main.SSO_FLOWS.clear()
        main.JWKS_CACHE.clear()
        main.OIDC_METADATA_CACHE.clear()
        self.seed_metadata(self.ISSUER)
        self.seed_jwks(self.ISSUER)

    def seed_metadata(self, issuer):
        main.OIDC_METADATA_CACHE[issuer] = {
            "metadata": {
                "issuer": issuer,
                "authorization_endpoint": f"{issuer}/authorize",
                "token_endpoint": f"{issuer}/token",
                "jwks_uri": f"{issuer}/jwks",
            },
            "fetched_at": time.time(),
        }

    def seed_jwks(self, issuer, kid="oidc-key-1", key=None):
        jwk = json.loads(
            jwt.algorithms.RSAAlgorithm.to_jwk((key or self.key).public_key()))
        jwk.update({"kid": kid, "use": "sig", "alg": "RS256"})
        main.JWKS_CACHE[f"{issuer}/jwks"] = {
            "jwks": {"keys": [jwk]}, "fetched_at": time.time()}

    def id_token(self, issuer=None, key=None, kid="oidc-key-1", **overrides):
        issuer = issuer or self.ISSUER
        now = int(time.time())
        claims = {
            "iss": issuer,
            "aud": self.CLIENT_ID,
            "exp": now + 600,
            "iat": now,
            "sub": "utilisateur-42",
            "email": "personne@example.com",
            "nonce": "nonce-oidc-0123456789",
        }
        claims.update(overrides)
        for empty in [k for k, v in claims.items() if v is None]:
            claims.pop(empty)
        return jwt.encode(claims, key or self.key, algorithm="RS256",
                          headers={"kid": kid})

    def oidc_config(self, issuer=None, **extra):
        patches = [
            patch.object(main, "OIDC_ISSUER", issuer or self.ISSUER),
            patch.object(main, "OIDC_CLIENT_ID", self.CLIENT_ID),
        ]
        for key, value in extra.items():
            patches.append(patch.object(main, key, value))
        return patches

    def start_flow(self, issuer=None, port=54000, **extra):
        patches = self.oidc_config(issuer, **extra)
        with contextlib.ExitStack() as stack:
            for item in patches:
                stack.enter_context(item)
            return self.client.post(
                "/api/auth/sso/oidc/start",
                json={"code_challenge": "o" * 43,
                      "nonce": "nonce-oidc-0123456789",
                      "redirect_uri": f"http://127.0.0.1:{port}/callback",
                      "machine": "poste"})

    def exchange(self, flow_id, token=None, issuer=None, port=54000, **extra):
        async def fake_redeem(provider, body):
            return {"id_token": token if token is not None else self.id_token()}

        patches = self.oidc_config(issuer, **extra)
        with contextlib.ExitStack() as stack:
            for item in patches:
                stack.enter_context(item)
            stack.enter_context(
                patch.object(main, "redeem_authorization_code", fake_redeem))
            return self.client.post(
                "/api/auth/sso/oidc/exchange",
                json={"flow_id": flow_id, "code": "c", "code_verifier": "v" * 64,
                      "redirect_uri": f"http://127.0.0.1:{port}/callback"})

    def test_a_complete_oidc_sign_in_issues_a_nova_session(self):
        flow_id = self.start_flow().json()["flow_id"]
        response = self.exchange(flow_id)
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["email"], "personne@example.com")

    def test_the_authorization_url_comes_from_discovery(self):
        body = self.start_flow().json()
        self.assertTrue(body["authorization_url"].startswith(f"{self.ISSUER}/authorize?"))
        self.assertIn("code_challenge_method=S256", body["authorization_url"])
        self.assertNotIn("client_secret", body["authorization_url"])

    def test_the_identity_records_its_issuer(self):
        flow_id = self.start_flow().json()["flow_id"]
        self.exchange(flow_id)
        with main.db() as conn:
            row = conn.execute(
                "SELECT * FROM federated_identities WHERE provider='oidc'").fetchone()
        self.assertEqual(row["external_issuer"], self.ISSUER)
        self.assertEqual(row["external_subject"], "utilisateur-42")
        self.assertEqual(row["verification"], "verified")

    def test_two_issuers_with_the_same_subject_are_two_identities(self):
        """Le point le plus important de l'adaptateur : un `sub` n'est unique
        que dans le périmètre de son émetteur."""
        flow_id = self.start_flow().json()["flow_id"]
        self.assertEqual(self.exchange(flow_id).status_code, 200)

        # Seconde organisation OIDC : autre émetteur, même sujet.
        self.seed_metadata(self.OTHER_ISSUER)
        self.seed_jwks(self.OTHER_ISSUER)
        with main.db() as conn:
            conn.execute("UPDATE organizations SET oidc_issuer=? WHERE id=?",
                         (self.OTHER_ISSUER, main.nova_organization_id()))
        second = self.start_flow(issuer=self.OTHER_ISSUER, port=54001).json()["flow_id"]
        response = self.exchange(
            second, token=self.id_token(issuer=self.OTHER_ISSUER),
            issuer=self.OTHER_ISSUER, port=54001)
        self.assertEqual(response.status_code, 200, response.text)

        with main.db() as conn:
            rows = conn.execute(
                "SELECT external_issuer FROM federated_identities "
                "WHERE provider='oidc' AND external_subject='utilisateur-42'").fetchall()
        self.assertEqual(len(rows), 2)
        self.assertEqual({r["external_issuer"] for r in rows},
                         {self.ISSUER, self.OTHER_ISSUER})

    def test_an_unconfigured_issuer_is_refused(self):
        flow_id = self.start_flow().json()["flow_id"]
        # Jeton signé pour un autre émetteur : la validation le refuse.
        self.seed_metadata(self.OTHER_ISSUER)
        self.seed_jwks(self.OTHER_ISSUER)
        response = self.exchange(flow_id, token=self.id_token(issuer=self.OTHER_ISSUER))
        self.assertEqual(response.status_code, 401)
        self.assertEqual(response.json()["detail"], "ID_TOKEN_INVALID")

    def test_a_wrong_signature_is_refused(self):
        flow_id = self.start_flow().json()["flow_id"]
        response = self.exchange(flow_id, token=self.id_token(key=self.other_key))
        self.assertEqual(response.json()["detail"], "ID_TOKEN_INVALID")

    def test_a_wrong_audience_or_expiry_or_nonce_is_refused(self):
        past = int(time.time()) - 10000
        for override in ({"aud": "autre-application"},
                         {"exp": past, "iat": past - 100},
                         {"nonce": "mauvais-nonce"},
                         {"nonce": None},
                         {"sub": None}):
            flow_id = self.start_flow().json()["flow_id"]
            response = self.exchange(flow_id, token=self.id_token(**override))
            self.assertEqual(response.status_code, 401, override)

    def test_an_unsigned_or_symmetric_token_is_refused(self):
        now = int(time.time())
        unsigned = jwt.encode({"iss": self.ISSUER, "aud": self.CLIENT_ID,
                               "exp": now + 600, "sub": "s"},
                              key="", algorithm="none",
                              headers={"kid": "oidc-key-1"})
        flow_id = self.start_flow().json()["flow_id"]
        self.assertEqual(self.exchange(flow_id, token=unsigned).status_code, 401)

    def test_an_unknown_kid_triggers_one_controlled_refresh(self):
        calls = []
        rotated = json.loads(jwt.algorithms.RSAAlgorithm.to_jwk(self.key.public_key()))
        rotated.update({"kid": "oidc-key-2", "use": "sig", "alg": "RS256"})

        async def fake_fetch(jwks_uri):
            calls.append(jwks_uri)
            return {"keys": [rotated]}

        self.seed_jwks(self.ISSUER, kid="oidc-key-ancienne")
        flow_id = self.start_flow().json()["flow_id"]
        with patch.object(main, "fetch_jwks", fake_fetch):
            response = self.exchange(flow_id, token=self.id_token(kid="oidc-key-2"))
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(len(calls), 1)

    def test_admin_claims_never_grant_administration(self):
        flow_id = self.start_flow().json()["flow_id"]
        token = self.id_token(role="admin", groups=["admins"], is_superuser=True)
        response = self.exchange(flow_id, token=token)
        me = self.client.get(
            "/api/me",
            headers={"Authorization": f"Bearer {response.json()['token']}"}).json()
        self.assertEqual(me["membership"]["security_role"], "member")
        self.assertEqual(me["identity"]["provider"], "oidc")

    def test_groups_claims_are_not_turned_into_nova_groups(self):
        flow_id = self.start_flow().json()["flow_id"]
        response = self.exchange(
            flow_id, token=self.id_token(groups=["engineering", "admins"]))
        me = self.client.get(
            "/api/me",
            headers={"Authorization": f"Bearer {response.json()['token']}"}).json()
        self.assertEqual(me["membership"]["groups"], [])

    def test_a_client_supplied_organization_is_ignored(self):
        flow_id = self.start_flow().json()["flow_id"]

        async def fake_redeem(provider, body):
            return {"id_token": self.id_token()}

        with contextlib.ExitStack() as stack:
            for item in self.oidc_config():
                stack.enter_context(item)
            stack.enter_context(
                patch.object(main, "redeem_authorization_code", fake_redeem))
            response = self.client.post(
                "/api/auth/sso/oidc/exchange",
                json={"flow_id": flow_id, "code": "c", "code_verifier": "v" * 64,
                      "redirect_uri": "http://127.0.0.1:54000/callback",
                      "organization_id": "organisation-b",
                      "security_role": "it_admin"})
        me = self.client.get(
            "/api/me",
            headers={"Authorization": f"Bearer {response.json()['token']}"}).json()
        self.assertEqual(me["organization_id"], main.nova_organization_id())
        self.assertEqual(me["membership"]["security_role"], "member")

    def test_a_required_claim_is_compared_exactly(self):
        flow_id = self.start_flow(
            OIDC_REQUIRED_CLAIM="department",
            OIDC_REQUIRED_CLAIM_VALUE="Engineering").json()["flow_id"]
        refused = self.exchange(
            flow_id, token=self.id_token(department="Sales"),
            OIDC_REQUIRED_CLAIM="department",
            OIDC_REQUIRED_CLAIM_VALUE="Engineering")
        self.assertEqual(refused.status_code, 403)
        self.assertEqual(refused.json()["detail"], "MEMBERSHIP_NOT_FOUND")

        flow_id = self.start_flow(
            OIDC_REQUIRED_CLAIM="department",
            OIDC_REQUIRED_CLAIM_VALUE="Engineering", port=54002).json()["flow_id"]
        accepted = self.exchange(
            flow_id, token=self.id_token(department="Engineering"), port=54002,
            OIDC_REQUIRED_CLAIM="department",
            OIDC_REQUIRED_CLAIM_VALUE="Engineering")
        self.assertEqual(accepted.status_code, 200, accepted.text)

    def test_the_client_secret_never_appears_in_public_responses(self):
        secret = "secret-oidc-de-test"
        with contextlib.ExitStack() as stack:
            for item in self.oidc_config():
                stack.enter_context(item)
            stack.enter_context(patch.object(main, "OIDC_CLIENT_SECRET", secret))
            stack.enter_context(
                patch.object(main, "OIDC_CLIENT_AUTH_METHOD", "client_secret_post"))
            for path in ("/api/config", "/api/health", "/api/auth/providers",
                         "/api/auth/entra/pkce/available"):
                self.assertNotIn(secret, self.client.get(path).text, path)
            start = self.start_flow(port=54003)
        self.assertNotIn(secret, start.text)

    def test_the_provider_is_announced_with_its_display_label(self):
        with contextlib.ExitStack() as stack:
            for item in self.oidc_config():
                stack.enter_context(item)
            stack.enter_context(patch.object(main, "OIDC_DISPLAY_NAME", "IPSA SSO"))
            body = self.client.get("/api/auth/providers").json()
        self.assertIn("oidc", body["providers"])
        self.assertEqual(body["display_names"]["oidc"], "IPSA SSO")

    def test_no_provider_without_configuration(self):
        with patch.object(main, "OIDC_CLIENT_ID", ""):
            providers = self.client.get("/api/auth/providers").json()["providers"]
        self.assertNotIn("oidc", providers)

    def test_an_invalid_client_auth_method_is_refused(self):
        response = self.start_flow(
            OIDC_CLIENT_AUTH_METHOD="private_key_jwt", port=54004)
        self.assertEqual(response.status_code, 500)
        self.assertEqual(response.json()["detail"], "PROVIDER_CONFIG_INVALID")


class CrossProviderIdentityTests(unittest.TestCase):
    """Trois fournisseurs, trois périmètres d'identité."""

    def setUp(self):
        with main.db() as conn:
            for table in ("tokens", "users", "federated_identities"):
                conn.execute(f"DELETE FROM {table}")

    def record(self, provider, subject, issuer="", email="personne@example.com"):
        main.record_federated_identity(
            email, provider, subject, None, verification="verified",
            external_issuer=issuer)

    def test_the_same_subject_across_three_providers_is_three_identities(self):
        shared = "identifiant-partage"
        self.record("microsoft_entra", shared)
        self.record("google_workspace", shared)
        self.record("oidc", shared, issuer="https://idp.example.com")
        with main.db() as conn:
            rows = conn.execute(
                "SELECT provider FROM federated_identities WHERE external_subject=?",
                (shared,)).fetchall()
        self.assertEqual(len(rows), 3)
        self.assertEqual({r["provider"] for r in rows},
                         {"microsoft_entra", "google_workspace", "oidc"})

    def test_the_same_email_across_three_providers_never_merges(self):
        self.record("microsoft_entra", "oid-ms")
        self.record("google_workspace", "sub-google")
        self.record("oidc", "sub-oidc", issuer="https://idp.example.com")
        with main.db() as conn:
            identities = conn.execute(
                "SELECT COUNT(*) c FROM federated_identities").fetchone()["c"]
            users = conn.execute("SELECT COUNT(*) c FROM users").fetchone()["c"]
        self.assertEqual(identities, 3)
        # Une seule personne, trois manières de prouver qui elle est.
        self.assertLessEqual(users, 1)

    def test_two_oidc_issuers_sharing_a_subject_coexist(self):
        self.record("oidc", "sujet", issuer="https://idp-a.example.com")
        self.record("oidc", "sujet", issuer="https://idp-b.example.com")
        with main.db() as conn:
            rows = conn.execute(
                "SELECT external_issuer FROM federated_identities "
                "WHERE provider='oidc'").fetchall()
        self.assertEqual(len(rows), 2)


class FederatedIdentityMigrationTests(unittest.TestCase):
    """Migration d'une base antérieure à l'ajout de l'émetteur."""

    def test_existing_identities_survive_the_key_change(self):
        path = os.path.join(TEST_DIRECTORY.name, f"identities-{uuid.uuid4().hex}.db")
        conn = sqlite3.connect(path)
        conn.executescript("""
            CREATE TABLE users(email TEXT PRIMARY KEY, role TEXT DEFAULT 'student',
                cohort TEXT DEFAULT '', note TEXT DEFAULT '', disabled INTEGER DEFAULT 0,
                created_at REAL, last_seen REAL);
            CREATE TABLE tokens(token TEXT PRIMARY KEY, email TEXT, machine TEXT,
                active INTEGER DEFAULT 1, created_at REAL);
            CREATE TABLE auth_codes(email TEXT, code TEXT, expires_at REAL,
                attempts INTEGER DEFAULT 0);
            CREATE TABLE usage(email TEXT, day TEXT, transcriptions INTEGER DEFAULT 0,
                reformulations INTEGER DEFAULT 0, audio_seconds REAL DEFAULT 0,
                PRIMARY KEY (email, day));
            CREATE TABLE shared_dictionary(id INTEGER PRIMARY KEY AUTOINCREMENT,
                term TEXT NOT NULL UNIQUE, replacement TEXT NOT NULL);
            CREATE TABLE personal_dictionary(id INTEGER PRIMARY KEY AUTOINCREMENT,
                email TEXT NOT NULL, term TEXT NOT NULL, replacement TEXT NOT NULL,
                source TEXT NOT NULL DEFAULT 'personal', UNIQUE(email, term));
            CREATE TABLE snippets(id INTEGER PRIMARY KEY AUTOINCREMENT, email TEXT NOT NULL,
                trigger TEXT NOT NULL, content TEXT NOT NULL, UNIQUE(email, trigger));
            CREATE TABLE formatting_rules(id INTEGER PRIMARY KEY AUTOINCREMENT,
                email TEXT, rule TEXT NOT NULL, shared INTEGER NOT NULL DEFAULT 0);
            CREATE TABLE server_state(key TEXT PRIMARY KEY, value TEXT NOT NULL);
            CREATE TABLE organizations(id TEXT PRIMARY KEY, slug TEXT,
                display_name TEXT NOT NULL, org_type TEXT NOT NULL DEFAULT 'education',
                entra_tenant_id TEXT, google_customer_id TEXT, oidc_issuer TEXT,
                created_at REAL);
            CREATE TABLE federated_identities(
                provider TEXT NOT NULL, external_subject TEXT NOT NULL,
                external_tenant_id TEXT, organization_id TEXT NOT NULL,
                email TEXT NOT NULL, verification TEXT NOT NULL DEFAULT 'transport_only',
                created_at REAL, last_seen REAL,
                PRIMARY KEY (provider, external_subject));
            INSERT INTO users(email, created_at) VALUES ('ancien@example.com', 1.0);
            INSERT INTO federated_identities(provider, external_subject,
                external_tenant_id, organization_id, email, verification,
                created_at, last_seen)
                VALUES ('microsoft_entra', 'oid-existant', 'tenant-1', 'org-1',
                        'ancien@example.com', 'verified', 1.0, 2.0);
        """)
        conn.commit()
        conn.close()

        with patch.object(main, "DB_PATH", path):
            main.init_db()
            main.init_db()  # idempotence
            with main.db() as conn:
                rows = conn.execute("SELECT * FROM federated_identities").fetchall()
                columns = {r["name"] for r in
                           conn.execute("PRAGMA table_info(federated_identities)")}

        self.assertIn("external_issuer", columns)
        self.assertEqual(len(rows), 1)
        row = rows[0]
        # Rien n'est perdu, et l'identité Microsoft garde exactement sa clé.
        self.assertEqual(row["external_subject"], "oid-existant")
        self.assertEqual(row["external_tenant_id"], "tenant-1")
        self.assertEqual(row["verification"], "verified")
        self.assertEqual(row["external_issuer"], "")


if __name__ == "__main__":
    unittest.main()
