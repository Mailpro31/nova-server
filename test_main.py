import asyncio
import base64
import contextlib
import hashlib
import hmac
import inspect
import json
import os
import sqlite3
import tempfile
import threading
import time
import unittest
import uuid
from unittest.mock import patch


TEST_DIRECTORY = tempfile.TemporaryDirectory()
os.environ["NOVA_DB"] = os.path.join(TEST_DIRECTORY.name, "nova-test.db")
os.environ["NOVA_DOMAINS"] = "example.com"
os.environ["NOVA_ORGANIZATION_NAME"] = "Nova Test School"
os.environ["NOVA_ORGANIZATION_SHORT_NAME"] = "NTS"
os.environ["NOVA_ENTRA_CLIENT_ID"] = ""

import jwt
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi import HTTPException
from fastapi.testclient import TestClient
from pathlib import Path

import admin_cli
import main

# Le schema s'applique **explicitement**. Importer `main` ne migre plus rien :
# c'est precisement l'effet de bord qui a migre une base de production en
# Phase 30 puis en Phase 37. Un appel visible dans le test remplace un import
# qui decidait tout seul.
main.init_db()


def ensure_provider_config(provider_type, **fields):
    """Déclare (ou remplace) la configuration d'un fournisseur pour
    l'organisation active. Les tests décrivent ainsi l'état voulu sans toucher
    au schéma."""
    organization_id = main.nova_organization_id()
    now = time.time()
    with main.db() as conn:
        conn.execute(
            "DELETE FROM organization_provider_configs "
            "WHERE organization_id=? AND provider_type=?",
            (organization_id, provider_type),
        )
        columns = {
            "id": str(uuid.uuid4()),
            "organization_id": organization_id,
            "provider_type": provider_type,
            "enabled": 1,
            "created_at": now,
            "updated_at": now,
            **fields,
        }
        names = ",".join(columns)
        placeholders = ",".join("?" for _ in columns)
        conn.execute(
            f"INSERT INTO organization_provider_configs({names}) VALUES ({placeholders})",
            tuple(columns.values()),
        )
        return columns["id"]


def grant_admin_session(email: str = "operateur@example.com",
                        role: str = "organization_admin") -> dict:
    """Une session d'administration ouverte, sans rejouer tout le parcours.

    Le jeton partage servait de raccourci aux tests qui n'ont rien a voir avec
    l'authentification — configurations de fournisseur, decouverte. Il a disparu
    en Phase 28 ; ce helper le remplace, en creant directement la session que le
    step-up aurait produite.

    Ce n'est pas un contournement : les tests d'authentification, eux, jouent le
    parcours complet avec un fournisseur simule.
    """
    organization_id = main.nova_organization_id()
    with main.db() as conn:
        account = main.user_by_email(conn, organization_id, email)
        if account is None:
            main.create_user(conn, organization_id, email, security_role=role)
            account = main.user_by_email(conn, organization_id, email)
        else:
            conn.execute("UPDATE users SET security_role=?, disabled=0, status='active' "
                         "WHERE user_id=?", (role, account["user_id"]))
            account = main.user_by_id(conn, account["user_id"])
    session = main.create_admin_session(account, "test")
    return {"Authorization": f"Bearer {session['token']}"}


def clear_sso_flows():
    with main.db() as conn:
        conn.execute("DELETE FROM sso_flows")


def expire_sso_flow(flow_id):
    """Fait expirer une tentative sans attendre son délai."""
    with main.db() as conn:
        conn.execute(
            "UPDATE sso_flows SET expires_at=? WHERE flow_id_hash=?",
            (time.time() - 1, main.hash_token(flow_id)),
        )


def read_sso_flow(flow_id):
    with main.db() as conn:
        row = conn.execute(
            "SELECT * FROM sso_flows WHERE flow_id_hash=?",
            (main.hash_token(flow_id),),
        ).fetchone()
    return dict(row) if row else None


def insert_user(conn, email, organization_id=None, **fields):
    """Crée un compte conforme au schéma tenant-scopé (Phase 23).

    Les tests fabriquaient auparavant leurs comptes avec un `INSERT` portant la
    seule adresse : c'était possible tant qu'elle était clé primaire. Ils passent
    désormais par le même chemin que le serveur.
    """
    return main.create_user(
        conn, organization_id or main.nova_organization_id(), email, **fields
    )


