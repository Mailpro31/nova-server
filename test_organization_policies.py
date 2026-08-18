"""Phase 29 — policies produit de l'organisation.

Un fichier a part, pour la meme raison que `test_admin_cli.py` : ce qui se joue
ici n'est pas l'authentification mais la **gouvernance produit**, et melanger
les deux rendrait les deux plus difficiles a relire.

Ces tests importent le socle de `test_main.py` — `AdminTestCase` joue le
parcours complet jusqu'a la session d'administration, step-up compris. Un
raccourci rendrait verts des tests qui n'exercent plus le chemin reel.
"""

import inspect
import json
from pathlib import Path
import tempfile
import os
import time
import unittest
from unittest.mock import patch

from test_main import AdminTestCase, grant_admin_session  # noqa: F401

import main


def clear_policies():
    with main.db() as conn:
        conn.execute("DELETE FROM organization_policies")


def stored_policy(organization_id=None) -> dict | None:
    organization_id = organization_id or main.nova_organization_id()
    with main.db() as conn:
        row = conn.execute(
            "SELECT * FROM organization_policies WHERE organization_id=?",
            (organization_id,),
        ).fetchone()
    return dict(row) if row else None


class PolicySchemaTests(unittest.TestCase):
    """Le schema est la propriete du serveur, pas un dictionnaire libre."""

    def test_every_default_is_permissive(self):
        """Une organisation qui n'a rien configure ne perd rien.

        C'est la regle qui protege les installations existantes : si un defaut
        valait `False`, la simple mise a jour du serveur eteindrait une
        fonction chez tout le monde.
        """
        for key, value in main.DEFAULT_ORGANIZATION_POLICY.items():
            with self.subTest(key):
                self.assertTrue(value, f"{key} devrait etre permissif par defaut")

    def test_the_default_document_matches_the_schema(self):
        self.assertEqual(
            set(main.DEFAULT_ORGANIZATION_POLICY), set(main.POLICY_SETTINGS)
        )

    def test_an_unknown_key_is_refused(self):
        with self.assertRaises(ValueError) as raised:
            main.validate_policy_settings({"ai_skills_enabled": True, "invente": True})
        self.assertIn("POLICY_KEY_UNKNOWN", str(raised.exception))

    def test_a_wrong_type_is_refused(self):
        for value in ("true", 1, 0, None, [], {}):
            with self.subTest(value=value):
                with self.assertRaises(ValueError):
                    main.validate_policy_settings({"ai_skills_enabled": value})

    def test_an_integer_is_not_a_boolean(self):
        """`isinstance(True, int)` vaut vrai en Python : sans controle dedie,
        `1` passerait pour un booleen."""
        with self.assertRaises(ValueError):
            main.validate_policy_settings({"ai_skills_enabled": 1})

    def test_a_document_matching_the_defaults_stores_no_override(self):
        """Depuis la Phase 30, seules les **surcharges** sont stockees.

        Recopier les defauts en base les figerait : le jour ou un defaut
        produit change, les organisations qui n'avaient rien demande
        garderaient l'ancien comportement sans l'avoir choisi.
        """
        self.assertEqual(main.validate_policy_settings({}), {})
        self.assertEqual(
            main.validate_policy_settings(dict(main.DEFAULT_ORGANIZATION_POLICY)), {}
        )

    def test_the_effective_document_still_carries_every_key(self):
        self.assertEqual(
            main.effective_policy_settings({}), main.DEFAULT_ORGANIZATION_POLICY
        )

    def test_a_value_differing_from_its_default_is_kept(self):
        self.assertEqual(
            main.validate_policy_settings({"ai_skills_enabled": False}),
            {"ai_skills_enabled": False},
        )

    def test_a_non_dictionary_is_refused(self):
        for candidate in ("", [], None, 3):
            with self.subTest(candidate=candidate):
                with self.assertRaises(ValueError):
                    main.validate_policy_settings(candidate)


class PolicyReadResilienceTests(unittest.TestCase):
    """En lecture, l'inconnu se degrade ; il ne fait pas tomber le serveur.

    La dissymetrie avec l'ecriture est voulue : une ligne ecrite par une
    version ulterieure doit rester lisible, sinon une mise a jour ratee
    priverait l'organisation de tout service.
    """

    def test_an_unknown_key_in_storage_is_ignored(self):
        settings = main.read_policy_settings(
            json.dumps({"ai_skills_enabled": False, "venu_du_futur": True})
        )
        self.assertEqual(settings, {"ai_skills_enabled": False})

    def test_corrupted_storage_falls_back_to_defaults(self):
        for raw in ("", None, "pas du json", "[]", '"texte"', "null"):
            with self.subTest(raw=raw):
                self.assertEqual(main.read_policy_settings(raw), {})
                self.assertEqual(
                    main.effective_policy_settings(main.read_policy_settings(raw)),
                    main.DEFAULT_ORGANIZATION_POLICY,
                )

    def test_a_wrong_type_in_storage_falls_back_to_its_default(self):
        overrides = main.read_policy_settings(json.dumps({"ai_skills_enabled": "non"}))
        self.assertEqual(overrides, {})
        self.assertTrue(
            main.effective_policy_settings(overrides)["ai_skills_enabled"])