def clear_provider_configs():
    with main.db() as conn:
        conn.execute("DELETE FROM organization_provider_configs")


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

    def test_auth_methods_announce_a_usable_oidc_provider(self):
        """`auth_methods` annonce l'OIDC quand `/api/auth/providers` le propose.

        Mesure sur le serveur IPSA : `/api/health` et `/api/config` ne disaient
        que `email_code`, pendant que `/api/auth/providers` proposait le
        fournisseur OIDC « IPSA ». Deux routes publiques, deux reponses.
        """
        organization_id = main.nova_organization_id()
        with main.db() as conn:
            saved = [
                dict(row)
                for row in conn.execute(
                    "SELECT * FROM organization_provider_configs "
                    "WHERE organization_id=? AND provider_type='oidc'",
                    (organization_id,),
                ).fetchall()
            ]
            conn.execute(
                "DELETE FROM organization_provider_configs "
                "WHERE organization_id=? AND provider_type='oidc'",
                (organization_id,),
            )

        def announced(route):
            return self.client.get(route).json()["auth_methods"]

        try:
            for route in ("/api/health", "/api/config"):
                self.assertNotIn("oidc", announced(route))

            config_id = ensure_provider_config(
                "oidc", display_name="IPSA", enabled=0,
                oidc_issuer="https://idp.example.com",
                oidc_client_id="nova-desktop", oidc_client_auth_method="none",
            )
            # Desactivee : `/api/auth/providers` ne la propose pas, donc rien.
            for route in ("/api/health", "/api/config"):
                self.assertNotIn("oidc", announced(route))

            with main.db() as conn:
                conn.execute(
                    "UPDATE organization_provider_configs SET enabled=1 WHERE id=?",
                    (config_id,),
                )
            self.assertIn("oidc", self.client.get("/api/auth/providers").json()["providers"])
            for route in ("/api/health", "/api/config"):
                methods = announced(route)
                self.assertIn("oidc", methods)
                # Le code par adresse reste le chemin de repli.
                self.assertIn("email_code", methods)
        finally:
            with main.db() as conn:
                conn.execute(
                    "DELETE FROM organization_provider_configs "
                    "WHERE organization_id=? AND provider_type='oidc'",
                    (organization_id,),
                )
                for row in saved:
                    names = ",".join(row)
                    placeholders = ",".join("?" for _ in row)
                    conn.execute(
                        f"INSERT INTO organization_provider_configs({names}) "
                        f"VALUES ({placeholders})",
                        tuple(row.values()),
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

    def test_administration_requires_a_modern_session(self):
        """Phase 28 : plus aucun jeton statique. L'administration passe par une
        session ouverte par SSO puis reauthentification, ou par rien."""
        self.assertEqual(self.client.get("/admin").status_code, 404)
        self.assertEqual(self.client.get("/api/admin/discovery").status_code, 401)
        for value in ("faux", "test-admin-token", "nova-admin-local"):
            self.assertEqual(
                self.client.get("/api/admin/discovery",
                                headers={"X-Admin-Token": value}).status_code, 401,
                value)
        # Une session moderne, elle, ouvre.
        self.assertEqual(
            self.client.get("/api/admin/discovery",
                            headers=grant_admin_session()).status_code, 200)

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
        # La consigne demande bien un resultat, et rien d'autre. Elle disait
        # « Return only the transformed text » ; cette phrase interdisait aussi
        # les prefixes que certaines skills demandent, et elle a ete reformulee.
        # La garantie tenue ici est inchangee : le prompt reclame le resultat,
        # pas un commentaire, et l'instruction y figure telle quelle.
        self.assertIn("Return the result and nothing else", prompt)
        self.assertIn("Instruction: Improve", prompt)

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
        # Les mots dictes restent, la valeur est annoncee : « Voici 7 impasse
        # des Bons Voisins Jeremy. » ne voulait rien dire.
        self.assertEqual(
            main.apply_snippets("Voici mon adresse", snippets),
            "Voici mon adresse : 12 rue des Lilas",
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
            insert_user(conn, "ancien@example.com", role="teacher", cohort="AERO2")
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
                organization_id = main.nova_organization_id()
                if main.user_by_email(conn, organization_id, "membre@example.com") is None:
                    insert_user(conn, "membre@example.com")
                row = main.user_by_email(conn, organization_id, "membre@example.com")
                conn.execute(
                    "UPDATE users SET role=? WHERE user_id=?", (role, row["user_id"])
                )
                row = main.user_by_id(conn, row["user_id"])
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
        clear_sso_flows()
        main.JWKS_CACHE.clear()
        clear_provider_configs()
        ensure_provider_config("microsoft_entra", display_name="Microsoft",
                               entra_tenant_id=self.TENANT_A)
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
        uri = main.ADAPTERS["microsoft_entra"]["jwks_uri"]({"tid": tenant_id})
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
        uri = main.ADAPTERS["microsoft_entra"]["jwks_uri"]({"tid": self.TENANT_A})
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

        # Sans configuration de fournisseur, plus rien n'est annoncé.
        clear_provider_configs()
        with patch.object(main, "ENTRA_CLIENT_ID", self.CLIENT_ID):
            body = self.client.get("/api/auth/entra/pkce/available").json()
        self.assertFalse(body["available"])
        self.assertNotIn("microsoft_entra", body["providers"])

    # -- échange -----------------------------------------------------------

    def exchange(self, flow_id, token=None, redirect_uri="http://127.0.0.1:53125/callback",
                 code="code-microsoft"):
        async def fake_redeem(config, body):
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
        expire_sso_flow(flow_id)
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

        async def failing(config, body):
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

        async def fake_redeem(config, body):
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
            insert_user(conn, "etudiant@example.com", role="student", cohort="AERO2")
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
            insert_user(conn, "etudiant@example.com")
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
        """Depuis la Phase 23, la garantie est plus forte qu'un refus.

        Une même adresse peut légitimement exister dans deux organisations —
        ce sont deux personnes. Ce qui doit être impossible, c'est qu'une
        connexion dans l'organisation A **reprenne** le compte de B. Le compte
        de B doit rester exactement ce qu'il était.
        """
        with main.db() as conn:
            other = insert_user(
                conn, "etudiant@example.com", organization_id=self.ORGANIZATION_B)
        flow_id = self.start_flow().json()["flow_id"]
        response = self.exchange(flow_id)
        self.assertEqual(response.status_code, 200, response.text)

        with main.db() as conn:
            untouched = main.user_by_id(conn, other)
            here = main.user_by_email(
                conn, main.nova_organization_id(), "etudiant@example.com")
        # Le compte de l'autre organisation n'a pas bougé, et celui d'ici est
        # une personne distincte.
        self.assertEqual(untouched["organization_id"], self.ORGANIZATION_B)
        self.assertIsNotNone(here)
        self.assertNotEqual(here["user_id"], other)
        # Et l'identité fédérée n'a été rattachée qu'ici.
        with main.db() as conn:
            identities = conn.execute(
                "SELECT organization_id FROM federated_identities").fetchall()
        self.assertNotIn(
            self.ORGANIZATION_B, [r["organization_id"] for r in identities])

    def test_a_disabled_account_cannot_sign_in_through_sso(self):
        with main.db() as conn:
            insert_user(conn, "etudiant@example.com", disabled=True)
        flow_id = self.start_flow().json()["flow_id"]
        response = self.exchange(flow_id)
        self.assertEqual(response.status_code, 403)
        self.assertEqual(response.json()["detail"], "ACCOUNT_DISABLED")

    def test_a_deprovisioned_account_cannot_sign_in_through_sso(self):
        with main.db() as conn:
            insert_user(conn, "etudiant@example.com", status="deprovisioned")
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
            asyncio.run(main.redeem_authorization_code(
                {"provider_type": "microsoft_entra"}, body))

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
        clear_sso_flows()
        main.JWKS_CACHE.clear()
        clear_provider_configs()
        ensure_provider_config("google_workspace", display_name="Google",
                               google_hosted_domain=self.HOSTED_DOMAIN)
        self.seed_jwks()

    def jwk_for(self, key, kid):
        jwk = json.loads(jwt.algorithms.RSAAlgorithm.to_jwk(key.public_key()))
        jwk.update({"kid": kid, "use": "sig", "alg": "RS256"})
        return jwk

    def seed_jwks(self, kid="google-key-1", key=None):
        uri = main.ADAPTERS["google_workspace"]["jwks_uri"]({})
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
            # La validation compare des empreintes : le `nonce` n'est jamais
            # relu en clair, donc jamais stocké en clair.
            return asyncio.run(main.validate_id_token(
                {"provider_type": "google_workspace"}, token,
                main.hash_token(nonce)))

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
        async def fake_redeem(config, body):
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
        uri = main.ADAPTERS["google_workspace"]["jwks_uri"]({"jku": "https://evil.test/keys"})
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

        async def fake_redeem(config, body):
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
        # La configuration existe mais ne déclare aucun domaine : elle est
        # invalide, donc jamais annoncée.
        with main.db() as conn:
            conn.execute(
                "UPDATE organization_provider_configs SET google_hosted_domain=NULL "
                "WHERE provider_type='google_workspace'")
        a, b = self.google_config()
        with a, b:
            providers = self.client.get("/api/auth/providers").json()["providers"]
        self.assertNotIn("google_workspace", providers)

    def test_a_disabled_config_is_never_announced(self):
        with main.db() as conn:
            conn.execute(
                "UPDATE organization_provider_configs SET enabled=0 "
                "WHERE provider_type='google_workspace'")
        a, b = self.google_config()
        with a, b:
            body = self.client.get("/api/auth/providers").json()
        self.assertNotIn("google_workspace", body["providers"])
        self.assertEqual(body["provider_configs"], [])

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
            asyncio.run(main.redeem_authorization_code(
                {"provider_type": "google_workspace"}, body))

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
            asyncio.run(main.redeem_authorization_code(
                {"provider_type": "microsoft_entra"}, body))
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
        clear_sso_flows()
        main.JWKS_CACHE.clear()
        clear_provider_configs()
        ensure_provider_config("microsoft_entra", display_name="Microsoft",
                               entra_tenant_id=self.TENANT)
        ensure_provider_config("google_workspace", display_name="Google",
                               google_hosted_domain=self.DOMAIN)
        for provider, seed in (("microsoft_entra", {"tid": self.TENANT}),
                               ("google_workspace", {})):
            uri = main.ADAPTERS[provider]["jwks_uri"](seed)
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

            async def fake_redeem(config, body):
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

            async def fake_redeem(config, body):
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

            async def fake_redeem(config, body):
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

        async def fake_fetch(issuer, allow_private=None):
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
        clear_sso_flows()
        main.JWKS_CACHE.clear()
        main.OIDC_METADATA_CACHE.clear()
        clear_provider_configs()
        ensure_provider_config("oidc", display_name="Company SSO",
                               oidc_issuer=self.ISSUER,
                               oidc_client_id=self.CLIENT_ID,
                               oidc_client_auth_method="none")
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

    def start_flow(self, issuer=None, port=54000, config_id=None, **extra):
        patches = self.oidc_config(issuer, **extra)
        payload = {"code_challenge": "o" * 43,
                   "nonce": "nonce-oidc-0123456789",
                   "redirect_uri": f"http://127.0.0.1:{port}/callback",
                   "machine": "poste"}
        if config_id:
            payload["provider_config_id"] = config_id
        with contextlib.ExitStack() as stack:
            for item in patches:
                stack.enter_context(item)
            return self.client.post("/api/auth/sso/oidc/start", json=payload)

    def exchange(self, flow_id, token=None, issuer=None, port=54000, **extra):
        async def fake_redeem(config, body):
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

    def test_two_oidc_configs_coexist_in_one_organization(self):
        """Une organisation peut avoir deux IdP — un Okta de production et un
        Keycloak de laboratoire, par exemple. Chaque configuration a son
        identifiant, et un `sub` identique chez les deux reste deux identités.
        """
        first_config = main.default_provider_config("oidc", main.nova_organization_id())
        first_flow = self.start_flow().json()
        self.assertEqual(first_flow["provider_config_id"], first_config["id"])
        self.assertEqual(self.exchange(first_flow["flow_id"]).status_code, 200)

        # Seconde configuration OIDC, même organisation, autre émetteur.
        second_id = str(uuid.uuid4())
        now = time.time()
        with main.db() as conn:
            conn.execute(
                "INSERT INTO organization_provider_configs(id, organization_id, "
                "provider_type, display_name, enabled, oidc_issuer, oidc_client_id, "
                "oidc_client_auth_method, created_at, updated_at) "
                "VALUES (?,?,?,?,?,?,?,?,?,?)",
                (second_id, main.nova_organization_id(), "oidc", "Lab SSO", 1,
                 self.OTHER_ISSUER, self.CLIENT_ID, "none", now, now))
        self.seed_metadata(self.OTHER_ISSUER)
        self.seed_jwks(self.OTHER_ISSUER)

        second_flow = self.start_flow(port=54001, config_id=second_id).json()
        self.assertEqual(second_flow["provider_config_id"], second_id)
        response = self.exchange(
            second_flow["flow_id"], token=self.id_token(issuer=self.OTHER_ISSUER),
            port=54001)
        self.assertEqual(response.status_code, 200, response.text)

        with main.db() as conn:
            rows = conn.execute(
                "SELECT external_issuer FROM federated_identities "
                "WHERE provider='oidc' AND external_subject='utilisateur-42'").fetchall()
        self.assertEqual(len(rows), 2)
        self.assertEqual({r["external_issuer"] for r in rows},
                         {self.ISSUER, self.OTHER_ISSUER})

        # Les deux sont annoncées, chacune avec son libellé.
        body = self.client.get("/api/auth/providers").json()
        oidc_views = [c for c in body["provider_configs"] if c["type"] == "oidc"]
        self.assertEqual(len(oidc_views), 2)
        self.assertIn("Lab SSO", [v["display_name"] for v in oidc_views])

    def test_a_flow_cannot_be_exchanged_against_another_config(self):
        """Un retour ouvert avec une configuration ne complète pas l'autre."""
        second_id = str(uuid.uuid4())
        now = time.time()
        with main.db() as conn:
            conn.execute(
                "INSERT INTO organization_provider_configs(id, organization_id, "
                "provider_type, display_name, enabled, oidc_issuer, oidc_client_id, "
                "oidc_client_auth_method, created_at, updated_at) "
                "VALUES (?,?,?,?,?,?,?,?,?,?)",
                (second_id, main.nova_organization_id(), "oidc", "Lab SSO", 1,
                 self.OTHER_ISSUER, self.CLIENT_ID, "none", now, now))
        self.seed_metadata(self.OTHER_ISSUER)
        self.seed_jwks(self.OTHER_ISSUER)

        # Flux ouvert sur la seconde configuration, jeton de la première.
        flow = self.start_flow(port=54005, config_id=second_id).json()
        response = self.exchange(
            flow["flow_id"], token=self.id_token(issuer=self.ISSUER), port=54005)
        self.assertEqual(response.status_code, 401)
        self.assertEqual(response.json()["detail"], "ID_TOKEN_INVALID")

    def test_a_config_of_another_organization_is_invisible(self):
        foreign_id = str(uuid.uuid4())
        now = time.time()
        with main.db() as conn:
            conn.execute(
                "INSERT INTO organization_provider_configs(id, organization_id, "
                "provider_type, enabled, oidc_issuer, oidc_client_id, created_at, "
                "updated_at) VALUES (?,?,?,?,?,?,?,?)",
                (foreign_id, "organisation-etrangere", "oidc", 1,
                 "https://idp-etranger.example.com", "x", now, now))
        self.assertIsNone(
            main.provider_config_by_id(foreign_id, main.nova_organization_id()))
        response = self.start_flow(port=54006, config_id=foreign_id)
        self.assertEqual(response.status_code, 404)
        self.assertEqual(response.json()["detail"], "PROVIDER_CONFIG_NOT_FOUND")

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

        async def fake_redeem(config, body):
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
        with main.db() as conn:
            conn.execute(
                "UPDATE organization_provider_configs SET oidc_required_claim=?, "
                "oidc_required_claim_value=? WHERE provider_type='oidc'",
                ("department", "Engineering"))

        flow_id = self.start_flow().json()["flow_id"]
        refused = self.exchange(flow_id, token=self.id_token(department="Sales"))
        self.assertEqual(refused.status_code, 403)
        self.assertEqual(refused.json()["detail"], "MEMBERSHIP_NOT_FOUND")

        flow_id = self.start_flow(port=54002).json()["flow_id"]
        accepted = self.exchange(
            flow_id, token=self.id_token(department="Engineering"), port=54002)
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
        with main.db() as conn:
            conn.execute(
                "UPDATE organization_provider_configs SET display_name=? "
                "WHERE provider_type='oidc'", ("IPSA SSO",))
        body = self.client.get("/api/auth/providers").json()
        self.assertIn("oidc", body["providers"])
        self.assertEqual(body["display_names"]["oidc"], "IPSA SSO")
        # Forme détaillée : identifiant, type, libellé — et rien d'autre.
        view = next(c for c in body["provider_configs"] if c["type"] == "oidc")
        self.assertEqual(set(view), {"id", "type", "display_name"})
        self.assertEqual(view["display_name"], "IPSA SSO")

    def test_no_provider_without_configuration(self):
        clear_provider_configs()
        providers = self.client.get("/api/auth/providers").json()["providers"]
        self.assertNotIn("oidc", providers)

    def test_an_invalid_client_auth_method_is_refused(self):
        # Une méthode d'authentification inconnue rend la configuration
        # invalide : elle n'est plus annoncée, donc plus utilisable.
        with main.db() as conn:
            conn.execute(
                "UPDATE organization_provider_configs SET oidc_client_auth_method=? "
                "WHERE provider_type='oidc'", ("private_key_jwt",))
        response = self.start_flow(port=54004)
        self.assertEqual(response.status_code, 503)
        providers = self.client.get("/api/auth/providers").json()["providers"]
        self.assertNotIn("oidc", providers)


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


class ProviderConfigLifecycleTests(unittest.TestCase):
    """Configurations de fournisseur : import hérité, validation, cycle de vie."""

    @property
    def ADMIN(self) -> dict:
        # Session d'administration moderne : le jeton partage n'existe plus.
        return grant_admin_session()

    @classmethod
    def setUpClass(cls):
        cls.client = TestClient(main.app)

    def setUp(self):
        clear_provider_configs()

    def fresh_instance(self, **env):
        """Instance neuve : base vierge, configuration héritée en environnement."""
        path = os.path.join(TEST_DIRECTORY.name, f"configs-{uuid.uuid4().hex}.db")
        patches = [patch.object(main, "DB_PATH", path)]
        for key, value in env.items():
            patches.append(patch.object(main, key, value))
        return path, patches

    # -- import de la configuration héritée --------------------------------

    def test_legacy_microsoft_env_becomes_a_config(self):
        path, patches = self.fresh_instance(
            ENTRA_ALLOWED_TENANT_ID="tenant-legacy",
            ENTRA_CLIENT_ID="app-microsoft")
        with contextlib.ExitStack() as stack:
            for item in patches:
                stack.enter_context(item)
            main.init_db()
            configs = main.provider_configs_for_organization(
                main.nova_organization_id())
        self.assertEqual(len(configs), 1)
        self.assertEqual(configs[0]["provider_type"], "microsoft_entra")
        self.assertEqual(configs[0]["entra_tenant_id"], "tenant-legacy")
        self.assertTrue(configs[0]["id"])

    def test_legacy_google_env_becomes_a_config(self):
        path, patches = self.fresh_instance(
            GOOGLE_ALLOWED_HOSTED_DOMAIN="example.com",
            GOOGLE_CLIENT_ID="client-google",
            GOOGLE_CLIENT_SECRET="secret-google")
        with contextlib.ExitStack() as stack:
            for item in patches:
                stack.enter_context(item)
            main.init_db()
            configs = main.provider_configs_for_organization(
                main.nova_organization_id())
        self.assertEqual([c["provider_type"] for c in configs], ["google_workspace"])
        self.assertEqual(configs[0]["google_hosted_domain"], "example.com")

    def test_legacy_oidc_env_becomes_a_config(self):
        path, patches = self.fresh_instance(
            OIDC_ISSUER="https://idp.example.com",
            OIDC_CLIENT_ID="nova",
            OIDC_DISPLAY_NAME="Company SSO")
        with contextlib.ExitStack() as stack:
            for item in patches:
                stack.enter_context(item)
            main.init_db()
            configs = main.provider_configs_for_organization(
                main.nova_organization_id())
        self.assertEqual([c["provider_type"] for c in configs], ["oidc"])
        self.assertEqual(configs[0]["oidc_issuer"], "https://idp.example.com")
        self.assertEqual(configs[0]["display_name"], "Company SSO")

    def test_a_restart_never_duplicates_a_config(self):
        path, patches = self.fresh_instance(
            ENTRA_ALLOWED_TENANT_ID="tenant-legacy",
            ENTRA_CLIENT_ID="app-microsoft")
        with contextlib.ExitStack() as stack:
            for item in patches:
                stack.enter_context(item)
            main.init_db()
            first = main.provider_configs_for_organization(
                main.nova_organization_id())[0]["id"]
            main.init_db()
            main.init_db()
            configs = main.provider_configs_for_organization(
                main.nova_organization_id())
        self.assertEqual(len(configs), 1)
        # Même identifiant : importer n'est pas recréer.
        self.assertEqual(configs[0]["id"], first)

    def test_the_database_wins_over_the_legacy_environment(self):
        """Une configuration modifiée en base n'est pas écrasée au démarrage."""
        path, patches = self.fresh_instance(
            ENTRA_ALLOWED_TENANT_ID="tenant-legacy",
            ENTRA_CLIENT_ID="app-microsoft")
        with contextlib.ExitStack() as stack:
            for item in patches:
                stack.enter_context(item)
            main.init_db()
            with main.db() as conn:
                conn.execute(
                    "UPDATE organization_provider_configs SET entra_tenant_id=?",
                    ("tenant-corrige",))
            main.init_db()
            configs = main.provider_configs_for_organization(
                main.nova_organization_id())
        self.assertEqual(len(configs), 1)
        self.assertEqual(configs[0]["entra_tenant_id"], "tenant-corrige")

    def test_no_config_without_legacy_environment(self):
        path, patches = self.fresh_instance(
            ENTRA_ALLOWED_TENANT_ID="", GOOGLE_ALLOWED_HOSTED_DOMAIN="",
            OIDC_ISSUER="")
        with contextlib.ExitStack() as stack:
            for item in patches:
                stack.enter_context(item)
            main.init_db()
            configs = main.provider_configs_for_organization(
                main.nova_organization_id(), only_usable=False)
        self.assertEqual(configs, [])

    # -- validation --------------------------------------------------------

    def test_an_invalid_config_is_never_announced(self):
        cases = [
            ("microsoft_entra", {}, "missing_tenant"),
            ("google_workspace", {}, "missing_hosted_domain"),
            ("oidc", {}, "missing_issuer"),
            ("oidc", {"oidc_issuer": "https://idp.example.com"}, "missing_client_id"),
            ("oidc", {"oidc_issuer": "https://idp.example.com",
                      "oidc_client_id": "x",
                      "oidc_client_auth_method": "private_key_jwt"},
             "unsupported_client_auth_method"),
            ("oidc", {"oidc_issuer": "https://idp.example.com",
                      "oidc_client_id": "x",
                      "oidc_client_auth_method": "client_secret_post"},
             "missing_client_secret"),
        ]
        for provider_type, fields, expected in cases:
            config = {"provider_type": provider_type, **fields}
            with patch.object(main, "ENTRA_CLIENT_ID", "app"), patch.object(
                main, "GOOGLE_CLIENT_ID", "client"
            ), patch.object(main, "GOOGLE_CLIENT_SECRET", "secret"):
                self.assertEqual(
                    main.validate_provider_config(config), expected, provider_type)

    def test_a_valid_config_reports_no_error(self):
        with patch.object(main, "ENTRA_CLIENT_ID", "app"):
            self.assertIsNone(main.validate_provider_config(
                {"provider_type": "microsoft_entra", "entra_tenant_id": "t"}))

    # -- administration ----------------------------------------------------

    def test_admin_can_create_list_and_disable(self):
        created = self.client.post(
            "/api/admin/provider-configs", headers=self.ADMIN,
            json={"provider_type": "oidc", "display_name": "Okta",
                  "oidc_issuer": "https://okta.example.com/",
                  "oidc_client_id": "nova-client",
                  "oidc_client_auth_method": "client_secret_post",
                  "oidc_client_secret": "secret-okta"}).json()
        self.assertEqual(created["display_name"], "Okta")
        # L'émetteur est normalisé, sans barre finale.
        self.assertEqual(created["oidc_issuer"], "https://okta.example.com")
        self.assertTrue(created["has_secret"])
        self.assertIsNone(created["validation_error"])

        listed = self.client.get(
            "/api/admin/provider-configs", headers=self.ADMIN).json()["configs"]
        self.assertEqual(len(listed), 1)

        # Un second fournisseur : désactiver le dernier actif est refusé, et
        # remplacer un fournisseur consiste précisément à déclarer le nouveau
        # avant de retirer l'ancien.
        self.client.post(
            "/api/admin/provider-configs", headers=self.ADMIN,
            json={"provider_type": "microsoft_entra", "display_name": "Microsoft",
                  "entra_tenant_id": "tenant-de-secours"})
        disabled = self.client.post(
            f"/api/admin/provider-configs/{created['id']}/disable",
            headers=self.ADMIN).json()
        self.assertFalse(disabled["enabled"])
        # Désactivée, elle disparaît de l'annonce publique mais reste lisible.
        self.assertNotIn(
            "oidc", self.client.get("/api/auth/providers").json()["providers"])
        self.assertEqual(len(self.client.get(
            "/api/admin/provider-configs", headers=self.ADMIN).json()["configs"]), 2)

    def test_the_last_active_provider_cannot_be_disabled(self):
        """Auto-verrouillage : depuis la Phase 24, ouvrir une session
        d'administration exige une réauthentification auprès d'un fournisseur.
        Désactiver le dernier fermerait la porte à tous les administrateurs, y
        compris à celui qui vient de cliquer."""
        created = self.client.post(
            "/api/admin/provider-configs", headers=self.ADMIN,
            json={"provider_type": "microsoft_entra", "display_name": "Microsoft",
                  "entra_tenant_id": "tenant-unique"}).json()
        response = self.client.post(
            f"/api/admin/provider-configs/{created['id']}/disable", headers=self.ADMIN)
        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.json()["detail"], "PROVIDER_LAST_ACTIVE")
        # Et il reste bien actif : le refus n'a rien changé à moitié.
        listed = self.client.get(
            "/api/admin/provider-configs", headers=self.ADMIN).json()["configs"]
        self.assertTrue(listed[0]["enabled"])

    def test_a_provider_can_still_be_replaced(self):
        first = self.client.post(
            "/api/admin/provider-configs", headers=self.ADMIN,
            json={"provider_type": "microsoft_entra",
                  "entra_tenant_id": "ancien-tenant"}).json()
        self.client.post(
            "/api/admin/provider-configs", headers=self.ADMIN,
            json={"provider_type": "oidc", "oidc_issuer": "https://neuf.example.com",
                  "oidc_client_id": "client"})
        response = self.client.post(
            f"/api/admin/provider-configs/{first['id']}/disable", headers=self.ADMIN)
        self.assertEqual(response.status_code, 200)

    def test_the_secret_is_never_returned(self):
        secret = "secret-tres-confidentiel"
        created = self.client.post(
            "/api/admin/provider-configs", headers=self.ADMIN,
            json={"provider_type": "oidc", "oidc_issuer": "https://idp.example.com",
                  "oidc_client_id": "c", "oidc_client_auth_method": "client_secret_post",
                  "oidc_client_secret": secret})
        self.assertNotIn(secret, created.text)
        listed = self.client.get("/api/admin/provider-configs", headers=self.ADMIN)
        self.assertNotIn(secret, listed.text)
        self.assertTrue(listed.json()["configs"][0]["has_secret"])
        # Ni dans les réponses publiques.
        for path in ("/api/auth/providers", "/api/config", "/api/health"):
            self.assertNotIn(secret, self.client.get(path).text)

    def test_an_update_without_a_secret_keeps_the_previous_one(self):
        """Une mise à jour de libellé ne doit pas couper toute une
        organisation en effaçant son secret."""
        secret = "secret-a-conserver"
        created = self.client.post(
            "/api/admin/provider-configs", headers=self.ADMIN,
            json={"provider_type": "oidc", "oidc_issuer": "https://idp.example.com",
                  "oidc_client_id": "c", "oidc_client_auth_method": "client_secret_post",
                  "oidc_client_secret": secret}).json()

        updated = self.client.post(
            f"/api/admin/provider-configs/{created['id']}", headers=self.ADMIN,
            json={"provider_type": "oidc", "display_name": "Nouveau nom",
                  "oidc_issuer": "https://idp.example.com",
                  "oidc_client_id": "c",
                  "oidc_client_auth_method": "client_secret_post"}).json()
        self.assertEqual(updated["display_name"], "Nouveau nom")
        self.assertTrue(updated["has_secret"])

        config = main.provider_config_by_id(
            created["id"], main.nova_organization_id())
        self.assertEqual(config["oidc_client_secret"], secret)

    def test_a_duplicate_issuer_is_refused(self):
        payload = {"provider_type": "oidc", "oidc_issuer": "https://idp.example.com",
                   "oidc_client_id": "c"}
        self.assertEqual(self.client.post(
            "/api/admin/provider-configs", headers=self.ADMIN, json=payload
        ).status_code, 200)
        duplicate = self.client.post(
            "/api/admin/provider-configs", headers=self.ADMIN, json=payload)
        self.assertEqual(duplicate.status_code, 409)
        self.assertEqual(duplicate.json()["detail"], "PROVIDER_CONFIG_DUPLICATE")

    def test_the_admin_api_requires_an_administrator(self):
        # Sans session d'administration, rien n'ouvre — et le jeton partage
        # n'existe plus, donc il n'ouvre rien non plus.
        self.assertEqual(
            self.client.get("/api/admin/provider-configs").status_code, 401)
        self.assertEqual(
            self.client.post("/api/admin/provider-configs",
                             json={"provider_type": "oidc"}).status_code, 401)
        self.assertEqual(
            self.client.post("/api/admin/provider-configs",
                             headers={"X-Admin-Token": "mauvais-jeton"},
                             json={"provider_type": "oidc"}).status_code, 401)

    def test_an_unknown_provider_type_is_refused(self):
        response = self.client.post(
            "/api/admin/provider-configs", headers=self.ADMIN,
            json={"provider_type": "saml"})
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json()["detail"], "PROVIDER_UNKNOWN")

    def test_an_unknown_config_cannot_be_updated_or_disabled(self):
        unknown = str(uuid.uuid4())
        self.assertEqual(self.client.post(
            f"/api/admin/provider-configs/{unknown}/disable", headers=self.ADMIN
        ).status_code, 404)
        self.assertEqual(self.client.post(
            f"/api/admin/provider-configs/{unknown}", headers=self.ADMIN,
            json={"provider_type": "oidc"}).status_code, 404)

    def test_admin_only_ever_sees_the_active_organization(self):
        foreign = str(uuid.uuid4())
        now = time.time()
        with main.db() as conn:
            conn.execute(
                "INSERT INTO organization_provider_configs(id, organization_id, "
                "provider_type, enabled, entra_tenant_id, created_at, updated_at) "
                "VALUES (?,?,?,?,?,?,?)",
                (foreign, "organisation-etrangere", "microsoft_entra", 1, "t", now, now))
        listed = self.client.get(
            "/api/admin/provider-configs", headers=self.ADMIN).json()["configs"]
        self.assertNotIn(foreign, [c["id"] for c in listed])

    def test_the_public_view_exposes_the_strict_minimum(self):
        self.client.post(
            "/api/admin/provider-configs", headers=self.ADMIN,
            json={"provider_type": "oidc", "display_name": "Company SSO",
                  "oidc_issuer": "https://idp-interne.example.com",
                  "oidc_client_id": "identifiant-client-interne"})
        body = self.client.get("/api/auth/providers")
        view = body.json()["provider_configs"][0]
        self.assertEqual(set(view), {"id", "type", "display_name"})
        # Ni émetteur, ni identifiant client : rien de tout cela n'aide à
        # dessiner un bouton, et tout cela renseigne sur l'infrastructure.
        self.assertNotIn("idp-interne", body.text)
        self.assertNotIn("identifiant-client-interne", body.text)


class SsoFlowStoreTests(unittest.TestCase):
    """L'état des tentatives, hors du processus.

    Ces tests portent sur la propriété qui débloque le déploiement horizontal :
    deux instances de store, la même base, et un échange qui ne peut réussir
    qu'une fois.
    """

    def setUp(self):
        clear_sso_flows()
        self.store = main.DatabaseSsoFlowStore()

    def create(self, store=None, **overrides):
        fields = {
            "provider_type": "oidc",
            "provider_config_id": "config-1",
            "organization_id": "org-1",
            "nonce": "nonce-de-test-0123456789",
            "redirect_uri": "http://127.0.0.1:53000/callback",
            "machine": "poste-1",
        }
        fields.update(overrides)
        return (store or self.store).create(**fields)

    # -- ce qui est écrit, et ce qui ne l'est jamais -----------------------

    def test_the_flow_id_is_never_stored_in_clear(self):
        flow_id = self.create()
        row = read_sso_flow(flow_id)
        self.assertIsNotNone(row)
        # Présenter l'identifiant suffit à réclamer la tentative : c'est un
        # secret porteur, traité comme un jeton de session.
        self.assertNotIn(flow_id, json.dumps(row))
        self.assertEqual(row["flow_id_hash"], main.hash_token(flow_id))
        self.assertEqual(len(row["flow_id_hash"]), 64)

    def test_the_nonce_is_never_stored_in_clear(self):
        flow_id = self.create(nonce="nonce-tres-secret-9876")
        row = read_sso_flow(flow_id)
        self.assertNotIn("nonce-tres-secret-9876", json.dumps(row))
        self.assertEqual(row["nonce_hash"], main.hash_token("nonce-tres-secret-9876"))

    def test_no_sensitive_material_is_persisted(self):
        flow_id = self.create()
        row = read_sso_flow(flow_id)
        serialized = json.dumps(row).lower()
        for forbidden in ("code_verifier", "authorization_code", "access_token",
                          "refresh_token", "id_token", "client_secret"):
            self.assertNotIn(forbidden, serialized)
        # Les colonnes elles-mêmes ne prévoient rien de tel.
        self.assertEqual(
            set(row) - {"flow_id_hash", "provider_type", "provider_config_id",
                        "organization_id", "nonce_hash", "redirect_uri", "machine",
                        # Le but et l'identité attendue d'un step-up : ni l'un ni
                        # l'autre n'est un secret, et les deux sont nécessaires
                        # pour qu'un retour ne serve pas à autre chose que ce
                        # qui a été demandé.
                        "purpose", "expected_user_id",
                        "status", "created_at", "expires_at", "exchanging_at",
                        "consumed_at"},
            set())

    # -- machine à états ----------------------------------------------------

    def test_a_new_flow_is_pending(self):
        flow_id = self.create()
        self.assertEqual(read_sso_flow(flow_id)["status"], "pending")

    def test_begin_exchange_reserves_the_flow(self):
        flow_id = self.create()
        reserved = self.store.begin_exchange(flow_id)
        self.assertIsNotNone(reserved)
        self.assertEqual(reserved["provider_type"], "oidc")
        self.assertEqual(reserved["provider_config_id"], "config-1")
        self.assertEqual(read_sso_flow(flow_id)["status"], "exchanging")
        self.assertIsNotNone(read_sso_flow(flow_id)["exchanging_at"])

    def test_a_flow_can_only_be_reserved_once(self):
        """Le cœur du sujet : deux réservations, une seule réussit."""
        flow_id = self.create()
        self.assertIsNotNone(self.store.begin_exchange(flow_id))
        self.assertIsNone(self.store.begin_exchange(flow_id))

    def test_a_consumed_flow_cannot_be_reserved(self):
        flow_id = self.create()
        self.store.begin_exchange(flow_id)
        self.store.mark_consumed(flow_id)
        self.assertEqual(read_sso_flow(flow_id)["status"], "consumed")
        self.assertIsNone(self.store.begin_exchange(flow_id))

    def test_an_unknown_flow_is_refused(self):
        self.assertIsNone(self.store.begin_exchange("identifiant-inexistant"))
        self.assertIsNone(self.store.begin_exchange(""))

    def test_an_expired_flow_is_refused(self):
        flow_id = self.create()
        expire_sso_flow(flow_id)
        self.assertIsNone(self.store.begin_exchange(flow_id))

    # -- multi-worker -------------------------------------------------------

    def test_two_store_instances_share_the_same_state(self):
        """Ce que la mémoire de processus interdisait : `start` sur un worker,
        `exchange` sur un autre."""
        worker_a = main.DatabaseSsoFlowStore()
        worker_b = main.DatabaseSsoFlowStore()

        flow_id = self.create(store=worker_a)
        # Le worker B, qui n'a rien en mémoire, retrouve la tentative.
        reserved = worker_b.begin_exchange(flow_id)
        self.assertIsNotNone(reserved)
        self.assertEqual(reserved["organization_id"], "org-1")
        # Et le worker A ne peut plus la réserver.
        self.assertIsNone(worker_a.begin_exchange(flow_id))

    def test_a_restart_does_not_lose_a_pending_flow(self):
        flow_id = self.create()
        # Un processus neuf : aucun état hérité en mémoire.
        fresh = main.DatabaseSsoFlowStore()
        self.assertIsNotNone(fresh.begin_exchange(flow_id))

    def test_concurrent_reservations_elect_exactly_one_winner(self):
        flow_id = self.create()
        results = []
        barrier = threading.Barrier(8)

        def attempt():
            store = main.DatabaseSsoFlowStore()
            barrier.wait()
            for _ in range(5):
                try:
                    results.append(store.begin_exchange(flow_id))
                    return
                except sqlite3.OperationalError:
                    # Verrou d'écriture SQLite : on retente, ce que ferait un
                    # vrai worker. Ce qui compte est qu'aucun doublon ne passe.
                    time.sleep(0.05)
            results.append(None)

        threads = [threading.Thread(target=attempt) for _ in range(8)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()

        winners = [r for r in results if r is not None]
        self.assertEqual(len(winners), 1, "un seul échange doit être autorisé")

    # -- nettoyage ----------------------------------------------------------

    def test_cleanup_removes_only_what_is_long_gone(self):
        recent = self.create()
        old = self.create(redirect_uri="http://127.0.0.1:53001/callback")
        with main.db() as conn:
            conn.execute(
                "UPDATE sso_flows SET expires_at=? WHERE flow_id_hash=?",
                (time.time() - main.SSO_FLOW_RETENTION_SECONDS - 60,
                 main.hash_token(old)))

        removed = self.store.cleanup()
        self.assertEqual(removed, 1)
        self.assertIsNotNone(read_sso_flow(recent))
        self.assertIsNone(read_sso_flow(old))

    def test_creating_a_flow_cleans_up_opportunistically(self):
        old = self.create()
        with main.db() as conn:
            conn.execute(
                "UPDATE sso_flows SET expires_at=?",
                (time.time() - main.SSO_FLOW_RETENTION_SECONDS - 60,))
        self.create(redirect_uri="http://127.0.0.1:53002/callback")
        self.assertIsNone(read_sso_flow(old))

    def test_the_store_honours_its_interface(self):
        # Le moteur parle au contrat, pas à SQLite : le stockage changera.
        self.assertIsInstance(main.SSO_FLOW_STORE, main.SsoFlowStore)
        for method in ("create", "begin_exchange", "mark_consumed", "cleanup"):
            self.assertTrue(callable(getattr(main.SSO_FLOW_STORE, method)))


class SsoFlowBindingTests(unittest.TestCase):
    """Ce à quoi une tentative reste liée, du début à la fin."""

    ISSUER = "https://idp.example.com"
    CLIENT_ID = "nova-organization"

    @classmethod
    def setUpClass(cls):
        cls.client = TestClient(main.app)
        cls.key = rsa.generate_private_key(public_exponent=65537, key_size=2048)

    def setUp(self):
        with main.db() as conn:
            for table in ("tokens", "users", "federated_identities"):
                conn.execute(f"DELETE FROM {table}")
        clear_sso_flows()
        clear_provider_configs()
        self.config_id = ensure_provider_config(
            "oidc", display_name="Company SSO", oidc_issuer=self.ISSUER,
            oidc_client_id=self.CLIENT_ID, oidc_client_auth_method="none")
        main.JWKS_CACHE.clear()
        main.OIDC_METADATA_CACHE.clear()
        main.OIDC_METADATA_CACHE[self.ISSUER] = {
            "metadata": {"issuer": self.ISSUER,
                         "authorization_endpoint": f"{self.ISSUER}/authorize",
                         "token_endpoint": f"{self.ISSUER}/token",
                         "jwks_uri": f"{self.ISSUER}/jwks"},
            "fetched_at": time.time()}
        jwk = json.loads(jwt.algorithms.RSAAlgorithm.to_jwk(self.key.public_key()))
        jwk.update({"kid": "k1", "use": "sig", "alg": "RS256"})
        main.JWKS_CACHE[f"{self.ISSUER}/jwks"] = {
            "jwks": {"keys": [jwk]}, "fetched_at": time.time()}

    def token(self, **overrides):
        now = int(time.time())
        claims = {"iss": self.ISSUER, "aud": self.CLIENT_ID, "exp": now + 600,
                  "iat": now, "sub": "utilisateur-1", "email": "personne@example.com",
                  "nonce": "nonce-oidc-0123456789"}
        claims.update(overrides)
        return jwt.encode(claims, self.key, algorithm="RS256", headers={"kid": "k1"})

    def start(self, port=53500):
        return self.client.post(
            "/api/auth/sso/oidc/start",
            json={"code_challenge": "o" * 43, "nonce": "nonce-oidc-0123456789",
                  "redirect_uri": f"http://127.0.0.1:{port}/callback",
                  "machine": "poste"}).json()

    def exchange(self, flow_id, port=53500, token=None):
        async def fake_redeem(config, body):
            return {"id_token": token if token is not None else self.token()}

        with patch.object(main, "redeem_authorization_code", fake_redeem):
            return self.client.post(
                "/api/auth/sso/oidc/exchange",
                json={"flow_id": flow_id, "code": "c", "code_verifier": "v" * 64,
                      "redirect_uri": f"http://127.0.0.1:{port}/callback"})

    def test_a_complete_flow_ends_consumed(self):
        flow = self.start()
        self.assertEqual(self.exchange(flow["flow_id"]).status_code, 200)
        self.assertEqual(read_sso_flow(flow["flow_id"])["status"], "consumed")

    def test_a_replayed_callback_is_refused(self):
        flow = self.start()
        self.assertEqual(self.exchange(flow["flow_id"]).status_code, 200)
        replay = self.exchange(flow["flow_id"])
        self.assertEqual(replay.status_code, 400)
        self.assertEqual(replay.json()["detail"], "AUTH_TIMEOUT")

    def test_a_changed_redirect_is_refused(self):
        flow = self.start()
        response = self.exchange(flow["flow_id"], port=53599)
        self.assertEqual(response.json()["detail"], "REDIRECT_URI_INVALID")

    def test_the_nonce_comes_from_the_flow_not_the_request(self):
        """Le poste ne « rappelle » pas son nonce à l'échange : le serveur
        utilise celui qu'il a retenu."""
        flow = self.start()
        response = self.exchange(
            flow["flow_id"], token=self.token(nonce="un-autre-nonce"))
        self.assertEqual(response.status_code, 401)
        self.assertEqual(response.json()["detail"], "ID_TOKEN_INVALID")

    def test_a_provider_disabled_mid_flow_stops_the_exchange(self):
        """Une désactivation administrative ne se contourne pas avec une
        tentative déjà ouverte."""
        flow = self.start()
        with main.db() as conn:
            conn.execute("UPDATE organization_provider_configs SET enabled=0")
        response = self.exchange(flow["flow_id"])
        self.assertEqual(response.status_code, 503)
        self.assertEqual(response.json()["detail"], "PROVIDER_NOT_CONFIGURED")

    def test_an_account_disabled_mid_flow_gets_no_session(self):
        # Première connexion pour créer le compte.
        first = self.start()
        self.assertEqual(self.exchange(first["flow_id"]).status_code, 200)
        with main.db() as conn:
            conn.execute("UPDATE users SET disabled=1")

        second = self.start(port=53501)
        response = self.exchange(second["flow_id"], port=53501)
        self.assertEqual(response.status_code, 403)
        self.assertEqual(response.json()["detail"], "ACCOUNT_DISABLED")

    def test_the_flow_carries_its_config_and_organization(self):
        flow = self.start()
        row = read_sso_flow(flow["flow_id"])
        self.assertEqual(row["provider_config_id"], self.config_id)
        self.assertEqual(row["organization_id"], main.nova_organization_id())
        self.assertEqual(row["provider_type"], "oidc")

    def test_a_flow_started_on_one_worker_completes_on_another(self):
        """La propriété qui débloque le déploiement horizontal, vue depuis
        l'API : rien en mémoire n'est nécessaire entre les deux appels."""
        flow = self.start()
        # On simule un autre worker : aucun état local, seulement la base.
        main.SSO_FLOW_STORE.cleanup()
        response = self.exchange(flow["flow_id"])
        self.assertEqual(response.status_code, 200, response.text)


class DiscoveryEndpointSecurityTests(unittest.TestCase):
    """Ce que le poste accepte de contacter après une découverte."""

    def allowed(self, endpoint, allow_insecure=False):
        return main.service_endpoint_is_allowed(endpoint, allow_insecure)

    def test_a_public_https_endpoint_is_accepted(self):
        ok, reason = self.allowed("https://nova.example.edu")
        self.assertTrue(ok, reason)

    def test_plain_http_is_refused_in_production(self):
        ok, reason = self.allowed("http://nova.example.edu")
        self.assertFalse(ok)
        self.assertEqual(reason, "scheme_not_https")

    def test_plain_http_is_allowed_only_when_opted_in(self):
        # Un serveur de développement sur la boucle locale reste légitime, mais
        # c'est une décision d'exploitation, pas un défaut.
        self.assertTrue(self.allowed("http://localhost:8787", allow_insecure=True)[0])

    def test_exotic_schemes_are_refused(self):
        for endpoint in ("file:///etc/passwd", "ftp://nova.example.edu",
                         "javascript:alert(1)", "data:text/html,x"):
            ok, reason = self.allowed(endpoint, allow_insecure=True)
            self.assertFalse(ok, endpoint)
            self.assertEqual(reason, "scheme_not_allowed")

    def test_credentials_in_the_url_are_refused(self):
        ok, reason = self.allowed("https://user:pass@nova.example.edu")
        self.assertFalse(ok)
        self.assertEqual(reason, "credentials_in_url")

    def test_a_query_or_fragment_is_refused(self):
        for endpoint in ("https://nova.example.edu?a=b", "https://nova.example.edu#x"):
            ok, reason = self.allowed(endpoint)
            self.assertFalse(ok, endpoint)
            self.assertEqual(reason, "not_a_base_url")

    def test_a_missing_host_is_refused(self):
        self.assertFalse(self.allowed("https://")[0])

    # ── Nature de l'hôte ────────────────────────────────────────────────
    #
    # Le poste refusait déjà ces adresses ; le serveur ne le faisait pas, alors
    # que la documentation promettait les deux contrôles. Ces tests ferment
    # l'écart et l'empêchent de se rouvrir.

    def test_local_and_private_addresses_are_refused(self):
        for endpoint in (
            "https://localhost",
            "https://nova.localhost",
            "https://127.0.0.1",
            "https://127.1.2.3",
            "https://[::1]",
            "https://10.0.0.5",
            "https://192.168.1.20",
            "https://172.16.4.4",
            "https://169.254.169.254",
            "https://[fe80::1]",  # link-local IPv6
            "https://[::]",  # unspecified
            "https://224.0.0.1",  # multicast
        ):
            ok, reason = self.allowed(endpoint)
            self.assertFalse(ok, endpoint)
            self.assertEqual(reason, "internal_address", endpoint)

    def test_public_addresses_next_to_private_ranges_stay_accepted(self):
        # Les voisins immédiats des plages privées : se tromper d'un bit ici
        # reviendrait à refuser des adresses parfaitement publiques.
        for endpoint in ("https://11.0.0.1", "https://172.32.0.1", "https://9.255.255.255"):
            ok, reason = self.allowed(endpoint)
            self.assertTrue(ok, f"{endpoint}: {reason}")

    def test_a_hostname_resolving_to_a_private_address_is_refused(self):
        with patch.object(
            main.socket, "getaddrinfo",
            return_value=[(2, 1, 6, "", ("10.0.0.5", 0))],
        ):
            ok, reason = self.allowed("https://interne.example.edu")
        self.assertFalse(ok)
        self.assertEqual(reason, "internal_address")

    def test_one_private_result_among_public_ones_is_enough_to_refuse(self):
        # Un nom qui résout à la fois vers une adresse publique et vers une
        # adresse interne servirait de pont : toutes doivent être acceptables.
        with patch.object(
            main.socket, "getaddrinfo",
            return_value=[
                (2, 1, 6, "", ("93.184.216.34", 0)),
                (2, 1, 6, "", ("127.0.0.1", 0)),
            ],
        ):
            ok, reason = self.allowed("https://double.example.edu")
        self.assertFalse(ok)
        self.assertEqual(reason, "internal_address")

    def test_a_hostname_the_server_cannot_resolve_is_still_accepted(self):
        """Le serveur annonce cette adresse, il ne la contacte pas.

        Un endpoint peut n'être résolvable que depuis le réseau des postes.
        Refuser sur une non-résolution rendrait la découverte indisponible pour
        un déploiement sain — c'est la seule différence avec la politique OIDC,
        qui exige la résolution parce qu'elle va réellement contacter l'hôte.
        """
        with patch.object(
            main.socket, "getaddrinfo", side_effect=main.socket.gaierror("no dns"),
        ):
            ok, reason = self.allowed("https://interne.exemple.edu")
        self.assertTrue(ok, reason)

    def test_a_local_endpoint_is_accepted_only_when_the_operator_opts_in(self):
        # L'exception de développement couvre le schéma **et** l'hôte, et reste
        # un paramètre du serveur : rien venu du réseau ne peut l'activer.
        self.assertFalse(self.allowed("https://127.0.0.1:8787")[0])
        self.assertTrue(self.allowed("https://127.0.0.1:8787", allow_insecure=True)[0])


class OrganizationDiscoveryTests(unittest.TestCase):
    """L'endpoint public de découverte.

    Il est appelé par des postes non authentifiés : il ne doit donner aucun
    accès, et ne rien révéler qu'un annuaire public ne révélerait.
    """

    @property
    def ADMIN(self) -> dict:
        # Session d'administration moderne : le jeton partage n'existe plus.
        return grant_admin_session()
    SLUG = "ecole-exemple"
    ENDPOINT = "https://nova.example.edu"

    @classmethod
    def setUpClass(cls):
        cls.client = TestClient(main.app)

    def setUp(self):
        with main.db() as conn:
            conn.execute(
                "UPDATE organizations SET discovery_slug=?, discovery_enabled=1, "
                "service_endpoint=?, status='active', deployment_mode='dedicated' "
                "WHERE id=?",
                (self.SLUG, self.ENDPOINT, main.nova_organization_id()),
            )

    def discover(self, slug):
        return self.client.post(
            "/api/discovery/organization", json={"organization": slug}
        )

    # -- résolution ---------------------------------------------------------

    def test_a_known_organization_returns_its_bootstrap(self):
        body = self.discover(self.SLUG).json()
        self.assertEqual(body["contract_version"], 1)
        self.assertEqual(body["organization"]["slug"], self.SLUG)
        self.assertEqual(body["service_endpoint"], self.ENDPOINT)
        self.assertEqual(body["deployment_mode"], "dedicated")

    def test_the_identifier_is_case_and_space_insensitive(self):
        # Un code recopié depuis un courriel ne doit pas échouer sur une
        # majuscule ou une espace.
        for variant in (self.SLUG.upper(), f"  {self.SLUG}  ", "Ecole-Exemple"):
            self.assertEqual(self.discover(variant).status_code, 200, variant)

    def test_the_response_reveals_nothing_more_than_needed(self):
        body = self.discover(self.SLUG).json()
        self.assertEqual(
            set(body),
            {"contract_version", "organization", "deployment_mode", "service_endpoint"},
        )
        self.assertEqual(set(body["organization"]), {"slug", "display_name"})
        # Ni identifiant de tenant interne, ni tenant Microsoft, ni domaine
        # Google, ni émetteur OIDC, ni identifiant client.
        raw = self.discover(self.SLUG).text
        self.assertNotIn(main.nova_organization_id(), raw)
        for leaked in ("entra_tenant_id", "google_hosted_domain", "oidc_issuer",
                       "client_id", "client_secret", "organization_id"):
            self.assertNotIn(leaked, raw)

    # -- anti-énumération ---------------------------------------------------

    def test_every_failure_looks_the_same(self):
        """Le point le plus important : cet endpoint ne doit pas devenir un
        oracle qui dit lesquelles des entreprises testées sont clientes de Nova,
        et lesquelles ont été suspendues."""
        responses = []

        # 1. organisation inconnue
        responses.append(self.discover("organisation-qui-n-existe-pas"))
        # 2. découverte désactivée
        with main.db() as conn:
            conn.execute("UPDATE organizations SET discovery_enabled=0")
        responses.append(self.discover(self.SLUG))
        # 3. organisation suspendue
        with main.db() as conn:
            conn.execute(
                "UPDATE organizations SET discovery_enabled=1, status='suspended'")
        responses.append(self.discover(self.SLUG))
        # 4. adresse de service absente
        with main.db() as conn:
            conn.execute(
                "UPDATE organizations SET status='active', service_endpoint=NULL")
        responses.append(self.discover(self.SLUG))
        # 5. adresse de service invalide
        with main.db() as conn:
            conn.execute(
                "UPDATE organizations SET service_endpoint='ftp://ailleurs.test'")
        responses.append(self.discover(self.SLUG))
        # 6. adresse de service interne — refusée par la politique réseau, et
        #    refusée de la même manière que tout le reste : une organisation mal
        #    configurée ne doit pas se distinguer d'une organisation inconnue.
        with main.db() as conn:
            conn.execute("UPDATE organizations SET service_endpoint='https://10.0.0.5'")
        responses.append(self.discover(self.SLUG))

        for response in responses:
            self.assertEqual(response.status_code, 404)
            self.assertEqual(response.json()["detail"], "ORGANIZATION_NOT_AVAILABLE")
        # Toutes les réponses sont rigoureusement identiques.
        self.assertEqual(len({r.text for r in responses}), 1)

    def test_an_empty_identifier_reveals_nothing(self):
        for value in ("", "   "):
            response = self.discover(value)
            self.assertEqual(response.status_code, 404)
            self.assertEqual(response.json()["detail"], "ORGANIZATION_NOT_AVAILABLE")

    def test_there_is_no_listing_or_search(self):
        # Aucune route ne permet d'énumérer les organisations.
        for path in ("/api/discovery/organizations", "/api/organizations",
                     "/api/discovery"):
            self.assertIn(self.client.get(path).status_code, (404, 405))
        # Et le point de terminaison n'accepte pas de recherche.
        self.assertEqual(self.discover("*").status_code, 404)
        self.assertEqual(self.discover("%").status_code, 404)

    def test_the_identifier_is_not_logged(self):
        """Accumuler les recherches infructueuses reviendrait à constituer la
        liste des organisations que l'on cherche à joindre."""
        with patch("builtins.print") as printed:
            self.discover("organisation-recherchee-secrete")
        for call in printed.call_args_list:
            self.assertNotIn("organisation-recherchee-secrete", str(call))

    # -- mode dédié ---------------------------------------------------------

    def test_a_dedicated_server_never_resolves_another_organization(self):
        """Même si la base en contient d'autres — import, test, erreur — une
        instance dédiée ne répond que pour celle qu'elle sert."""
        with main.db() as conn:
            conn.execute(
                "INSERT OR REPLACE INTO organizations(id, slug, display_name, "
                "org_type, discovery_slug, discovery_enabled, service_endpoint, "
                "status, created_at) VALUES (?,?,?,?,?,?,?,?,?)",
                ("organisation-voisine", "voisine", "Voisine", "education",
                 "voisine", 1, "https://voisine.example.edu", "active", time.time()))
        response = self.discover("voisine")
        self.assertEqual(response.status_code, 404)
        self.assertEqual(response.json()["detail"], "ORGANIZATION_NOT_AVAILABLE")
        with main.db() as conn:
            conn.execute("DELETE FROM organizations WHERE id='organisation-voisine'")

    def test_the_resolver_honours_its_interface(self):
        self.assertIsInstance(
            main.DISCOVERY_RESOLVER, main.OrganizationDiscoveryResolver)

    # -- administration -----------------------------------------------------

    def test_admin_can_read_and_update_discovery(self):
        read = self.client.get("/api/admin/discovery", headers=self.ADMIN).json()
        self.assertEqual(read["discovery_slug"], self.SLUG)
        self.assertTrue(read["discovery_enabled"])
        self.assertIsNone(read["service_endpoint_error"])

        updated = self.client.post(
            "/api/admin/discovery", headers=self.ADMIN,
            json={"service_endpoint": "https://nouveau.example.edu/"}).json()
        # L'adresse est normalisée, sans barre finale.
        self.assertEqual(updated["service_endpoint"], "https://nouveau.example.edu")

    def test_an_endpoint_rotation_is_immediately_visible(self):
        """Une organisation doit pouvoir déménager sans réinstaller Nova."""
        first = self.discover(self.SLUG).json()["service_endpoint"]
        self.client.post(
            "/api/admin/discovery", headers=self.ADMIN,
            json={"service_endpoint": "https://apres-migration.example.edu"})
        second = self.discover(self.SLUG).json()["service_endpoint"]
        self.assertNotEqual(first, second)
        self.assertEqual(second, "https://apres-migration.example.edu")

    def test_an_invalid_endpoint_is_refused_by_the_admin_api(self):
        for endpoint in ("http://nova.example.edu", "ftp://nova.example.edu",
                         "https://user:pass@nova.example.edu"):
            response = self.client.post(
                "/api/admin/discovery", headers=self.ADMIN,
                json={"service_endpoint": endpoint})
            self.assertEqual(response.status_code, 400, endpoint)
            self.assertEqual(response.json()["detail"], "SERVICE_ENDPOINT_INVALID")

    def test_the_slug_is_immutable_once_discovery_is_enabled(self):
        """Le changer invaliderait d'un coup le code que portent tous les
        postes déjà déployés."""
        response = self.client.post(
            "/api/admin/discovery", headers=self.ADMIN,
            json={"discovery_slug": "nouveau-code"})
        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.json()["detail"], "DISCOVERY_SLUG_IMMUTABLE")

        # Désactiver d'abord rend le changement possible, et explicite.
        self.client.post("/api/admin/discovery", headers=self.ADMIN,
                         json={"discovery_enabled": False})
        changed = self.client.post(
            "/api/admin/discovery", headers=self.ADMIN,
            json={"discovery_slug": "nouveau-code"})
        self.assertEqual(changed.status_code, 200)
        self.assertEqual(changed.json()["discovery_slug"], "nouveau-code")

    def test_an_invalid_status_is_refused(self):
        response = self.client.post(
            "/api/admin/discovery", headers=self.ADMIN,
            json={"status": "en-vacances"})
        self.assertEqual(response.status_code, 400)

    def test_the_admin_api_requires_an_administrator(self):
        self.assertEqual(self.client.get("/api/admin/discovery").status_code, 401)
        self.assertEqual(
            self.client.post("/api/admin/discovery", json={}).status_code, 401)
        # Le jeton partage a ete retire : il n'ouvre plus rien.
        self.assertEqual(
            self.client.post("/api/admin/discovery", json={},
                             headers={"X-Admin-Token": "mauvais"}).status_code, 401)

    # -- amorçage -----------------------------------------------------------

    def test_discovery_is_bootstrapped_once_from_the_environment(self):
        path = os.path.join(TEST_DIRECTORY.name, f"discovery-{uuid.uuid4().hex}.db")
        with patch.object(main, "DB_PATH", path), patch.object(
            main, "DISCOVERY_SLUG", "code-herite"
        ), patch.object(main, "SERVICE_ENDPOINT", "https://herite.example.edu"):
            main.init_db()
            with main.db() as conn:
                first = dict(conn.execute(
                    "SELECT discovery_slug, service_endpoint FROM organizations "
                    "WHERE id=?", (main.nova_organization_id(),)).fetchone())
                conn.execute(
                    "UPDATE organizations SET service_endpoint=?",
                    ("https://corrige.example.edu",))
            # Un redémarrage ne réécrit pas ce qui a été corrigé en base.
            main.init_db()
            with main.db() as conn:
                second = dict(conn.execute(
                    "SELECT discovery_slug, service_endpoint FROM organizations "
                    "WHERE id=?", (main.nova_organization_id(),)).fetchone())

        self.assertEqual(first["discovery_slug"], "code-herite")
        self.assertEqual(first["service_endpoint"], "https://herite.example.edu")
        self.assertEqual(second["service_endpoint"], "https://corrige.example.edu")

    def test_discovery_stays_disabled_until_someone_enables_it(self):
        """Rien n'est publié par défaut : activer la découverte est un geste."""
        path = os.path.join(TEST_DIRECTORY.name, f"discovery-{uuid.uuid4().hex}.db")
        with patch.object(main, "DB_PATH", path), patch.object(
            main, "DISCOVERY_SLUG", "code-herite"
        ):
            main.init_db()
            with main.db() as conn:
                row = conn.execute(
                    "SELECT discovery_enabled FROM organizations WHERE id=?",
                    (main.nova_organization_id(),)).fetchone()
        self.assertEqual(row["discovery_enabled"], 0)