class EffectiveCapabilityTests(unittest.TestCase):
    """base ∩ policy = effective. Les quatre cases, explicitement."""

    def resolve(self, base: bool, policy: bool) -> bool:
        return main.resolve_effective_product_capabilities(
            {"aiSkills": base}, {"ai_skills_enabled": policy}
        )["aiSkills"]

    def test_base_false_policy_false(self):
        self.assertFalse(self.resolve(False, False))

    def test_base_false_policy_true(self):
        """Le test fondamental : une policy n'invente aucune capacite.

        Si l'organisation pouvait ouvrir ce que le produit ne fournit pas, le
        catalogue produit cesserait d'etre la reference et chaque tenant
        pourrait s'attribuer des fonctions inexistantes.
        """
        self.assertFalse(self.resolve(False, True))

    def test_base_true_policy_false(self):
        self.assertFalse(self.resolve(True, False))

    def test_base_true_policy_true(self):
        self.assertTrue(self.resolve(True, True))

    def test_a_capability_absent_from_the_base_is_not_invented(self):
        effective = main.resolve_effective_product_capabilities(
            {}, {"ai_skills_enabled": True}
        )
        self.assertNotIn("aiSkills", effective)

    def test_capabilities_the_policy_does_not_govern_pass_through(self):
        effective = main.resolve_effective_product_capabilities(
            {"dictation": True, "rewrite": False}, {"ai_skills_enabled": False}
        )
        self.assertTrue(effective["dictation"])
        self.assertFalse(effective["rewrite"])


class PolicyBoundaryTests(unittest.TestCase):
    """Aucune policy ne touche au plan d'identite.

    Une policy produit qui pourrait eteindre le SSO, le step-up ou l'audit
    serait un moyen de se verrouiller dehors depuis un ecran de configuration —
    ou de desarmer la tracabilite. La frontiere est structurelle : ces notions
    n'ont pas de cle.
    """

    FORBIDDEN = (
        "sso", "entra", "google", "oidc", "step_up", "stepup", "session",
        "provider", "discovery", "admin", "role", "audit", "auth",
        "capability", "identity", "security", "token",
    )

    def test_no_policy_key_touches_the_identity_plane(self):
        for key in main.POLICY_SETTINGS:
            for forbidden in self.FORBIDDEN:
                with self.subTest(key=key, forbidden=forbidden):
                    self.assertNotIn(forbidden, key)

    def test_admin_capabilities_and_policy_keys_never_overlap(self):
        """Les deux dictionnaires ne doivent partager aucun nom.

        Un nom commun finirait par etre lu par le mauvais mecanisme — une
        capacite d'administration prise pour une policy ouvrirait la console.
        """
        admin_capabilities = set()
        for capabilities in main.CAPABILITIES_BY_ROLE.values():
            admin_capabilities |= set(capabilities)
        self.assertEqual(admin_capabilities & set(main.POLICY_SETTINGS), set())

    def test_policy_manage_is_not_a_product_policy(self):
        self.assertIn(main.CAPABILITY_POLICY_MANAGE, main.capabilities_for_role(
            main.SECURITY_ROLE_ORGANIZATION_ADMIN))
        self.assertNotIn(main.CAPABILITY_POLICY_MANAGE, main.POLICY_SETTINGS)


class PolicyRoleMatrixTests(unittest.TestCase):
    def test_only_organization_admin_may_change_policies(self):
        self.assertIn(
            main.CAPABILITY_POLICY_MANAGE,
            main.capabilities_for_role(main.SECURITY_ROLE_ORGANIZATION_ADMIN),
        )
        for role in (main.SECURITY_ROLE_IT_ADMIN, main.SECURITY_ROLE_READ_ONLY,
                     main.SECURITY_ROLE_MEMBER):
            with self.subTest(role):
                self.assertNotIn(
                    main.CAPABILITY_POLICY_MANAGE, main.capabilities_for_role(role)
                )

    def test_every_administrator_may_read_the_policy(self):
        for role in (main.SECURITY_ROLE_ORGANIZATION_ADMIN,
                     main.SECURITY_ROLE_IT_ADMIN, main.SECURITY_ROLE_READ_ONLY):
            with self.subTest(role):
                self.assertIn(
                    main.CAPABILITY_ORGANIZATION_READ, main.capabilities_for_role(role)
                )


class PolicyAdminApiTests(AdminTestCase):
    PATH = "/api/control/organization/policy"

    def setUp(self):
        super().setUp()
        clear_policies()

    def test_an_unconfigured_organization_reads_the_defaults(self):
        token = self.admin_session()
        body = self.client.get(self.PATH, headers=self.auth(token)).json()
        self.assertEqual(body["revision"], 0)
        self.assertEqual(body["schema_version"], main.POLICY_SCHEMA_VERSION)
        self.assertEqual(body["settings"], main.DEFAULT_ORGANIZATION_POLICY)
        self.assertFalse(body["configured"])
        self.assertIsNone(body["updated_at"])
        # Rien n'a ete ecrit : lire ne configure pas.
        self.assertIsNone(stored_policy())

    def test_an_update_writes_and_increments_the_revision(self):
        token = self.admin_session()
        response = self.client.put(
            self.PATH, headers=self.auth(token),
            json={"expected_revision": 0,
                  "settings": {"ai_skills_enabled": False}})
        self.assertEqual(response.status_code, 200, response.text)
        body = response.json()
        self.assertEqual(body["revision"], 1)
        self.assertFalse(body["settings"]["ai_skills_enabled"])
        self.assertTrue(body["configured"])
        self.assertEqual(stored_policy()["revision"], 1)

    def test_the_response_is_authoritative(self):
        """Ce que la console affiche apres sauvegarde vient du serveur.

        Renvoyer l'echo de la requete laisserait l'interface montrer un etat
        que la base ne porte pas.
        """
        token = self.admin_session()
        body = self.client.put(
            self.PATH, headers=self.auth(token),
            json={"expected_revision": 0, "settings": {}}).json()
        self.assertEqual(body["settings"], main.DEFAULT_ORGANIZATION_POLICY)
        read = self.client.get(self.PATH, headers=self.auth(token)).json()
        self.assertEqual(read, body)

    def test_revisions_increase_monotonically(self):
        token = self.admin_session()
        for expected in range(4):
            response = self.client.put(
                self.PATH, headers=self.auth(token),
                json={"expected_revision": expected,
                      "settings": {"ai_skills_enabled": expected % 2 == 0}})
            self.assertEqual(response.json()["revision"], expected + 1)

    def test_a_stale_revision_is_refused(self):
        token = self.admin_session()
        self.client.put(self.PATH, headers=self.auth(token),
                        json={"expected_revision": 0,
                              "settings": {"ai_skills_enabled": False}})
        response = self.client.put(
            self.PATH, headers=self.auth(token),
            json={"expected_revision": 0,
                  "settings": {"ai_skills_enabled": True}})
        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.json()["detail"], "POLICY_REVISION_CONFLICT")

    def test_a_refused_write_changes_nothing(self):
        token = self.admin_session()
        self.client.put(self.PATH, headers=self.auth(token),
                        json={"expected_revision": 0,
                              "settings": {"ai_skills_enabled": False}})
        before = stored_policy()
        self.client.put(self.PATH, headers=self.auth(token),
                        json={"expected_revision": 0,
                              "settings": {"ai_skills_enabled": True}})
        self.assertEqual(stored_policy(), before)

    def test_an_unknown_key_is_refused(self):
        token = self.admin_session()
        response = self.client.put(
            self.PATH, headers=self.auth(token),
            json={"expected_revision": 0,
                  "settings": {"ai_skills_enabled": True, "invente": True}})
        self.assertEqual(response.status_code, 400)
        self.assertIn("POLICY_KEY_UNKNOWN", response.json()["detail"])
        self.assertIsNone(stored_policy())

    def test_an_invalid_type_is_refused(self):
        token = self.admin_session()
        response = self.client.put(
            self.PATH, headers=self.auth(token),
            json={"expected_revision": 0,
                  "settings": {"ai_skills_enabled": "non"}})
        self.assertEqual(response.status_code, 400)
        self.assertIn("POLICY_VALUE_INVALID", response.json()["detail"])

    def test_it_admin_may_read_but_not_write(self):
        token = self.admin_session("it@example.com", main.SECURITY_ROLE_IT_ADMIN,
                                   subject="oid-it")
        self.assertEqual(
            self.client.get(self.PATH, headers=self.auth(token)).status_code, 200)
        response = self.client.put(
            self.PATH, headers=self.auth(token),
            json={"expected_revision": 0, "settings": {"ai_skills_enabled": False}})
        self.assertEqual(response.status_code, 403)
        self.assertIsNone(stored_policy())

    def test_read_only_may_read_but_not_write(self):
        token = self.admin_session("lecture@example.com",
                                   main.SECURITY_ROLE_READ_ONLY, subject="oid-ro")
        self.assertEqual(
            self.client.get(self.PATH, headers=self.auth(token)).status_code, 200)
        self.assertEqual(
            self.client.put(
                self.PATH, headers=self.auth(token),
                json={"expected_revision": 0,
                      "settings": {"ai_skills_enabled": False}}).status_code, 403)

    def test_a_member_session_opens_nothing(self):
        """Une session utilisateur ordinaire n'administre rien, meme en
        lecture."""
        user_token = self.sign_in("membre@example.com")
        self.assertEqual(
            self.client.get(self.PATH, headers=self.auth(user_token)).status_code, 401)
        self.assertEqual(
            self.client.put(self.PATH, headers=self.auth(user_token),
                            json={"expected_revision": 0,
                                  "settings": {}}).status_code, 401)

    def test_no_session_opens_nothing(self):
        self.assertEqual(self.client.get(self.PATH).status_code, 401)
        self.assertEqual(
            self.client.put(self.PATH,
                            json={"expected_revision": 0,
                                  "settings": {}}).status_code, 401)


class PolicyAuditTests(AdminTestCase):
    PATH = "/api/control/organization/policy"

    def setUp(self):
        super().setUp()
        clear_policies()

    def rows(self, action="organization_policy.update"):
        with main.db() as conn:
            return [
                dict(row) for row in conn.execute(
                    "SELECT * FROM admin_audit_log WHERE action=? "
                    "ORDER BY created_at", (action,)).fetchall()
            ]

    def test_an_update_is_recorded(self):
        token = self.admin_session()
        self.client.put(self.PATH, headers=self.auth(token),
                        json={"expected_revision": 0,
                              "settings": {"ai_skills_enabled": False}})
        rows = self.rows()
        self.assertEqual(len(rows), 1)
        metadata = json.loads(rows[0]["metadata"])
        self.assertEqual(metadata["old_revision"], 0)
        self.assertEqual(metadata["new_revision"], 1)
        self.assertEqual(metadata["changed_keys"], ["ai_skills_enabled"])
        self.assertEqual(rows[0]["organization_id"], main.nova_organization_id())
        self.assertIsNotNone(rows[0]["actor_user_id"])

    def test_the_audit_records_names_not_the_document(self):
        """Les cles modifiees suffisent. Recopier la configuration entiere a
        chaque ecriture ferait grossir le journal sans rien apprendre."""
        token = self.admin_session()
        self.client.put(self.PATH, headers=self.auth(token),
                        json={"expected_revision": 0,
                              "settings": {"ai_skills_enabled": False}})
        metadata = json.loads(self.rows()[0]["metadata"])
        self.assertEqual(set(metadata),
                         {"old_revision", "new_revision", "changed_keys"})

    def test_an_unchanged_write_records_no_changed_key(self):
        token = self.admin_session()
        self.client.put(self.PATH, headers=self.auth(token),
                        json={"expected_revision": 0,
                              "settings": {"ai_skills_enabled": True}})
        metadata = json.loads(self.rows()[0]["metadata"])
        self.assertEqual(metadata["changed_keys"], [])

    def test_a_refused_write_records_nothing(self):
        token = self.admin_session()
        self.client.put(self.PATH, headers=self.auth(token),
                        json={"expected_revision": 0,
                              "settings": {"invente": True}})
        self.assertEqual(self.rows(), [])

    def test_the_mutation_and_its_trace_share_a_transaction(self):
        """Si l'ecriture d'audit echoue, la policy ne doit pas subsister."""
        token = self.admin_session()

        def explode(*args, **kwargs):
            raise sqlite_failure()

        def sqlite_failure():
            import sqlite3
            return sqlite3.OperationalError("audit indisponible")

        with patch.object(main, "_write_audit_row", explode):
            with self.assertRaises(Exception):
                self.client.put(self.PATH, headers=self.auth(token),
                                json={"expected_revision": 0,
                                      "settings": {"ai_skills_enabled": False}})
        self.assertIsNone(stored_policy())