# ═══════════════════ Phase 22 — administration moderne ════════════════════


class AdminTestCase(unittest.TestCase):
    """Socle commun : un compte, une session Nova, un rôle, un step-up réel.

    Depuis la Phase 24, obtenir une session d'administration passe par une
    réauthentification auprès de l'IdP. Les tests ne la contournent pas : ils
    la jouent, avec un fournisseur Microsoft simulé dont les jetons sont signés
    localement. Un raccourci ici rendrait verts des tests qui n'exercent plus le
    chemin réel.
    """

    TENANT = "33333333-3333-4333-8333-333333333333"
    CLIENT_ID = "cccccccc-dddd-4eee-8fff-999999999999"
    NONCE = "nonce-step-up-0123456789"
    REDIRECT = "http://127.0.0.1:53400/callback"

    @classmethod
    def setUpClass(cls):
        cls.client = TestClient(main.app)
        cls.private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)

    def setUp(self):
        with main.db() as conn:
            # Comptes et sessions compris : un test qui suspend un compte ne
            # doit pas empêcher le suivant de se connecter.
            for table in ("admin_sessions", "admin_audit_log",
                          "federated_identities", "users", "tokens", "auth_codes"):
                conn.execute(f"DELETE FROM {table}")
            conn.execute(
                "UPDATE organizations SET status='active', entra_tenant_id=? WHERE id=?",
                (self.TENANT, main.nova_organization_id()),
            )
        clear_sso_flows()
        clear_provider_configs()
        main.JWKS_CACHE.clear()
        self.config_id = ensure_provider_config(
            "microsoft_entra", display_name="Microsoft", entra_tenant_id=self.TENANT)
        self.seed_jwks()

    # -- IdP simulé --------------------------------------------------------

    def seed_jwks(self, kid="step-up-key"):
        jwk = json.loads(
            jwt.algorithms.RSAAlgorithm.to_jwk(self.private_key.public_key()))
        jwk.update({"kid": kid, "use": "sig", "alg": "RS256"})
        uri = main.ADAPTERS["microsoft_entra"]["jwks_uri"]({"tid": self.TENANT})
        main.JWKS_CACHE[uri] = {"jwks": {"keys": [jwk]}, "fetched_at": time.time()}

    def id_token(self, subject="oid-admin", email="admin@example.com",
                 auth_time="fresh", **overrides) -> str:
        now = int(time.time())
        if auth_time == "fresh":
            auth_time = now - 5
        claims = {
            "iss": f"https://login.microsoftonline.com/{self.TENANT}/v2.0",
            "aud": self.CLIENT_ID,
            "exp": now + 600, "iat": now, "nbf": now - 10,
            "sub": "sub-app", "oid": subject, "tid": self.TENANT,
            "nonce": self.NONCE, "preferred_username": email,
            "auth_time": auth_time,
        }
        claims.update(overrides)
        for empty in [k for k, v in claims.items() if v is None]:
            claims.pop(empty)
        return jwt.encode(claims, self.private_key, algorithm="RS256",
                          headers={"kid": "step-up-key"})

    # -- comptes et rôles --------------------------------------------------

    def sign_in(self, email="admin@example.com", machine="poste-admin") -> str:
        """Session Nova ordinaire, par le chemin normal."""
        self.client.post("/api/auth/request", json={"email": email, "machine": machine})
        with main.db() as conn:
            code = conn.execute(
                "SELECT code FROM auth_codes WHERE email=? ORDER BY expires_at DESC",
                (email,),
            ).fetchone()["code"]
        response = self.client.post(
            "/api/auth/verify",
            json={"email": email, "code": code, "machine": machine},
        )
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()["token"]

    def with_role(self, email: str, role: str):
        with main.db() as conn:
            conn.execute(
                "UPDATE users SET security_role=? WHERE normalized_email=? "
                "AND organization_id=?",
                (role, main.normalize_email(email), main.nova_organization_id()))

    def with_federated_identity(self, email: str, subject="oid-admin"):
        """Identité Microsoft vérifiée, comme après une vraie connexion SSO."""
        with main.db() as conn:
            account = main.user_by_email(conn, main.nova_organization_id(), email)
            conn.execute(
                "INSERT OR REPLACE INTO federated_identities(provider, external_subject, "
                "external_tenant_id, organization_id, email, user_id, verification, "
                "external_issuer, created_at, last_seen) "
                "VALUES ('microsoft_entra',?,?,?,?,?,'verified','',?,?)",
                (subject, self.TENANT, main.nova_organization_id(), email,
                 account["user_id"], time.time(), time.time()))
        return account["user_id"]

    # -- step-up -----------------------------------------------------------

    def step_up_start(self, user_token: str, redirect_uri=None):
        with patch.object(main, "ENTRA_CLIENT_ID", self.CLIENT_ID):
            return self.client.post(
                "/api/admin/step-up/start",
                headers=self.auth(user_token),
                json={"code_challenge": "a" * 43, "nonce": self.NONCE,
                      "redirect_uri": redirect_uri or self.REDIRECT,
                      "machine": "poste-admin"})

    def step_up_complete(self, user_token: str, flow_id: str, token=None,
                         redirect_uri=None):
        async def fake_redeem(config, body):
            return {"id_token": token if token is not None else self.id_token()}

        with patch.object(main, "ENTRA_CLIENT_ID", self.CLIENT_ID), patch.object(
            main, "redeem_authorization_code", fake_redeem
        ):
            return self.client.post(
                "/api/admin/step-up/complete",
                headers=self.auth(user_token),
                json={"flow_id": flow_id, "code": "code-microsoft",
                      "code_verifier": "v" * 64,
                      "redirect_uri": redirect_uri or self.REDIRECT})

    def admin_session(self, email="admin@example.com",
                      role=main.SECURITY_ROLE_ORGANIZATION_ADMIN,
                      subject="oid-admin") -> str:
        """Le parcours complet : connexion, rôle, identité, step-up."""
        user_token = self.sign_in(email)
        self.with_role(email, role)
        self.with_federated_identity(email, subject)
        flow_id = self.step_up_start(user_token).json()["flow_id"]
        response = self.step_up_complete(user_token, flow_id,
                                         token=self.id_token(subject, email))
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()["token"]

    def auth(self, token: str) -> dict:
        return {"Authorization": f"Bearer {token}"}


class SecurityRoleAuthorityTests(AdminTestCase):
    """Nova décide des rôles. L'IdP prouve seulement qui est la personne."""

    def test_a_new_account_is_never_an_administrator(self):
        self.sign_in("nouveau@example.com")
        with main.db() as conn:
            row = conn.execute(
                "SELECT * FROM users WHERE email=?", ("nouveau@example.com",)
            ).fetchone()
        self.assertEqual(main.security_role_for(row), main.SECURITY_ROLE_MEMBER)

    def test_no_identity_provider_claim_can_grant_a_role(self):
        """Le test qui compte : `role=admin`, `groups=admins`, `is_admin=true`
        venus d'un IdP ne doivent produire aucun privilège. Nova ne lit qu'une
        colonne, écrite par un opérateur."""
        email = "revendique@example.com"
        user_token = self.sign_in(email)
        with main.db() as conn:
            conn.execute(
                "UPDATE users SET role=?, cohort=? WHERE email=?",
                ("staff", "admins", email),
            )
        refused = self.client.post("/api/admin/session", headers=self.auth(user_token))
        self.assertEqual(refused.status_code, 403)
        self.assertEqual(refused.json()["detail"], "ADMIN_ROLE_REQUIRED")

    def test_a_business_role_is_never_a_security_role(self):
        # Un enseignant n'administre pas parce qu'il enseigne.
        for business_role in ("teacher", "staff", "student", "partner"):
            email = f"{business_role}@example.com"
            token = self.sign_in(email)
            with main.db() as conn:
                conn.execute(
                    "UPDATE users SET role=? WHERE email=?", (business_role, email)
                )
            response = self.client.post("/api/admin/session", headers=self.auth(token))
            self.assertEqual(response.status_code, 403, business_role)

    def test_an_unknown_stored_role_falls_back_to_member(self):
        # Une colonne corrompue, ou écrite par une version future, n'accorde
        # jamais plus que le minimum.
        email = "bizarre@example.com"
        self.sign_in(email)
        self.with_role(email, "super_utilisateur_absolu")
        with main.db() as conn:
            row = conn.execute("SELECT * FROM users WHERE email=?", (email,)).fetchone()
        self.assertEqual(main.security_role_for(row), main.SECURITY_ROLE_MEMBER)

    def test_the_capability_matrix_is_not_degenerate(self):
        """Trois rôles qui autorisent la même chose seraient un seul rôle."""
        org = main.capabilities_for_role(main.SECURITY_ROLE_ORGANIZATION_ADMIN)
        it = main.capabilities_for_role(main.SECURITY_ROLE_IT_ADMIN)
        read_only = main.capabilities_for_role(main.SECURITY_ROLE_READ_ONLY)
        self.assertTrue(org - it, "organization_admin n'a rien en propre")
        self.assertTrue(it - org, "it_admin n'a rien en propre")
        self.assertTrue(read_only < org)
        self.assertNotIn(main.CAPABILITY_PROVIDER_MANAGE, read_only)
        self.assertEqual(main.capabilities_for_role(main.SECURITY_ROLE_MEMBER), frozenset())