class PolicyEnforcementTests(AdminTestCase):
    """La policy appliquee de bout en bout, pas seulement affichee."""

    PATH = "/api/control/organization/policy"

    def setUp(self):
        super().setUp()
        clear_policies()

    def deny_ai_skills(self):
        token = self.admin_session()
        response = self.client.put(
            self.PATH, headers=self.auth(token),
            json={"expected_revision": 0,
                  "settings": {"ai_skills_enabled": False}})
        self.assertEqual(response.status_code, 200, response.text)

    def test_by_default_ai_skills_are_served(self):
        user_token = self.sign_in("etudiant@example.com")
        response = self.client.get("/api/ai-skills", headers=self.auth(user_token))
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.json()["skills"])

    def test_a_denied_policy_closes_the_server_route(self):
        """Le point de toute la phase : le refus ne se limite pas a masquer un
        bouton. Un poste ancien, modifie, ou appelant la route directement se
        heurte au meme refus."""
        self.deny_ai_skills()
        user_token = self.sign_in("etudiant@example.com")
        response = self.client.get("/api/ai-skills", headers=self.auth(user_token))
        self.assertEqual(response.status_code, 403)
        # Contrat commun depuis la Phase 30 : cinq fonctions se refusent pour
        # la meme raison, et cinq codes differents auraient oblige la console a
        # les traiter une par une.
        self.assertEqual(response.json()["detail"]["code"],
                         "ORGANIZATION_POLICY_DISABLED")
        self.assertEqual(response.json()["detail"]["feature"], "ai_skills")

    def test_a_denied_policy_closes_the_announced_capability(self):
        self.deny_ai_skills()
        user_token = self.sign_in("etudiant@example.com")
        body = self.client.get("/api/me", headers=self.auth(user_token)).json()
        self.assertNotIn("aiSkills", body["capabilities"])

    def test_a_denied_policy_closes_the_public_config(self):
        self.deny_ai_skills()
        config = self.client.get("/api/config").json()
        self.assertFalse(config["capabilities"]["aiSkills"])
        self.assertFalse(config["ai_skills"]["enabled"])

    def test_an_allowed_policy_reopens_everything(self):
        self.deny_ai_skills()
        token = self.admin_session()
        self.client.put(self.PATH, headers=self.auth(token),
                        json={"expected_revision": 1,
                              "settings": {"ai_skills_enabled": True}})
        user_token = self.sign_in("etudiant@example.com")
        self.assertEqual(
            self.client.get("/api/ai-skills",
                            headers=self.auth(user_token)).status_code, 200)

    def test_the_public_config_carries_the_revision(self):
        config = self.client.get("/api/config").json()
        self.assertEqual(config["policy_revision"], 0)
        self.deny_ai_skills()
        self.assertEqual(
            self.client.get("/api/config").json()["policy_revision"], 1)

    def test_the_policy_never_reopens_what_the_deployment_closed(self):
        """Mode examen : la base ferme la reformulation. Une policy permissive
        ne la rouvre pas."""
        with patch.object(main, "EDUCATION_MODE", "assessment"):
            config = main.public_campus_config()
        self.assertFalse(config["capabilities"]["rewrite"])
        self.assertFalse(config["ai_skills"]["enabled"])


class MemberPolicyApiTests(AdminTestCase):
    PATH = "/api/organization/policy"

    def setUp(self):
        super().setUp()
        clear_policies()

    def test_a_member_reads_the_applicable_policy(self):
        user_token = self.sign_in("etudiant@example.com")
        response = self.client.get(self.PATH, headers=self.auth(user_token))
        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(body["revision"], 0)
        self.assertEqual(body["settings"], main.DEFAULT_ORGANIZATION_POLICY)
        self.assertTrue(body["effective_capabilities"]["aiSkills"])

    def test_no_admin_session_is_required(self):
        """Savoir ce que l'on a le droit d'utiliser n'est pas administrer."""
        user_token = self.sign_in("etudiant@example.com")
        self.assertEqual(
            self.client.get(self.PATH, headers=self.auth(user_token)).status_code, 200)

    def test_an_anonymous_call_is_refused(self):
        self.assertEqual(self.client.get(self.PATH).status_code, 401)

    def test_the_member_sees_the_denied_capability(self):
        token = self.admin_session()
        self.client.put("/api/control/organization/policy", headers=self.auth(token),
                        json={"expected_revision": 0,
                              "settings": {"ai_skills_enabled": False}})
        user_token = self.sign_in("etudiant@example.com")
        body = self.client.get(self.PATH, headers=self.auth(user_token)).json()
        self.assertFalse(body["settings"]["ai_skills_enabled"])
        self.assertFalse(body["effective_capabilities"]["aiSkills"])

    def test_no_client_supplied_organization_is_honoured(self):
        """Un identifiant d'organisation venu du client n'a aucun effet.

        Sans cela, il suffirait de changer une chaine pour lire la
        configuration d'une autre organisation.
        """
        user_token = self.sign_in("etudiant@example.com")
        reference = self.client.get(self.PATH, headers=self.auth(user_token)).json()
        for attempt in (
            {"organization_id": "une-autre"},
            {"organization": "une-autre"},
        ):
            with self.subTest(attempt=attempt):
                response = self.client.get(
                    self.PATH, headers=self.auth(user_token), params=attempt)
                self.assertEqual(response.json(), reference)

    def test_the_response_carries_no_governance_detail(self):
        """Un membre n'a pas a savoir qui a change la policy, ni quand."""
        user_token = self.sign_in("etudiant@example.com")
        body = self.client.get(self.PATH, headers=self.auth(user_token)).json()
        self.assertEqual(
            set(body),
            {"schema_version", "revision", "settings", "effective_capabilities"})


class PolicyTenantIsolationTests(AdminTestCase):
    """Deux organisations, deux policies, deux revisions."""

    OTHER = "org-voisine"

    def setUp(self):
        super().setUp()
        clear_policies()
        with main.db() as conn:
            conn.execute(
                "INSERT OR REPLACE INTO organizations(id, slug, display_name, "
                "org_type, status, created_at) VALUES (?,?,?,?,?,?)",
                (self.OTHER, "voisine", "Organisation voisine", "education",
                 "active", time.time()))

    def tearDown(self):
        with main.db() as conn:
            conn.execute("DELETE FROM organizations WHERE id=?", (self.OTHER,))

    def test_each_organization_carries_its_own_policy(self):
        token = self.admin_session()
        self.client.put("/api/control/organization/policy", headers=self.auth(token),
                        json={"expected_revision": 0,
                              "settings": {"ai_skills_enabled": False}})
        # La voisine n'a rien configure : elle garde ses defauts.
        neighbour = main.organization_policy(self.OTHER)
        self.assertEqual(neighbour["revision"], 0)
        self.assertTrue(neighbour["settings"]["ai_skills_enabled"])

    def test_revisions_are_independent(self):
        token = self.admin_session()
        for expected in range(3):
            self.client.put(
                "/api/control/organization/policy", headers=self.auth(token),
                json={"expected_revision": expected, "settings": {}})
        self.assertEqual(main.organization_policy(self.OTHER)["revision"], 0)

    def test_an_administrator_only_writes_its_own_organization(self):
        """La route ne prend aucun identifiant d'organisation.

        C'est la protection la plus sure : ce qu'on ne peut pas designer, on ne
        peut pas l'atteindre par erreur de controle.
        """
        token = self.admin_session()
        self.client.put("/api/control/organization/policy", headers=self.auth(token),
                        json={"expected_revision": 0,
                              "settings": {"ai_skills_enabled": False}},
                        params={"organization_id": self.OTHER})
        self.assertIsNone(stored_policy(self.OTHER))
        self.assertIsNotNone(stored_policy())

    def test_the_audit_stays_within_its_organization(self):
        token = self.admin_session()
        self.client.put("/api/control/organization/policy", headers=self.auth(token),
                        json={"expected_revision": 0,
                              "settings": {"ai_skills_enabled": False}})
        with main.db() as conn:
            rows = conn.execute(
                "SELECT organization_id FROM admin_audit_log "
                "WHERE action='organization_policy.update'").fetchall()
        self.assertTrue(rows)
        for row in rows:
            self.assertEqual(row["organization_id"], main.nova_organization_id())


class PolicyMigrationTests(unittest.TestCase):
    """Une base d'avant la Phase 29 ne perd aucune fonctionnalite."""

    def test_a_database_without_the_table_gains_it(self):
        with main.db() as conn:
            tables = {
                row["name"] for row in conn.execute(
                    "SELECT name FROM sqlite_master WHERE type='table'").fetchall()
            }
        self.assertIn("organization_policies", tables)

    def test_an_organization_without_a_row_keeps_every_capability(self):
        clear_policies()
        config = main.public_campus_config()
        self.assertTrue(config["capabilities"]["aiSkills"])
        self.assertTrue(config["ai_skills"]["enabled"])
        self.assertEqual(config["policy_revision"], 0)

    def test_the_schema_version_is_stored_with_the_document(self):
        """Sans elle, une lecture future ne saurait pas selon quelles regles le
        document a ete ecrit."""
        with main.db() as conn:
            conn.execute(
                "INSERT OR REPLACE INTO organization_policies(organization_id, "
                "schema_version, revision, settings, updated_at) VALUES (?,?,?,?,?)",
                (main.nova_organization_id(), main.POLICY_SCHEMA_VERSION, 7,
                 json.dumps({"ai_skills_enabled": False}), time.time()))
        policy = main.organization_policy(main.nova_organization_id())
        self.assertEqual(policy["schema_version"], main.POLICY_SCHEMA_VERSION)
        self.assertEqual(policy["revision"], 7)
        clear_policies()