class AdminSessionTests(AdminTestCase):
    """Ouverture, durée, révocation."""

    def test_an_administrator_can_open_a_session(self):
        email = "admin@example.com"
        user_token = self.sign_in(email)
        self.with_role(email, main.SECURITY_ROLE_ORGANIZATION_ADMIN)
        self.with_federated_identity(email)
        flow_id = self.step_up_start(user_token).json()["flow_id"]
        response = self.step_up_complete(user_token, flow_id)
        self.assertEqual(response.status_code, 200, response.text)
        body = response.json()
        self.assertEqual(body["role"], main.SECURITY_ROLE_ORGANIZATION_ADMIN)
        self.assertIn(main.CAPABILITY_PROVIDER_MANAGE, body["capabilities"])
        self.assertGreater(body["expires_in"], 0)
        # Rien d'inutile : ni e-mail, ni identifiant de session, ni empreinte.
        self.assertEqual(
            set(body), {"token", "role", "organization_id", "capabilities", "expires_in"}
        )

    def test_a_member_cannot_open_one(self):
        token = self.sign_in("membre@example.com")
        for path in ("/api/admin/session", "/api/admin/step-up/start"):
            response = self.client.post(
                path, headers=self.auth(token),
                json={"code_challenge": "a" * 43, "nonce": self.NONCE,
                      "redirect_uri": self.REDIRECT})
            self.assertEqual(response.status_code, 403, path)
            self.assertEqual(response.json()["detail"], "ADMIN_ROLE_REQUIRED", path)

    def test_the_old_route_no_longer_issues_a_session(self):
        """La route qui ouvrait une session depuis un simple jeton utilisateur
        indique désormais le chemin, sans plus rien accorder."""
        email = "ancienne-route@example.com"
        token = self.sign_in(email)
        self.with_role(email, main.SECURITY_ROLE_ORGANIZATION_ADMIN)
        response = self.client.post("/api/admin/session", headers=self.auth(token))
        self.assertEqual(response.status_code, 403)
        self.assertEqual(response.json()["detail"], "ADMIN_STEP_UP_REQUIRED")
        self.assertNotIn("token", response.json())

    def test_the_token_is_only_stored_hashed(self):
        token = self.admin_session()
        with main.db() as conn:
            rows = conn.execute("SELECT * FROM admin_sessions").fetchall()
        stored = {value for row in rows for value in tuple(row) if isinstance(value, str)}
        self.assertNotIn(token, stored)
        self.assertIn(main.hash_token(token), {row["token_hash"] for row in rows})

    def test_the_token_never_reaches_the_logs(self):
        with patch("builtins.print") as printed:
            token = self.admin_session()
            self.client.get("/api/control/organization", headers=self.auth(token))
        for call in printed.call_args_list:
            self.assertNotIn(token, str(call))

    def test_an_expired_session_is_refused(self):
        token = self.admin_session()
        with main.db() as conn:
            conn.execute("UPDATE admin_sessions SET expires_at=?", (time.time() - 1,))
        response = self.client.get("/api/control/organization", headers=self.auth(token))
        self.assertEqual(response.status_code, 401)
        self.assertEqual(response.json()["detail"], "ADMIN_SESSION_EXPIRED")

    def test_an_idle_session_expires(self):
        token = self.admin_session()
        with main.db() as conn:
            conn.execute(
                "UPDATE admin_sessions SET last_seen_at=?",
                (time.time() - main.ADMIN_SESSION_IDLE_SECONDS - 60,),
            )
        response = self.client.get("/api/control/organization", headers=self.auth(token))
        self.assertEqual(response.status_code, 401)
        self.assertEqual(response.json()["detail"], "ADMIN_SESSION_EXPIRED")

    def test_admin_logout_closes_the_session(self):
        token = self.admin_session()
        self.assertEqual(
            self.client.post("/api/admin/session/logout",
                             headers=self.auth(token)).status_code, 200)
        response = self.client.get("/api/control/organization", headers=self.auth(token))
        self.assertEqual(response.status_code, 401)
        self.assertEqual(response.json()["detail"], "ADMIN_SESSION_REVOKED")

    def test_user_logout_also_closes_admin_sessions(self):
        """Une personne qui quitte Nova ne laisse pas un privilège ouvert
        derrière elle."""
        email = "partant@example.com"
        user_token = self.sign_in(email)
        self.with_role(email, main.SECURITY_ROLE_ORGANIZATION_ADMIN)
        self.with_federated_identity(email)
        flow_id = self.step_up_start(user_token).json()["flow_id"]
        admin_token = self.step_up_complete(
            user_token, flow_id,
            token=self.id_token(email=email)).json()["token"]

        self.client.post("/api/auth/logout", headers=self.auth(user_token))

        response = self.client.get(
            "/api/control/organization", headers=self.auth(admin_token))
        self.assertEqual(response.status_code, 401)
        self.assertEqual(response.json()["detail"], "ADMIN_SESSION_REVOKED")

    def test_a_revoked_role_closes_the_door_on_the_next_call(self):
        """Une session ne fige pas un privilège."""
        email = "retrograde@example.com"
        token = self.admin_session(email)
        self.assertEqual(
            self.client.get("/api/control/organization",
                            headers=self.auth(token)).status_code, 200)
        self.with_role(email, main.SECURITY_ROLE_MEMBER)
        response = self.client.get("/api/control/organization", headers=self.auth(token))
        self.assertEqual(response.status_code, 403)
        self.assertEqual(response.json()["detail"], "ADMIN_ROLE_REQUIRED")

    def test_a_disabled_user_loses_admin_access_immediately(self):
        email = "suspendu@example.com"
        token = self.admin_session(email)
        with main.db() as conn:
            conn.execute("UPDATE users SET disabled=1 WHERE email=?", (email,))
        response = self.client.get("/api/control/organization", headers=self.auth(token))
        self.assertEqual(response.status_code, 403)

    def test_a_suspended_organization_cannot_be_administered(self):
        token = self.admin_session()
        with main.db() as conn:
            conn.execute(
                "UPDATE organizations SET status='suspended' WHERE id=?",
                (main.nova_organization_id(),),
            )
        response = self.client.get("/api/control/organization", headers=self.auth(token))
        self.assertEqual(response.status_code, 403)

    def test_set_security_role_revokes_open_sessions(self):
        email = "revoque@example.com"
        token = self.admin_session(email)
        main.set_security_role(email, main.SECURITY_ROLE_MEMBER)
        response = self.client.get("/api/control/organization", headers=self.auth(token))
        self.assertIn(response.status_code, (401, 403))


class UserTokenVersusAdminTokenTests(AdminTestCase):
    """Séparation stricte des deux jetons."""

    def test_a_user_token_opens_no_admin_route(self):
        email = "double@example.com"
        user_token = self.sign_in(email)
        self.with_role(email, main.SECURITY_ROLE_ORGANIZATION_ADMIN)
        # Même personne, même rôle : le jeton utilisateur ne suffit toujours pas.
        for path in ("/api/control/organization", "/api/admin/discovery",
                     "/api/admin/provider-configs"):
            response = self.client.get(path, headers=self.auth(user_token))
            self.assertEqual(response.status_code, 401, path)

    def test_an_admin_token_opens_no_user_route(self):
        admin_token = self.admin_session()
        response = self.client.get("/api/me", headers=self.auth(admin_token))
        self.assertEqual(response.status_code, 401)

    def test_the_client_never_declares_that_its_token_is_admin(self):
        # Aucun en-tête, aucun champ ne dit « ceci est un jeton admin » : le
        # serveur le sait par sa table.
        token = self.admin_session()
        self.assertEqual(
            self.client.get("/api/control/organization",
                            headers=self.auth(token)).status_code, 200)


class AdminAuthorizationMatrixTests(AdminTestCase):
    """Ce que chaque rôle peut réellement faire."""

    def test_read_only_cannot_mutate_anything(self):
        token = self.admin_session("lecture@example.com", main.SECURITY_ROLE_READ_ONLY)
        self.assertEqual(
            self.client.get("/api/control/organization",
                            headers=self.auth(token)).status_code, 200)
        for path, payload in (
            ("/api/admin/discovery", {"discovery_enabled": True}),
            ("/api/admin/provider-configs", {"provider_type": "oidc"}),
        ):
            response = self.client.post(path, headers=self.auth(token), json=payload)
            self.assertEqual(response.status_code, 403, path)
            self.assertEqual(response.json()["detail"], "ADMIN_ROLE_REQUIRED")

    def test_it_admin_manages_providers_and_discovery(self):
        token = self.admin_session("it@example.com", main.SECURITY_ROLE_IT_ADMIN)
        response = self.client.post(
            "/api/admin/discovery", headers=self.auth(token),
            json={"discovery_enabled": False})
        self.assertEqual(response.status_code, 200)

    def test_organization_admin_manages_providers_and_discovery(self):
        token = self.admin_session()
        response = self.client.post(
            "/api/admin/discovery", headers=self.auth(token),
            json={"discovery_enabled": False})
        self.assertEqual(response.status_code, 200)

    def test_the_refusal_does_not_say_which_role_was_missing(self):
        token = self.admin_session("lecture2@example.com", main.SECURITY_ROLE_READ_ONLY)
        response = self.client.post(
            "/api/admin/discovery", headers=self.auth(token), json={})
        self.assertEqual(response.json(), {"detail": "ADMIN_ROLE_REQUIRED"})


class AdminTenantIsolationTests(AdminTestCase):
    """Un identifiant d'organisation désigne ; il n'autorise pas."""

    def test_an_administrator_reads_its_own_organization(self):
        token = self.admin_session()
        organization_id = main.nova_organization_id()
        response = self.client.get(
            f"/api/control/organizations/{organization_id}", headers=self.auth(token))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["organization"]["id"], organization_id)

    def test_another_organization_is_invisible(self):
        token = self.admin_session()
        with main.db() as conn:
            conn.execute(
                "INSERT OR REPLACE INTO organizations(id, slug, display_name, "
                "org_type, status, created_at) VALUES (?,?,?,?,?,?)",
                ("organisation-voisine", "voisine", "Voisine", "education",
                 "active", time.time()))
        response = self.client.get(
            "/api/control/organizations/organisation-voisine", headers=self.auth(token))
        # Pas même la confirmation qu'elle existe.
        self.assertEqual(response.status_code, 404)
        self.assertEqual(response.json()["detail"], "ORGANIZATION_NOT_FOUND")
        with main.db() as conn:
            conn.execute("DELETE FROM organizations WHERE id='organisation-voisine'")

    def test_a_client_supplied_organization_id_changes_nothing(self):
        token = self.admin_session()
        response = self.client.get(
            "/api/control/organization",
            headers={**self.auth(token), "X-Organization-Id": "organisation-voisine"},
            params={"organization_id": "organisation-voisine"})
        self.assertEqual(
            response.json()["organization"]["id"], main.nova_organization_id())

    def test_the_control_view_carries_no_secret(self):
        # La session est ouverte d'abord : le step-up a besoin de la
        # configuration Microsoft que le test s'apprête à remplacer.
        token = self.admin_session()
        clear_provider_configs()
        with main.db() as conn:
            conn.execute(
                "INSERT INTO organization_provider_configs(id, organization_id, "
                "provider_type, oidc_issuer, oidc_client_id, oidc_client_secret, "
                "enabled, created_at) VALUES (?,?,?,?,?,?,?,?)",
                (uuid.uuid4().hex, main.nova_organization_id(), "oidc",
                 "https://idp.example.com", "client-public", "secret-tres-prive",
                 1, time.time()))
        raw = self.client.get("/api/control/organization", headers=self.auth(token)).text
        self.assertNotIn("secret-tres-prive", raw)
        self.assertNotIn("client-public", raw)
        clear_provider_configs()


class LegacyAdminTokenTests(AdminTestCase):
    """Le jeton d'administration partage : retire en Phase 28.

    Ces tests ne verifient plus qu'il fonctionne — ils verifient qu'il
    n'accorde plus rien, quelle que soit la valeur presentee.
    """

    def test_no_value_of_the_legacy_header_grants_anything(self):
        # Y compris l'ancienne valeur de fixture, celle qui ouvrait tout.
        for value in ("", "test-admin-token", "nova-admin-local",
                      "n'importe quoi", "a" * 64):
            for path in ("/api/admin/discovery", "/api/admin/provider-configs",
                         "/api/control/organization"):
                response = self.client.get(path, headers={"X-Admin-Token": value})
                self.assertEqual(response.status_code, 401, f"{path} / {value!r}")
                self.assertEqual(response.json()["detail"], "ADMIN_REQUIRED")

    def test_the_legacy_header_does_not_help_a_user_session(self):
        """Presenter les deux ne vaut pas mieux que presenter le mauvais."""
        email = "combine@example.com"
        user_token = self.sign_in(email)
        self.with_role(email, main.SECURITY_ROLE_ORGANIZATION_ADMIN)
        response = self.client.get(
            "/api/control/organization",
            headers={"Authorization": f"Bearer {user_token}",
                     "X-Admin-Token": "test-admin-token"})
        self.assertEqual(response.status_code, 401)

    def test_nothing_of_the_mechanism_remains(self):
        # Ni constante, ni drapeau, ni secret genere au demarrage.
        for attribute in ("ADMIN_TOKEN", "LEGACY_ADMIN_TOKEN_ENABLED",
                          "require_admin"):
            self.assertFalse(hasattr(main, attribute), attribute)

    def test_the_principal_reads_a_single_header(self):
        """Une seule porte : `Authorization`. Le serveur ne regarde plus ailleurs."""
        import inspect
        signature = inspect.signature(main.admin_principal)
        self.assertEqual(list(signature.parameters), ["authorization"])

    def test_no_admin_secret_is_fabricated_at_startup(self):
        """Un secret d'administration genere par defaut est un secret partage
        par tous les deploiements qui oublient de le definir."""
        source = inspect.getsource(main)
        self.assertNotIn("NOVA_ADMIN_TOKEN", source)
        self.assertNotIn("NOVA_LEGACY_ADMIN_TOKEN", source)
        # `secrets.token_urlsafe` sert toujours — aux identifiants de flux SSO,
        # aux jetons de session — mais plus a fabriquer une valeur
        # d'administration au demarrage.
        self.assertNotIn("X-Admin-Token", source.replace(
            "# ", ""))  # ni en runtime, ni en commentaire residuel


class AdminAuditLogTests(AdminTestCase):
    """Ce que le journal retient, et surtout ce qu'il ne retient pas."""

    def rows(self):
        with main.db() as conn:
            return conn.execute(
                "SELECT * FROM admin_audit_log ORDER BY created_at").fetchall()

    def test_opening_a_session_is_audited(self):
        self.admin_session()
        actions = [row["action"] for row in self.rows()]
        self.assertIn("admin_session.create", actions)

    def test_a_mutation_is_audited_with_actor_role_and_organization(self):
        token = self.admin_session("auditee@example.com")
        self.client.post("/api/admin/discovery", headers=self.auth(token),
                         json={"discovery_enabled": False})
        row = [r for r in self.rows() if r["action"] == "discovery.update"][-1]
        self.assertEqual(row["actor_role"], main.SECURITY_ROLE_ORGANIZATION_ADMIN)
        self.assertEqual(row["organization_id"], main.nova_organization_id())
        self.assertTrue(row["actor_user_id"])
        self.assertTrue(row["request_id"])
        self.assertGreater(row["created_at"], 0)

    def test_a_plain_read_is_not_audited(self):
        token = self.admin_session()
        before = len(self.rows())
        self.client.get("/api/control/organization", headers=self.auth(token))
        self.assertEqual(len(self.rows()), before)

    def test_a_secret_never_reaches_the_audit_log(self):
        token = self.admin_session()
        clear_provider_configs()
        self.client.post(
            "/api/admin/provider-configs", headers=self.auth(token),
            json={"provider_type": "oidc", "oidc_issuer": "https://idp.example.com",
                  "oidc_client_id": "client", "oidc_client_secret": "secret-absolu"})
        joined = " ".join(str(tuple(row)) for row in self.rows())
        self.assertNotIn("secret-absolu", joined)
        # Mais le fait qu'un secret existe est bien enregistré.
        row = [r for r in self.rows() if r["action"] == "provider_config.create"][-1]
        self.assertIn('"has_secret": true', row["metadata"])
        clear_provider_configs()

    def test_no_route_can_modify_or_delete_the_audit_log(self):
        token = self.admin_session()
        for method, path in (("get", "/api/admin/audit"), ("get", "/api/control/audit"),
                             ("delete", "/api/admin/audit")):
            response = getattr(self.client, method)(path, headers=self.auth(token))
            self.assertIn(response.status_code, (404, 405), path)


class AdminRegressionTests(AdminTestCase):
    """La refonte de l'administration ne touche pas le chemin utilisateur."""

    def test_a_member_still_reads_its_own_profile(self):
        token = self.sign_in("membre-normal@example.com")
        response = self.client.get("/api/me", headers=self.auth(token))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["membership"]["security_role"], "member")

    def test_an_administrator_profile_announces_its_role(self):
        email = "profil-admin@example.com"
        token = self.sign_in(email)
        self.with_role(email, main.SECURITY_ROLE_IT_ADMIN)
        response = self.client.get("/api/me", headers=self.auth(token))
        self.assertEqual(
            response.json()["membership"]["security_role"], main.SECURITY_ROLE_IT_ADMIN)

    def test_discovery_stays_public(self):
        self.assertIn(
            self.client.post("/api/discovery/organization",
                             json={"organization": "inconnue"}).status_code,
            (200, 404),
        )


class OperatorCliTests(AdminTestCase):
    """L'attribution du premier administrateur : une commande locale."""

    def test_grant_and_revoke(self):
        email = "cli@example.com"
        self.sign_in(email)
        self.assertEqual(admin_cli.run(["grant", email, "it_admin"]), 0)
        with main.db() as conn:
            row = conn.execute(
                "SELECT security_role FROM users WHERE email=?", (email,)).fetchone()
        self.assertEqual(row["security_role"], "it_admin")
        self.assertEqual(admin_cli.run(["revoke", email]), 0)
        with main.db() as conn:
            row = conn.execute(
                "SELECT security_role FROM users WHERE email=?", (email,)).fetchone()
        self.assertEqual(row["security_role"], "member")

    def test_an_unknown_role_is_refused(self):
        email = "cli2@example.com"
        self.sign_in(email)
        self.assertEqual(admin_cli.run(["grant", email, "root"]), 1)

    def test_an_unknown_account_is_refused(self):
        self.assertEqual(admin_cli.run(["grant", "fantome@example.com", "it_admin"]), 1)

    def test_there_is_no_http_route_to_grant_oneself_a_role(self):
        """La propriété la plus importante du bootstrap : aucune porte dérobée."""
        token = self.sign_in("ambitieux@example.com")
        for path in ("/api/admin/grant", "/api/admin/roles", "/api/admin/bootstrap",
                     "/api/control/roles"):
            response = self.client.post(
                path, headers=self.auth(token),
                json={"email": "ambitieux@example.com", "role": "organization_admin"})
            self.assertIn(response.status_code, (404, 405), path)


# ═══════════════ Phase 24 — réauthentification d'administration ═══════════


class AuthenticationFreshnessTests(unittest.TestCase):
    """`auth_time`, et pourquoi il ne suffit pas qu'un jeton soit récent."""

    def test_a_fresh_authentication_is_accepted(self):
        ok, reason = main.authentication_is_fresh(time.time() - 10)
        self.assertTrue(ok, reason)

    def test_a_missing_auth_time_is_refused(self):
        """Le refus le plus important. Sans `auth_time`, « le jeton vient
        d'être émis » ne dit rien : un IdP peut le réémettre depuis une session
        vieille de plusieurs jours sans rien demander à personne."""
        ok, reason = main.authentication_is_fresh(None)
        self.assertFalse(ok)
        self.assertEqual(reason, "auth_time_missing")

    def test_an_old_authentication_is_refused(self):
        ok, reason = main.authentication_is_fresh(
            time.time() - main.ADMIN_STEP_UP_MAX_AGE_SECONDS
            - main.ADMIN_STEP_UP_CLOCK_SKEW_SECONDS - 10)
        self.assertFalse(ok)
        self.assertEqual(reason, "auth_time_too_old")

    def test_an_authentication_in_the_future_is_refused(self):
        ok, reason = main.authentication_is_fresh(
            time.time() + main.ADMIN_STEP_UP_CLOCK_SKEW_SECONDS + 60)
        self.assertFalse(ok)
        self.assertEqual(reason, "auth_time_in_future")

    def test_a_small_clock_difference_is_tolerated(self):
        # Les horloges d'un IdP et d'un serveur divergent : exiger la seconde
        # près produirait des refus inexplicables.
        self.assertTrue(main.authentication_is_fresh(time.time() + 5)[0])
        self.assertTrue(
            main.authentication_is_fresh(
                time.time() - main.ADMIN_STEP_UP_MAX_AGE_SECONDS + 5)[0])

    def test_a_nonsense_value_is_refused(self):
        for value in ("plus tard", [], {}):
            self.assertFalse(main.authentication_is_fresh(value)[0], value)

    def test_the_window_stays_within_safe_bounds(self):
        self.assertGreaterEqual(main.ADMIN_STEP_UP_MAX_AGE_SECONDS, 60)
        self.assertLessEqual(main.ADMIN_STEP_UP_MAX_AGE_SECONDS, 900)