class PolicyPrivacyTests(AdminTestCase):
    """Une policy est une regle. Elle ne contient aucune donnee de personne."""

    def setUp(self):
        super().setUp()
        clear_policies()

    def test_the_policy_document_carries_no_user_content(self):
        token = self.admin_session()
        raw = self.client.get("/api/control/organization/policy",
                              headers=self.auth(token)).text.lower()
        for forbidden in ("audio", "transcript", "dictation_text", "prompt",
                          "clipboard", "history", "secret", "token", "password"):
            self.assertNotIn(forbidden, raw)

    def test_free_form_content_cannot_enter_the_document(self):
        """Le schema n'accepte que des booleens declares : impossible d'y
        glisser du texte, donc impossible d'y glisser des donnees."""
        token = self.admin_session()
        for payload in (
            {"note": "quelque chose"},
            {"ai_skills_enabled": "texte libre"},
            {"ai_skills_enabled": {"nested": True}},
        ):
            with self.subTest(payload=payload):
                response = self.client.put(
                    "/api/control/organization/policy", headers=self.auth(token),
                    json={"expected_revision": 0, "settings": payload})
                self.assertEqual(response.status_code, 400)


class ModeCompositionTests(AdminTestCase):
    """Policy d'organisation et restriction de mode se **composent**.

    C'est l'invariant que la Phase 30 devait preserver : le mode `classroom`
    ferme historiquement les commandes et les notes d'ingenierie. Une policy
    permissive ne doit pas les rouvrir — sans quoi une migration aurait rendu a
    des classes entieres des fonctions qu'un etablissement avait fermees.

    La propriete ne repose sur aucune regle de priorite ecrite quelque part :
    la restriction de mode vit dans la capacite de **base**, et l'intersection
    la preserve parce que `False and True` vaut `False`. Il n'y a rien a se
    rappeler.
    """

    def setUp(self):
        super().setUp()
        clear_policies()

    def apply(self, **overrides):
        token = self.admin_session()
        response = self.client.put(
            "/api/control/organization/policy", headers=self.auth(token),
            json={"expected_revision": 0, "settings": overrides})
        self.assertEqual(response.status_code, 200, response.text)

    def effective(self, mode: str):
        with patch.object(main, "EDUCATION_MODE", mode):
            return main.public_campus_config()["capabilities"]

    def test_classroom_keeps_commands_closed_whatever_the_policy(self):
        for policy in (True, False):
            with self.subTest(policy=policy):
                clear_policies()
                self.apply(voice_commands_enabled=policy)
                self.assertFalse(self.effective("classroom")["commands"])

    def test_classroom_keeps_engineering_notes_closed_whatever_the_policy(self):
        for policy in (True, False):
            with self.subTest(policy=policy):
                clear_policies()
                self.apply(engineering_notes_enabled=policy)
                self.assertFalse(self.effective("classroom")["engineeringNotes"])

    def test_normal_mode_follows_the_policy(self):
        self.apply(voice_commands_enabled=True)
        self.assertTrue(self.effective("normal")["commands"])
        clear_policies()
        self.apply(voice_commands_enabled=False)
        self.assertFalse(self.effective("normal")["commands"])

    def test_assessment_keeps_everything_closed(self):
        capabilities = self.effective("assessment")
        for name in ("commands", "engineeringNotes", "dictionary", "aiSkills",
                     "fileTranscription"):
            with self.subTest(name):
                self.assertFalse(capabilities[name])

    def test_a_policy_never_reopens_what_a_mode_closed(self):
        """La formulation generale de l'invariant, sur toutes les cles."""
        for setting in main.POLICY_SETTINGS.values():
            if setting.capability is None:
                continue
            with self.subTest(setting.key):
                effective = main.resolve_effective_product_capabilities(
                    {setting.capability: False}, {setting.key: True})
                self.assertFalse(effective[setting.capability])


class DictationInvariantTests(AdminTestCase):
    """La dictee au micro ne depend d'aucune policy produit.

    Avant la Phase 30, l'import de fichier et la dictee partageaient
    `/api/transcribe`. Gouverner l'import y aurait coupe la dictee — le produit
    lui-meme — sur une case cochee dans un panneau d'import.
    """

    def setUp(self):
        super().setUp()
        clear_policies()

    def deny_file_import(self):
        token = self.admin_session()
        response = self.client.put(
            "/api/control/organization/policy", headers=self.auth(token),
            json={"expected_revision": 0, "settings": {"file_import_enabled": False}})
        self.assertEqual(response.status_code, 200, response.text)

    def transcribe(self, path: str, token: str):
        async def engine(file, user):
            return {"text": "bonjour"}

        with patch.object(main, "transcribe_audio", engine):
            return self.client.post(
                path, headers=self.auth(token),
                files={"file": ("a.wav", b"RIFF0000WAVE", "audio/wav")})

    def test_microphone_dictation_survives_a_closed_file_import(self):
        """L'invariant P0 de cette phase."""
        self.deny_file_import()
        token = self.sign_in("etudiant@example.com")
        self.assertEqual(self.transcribe("/api/transcribe", token).status_code, 200)

    def test_file_import_is_refused_when_the_policy_closes_it(self):
        self.deny_file_import()
        token = self.sign_in("etudiant@example.com")
        response = self.transcribe("/api/import/transcribe", token)
        self.assertEqual(response.status_code, 403)
        self.assertEqual(response.json()["detail"]["code"],
                         "ORGANIZATION_POLICY_DISABLED")
        self.assertEqual(response.json()["detail"]["feature"], "file_import")

    def test_file_import_works_by_default(self):
        token = self.sign_in("etudiant@example.com")
        self.assertEqual(
            self.transcribe("/api/import/transcribe", token).status_code, 200)

    def test_both_routes_share_one_engine(self):
        """Deux routes, un moteur. Dupliquer le pipeline ferait diverger le
        comptage d'usage et la gestion d'erreur."""
        self.assertIn("transcribe_audio", inspect.getsource(main.transcribe))
        self.assertIn("transcribe_audio", inspect.getsource(main.transcribe_import))

    def test_no_policy_governs_the_dictation_route(self):
        route = next(r for r in main.app.routes
                     if getattr(r, "path", "") == "/api/transcribe")
        self.assertNotIn("product_capability", repr(route.dependant.dependencies))


class PolicyEnforcementMatrixTests(AdminTestCase):
    """Chaque nouvelle policy refuse reellement, cote serveur."""

    ROUTES = {
        "organization_vocabulary_enabled": ("GET", "/api/vocabulary",
                                            "organization_vocabulary"),
        "voice_commands_enabled": ("POST", "/api/command", "voice_commands"),
        "engineering_notes_enabled": ("POST", "/api/engineering-notes",
                                      "engineering_notes"),
    }

    def setUp(self):
        super().setUp()
        clear_policies()

    def call(self, method: str, path: str, token: str):
        if method == "GET":
            return self.client.get(path, headers=self.auth(token))
        return self.client.post(path, headers=self.auth(token), json={})

    def deny(self, key: str):
        token = self.admin_session()
        response = self.client.put(
            "/api/control/organization/policy", headers=self.auth(token),
            json={"expected_revision": 0, "settings": {key: False}})
        self.assertEqual(response.status_code, 200, response.text)

    def test_every_policy_closes_its_route(self):
        for key, (method, path, feature) in self.ROUTES.items():
            with self.subTest(key):
                clear_policies()
                self.deny(key)
                user = self.sign_in("etudiant@example.com")
                response = self.call(method, path, user)
                self.assertEqual(response.status_code, 403, path)
                self.assertEqual(response.json()["detail"]["code"],
                                 "ORGANIZATION_POLICY_DISABLED")
                self.assertEqual(response.json()["detail"]["feature"], feature)

    def test_closing_one_policy_leaves_the_others_open(self):
        """Une policy ne doit pas en fermer une autre par effet de bord."""
        self.deny("voice_commands_enabled")
        user = self.sign_in("etudiant@example.com")
        self.assertNotEqual(
            self.client.get("/api/vocabulary", headers=self.auth(user)).status_code,
            403)

    def test_the_error_contract_carries_nothing_else(self):
        self.deny("voice_commands_enabled")
        user = self.sign_in("etudiant@example.com")
        detail = self.client.post("/api/command", headers=self.auth(user),
                                  json={}).json()["detail"]
        self.assertEqual(set(detail), {"code", "feature"})


class PolicyOverrideStorageTests(AdminTestCase):
    """Ce qui est stocke, et ce qui ne l'est pas."""

    PATH = "/api/control/organization/policy"

    def setUp(self):
        super().setUp()
        clear_policies()

    def test_only_explicit_overrides_are_stored(self):
        token = self.admin_session()
        self.client.put(self.PATH, headers=self.auth(token),
                        json={"expected_revision": 0,
                              "settings": {**main.DEFAULT_ORGANIZATION_POLICY,
                                           "voice_commands_enabled": False}})
        self.assertEqual(json.loads(stored_policy()["settings"]),
                         {"voice_commands_enabled": False})

    def test_returning_a_value_to_its_default_removes_the_override(self):
        """C'est ainsi qu'on revient au defaut, sans bouton dedie."""
        token = self.admin_session()
        self.client.put(self.PATH, headers=self.auth(token),
                        json={"expected_revision": 0,
                              "settings": {"voice_commands_enabled": False}})
        self.client.put(self.PATH, headers=self.auth(token),
                        json={"expected_revision": 1,
                              "settings": {"voice_commands_enabled": True}})
        self.assertEqual(json.loads(stored_policy()["settings"]), {})

    def test_the_view_separates_effective_from_explicit(self):
        token = self.admin_session()
        self.client.put(self.PATH, headers=self.auth(token),
                        json={"expected_revision": 0,
                              "settings": {"voice_commands_enabled": False}})
        view = self.client.get(self.PATH, headers=self.auth(token)).json()
        self.assertEqual(view["overrides"], {"voice_commands_enabled": False})
        self.assertEqual(set(view["settings"]), set(main.POLICY_SETTINGS))
        self.assertEqual(view["defaults"], main.DEFAULT_ORGANIZATION_POLICY)

    def test_a_no_op_save_creates_neither_revision_nor_audit(self):
        """Enregistrer sans rien changer n'est pas un evenement.

        Une revision qui avance sans qu'aucune valeur ne bouge rend le compteur
        illisible, et un journal qui note des non-evenements finit par ne plus
        etre lu.
        """
        token = self.admin_session()
        self.client.put(self.PATH, headers=self.auth(token),
                        json={"expected_revision": 0,
                              "settings": {"voice_commands_enabled": False}})
        with main.db() as conn:
            before = conn.execute(
                "SELECT COUNT(*) n FROM admin_audit_log "
                "WHERE action='organization_policy.update'").fetchone()["n"]
        response = self.client.put(
            self.PATH, headers=self.auth(token),
            json={"expected_revision": 1,
                  "settings": {"voice_commands_enabled": False}})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["revision"], 1)
        with main.db() as conn:
            after = conn.execute(
                "SELECT COUNT(*) n FROM admin_audit_log "
                "WHERE action='organization_policy.update'").fetchone()["n"]
        self.assertEqual(after, before)

    def test_several_keys_change_in_one_revision(self):
        token = self.admin_session()
        response = self.client.put(
            self.PATH, headers=self.auth(token),
            json={"expected_revision": 0,
                  "settings": {"voice_commands_enabled": False,
                               "engineering_notes_enabled": False,
                               "file_import_enabled": False}})
        self.assertEqual(response.json()["revision"], 1)
        with main.db() as conn:
            metadata = json.loads(conn.execute(
                "SELECT metadata FROM admin_audit_log "
                "WHERE action='organization_policy.update' "
                "ORDER BY created_at DESC LIMIT 1").fetchone()["metadata"])
        self.assertEqual(metadata["changed_keys"],
                         ["engineering_notes_enabled", "file_import_enabled",
                          "voice_commands_enabled"])

    def test_a_rejected_key_writes_nothing(self):
        """Plusieurs interrupteurs modifies, une seule transaction."""
        token = self.admin_session()
        response = self.client.put(
            self.PATH, headers=self.auth(token),
            json={"expected_revision": 0,
                  "settings": {"voice_commands_enabled": False, "invente": True}})
        self.assertEqual(response.status_code, 400)
        self.assertIsNone(stored_policy())