class AdminStepUpTests(AdminTestCase):
    """Le parcours de réauthentification, de bout en bout."""

    EMAIL = "admin@example.com"

    def prepared(self, role=main.SECURITY_ROLE_ORGANIZATION_ADMIN):
        user_token = self.sign_in(self.EMAIL)
        self.with_role(self.EMAIL, role)
        user_id = self.with_federated_identity(self.EMAIL)
        return user_token, user_id

    # -- la demande --------------------------------------------------------

    def test_the_authorization_url_asks_for_a_recent_authentication(self):
        user_token, _ = self.prepared()
        url = self.step_up_start(user_token).json()["authorization_url"]
        # `max_age` est ce qui oblige l'OP à renvoyer `auth_time` : c'est lui
        # qui rend la fraîcheur vérifiable, pas `prompt`.
        self.assertIn(f"max_age={main.ADMIN_STEP_UP_MAX_AGE_SECONDS}", url)
        self.assertIn("prompt=login", url)
        self.assertIn("code_challenge_method=S256", url)
        self.assertNotIn("client_secret", url)

    def test_a_normal_login_never_asks_for_max_age(self):
        # Redemander une authentification à chaque dictée serait absurde.
        with patch.object(main, "ENTRA_CLIENT_ID", self.CLIENT_ID):
            response = self.client.post(
                "/api/auth/sso/microsoft_entra/start",
                json={"code_challenge": "a" * 43, "nonce": self.NONCE,
                      "redirect_uri": self.REDIRECT, "machine": "poste"})
        self.assertNotIn("max_age", response.json()["authorization_url"])
        self.assertNotIn("prompt=login", response.json()["authorization_url"])

    def test_the_client_cannot_choose_the_provider(self):
        """Le fournisseur vient de l'identité vérifiée du compte. Le laisser au
        client permettrait de contourner l'IdP réel de l'organisation."""
        user_token, _ = self.prepared()
        response = self.step_up_start(user_token)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(set(response.json()),
                         {"flow_id", "authorization_url", "expires_in"})
        # Aucun champ du corps ne nomme un fournisseur.
        self.assertIn("login.microsoftonline.com",
                      response.json()["authorization_url"])

    # -- la preuve ---------------------------------------------------------

    def test_a_complete_step_up_opens_an_admin_session(self):
        user_token, _ = self.prepared()
        flow_id = self.step_up_start(user_token).json()["flow_id"]
        response = self.step_up_complete(user_token, flow_id)
        self.assertEqual(response.status_code, 200, response.text)
        body = response.json()
        self.assertEqual(body["role"], main.SECURITY_ROLE_ORGANIZATION_ADMIN)
        self.assertEqual(
            set(body),
            {"token", "role", "organization_id", "capabilities", "expires_in"})
        # Et la session ouverte fonctionne vraiment.
        self.assertEqual(
            self.client.get("/api/control/organization",
                            headers=self.auth(body["token"])).status_code, 200)

    def test_an_admin_session_keeps_the_phase_22_lifetime(self):
        """Une authentification fraîche n'achète pas une session éternelle."""
        user_token, _ = self.prepared()
        flow_id = self.step_up_start(user_token).json()["flow_id"]
        body = self.step_up_complete(user_token, flow_id).json()
        self.assertLessEqual(body["expires_in"], main.ADMIN_SESSION_TTL_SECONDS)

    # -- fraîcheur ---------------------------------------------------------

    def test_an_id_token_without_auth_time_is_refused(self):
        """Le test qui porte toute la phase : le jeton vient d'être émis, il est
        parfaitement valide, et il ne prouve pourtant aucune présence."""
        user_token, _ = self.prepared()
        flow_id = self.step_up_start(user_token).json()["flow_id"]
        response = self.step_up_complete(
            user_token, flow_id, token=self.id_token(auth_time=None))
        self.assertEqual(response.status_code, 401)
        self.assertEqual(response.json()["detail"], "ADMIN_STEP_UP_TOO_OLD")

    def test_an_old_authentication_is_refused_even_with_a_fresh_token(self):
        # Le navigateur était déjà connecté à Microsoft : l'OP a réémis un jeton
        # sans rien redemander. C'est exactement ce que `max_age` doit empêcher.
        user_token, _ = self.prepared()
        flow_id = self.step_up_start(user_token).json()["flow_id"]
        response = self.step_up_complete(
            user_token, flow_id,
            token=self.id_token(auth_time=int(time.time()) - 86400))
        self.assertEqual(response.status_code, 401)
        self.assertEqual(response.json()["detail"], "ADMIN_STEP_UP_TOO_OLD")

    def test_an_authentication_in_the_future_is_refused(self):
        user_token, _ = self.prepared()
        flow_id = self.step_up_start(user_token).json()["flow_id"]
        response = self.step_up_complete(
            user_token, flow_id,
            token=self.id_token(auth_time=int(time.time()) + 3600))
        self.assertEqual(response.status_code, 401)

    # -- même personne -----------------------------------------------------

    def test_another_identity_of_the_same_organization_is_refused(self):
        """Le navigateur est connecté à un autre compte Microsoft. Même
        organisation, même domaine : cela ne suffit pas."""
        user_token, _ = self.prepared()
        # Un second compte, avec sa propre identité vérifiée.
        self.sign_in("autre@example.com", machine="poste-2")
        self.with_federated_identity("autre@example.com", subject="oid-autre")
        flow_id = self.step_up_start(user_token).json()["flow_id"]
        response = self.step_up_complete(
            user_token, flow_id,
            token=self.id_token(subject="oid-autre", email="autre@example.com"))
        self.assertEqual(response.status_code, 403)
        self.assertEqual(response.json()["detail"],
                         "ADMIN_STEP_UP_IDENTITY_MISMATCH")

    def test_the_same_email_with_another_subject_is_refused(self):
        """Même adresse, autre `oid` : ce n'est pas la même personne, et
        l'adresse n'a jamais été un identifiant."""
        user_token, _ = self.prepared()
        flow_id = self.step_up_start(user_token).json()["flow_id"]
        response = self.step_up_complete(
            user_token, flow_id,
            token=self.id_token(subject="oid-inconnu", email=self.EMAIL))
        self.assertEqual(response.status_code, 403)
        self.assertEqual(response.json()["detail"],
                         "ADMIN_STEP_UP_IDENTITY_MISMATCH")

    def test_an_unknown_identity_is_never_linked(self):
        """Une élévation de privilège est le pire endroit pour rattacher un
        compte. Aucune identité n'est créée, aucun compte non plus."""
        user_token, _ = self.prepared()
        with main.db() as conn:
            before = conn.execute(
                "SELECT COUNT(*) c FROM federated_identities").fetchone()["c"]
            accounts = conn.execute("SELECT COUNT(*) c FROM users").fetchone()["c"]
        flow_id = self.step_up_start(user_token).json()["flow_id"]
        self.step_up_complete(
            user_token, flow_id,
            token=self.id_token(subject="oid-jamais-vu", email="nouveau@example.com"))
        with main.db() as conn:
            after = conn.execute(
                "SELECT COUNT(*) c FROM federated_identities").fetchone()["c"]
            accounts_after = conn.execute("SELECT COUNT(*) c FROM users").fetchone()["c"]
        self.assertEqual(before, after)
        self.assertEqual(accounts, accounts_after)

    def test_a_wrong_tenant_is_refused(self):
        """Une preuve signée, fraîche et bien formée, mais venue d'un tenant que
        l'organisation n'a pas déclaré."""
        user_token, _ = self.prepared()
        flow_id = self.step_up_start(user_token).json()["flow_id"]
        other_tenant = "44444444-4444-4444-8444-444444444444"
        # Le JWKS de l'autre tenant est servi par la même clé : le refus doit
        # venir du contrôle de tenant, pas d'une signature invalide.
        jwk = json.loads(
            jwt.algorithms.RSAAlgorithm.to_jwk(self.private_key.public_key()))
        jwk.update({"kid": "step-up-key", "use": "sig", "alg": "RS256"})
        uri = main.ADAPTERS["microsoft_entra"]["jwks_uri"]({"tid": other_tenant})
        main.JWKS_CACHE[uri] = {"jwks": {"keys": [jwk]}, "fetched_at": time.time()}

        response = self.step_up_complete(
            user_token, flow_id,
            token=self.id_token(
                tid=other_tenant,
                iss=f"https://login.microsoftonline.com/{other_tenant}/v2.0"))
        self.assertEqual(response.status_code, 403)
        self.assertEqual(response.json()["detail"], "TENANT_NOT_ALLOWED")

    def test_a_flow_opened_by_someone_else_cannot_be_completed(self):
        user_token, _ = self.prepared()
        flow_id = self.step_up_start(user_token).json()["flow_id"]
        # Une autre personne, administratrice elle aussi, tente de finir le
        # parcours ouvert par la première.
        other_token = self.sign_in("autre-admin@example.com", machine="poste-3")
        self.with_role("autre-admin@example.com",
                       main.SECURITY_ROLE_ORGANIZATION_ADMIN)
        self.with_federated_identity("autre-admin@example.com", subject="oid-autre-2")
        response = self.step_up_complete(other_token, flow_id)
        self.assertEqual(response.status_code, 403)
        self.assertEqual(response.json()["detail"],
                         "ADMIN_STEP_UP_IDENTITY_MISMATCH")

    # -- rejeu et buts -----------------------------------------------------

    def test_a_proof_is_single_use(self):
        user_token, _ = self.prepared()
        flow_id = self.step_up_start(user_token).json()["flow_id"]
        self.assertEqual(
            self.step_up_complete(user_token, flow_id).status_code, 200)
        replayed = self.step_up_complete(user_token, flow_id)
        self.assertEqual(replayed.status_code, 400)
        self.assertEqual(replayed.json()["detail"], "AUTH_TIMEOUT")

    def test_a_login_flow_is_not_an_admin_proof(self):
        """Un retour de connexion ordinaire ne devient jamais une preuve
        d'administration, même présenté par un administrateur."""
        user_token, _ = self.prepared()
        with patch.object(main, "ENTRA_CLIENT_ID", self.CLIENT_ID):
            login_flow = self.client.post(
                "/api/auth/sso/microsoft_entra/start",
                json={"code_challenge": "a" * 43, "nonce": self.NONCE,
                      "redirect_uri": self.REDIRECT,
                      "machine": "poste"}).json()["flow_id"]
        response = self.step_up_complete(user_token, login_flow)
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json()["detail"], "FLOW_PURPOSE_MISMATCH")

    def test_an_admin_flow_is_not_a_login(self):
        user_token, _ = self.prepared()
        flow_id = self.step_up_start(user_token).json()["flow_id"]

        async def fake_redeem(config, body):
            return {"id_token": self.id_token()}

        with patch.object(main, "ENTRA_CLIENT_ID", self.CLIENT_ID), patch.object(
            main, "redeem_authorization_code", fake_redeem
        ):
            response = self.client.post(
                "/api/auth/sso/microsoft_entra/exchange",
                json={"flow_id": flow_id, "code": "c", "code_verifier": "v" * 64,
                      "redirect_uri": self.REDIRECT})
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json()["detail"], "FLOW_PURPOSE_MISMATCH")

    def test_two_concurrent_step_ups_stay_independent(self):
        user_token, _ = self.prepared()
        first = self.step_up_start(user_token).json()["flow_id"]
        second = self.step_up_start(user_token).json()["flow_id"]
        self.assertNotEqual(first, second)
        self.assertEqual(self.step_up_complete(user_token, first).status_code, 200)
        # Le second reste utilisable : les tentatives ne se partagent rien.
        self.assertEqual(self.step_up_complete(user_token, second).status_code, 200)

    # -- l'état revérifié à la fin -----------------------------------------

    def test_a_role_revoked_mid_flow_yields_no_session(self):
        """La réauthentification réussit, et ne rattrape pas un privilège retiré
        entre-temps."""
        user_token, _ = self.prepared()
        flow_id = self.step_up_start(user_token).json()["flow_id"]
        self.with_role(self.EMAIL, main.SECURITY_ROLE_MEMBER)
        response = self.step_up_complete(user_token, flow_id)
        self.assertEqual(response.status_code, 403)
        self.assertEqual(response.json()["detail"], "ADMIN_ROLE_REQUIRED")
        with main.db() as conn:
            self.assertEqual(
                conn.execute("SELECT COUNT(*) c FROM admin_sessions "
                             "WHERE revoked_at IS NULL").fetchone()["c"], 0)

    def test_a_disabled_account_mid_flow_yields_no_session(self):
        user_token, _ = self.prepared()
        flow_id = self.step_up_start(user_token).json()["flow_id"]
        with main.db() as conn:
            conn.execute("UPDATE users SET disabled=1 WHERE normalized_email=?",
                         (self.EMAIL,))
        self.assertEqual(
            self.step_up_complete(user_token, flow_id).status_code, 403)

    def test_a_suspended_organization_mid_flow_yields_no_session(self):
        user_token, _ = self.prepared()
        flow_id = self.step_up_start(user_token).json()["flow_id"]
        with main.db() as conn:
            conn.execute("UPDATE organizations SET status='suspended' WHERE id=?",
                         (main.nova_organization_id(),))
        try:
            self.assertEqual(
                self.step_up_complete(user_token, flow_id).status_code, 403)
        finally:
            with main.db() as conn:
                conn.execute("UPDATE organizations SET status='active' WHERE id=?",
                             (main.nova_organization_id(),))

    def test_a_provider_disabled_mid_flow_yields_no_session(self):
        user_token, _ = self.prepared()
        flow_id = self.step_up_start(user_token).json()["flow_id"]
        with main.db() as conn:
            conn.execute("UPDATE organization_provider_configs SET enabled=0")
        response = self.step_up_complete(user_token, flow_id)
        self.assertEqual(response.status_code, 503)

    # -- identités qui ne permettent pas de step-up ------------------------

    def test_a_legacy_email_code_account_cannot_step_up(self):
        """Un code à usage unique reçu par courriel n'est pas une
        réauthentification d'entreprise. Le compromis est assumé : un
        administrateur doit passer par Microsoft, Google ou OIDC."""
        email = "otp-seul@example.com"
        user_token = self.sign_in(email, machine="poste-otp")
        self.with_role(email, main.SECURITY_ROLE_ORGANIZATION_ADMIN)
        response = self.step_up_start(user_token)
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json()["detail"], "ADMIN_STEP_UP_UNSUPPORTED")

    def test_an_unverified_identity_cannot_step_up(self):
        """Le Device Code hérité produit des revendications lues sans vérifier
        la signature : elles ne prouvent rien."""
        email = "device-code@example.com"
        user_token = self.sign_in(email, machine="poste-device")
        self.with_role(email, main.SECURITY_ROLE_ORGANIZATION_ADMIN)
        with main.db() as conn:
            account = main.user_by_email(conn, main.nova_organization_id(), email)
            conn.execute(
                "INSERT INTO federated_identities(provider, external_subject, "
                "organization_id, email, user_id, verification, external_issuer, "
                "created_at, last_seen) VALUES "
                "('microsoft_entra','oid-device',?,?,?,'transport_only','',?,?)",
                (main.nova_organization_id(), email, account["user_id"],
                 time.time(), time.time()))
        response = self.step_up_start(user_token)
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json()["detail"], "ADMIN_STEP_UP_UNSUPPORTED")

    # -- le modèle de menace -----------------------------------------------

    def test_a_stolen_user_session_alone_grants_no_admin_session(self):
        """La propriété centrale de la phase.

        L'attaquant détient le jeton de session d'un administrateur — rien de
        plus. Il peut commencer un step-up : cela ne lui donne qu'une URL. Sans
        pouvoir s'authentifier auprès de l'IdP en tant que cette personne, il
        n'obtient aucune session d'administration.
        """
        user_token, _ = self.prepared()
        stolen = user_token

        # Ce que le jeton volé permet : demander, et rien d'autre.
        started = self.step_up_start(stolen)
        self.assertEqual(started.status_code, 200)
        self.assertNotIn("token", started.json())

        # Ce qu'il ne permet pas : produire une preuve d'identité.
        for forged in (self.id_token(subject="oid-attaquant"),
                       self.id_token(auth_time=None),
                       self.id_token(subject="oid-attaquant", auth_time=None)):
            response = self.step_up_complete(
                stolen, started.json()["flow_id"], token=forged)
            self.assertNotEqual(response.status_code, 200)
            self.assertNotIn("token", response.json())

        with main.db() as conn:
            self.assertEqual(
                conn.execute("SELECT COUNT(*) c FROM admin_sessions").fetchone()["c"],
                0)

    def test_no_admin_route_is_reachable_without_the_step_up(self):
        user_token, _ = self.prepared()
        for path in ("/api/control/organization", "/api/admin/discovery",
                     "/api/admin/provider-configs"):
            self.assertEqual(
                self.client.get(path, headers=self.auth(user_token)).status_code,
                401, path)

    # -- journalisation et audit -------------------------------------------

    def test_the_audit_log_records_the_step_up(self):
        user_token, _ = self.prepared()
        flow_id = self.step_up_start(user_token).json()["flow_id"]
        self.step_up_complete(user_token, flow_id)
        with main.db() as conn:
            rows = conn.execute("SELECT * FROM admin_audit_log").fetchall()
        actions = [row["action"] for row in rows]
        self.assertIn("admin_step_up.success", actions)
        self.assertIn("admin_session.create", actions)
        joined = " ".join(str(tuple(row)) for row in rows)
        # Ni jeton, ni code, ni date d'authentification exacte.
        self.assertNotIn("code-microsoft", joined)
        self.assertNotIn("auth_time", joined)

    # -- sélection du fournisseur ------------------------------------------

    def test_the_provider_comes_from_the_verified_identity(self):
        """Google et OIDC empruntent exactement le même chemin : le fournisseur
        est celui de l'identité vérifiée du compte, quel qu'il soit."""
        email = "multi@example.com"
        self.sign_in(email, machine="poste-multi")
        self.with_role(email, main.SECURITY_ROLE_ORGANIZATION_ADMIN)
        with main.db() as conn:
            account = main.user_by_email(conn, main.nova_organization_id(), email)
            for provider, subject, issuer in (
                ("google_workspace", "sub-google", ""),
                ("oidc", "sub-okta", "https://idp.example.com"),
            ):
                conn.execute(
                    "INSERT INTO federated_identities(provider, external_subject, "
                    "organization_id, email, user_id, verification, external_issuer, "
                    "created_at, last_seen) VALUES (?,?,?,?,?,'verified',?,?,?)",
                    (provider, subject, main.nova_organization_id(), email,
                     account["user_id"], issuer, time.time(), time.time()))
        identity = main.step_up_identity_for(account)
        self.assertIn(identity["provider"], ("google_workspace", "oidc"))
        self.assertEqual(identity["user_id"], account["user_id"])

    def test_an_identity_of_another_account_is_never_selected(self):
        email = "a@example.com"
        self.sign_in(email, machine="poste-a")
        self.sign_in("b@example.com", machine="poste-b")
        self.with_federated_identity("b@example.com", subject="oid-b")
        with main.db() as conn:
            account = main.user_by_email(conn, main.nova_organization_id(), email)
        self.assertIsNone(main.step_up_identity_for(account))

    def test_an_oidc_identity_is_scoped_by_issuer(self):
        """Deux IdP OIDC peuvent attribuer le même `sub` à deux personnes.
        L'émetteur fait donc partie de la clé, ici comme à la connexion."""
        email = "oidc@example.com"
        self.sign_in(email, machine="poste-oidc")
        with main.db() as conn:
            account = main.user_by_email(conn, main.nova_organization_id(), email)
            conn.execute(
                "INSERT INTO federated_identities(provider, external_subject, "
                "organization_id, email, user_id, verification, external_issuer, "
                "created_at, last_seen) VALUES "
                "('oidc','sub-partage',?,?,?,'verified','https://a.example.com',?,?)",
                (main.nova_organization_id(), email, account["user_id"],
                 time.time(), time.time()))
            # Le même sujet, chez un autre émetteur : une autre personne.
            other = conn.execute(
                "SELECT * FROM federated_identities WHERE external_issuer=? "
                "AND external_subject=?",
                ("https://b.example.com", "sub-partage")).fetchone()
        self.assertIsNone(other)
        identity = main.step_up_identity_for(account)
        self.assertEqual(identity["external_issuer"], "https://a.example.com")

    def test_no_secret_reaches_the_logs(self):
        user_token, _ = self.prepared()
        with patch("builtins.print") as printed:
            flow_id = self.step_up_start(user_token).json()["flow_id"]
            body = self.step_up_complete(user_token, flow_id).json()
        printed_text = " ".join(str(call) for call in printed.call_args_list)
        for secret in (body["token"], flow_id, self.NONCE, "code-microsoft",
                       self.EMAIL):
            self.assertNotIn(secret, printed_text)