class RecipeGuardTests(unittest.TestCase):
    """Le garde-fou qui refuse la base reelle.

    Ne du defaut de methode de la Phase 30 : deux verifications lancees par
    `python -c "import main"` sans `NOVA_DB` ont applique la migration additive
    a la base de production. Rien n'a ete perdu — la table est vide et les
    defauts sont permissifs — mais la recette etait censee se derouler sur une
    copie.

    La lecon n'est pas « faire attention ». `import main` execute `init_db()`,
    donc **importer est une operation mutante**, et une regle qui repose sur la
    vigilance cede un jour de fatigue. D'ou un module qui refuse plutot qu'un
    rappel qui previent.
    """

    def guard(self, value):
        environment = dict(os.environ)
        if value is None:
            environment.pop("NOVA_DB", None)
        else:
            environment["NOVA_DB"] = value
        with patch.dict(os.environ, environment, clear=True):
            import recipe_env
            return recipe_env.require_temporary_database()

    def test_an_undefined_database_is_refused(self):
        with self.assertRaises(Exception) as raised:
            self.guard(None)
        self.assertIn("NOVA_DB", str(raised.exception))

    def test_the_real_database_is_refused(self):
        with self.assertRaises(Exception):
            self.guard(str(Path.cwd() / "nova.db"))

    def test_a_path_outside_the_temporary_directory_is_refused(self):
        """Une variable definie ne prouve rien si elle pointe ailleurs."""
        with self.assertRaises(Exception):
            self.guard(str(Path.cwd() / "ailleurs.db"))

    def test_a_temporary_copy_is_accepted(self):
        candidate = Path(tempfile.gettempdir()) / "nova-recette.db"
        self.assertEqual(self.guard(str(candidate)), candidate.resolve())


class BrowserReachabilityTests(unittest.TestCase):
    """Ce que `TestClient` ne peut pas voir.

    La console est servie depuis une autre origine que l'API. Avant toute
    requete non simple, le navigateur envoie un controle prealable, et il
    refuse si la methode n'est pas annoncee. `TestClient` n'en envoie jamais :
    une methode oubliee passe donc tous les tests serveur et ne casse que dans
    un vrai navigateur.

    C'est exactement ce qui est arrive en Phase 29. `PUT` manquait, la mise a
    jour de policy etait le premier `PUT` de la console, et l'echec remontait
    en « panne reseau » — un message qui envoyait chercher le probleme du cote
    de la connexion. Trouve par une recette humaine, pas par les tests.

    Ces tests lisent les constantes plutot que le middleware installe : la
    liste doit etre correcte meme quand aucune origine n'est configuree, comme
    c'est le cas dans la suite de tests.
    """

    def api_methods(self) -> set[str]:
        methods = set()
        for route in main.app.routes:
            if not getattr(route, "path", "").startswith("/api/"):
                continue
            methods |= {m.upper() for m in getattr(route, "methods", set())}
        return methods - {"HEAD"}

    def test_every_method_the_api_exposes_is_announced_to_browsers(self):
        declared = {m.upper() for m in main.ADMIN_CORS_METHODS}
        missing = self.api_methods() - declared
        self.assertEqual(
            missing, set(),
            f"methodes exposees mais absentes de la liste CORS : {sorted(missing)}. "
            "Un navigateur refusera le controle prealable et la console "
            "annoncera une panne reseau qui n'existe pas.")

    def test_the_policy_update_method_is_reachable_from_a_browser(self):
        """Le cas concret, nomme : la route qui a revele le defaut."""
        route = next(
            r for r in main.app.routes
            if getattr(r, "path", "") == "/api/control/organization/policy"
            and "PUT" in getattr(r, "methods", set()))
        self.assertIn("PUT", {m.upper() for m in main.ADMIN_CORS_METHODS}, route.path)

    def test_the_authorization_header_is_announced(self):
        """Sans lui, aucune session d'administration ne franchirait le
        controle prealable."""
        headers = {h.lower() for h in main.ADMIN_CORS_HEADERS}
        self.assertIn("authorization", headers)
        self.assertIn("content-type", headers)

    def test_no_wildcard_method_or_header(self):
        """Une etoile passerait tous les tests et annulerait la reflexion.

        La liste doit rester explicite : c'est elle qui oblige a se demander,
        a chaque nouvelle route, ce que le navigateur a le droit de faire.
        """
        self.assertNotIn("*", main.ADMIN_CORS_METHODS)
        self.assertNotIn("*", main.ADMIN_CORS_HEADERS)

    def test_the_middleware_uses_those_constants(self):
        """Les constantes ne servent a rien si le middleware en declare
        d'autres."""
        source = inspect.getsource(main)
        self.assertIn("allow_methods=ADMIN_CORS_METHODS", source)
        self.assertIn("allow_headers=ADMIN_CORS_HEADERS", source)


if __name__ == "__main__":
    unittest.main()