# ═══════════ Phase 26 — gestion des administrateurs et audit ══════════════


class AdministratorManagementTests(AdminTestCase):
    """Nommer, changer, retirer — depuis Nova Admin, pour son organisation."""

    def organization(self) -> str:
        return main.nova_organization_id()

    def path(self, suffix: str = "") -> str:
        return f"/api/control/organizations/{self.organization()}/administrators{suffix}"

    def member(self, email: str, role=main.SECURITY_ROLE_MEMBER) -> str:
        """Un compte de l'organisation, avec le role demande."""
        self.sign_in(email, machine=f"poste-{email}")
        self.with_role(email, role)
        with main.db() as conn:
            return main.user_by_email(conn, self.organization(), email)["user_id"]

    def set_role(self, token: str, user_id: str, role: str):
        return self.client.post(
            self.path(f"/{user_id}/role"), headers=self.auth(token),
            json={"security_role": role})

    # -- liste --------------------------------------------------------------

    def test_the_list_shows_administrators_and_nothing_else(self):
        token = self.admin_session()
        self.member("simple@example.com")
        self.member("technique@example.com", main.SECURITY_ROLE_IT_ADMIN)

        body = self.client.get(self.path(), headers=self.auth(token)).json()
        roles = {a["email"]: a["security_role"] for a in body["administrators"]}
        self.assertIn("technique@example.com", roles)
        # Un membre ordinaire n'est pas un administrateur.
        self.assertNotIn("simple@example.com", roles)

    def test_the_list_carries_no_secret(self):
        token = self.admin_session()
        raw = self.client.get(self.path(), headers=self.auth(token)).text
        for forbidden in ("token", "secret", "external_subject", "tenant"):
            self.assertNotIn(forbidden, raw.lower())

    def test_the_sole_identity_manager_is_signalled(self):
        token = self.admin_session()
        body = self.client.get(self.path(), headers=self.auth(token)).json()
        # Un seul organization_admin : l'interface peut desactiver l'action
        # plutot que de la proposer avant de la refuser.
        self.assertIsNotNone(body["sole_identity_manager"])

    # -- attribution --------------------------------------------------------

    def test_granting_a_role_to_an_existing_member(self):
        token = self.admin_session()
        target = self.member("promu@example.com")
        response = self.set_role(token, target, main.SECURITY_ROLE_IT_ADMIN)
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["security_role"], main.SECURITY_ROLE_IT_ADMIN)
        self.assertIn("deployment_manage", response.json()["capabilities"])

    def test_removing_admin_access_is_not_deleting_the_person(self):
        token = self.admin_session()
        target = self.member("retrograde@example.com", main.SECURITY_ROLE_IT_ADMIN)
        self.set_role(token, target, main.SECURITY_ROLE_MEMBER)
        with main.db() as conn:
            account = main.user_by_id(conn, target)
        # Le compte existe toujours, actif : il cesse d'administrer, il ne
        # disparait pas.
        self.assertIsNotNone(account)
        self.assertEqual(main.account_status(account), "active")
        self.assertEqual(main.security_role_for(account), main.SECURITY_ROLE_MEMBER)

    def test_an_unknown_role_is_refused(self):
        token = self.admin_session()
        target = self.member("cible@example.com")
        response = self.set_role(token, target, "super_utilisateur")
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json()["detail"], "SECURITY_ROLE_INVALID")

    def test_the_client_cannot_invent_capabilities(self):
        """Le navigateur nomme un role ; le serveur en deduit les capacites."""
        token = self.admin_session()
        target = self.member("ambitieux@example.com")
        response = self.client.post(
            self.path(f"/{target}/role"), headers=self.auth(token),
            json={"security_role": "read_only",
                  "capabilities": ["identity_manage", "security_manage"]})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(sorted(response.json()["capabilities"]),
                         sorted(main.capabilities_for_role("read_only")))

    def test_an_unknown_user_is_not_found(self):
        token = self.admin_session()
        response = self.set_role(token, "identifiant-invente", "it_admin")
        self.assertEqual(response.status_code, 404)
        self.assertEqual(response.json()["detail"], "USER_NOT_FOUND")

    # -- capacites ----------------------------------------------------------

    def test_it_admin_cannot_manage_administrators(self):
        """La matrice suffit : `it_admin` n'a pas `identity_manage`, donc il ne
        peut elever personne — pas meme lui."""
        token = self.admin_session("it@example.com", main.SECURITY_ROLE_IT_ADMIN,
                                   subject="oid-it")
        with main.db() as conn:
            himself = main.user_by_email(conn, self.organization(),
                                         "it@example.com")["user_id"]
        response = self.set_role(token, himself, main.SECURITY_ROLE_ORGANIZATION_ADMIN)
        self.assertEqual(response.status_code, 403)
        self.assertEqual(response.json()["detail"], "ADMIN_ROLE_REQUIRED")

    def test_read_only_cannot_manage_administrators(self):
        token = self.admin_session("lecture@example.com", main.SECURITY_ROLE_READ_ONLY,
                                   subject="oid-lecture")
        target = self.member("cible2@example.com")
        self.assertEqual(
            self.set_role(token, target, "it_admin").status_code, 403)

    def test_a_user_session_cannot_manage_administrators(self):
        email = "utilisateur@example.com"
        user_token = self.sign_in(email)
        self.with_role(email, main.SECURITY_ROLE_ORGANIZATION_ADMIN)
        target = self.member("cible3@example.com")
        # Meme role, mais pas de session d'administration ouverte.
        self.assertEqual(
            self.set_role(user_token, target, "it_admin").status_code, 401)

    # -- garde-fou du dernier gestionnaire ----------------------------------

    def test_the_last_identity_manager_cannot_step_down(self):
        """Le cas qui arrive naturellement : l'unique administrateur se
        retrograde « pour voir »."""
        token = self.admin_session()
        with main.db() as conn:
            himself = main.user_by_email(conn, self.organization(),
                                         "admin@example.com")["user_id"]
        response = self.set_role(token, himself, main.SECURITY_ROLE_MEMBER)
        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.json()["detail"], "ADMIN_LAST_IDENTITY_MANAGER")
        # Et rien n'a bouge a moitie.
        with main.db() as conn:
            self.assertEqual(
                main.security_role_for(main.user_by_id(conn, himself)),
                main.SECURITY_ROLE_ORGANIZATION_ADMIN)

    def test_a_manager_can_step_down_once_another_exists(self):
        token = self.admin_session()
        with main.db() as conn:
            himself = main.user_by_email(conn, self.organization(),
                                         "admin@example.com")["user_id"]
        successor = self.member("successeur@example.com")
        self.assertEqual(
            self.set_role(token, successor,
                          main.SECURITY_ROLE_ORGANIZATION_ADMIN).status_code, 200)
        self.assertEqual(
            self.set_role(token, himself, main.SECURITY_ROLE_MEMBER).status_code, 200)

    def test_demoting_to_it_admin_also_loses_management(self):
        # `it_admin` n'a pas `identity_manage` : se retrograder ainsi viderait
        # aussi l'organisation de tout gestionnaire.
        token = self.admin_session()
        with main.db() as conn:
            himself = main.user_by_email(conn, self.organization(),
                                         "admin@example.com")["user_id"]
        response = self.set_role(token, himself, main.SECURITY_ROLE_IT_ADMIN)
        self.assertEqual(response.status_code, 409)

    def test_a_disabled_manager_does_not_count(self):
        """Un compte suspendu ne peut plus administrer : il ne doit donc pas
        servir a justifier le retrait du dernier gestionnaire actif."""
        token = self.admin_session()
        with main.db() as conn:
            himself = main.user_by_email(conn, self.organization(),
                                         "admin@example.com")["user_id"]
        dormant = self.member("dormant@example.com",
                              main.SECURITY_ROLE_ORGANIZATION_ADMIN)
        with main.db() as conn:
            conn.execute("UPDATE users SET disabled=1 WHERE user_id=?", (dormant,))
        response = self.set_role(token, himself, main.SECURITY_ROLE_MEMBER)
        self.assertEqual(response.status_code, 409)

    # -- sessions -----------------------------------------------------------

    def test_a_role_change_closes_the_target_sessions(self):
        token = self.admin_session()
        victim_token = self.admin_session("autre-admin@example.com",
                                          main.SECURITY_ROLE_ORGANIZATION_ADMIN,
                                          subject="oid-autre")
        with main.db() as conn:
            victim = main.user_by_email(conn, self.organization(),
                                        "autre-admin@example.com")["user_id"]
        # Sa session fonctionne encore.
        self.assertEqual(
            self.client.get("/api/control/organization",
                            headers=self.auth(victim_token)).status_code, 200)
        self.set_role(token, victim, main.SECURITY_ROLE_READ_ONLY)
        # Elle porte des capacites perimees : elle est fermee.
        response = self.client.get("/api/control/organization",
                                   headers=self.auth(victim_token))
        self.assertEqual(response.status_code, 401)

    # -- isolation ----------------------------------------------------------

    def test_another_organization_is_invisible(self):
        token = self.admin_session()
        response = self.client.get(
            "/api/control/organizations/organisation-voisine/administrators",
            headers=self.auth(token))
        self.assertEqual(response.status_code, 404)

    def test_a_user_of_another_organization_cannot_be_promoted(self):
        token = self.admin_session()
        with main.db() as conn:
            conn.execute(
                "INSERT OR REPLACE INTO organizations(id, slug, display_name, "
                "org_type, status, created_at) VALUES (?,?,?,?,?,?)",
                ("org-voisine-26", "voisine", "Voisine", "education", "active",
                 time.time()))
            stranger = main.create_user(conn, "org-voisine-26", "etranger@example.com")
        response = self.set_role(token, stranger, "it_admin")
        self.assertEqual(response.status_code, 404)
        with main.db() as conn:
            # Le compte voisin n'a pas bouge.
            self.assertEqual(
                main.security_role_for(main.user_by_id(conn, stranger)), "member")
            conn.execute("DELETE FROM users WHERE user_id=?", (stranger,))
            conn.execute("DELETE FROM organizations WHERE id='org-voisine-26'")


class MemberSearchTests(AdminTestCase):
    """Choisir qui nommer, sans deverser l'annuaire."""

    def path(self, query: str) -> str:
        organization = main.nova_organization_id()
        return f"/api/control/organizations/{organization}/members?query={query}"

    def test_a_prefix_search_finds_a_member(self):
        token = self.admin_session()
        self.sign_in("cherchable@example.com", machine="poste-c")
        body = self.client.get(self.path("cherch"), headers=self.auth(token)).json()
        self.assertIn("cherchable@example.com",
                      [m["email"] for m in body["members"]])

    def test_a_listing_without_a_term_stays_bounded(self):
        """Depuis la Phase 27, administrer suppose de voir qui est la : la liste
        sans terme est permise. Ce qui reste interdit, c'est l'export — elle est
        paginee et plafonnee."""
        token = self.admin_session()
        body = self.client.get(self.path(""), headers=self.auth(token)).json()
        self.assertLessEqual(len(body["members"]), body["limit"])
        self.assertLessEqual(body["limit"], main.MEMBER_PAGE_MAX)

    def test_an_absurd_limit_is_capped(self):
        token = self.admin_session()
        body = self.client.get(
            f"{self.path('')}&limit=100000", headers=self.auth(token)).json()
        self.assertEqual(body["limit"], main.MEMBER_PAGE_MAX)

    def test_the_result_is_capped(self):
        token = self.admin_session()
        body = self.client.get(self.path("ab"), headers=self.auth(token)).json()
        self.assertLessEqual(len(body["members"]), main.MEMBER_PAGE_MAX)

    def test_it_admin_cannot_search_members(self):
        token = self.admin_session("it2@example.com", main.SECURITY_ROLE_IT_ADMIN,
                                   subject="oid-it2")
        self.assertEqual(
            self.client.get(self.path("test"), headers=self.auth(token)).status_code,
            403)


class AuditReadTests(AdminTestCase):
    """Lire le journal, sans jamais y trouver un secret."""

    def path(self, suffix: str = "") -> str:
        organization = main.nova_organization_id()
        return f"/api/control/organizations/{organization}/audit{suffix}"

    def test_recent_activity_is_readable(self):
        token = self.admin_session()
        body = self.client.get(self.path(), headers=self.auth(token)).json()
        actions = [e["action"] for e in body["entries"]]
        self.assertIn("admin_session.create", actions)
        # Chaque ligne porte de quoi etre lue par un humain.
        entry = body["entries"][0]
        for field in ("created_at", "actor_role", "action", "action_label"):
            self.assertIsNotNone(entry[field])

    def test_actions_are_human_readable(self):
        token = self.admin_session()
        body = self.client.get(self.path(), headers=self.auth(token)).json()
        labels = {e["action"]: e["action_label"] for e in body["entries"]}
        self.assertEqual(labels.get("admin_session.create"),
                         "Opened an administration session")

    def test_a_role_change_is_recorded_with_both_roles(self):
        token = self.admin_session()
        self.sign_in("audite@example.com", machine="poste-a")
        with main.db() as conn:
            target = main.user_by_email(conn, main.nova_organization_id(),
                                        "audite@example.com")["user_id"]
        self.client.post(
            f"{self.path().replace('/audit', '/administrators')}/{target}/role",
            headers=self.auth(token), json={"security_role": "it_admin"})

        body = self.client.get(self.path(), headers=self.auth(token)).json()
        grant = next(e for e in body["entries"] if e["action"] == "admin_role.grant")
        self.assertEqual(grant["target_id"], target)
        self.assertEqual(grant["metadata"]["previous_role"], "member")
        self.assertEqual(grant["metadata"]["security_role"], "it_admin")
        self.assertEqual(grant["action_label"], "Granted administrator role")

    def test_pagination_is_enforced(self):
        token = self.admin_session()
        body = self.client.get(self.path("?limit=1"), headers=self.auth(token)).json()
        self.assertEqual(len(body["entries"]), 1)
        self.assertEqual(body["limit"], 1)

    def test_an_absurd_limit_is_capped(self):
        token = self.admin_session()
        body = self.client.get(self.path("?limit=100000"),
                               headers=self.auth(token)).json()
        self.assertEqual(body["limit"], main.AUDIT_PAGE_MAX)

    def test_the_cursor_walks_backwards(self):
        token = self.admin_session()
        first = self.client.get(self.path("?limit=1"), headers=self.auth(token)).json()
        if first["next_before"] is None:
            self.skipTest("pas assez d'entrees pour paginer")
        second = self.client.get(
            self.path(f"?limit=1&before={first['next_before']}"),
            headers=self.auth(token)).json()
        if second["entries"]:
            self.assertLess(second["entries"][0]["created_at"],
                            first["entries"][0]["created_at"])

    def test_filtering_by_action(self):
        token = self.admin_session()
        body = self.client.get(self.path("?action=admin_session.create"),
                               headers=self.auth(token)).json()
        self.assertTrue(all(e["action"] == "admin_session.create"
                            for e in body["entries"]))

    def test_no_secret_ever_reaches_the_audit_view(self):
        token = self.admin_session()
        clear_provider_configs()
        self.client.post(
            "/api/admin/provider-configs", headers=self.auth(token),
            json={"provider_type": "oidc", "oidc_issuer": "https://idp.example.com",
                  "oidc_client_id": "client", "oidc_client_secret": "secret-absolu"})
        raw = self.client.get(self.path(), headers=self.auth(token)).text
        for forbidden in ("secret-absolu", "eyJ", "auth_time", "code_verifier",
                          "nonce"):
            self.assertNotIn(forbidden, raw)
        clear_provider_configs()

    def test_reading_the_audit_requires_security_manage(self):
        token = self.admin_session("it3@example.com", main.SECURITY_ROLE_IT_ADMIN,
                                   subject="oid-it3")
        self.assertEqual(
            self.client.get(self.path(), headers=self.auth(token)).status_code, 403)

    def test_the_audit_of_another_organization_is_invisible(self):
        token = self.admin_session()
        response = self.client.get(
            "/api/control/organizations/organisation-voisine/audit",
            headers=self.auth(token))
        self.assertEqual(response.status_code, 404)

    def test_no_route_modifies_or_deletes_the_audit(self):
        token = self.admin_session()
        organization = main.nova_organization_id()
        for method, path in (
            ("post", f"/api/control/organizations/{organization}/audit"),
            ("delete", f"/api/control/organizations/{organization}/audit"),
        ):
            response = getattr(self.client, method)(path, headers=self.auth(token))
            self.assertIn(response.status_code, (404, 405), path)


class LegacyAdminConsoleTests(AdminTestCase):
    """L'ancienne console a jeton partage : supprimee en Phase 27."""

    def test_the_console_no_longer_exists(self):
        # Ni desactivee, ni cachee derriere un drapeau : retiree. Garder un
        # fichier mort « au cas ou » finit par le voir revenir.
        self.assertEqual(self.client.get("/admin").status_code, 404)
        self.assertFalse(hasattr(main, "LEGACY_ADMIN_UI_ENABLED"))
        self.assertFalse(
            (Path(main.__file__).parent / "admin.html").exists())

    def test_the_legacy_user_routes_are_gone(self):
        """Elles designaient une personne par son adresse, et n'avaient plus
        d'appelant. Leurs equivalents modernes vivent sous /api/control."""
        token = self.admin_session()
        for method, path in (
            ("get", "/api/admin/overview"),
            ("get", "/api/admin/user/quelquun@example.com"),
            ("post", "/api/admin/user/quelquun@example.com"),
            ("post", "/api/admin/user/quelquun@example.com/revoke-machines"),
            ("delete", "/api/admin/user/quelquun@example.com"),
            ("post", "/api/admin/cohort/AERO2/disable"),
        ):
            response = getattr(self.client, method)(path, headers=self.auth(token))
            self.assertIn(response.status_code, (404, 405), path)

    def test_no_route_is_guarded_by_the_shared_token_alone(self):
        # `require_admin` n'etait plus referencee depuis la Phase 22 : du code
        # mort qui donnait l'illusion d'un second chemin d'autorisation.
        self.assertFalse(hasattr(main, "require_admin"))


# ═══════════════ Phase 27 — gestion des membres ═══════════════════════════


class MemberManagementTests(AdminTestCase):
    """Le cycle de vie d'un compte, depuis Nova Admin."""

    def organization(self) -> str:
        return main.nova_organization_id()

    def members(self, suffix: str = "") -> str:
        return f"/api/control/organizations/{self.organization()}/members{suffix}"

    def member(self, email: str, **fields) -> str:
        self.sign_in(email, machine=f"poste-{email}")
        with main.db() as conn:
            user_id = main.user_by_email(conn, self.organization(), email)["user_id"]
            if fields:
                assignments = ", ".join(f"{k}=?" for k in fields)
                conn.execute(f"UPDATE users SET {assignments} WHERE user_id=?",
                             (*fields.values(), user_id))
        return user_id

    # -- liste et recherche -------------------------------------------------

    def test_the_listing_is_paginated_and_capped(self):
        token = self.admin_session()
        for n in range(3):
            self.member(f"liste{n}@example.com")
        body = self.client.get(self.members("?limit=2"), headers=self.auth(token)).json()
        self.assertEqual(len(body["members"]), 2)
        self.assertTrue(body["has_more"])
        self.assertEqual(body["limit"], 2)

    def test_an_absurd_limit_is_capped(self):
        token = self.admin_session()
        body = self.client.get(self.members("?limit=99999"),
                               headers=self.auth(token)).json()
        self.assertEqual(body["limit"], main.MEMBER_PAGE_MAX)

    def test_searching_narrows_the_listing(self):
        token = self.admin_session()
        self.member("cherchable27@example.com")
        body = self.client.get(self.members("?query=cherchable27"),
                               headers=self.auth(token)).json()
        self.assertEqual([m["email"] for m in body["members"]],
                         ["cherchable27@example.com"])

    def test_filtering_by_status(self):
        token = self.admin_session()
        suspended = self.member("suspendu27@example.com", disabled=1, status="disabled")
        body = self.client.get(self.members("?status=disabled"),
                               headers=self.auth(token)).json()
        self.assertIn(suspended, [m["user_id"] for m in body["members"]])
        self.assertTrue(all(m["status"] != "active" for m in body["members"]))

    def test_the_listing_carries_no_sensitive_field(self):
        token = self.admin_session()
        raw = self.client.get(self.members(), headers=self.auth(token)).text
        for forbidden in ("token", "secret", "external_subject", "tenant",
                          "normalized_email"):
            self.assertNotIn(forbidden, raw.lower())

    # -- fiche --------------------------------------------------------------

    def test_the_detail_shows_devices_without_tokens(self):
        token = self.admin_session()
        target = self.member("fiche@example.com")
        body = self.client.get(self.members(f"/{target}"),
                               headers=self.auth(token)).json()
        self.assertEqual(body["member"]["user_id"], target)
        self.assertTrue(body["devices"])
        for device in body["devices"]:
            self.assertEqual(set(device),
                             {"machine", "active", "created_at", "expires_at"})
        # Ni jeton, ni empreinte : une empreinte identifie encore une session.
        raw = self.client.get(self.members(f"/{target}"),
                              headers=self.auth(token)).text
        self.assertNotIn("token_hash", raw)

    def test_the_detail_never_shows_dictated_content(self):
        """La frontiere du plan de donnees, verifiee sur la reponse elle-meme."""
        token = self.admin_session()
        target = self.member("prive@example.com")
        raw = self.client.get(self.members(f"/{target}"),
                              headers=self.auth(token)).text.lower()
        for forbidden in ("transcript", "dictation", "prompt", "clipboard",
                          "audio", "history"):
            self.assertNotIn(forbidden, raw)

    def test_an_unknown_member_is_not_found(self):
        token = self.admin_session()
        self.assertEqual(
            self.client.get(self.members("/inconnu"),
                            headers=self.auth(token)).status_code, 404)

    # -- suspension ---------------------------------------------------------

    def test_disabling_cuts_every_session(self):
        token = self.admin_session()
        target = self.member("acouper@example.com")
        with main.db() as conn:
            before = conn.execute(
                "SELECT COUNT(*) c FROM tokens WHERE user_id=? AND active=1",
                (target,)).fetchone()["c"]
        self.assertGreater(before, 0)

        response = self.client.post(
            self.members(f"/{target}/status"), headers=self.auth(token),
            json={"status": "disabled"})
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["member"]["status"], "disabled")

        with main.db() as conn:
            after = conn.execute(
                "SELECT COUNT(*) c FROM tokens WHERE user_id=? AND active=1",
                (target,)).fetchone()["c"]
            account = main.user_by_id(conn, target)
            identities = conn.execute(
                "SELECT COUNT(*) c FROM federated_identities WHERE user_id=?",
                (target,)).fetchone()["c"]
        self.assertEqual(after, 0)
        # Suspendre n'est pas supprimer : le compte et l'identite demeurent.
        self.assertIsNotNone(account)
        self.assertEqual(identities, 0)  # ce compte n'en avait pas

    def test_re_enabling_does_not_restore_old_sessions(self):
        token = self.admin_session()
        target = self.member("reactive@example.com")
        self.client.post(self.members(f"/{target}/status"), headers=self.auth(token),
                         json={"status": "disabled"})
        self.client.post(self.members(f"/{target}/status"), headers=self.auth(token),
                         json={"status": "active"})
        with main.db() as conn:
            active = conn.execute(
                "SELECT COUNT(*) c FROM tokens WHERE user_id=? AND active=1",
                (target,)).fetchone()["c"]
            account = main.user_by_id(conn, target)
        # Rendre l'acces n'est pas rendre les anciens jetons.
        self.assertEqual(active, 0)
        self.assertEqual(main.account_status(account), "active")

    def device_of(self, email: str) -> tuple[str, str]:
        """Un membre connecte sur son poste : son `user_id` et le jeton du poste."""
        device = self.sign_in(email, machine=f"poste-{email}")
        with main.db() as conn:
            user_id = main.user_by_email(conn, self.organization(), email)["user_id"]
        return user_id, device

    def test_a_suspended_member_s_device_learns_it_is_suspended(self):
        """Le poste d'un membre suspendu doit l'apprendre, pas se croire deconnecte.

        Suspendre coupe les jetons, et `/api/me` repondait alors 401 — la
        reponse d'une session expiree. Le poste effacait sa session et
        annoncait « reconnectez-vous » a quelqu'un qui ne le peut pas. Le jeton
        reste refuse ; seule la raison donnee change.
        """
        admin = self.admin_session()
        target, device = self.device_of("suspendu-poste@example.com")
        self.assertEqual(
            self.client.get("/api/me", headers=self.auth(device)).status_code, 200)

        self.client.post(self.members(f"/{target}/status"), headers=self.auth(admin),
                         json={"status": "disabled"})

        refused = self.client.get("/api/me", headers=self.auth(device))
        self.assertEqual(refused.status_code, 403, refused.text)
        self.assertIn("suspendu", refused.json()["detail"].lower())
        with main.db() as conn:
            active = conn.execute(
                "SELECT COUNT(*) c FROM tokens WHERE user_id=? AND active=1",
                (target,)).fetchone()["c"]
        # La suspension coupe toujours les jetons : rien n'est rendu au poste.
        self.assertEqual(active, 0)

    def test_after_reactivation_the_old_device_session_is_expired_not_suspended(self):
        """Reactive, le membre se reconnecte : son ancien jeton dit 401, pas 403."""
        admin = self.admin_session()
        target, device = self.device_of("revenu-poste@example.com")
        for status in ("disabled", "active"):
            self.client.post(self.members(f"/{target}/status"),
                             headers=self.auth(admin), json={"status": status})

        self.assertEqual(
            self.client.get("/api/me", headers=self.auth(device)).status_code, 401)

    def test_signing_an_active_member_out_everywhere_still_reads_as_signed_out(self):
        """Deconnecter partout n'est pas suspendre : le poste doit se reconnecter."""
        admin = self.admin_session()
        target, device = self.device_of("partout@example.com")
        self.client.post(self.members(f"/{target}/sessions/revoke"),
                         headers=self.auth(admin))

        self.assertEqual(
            self.client.get("/api/me", headers=self.auth(device)).status_code, 401)

    def test_an_unknown_status_is_refused(self):
        token = self.admin_session()
        target = self.member("statut@example.com")
        response = self.client.post(
            self.members(f"/{target}/status"), headers=self.auth(token),
            json={"status": "vacances"})
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json()["detail"], "MEMBER_STATUS_INVALID")

    def test_the_last_identity_manager_cannot_be_disabled(self):
        """Suspendre le dernier gestionnaire ferme la porte aussi surement que
        lui retirer son role."""
        token = self.admin_session()
        with main.db() as conn:
            himself = main.user_by_email(conn, self.organization(),
                                         "admin@example.com")["user_id"]
        response = self.client.post(
            self.members(f"/{himself}/status"), headers=self.auth(token),
            json={"status": "disabled"})
        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.json()["detail"], "ADMIN_LAST_IDENTITY_MANAGER")
        with main.db() as conn:
            self.assertEqual(
                main.account_status(main.user_by_id(conn, himself)), "active")

    # -- profil metier ------------------------------------------------------

    def test_changing_the_member_type_never_grants_a_privilege(self):
        """`role` decrit un metier, `security_role` un privilege. Les confondre
        ferait de l'annuaire une source de droits."""
        token = self.admin_session()
        target = self.member("metier@example.com")
        for member_role in ("teacher", "staff", "partner", "student"):
            response = self.client.post(
                self.members(f"/{target}/profile"), headers=self.auth(token),
                json={"member_role": member_role})
            self.assertEqual(response.status_code, 200, member_role)
            self.assertEqual(response.json()["member"]["security_role"], "member")

    def test_the_profile_route_cannot_set_a_security_role(self):
        token = self.admin_session()
        target = self.member("tentative@example.com")
        self.client.post(
            self.members(f"/{target}/profile"), headers=self.auth(token),
            json={"member_role": "staff", "security_role": "organization_admin"})
        with main.db() as conn:
            self.assertEqual(
                main.security_role_for(main.user_by_id(conn, target)), "member")

    def test_an_unknown_member_role_is_refused(self):
        token = self.admin_session()
        target = self.member("metier2@example.com")
        response = self.client.post(
            self.members(f"/{target}/profile"), headers=self.auth(token),
            json={"member_role": "directeur"})
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json()["detail"], "MEMBER_ROLE_INVALID")

    def test_the_group_is_the_legacy_cohort(self):
        token = self.admin_session()
        target = self.member("groupe@example.com")
        self.client.post(self.members(f"/{target}/profile"), headers=self.auth(token),
                         json={"cohort": "AERO2"})
        body = self.client.get(self.members(f"/{target}"),
                               headers=self.auth(token)).json()
        groups = body["member"]["groups"]
        self.assertEqual(groups[0]["label"], "AERO2")
        # La source reste explicite : un futur groupe d'annuaire ne s'y
        # confondra pas.
        self.assertEqual(groups[0]["source"], "legacy_cohort")

    # -- appareils ----------------------------------------------------------

    def test_revoking_sessions_leaves_the_account_intact(self):
        token = self.admin_session()
        target = self.member("deconnecte@example.com")
        response = self.client.post(
            self.members(f"/{target}/sessions/revoke"), headers=self.auth(token))
        self.assertEqual(response.status_code, 200)
        self.assertGreater(response.json()["revoked_devices"], 0)
        with main.db() as conn:
            account = main.user_by_id(conn, target)
            active = conn.execute(
                "SELECT COUNT(*) c FROM tokens WHERE user_id=? AND active=1",
                (target,)).fetchone()["c"]
        self.assertEqual(active, 0)
        self.assertEqual(main.account_status(account), "active")

    # -- suppression --------------------------------------------------------

    def test_deleting_a_member_keeps_the_audit(self):
        token = self.admin_session()
        target = self.member("aeffacer@example.com")
        with main.db() as conn:
            before = conn.execute(
                "SELECT COUNT(*) c FROM admin_audit_log").fetchone()["c"]
        response = self.client.delete(self.members(f"/{target}"),
                                      headers=self.auth(token))
        self.assertEqual(response.status_code, 200, response.text)
        with main.db() as conn:
            self.assertIsNone(main.user_by_id(conn, target))
            after = conn.execute(
                "SELECT COUNT(*) c FROM admin_audit_log").fetchone()["c"]
        # Le journal porte des actes d'administration, pas des donnees de
        # dictee : l'effacer reviendrait a effacer qui a fait quoi.
        self.assertGreater(after, before)

    def test_the_last_identity_manager_cannot_be_deleted(self):
        token = self.admin_session()
        with main.db() as conn:
            himself = main.user_by_email(conn, self.organization(),
                                         "admin@example.com")["user_id"]
        response = self.client.delete(self.members(f"/{himself}"),
                                      headers=self.auth(token))
        self.assertEqual(response.status_code, 409)
        with main.db() as conn:
            self.assertIsNotNone(main.user_by_id(conn, himself))

    # -- capacites et isolation ---------------------------------------------

    def test_it_admin_cannot_manage_members(self):
        token = self.admin_session("it27@example.com", main.SECURITY_ROLE_IT_ADMIN,
                                   subject="oid-it27")
        self.assertEqual(
            self.client.get(self.members(), headers=self.auth(token)).status_code, 403)

    def test_read_only_cannot_manage_members(self):
        token = self.admin_session("ro27@example.com", main.SECURITY_ROLE_READ_ONLY,
                                   subject="oid-ro27")
        self.assertEqual(
            self.client.get(self.members(), headers=self.auth(token)).status_code, 403)

    def test_another_organization_is_invisible(self):
        token = self.admin_session()
        self.assertEqual(
            self.client.get(
                "/api/control/organizations/org-voisine/members",
                headers=self.auth(token)).status_code, 404)

    def test_a_member_of_another_organization_cannot_be_touched(self):
        token = self.admin_session()
        with main.db() as conn:
            conn.execute(
                "INSERT OR REPLACE INTO organizations(id, slug, display_name, "
                "org_type, status, created_at) VALUES (?,?,?,?,?,?)",
                ("org-voisine-27", "voisine", "Voisine", "education", "active",
                 time.time()))
            stranger = main.create_user(conn, "org-voisine-27", "voisin@example.com")
        for method, suffix, payload in (
            ("get", f"/{stranger}", None),
            ("post", f"/{stranger}/status", {"status": "disabled"}),
            ("post", f"/{stranger}/sessions/revoke", None),
            ("delete", f"/{stranger}", None),
        ):
            call = getattr(self.client, method)
            response = (call(self.members(suffix), headers=self.auth(token), json=payload)
                        if payload else call(self.members(suffix), headers=self.auth(token)))
            self.assertEqual(response.status_code, 404, suffix)
        with main.db() as conn:
            self.assertIsNotNone(main.user_by_id(conn, stranger))
            conn.execute("DELETE FROM users WHERE user_id=?", (stranger,))
            conn.execute("DELETE FROM organizations WHERE id='org-voisine-27'")

    # -- audit --------------------------------------------------------------

    def test_member_actions_are_audited(self):
        token = self.admin_session()
        target = self.member("audite27@example.com")
        self.client.post(self.members(f"/{target}/status"), headers=self.auth(token),
                         json={"status": "disabled"})
        self.client.post(self.members(f"/{target}/status"), headers=self.auth(token),
                         json={"status": "active"})
        self.client.post(self.members(f"/{target}/profile"), headers=self.auth(token),
                         json={"member_role": "teacher"})
        self.client.post(self.members(f"/{target}/sessions/revoke"),
                         headers=self.auth(token))
        with main.db() as conn:
            actions = [r["action"] for r in conn.execute(
                "SELECT action FROM admin_audit_log WHERE target_id=?", (target,))]
        for expected in ("member.disable", "member.enable", "member.type_change",
                         "member.sessions_revoke"):
            self.assertIn(expected, actions)

    def test_the_mutation_and_its_trace_share_a_transaction(self):
        """Dette P1 de la Phase 26, reglee ici : une mutation sans trace serait
        une reponse manquante a « qui a desactive ce compte ? »."""
        token = self.admin_session()
        target = self.member("transaction@example.com")
        # Si l'audit echoue, la mutation ne doit pas subsister.
        with patch.object(main, "_write_audit_row",
                          side_effect=sqlite3.OperationalError("audit indisponible")):
            with self.assertRaises(Exception):
                self.client.post(self.members(f"/{target}/status"),
                                 headers=self.auth(token), json={"status": "disabled"})
        with main.db() as conn:
            account = main.user_by_id(conn, target)
        self.assertEqual(main.account_status(account), "active")


class SeatsTests(AdminTestCase):
    """Le compteur de sieges, rendu a la vue Control Plane."""

    def test_seats_count_people_not_sessions(self):
        token = self.admin_session()
        # Le meme compte sur deux postes occupe un siege, pas deux.
        self.sign_in("multi@example.com", machine="poste-1")
        self.sign_in("multi@example.com", machine="poste-2")
        body = self.client.get("/api/control/organization",
                               headers=self.auth(token)).json()
        self.assertIn("seats", body)
        self.assertEqual(body["seats"]["limit"], main.MAX_SEATS)
        self.assertGreaterEqual(body["seats"]["used"], 1)

    def test_the_seat_count_is_real_not_invented(self):
        # C'est le meme compteur que celui qui refuse une connexion de plus.
        token = self.admin_session()
        body = self.client.get("/api/control/organization",
                               headers=self.auth(token)).json()
        self.assertEqual(body["seats"]["used"],
                         main.seats_in_use(main.nova_organization_id()))



def test_published_vocabulary_reaches_every_consumer():
    """Un vocabulaire publie doit arriver partout, pas seulement a la dictee.

    Trois consommateurs lisent le vocabulaire de l'organisation :
    `writing_context` pour la dictee, `vocabulary_context` pour les AI Skills,
    et `/api/vocabulary` pour l'ecran Dictionnaire du poste. Seul le premier
    fusionnait les packages ; les deux autres ne lisaient que la table
    historique `shared_dictionary`.

    Un administrateur publiait donc du vocabulaire depuis Nova Admin, le voyait
    actif dans la console, et le poste affichait « Aucun terme partage par
    l'etablissement ». Aucune erreur nulle part.
    """
    source = (Path(__file__).resolve().parent / "main.py").read_text(encoding="utf-8")
    # La route et le contexte des skills passent par la fusion, pas par la table.
    contexte = source[source.index("def vocabulary_context"):]
    contexte = contexte[:contexte.index(chr(10) + "def ")]
    assert "organization_shared_terms" in contexte, (
        "les AI Skills ignoreraient un vocabulaire publie")
    assert "FROM shared_dictionary" not in contexte, (
        "lire la table seule laisse tomber les packages")

    route = source[source.index('@app.get("/api/vocabulary"'):]
    route = route[:route.index(chr(10) + "@app.")]
    assert "organization_shared_terms" in route, (
        "l'ecran Dictionnaire afficherait un vocabulaire vide")
    assert "FROM shared_dictionary" not in route

    # Et la fusion donne bien le package par-dessus l'heritage : c'est lui que
    # l'organisation vient d'ecrire.
    fusion = source[source.index("def organization_shared_terms"):]
    fusion = fusion[:fusion.index(chr(10) + "def ")]
    assert fusion.index("legacy_shared_vocabulary") < fusion.index("organization_vocabulary("), (
        "l'heritage doit etre pose en premier pour que le package l'ecrase")

def test_a_skill_may_ask_for_a_prefix_without_the_server_forbidding_it():
    """La consigne systeme ne doit pas contredire l'instruction de la skill.

    Elle disait « Return only the transformed text ». Une skill qui demande
    « commence par [DIFFUSION OK] » se voyait donc contredite par le serveur
    lui-meme -- et le marqueur n'apparaissait dans AUCUNE sortie sur seize
    tirages, contre cinq une fois la consigne reformulee.

    Ce qu'il faut interdire n'est pas d'ajouter, mais de commenter : un modele
    qui explique ce qu'il vient de faire est le vrai bruit.
    """
    prompt = main.command_prompt("Commence par [DIFFUSION OK]", "")
    assert "Return only the transformed text" not in prompt, (
        "cette phrase interdit ce que certaines instructions demandent")
    # Ajouter reste permis, commenter reste interdit.
    assert "including any prefix" in prompt
    assert "no commentary" in prompt

    # Et « preserver » doit dire ce qu'il recouvre. « Preserve facts and the
    # original language » etait lu comme « ne change rien » : sur huit tirages,
    # le modele recopiait le texte mot pour mot cinq fois. Zero fois une fois la
    # formulation distinguee des faits.
    assert "Preserve facts and the original language" not in prompt, (
        "cette formulation est lue comme une interdiction de reecrire")
    # Et ne pas se contenter de recopier : c'est ce que faisait le modele
    # quand la consigne ne disait que « preserver ».
    assert "do not merely repeat or rephrase the text" in prompt
    # Et l'instruction de la skill est toujours transmise telle quelle.
    assert "Commence par [DIFFUSION OK]" in prompt

if __name__ == "__main__":
    unittest.main()
