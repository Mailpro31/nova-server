"""Nova Business — une organisation de type `business` traverse le même
lifecycle Organization qu'un établissement.

## Ce que ce fichier prouve, et ce qu'il refuse de prouver

Il n'y a **pas** de backend Business. Il n'y a pas de route Business, pas de
policy Business, pas de package Business, pas de runtime Business. Une
entreprise est une organisation dont `org_type` vaut `business`, et c'est tout :
le reste — SSO, SCIM, policies, packages, AI Runtime, Connector, deployment,
rôles de sécurité — est exactement l'infrastructure déjà en place.

Ce fichier existe donc pour vérifier une seule chose, sous beaucoup d'angles :

    changer `org_type` ne fait rien perdre, et ne fait rien gagner
    qui n'ait été explicitement accordé.

Chaque test qui ajouterait une route, une clé de policy ou un type de package
propre à Business serait un test de trop : il décrirait une architecture
parallèle que Nova n'a pas.

## Les deux notions qui ne se confondent jamais

    member_role / member_type    le métier            `employee`, `manager`
    security_role                les droits Nova      `member`, `it_admin`

`manager` est un métier. Un responsable d'équipe n'administre pas Nova parce
qu'il encadre des gens — pas plus qu'un enseignant n'administre parce qu'il
enseigne. Plusieurs tests ci-dessous ne servent qu'à rendre cette séparation
impossible à casser par inadvertance.

## Fixtures

Aucune organisation réelle, aucun domaine réel, aucun tenant réel. `example.com`
est réservé par la RFC 2606 précisément pour cet usage.
"""

import json
import time
import unittest
import uuid

from test_main import AdminTestCase, insert_user  # noqa: F401

import main

# Le schema s'applique **explicitement**. Importer `main` ne migre rien.
main.init_db()


SCIM = {"Content-Type": "application/scim+json"}

# Contenu d'organisation fictif, du genre qu'une entreprise publierait. Il vit
# ici, dans les tests : ce ne sont pas des fonctionnalités du produit, et les
# câbler dans le serveur ferait de deux exemples une promesse.
BUSINESS_STYLE = {
    "name": "Professional Email",
    "instruction": "Rewrite the dictated text as a concise professional email.",
}
BUSINESS_AI_SKILL = {
    "title": "Summarize Meeting Notes",
    "summary": "Turn raw meeting notes into decisions and owners.",
    "instruction": "List decisions, then owners, then open questions.",
    "practice": "Summarize a set of notes into three decisions.",
    "duration_minutes": 5,
    "steps": ["Read the notes", "Extract decisions"],
}
BUSINESS_VOCABULARY = {
    "entries": [
        {"term": "ACME-1", "replacement": "Acme One"},
        {"term": "QBR", "replacement": "quarterly business review"},
    ]
}


def set_organization_type(organization_id: str, org_type: str) -> None:
    with main.db() as conn:
        conn.execute(
            "UPDATE organizations SET org_type=? WHERE id=?",
            (org_type, organization_id),
        )


def make_organization(org_type: str, slug: str, name: str) -> str:
    """Une organisation supplémentaire, servie par le même processus."""
    organization_id = f"org-{uuid.uuid4().hex[:12]}"
    with main.db() as conn:
        conn.execute(
            "INSERT INTO organizations(id, slug, display_name, org_type, "
            "status, created_at) VALUES (?,?,?,?,?,?)",
            (organization_id, slug, name, org_type, "active", time.time()),
        )
    return organization_id


def admin_of(organization_id: str, role: str = main.SECURITY_ROLE_ORGANIZATION_ADMIN) -> dict:
    """Session d'administration d'une organisation donnée.

    Le step-up réel est joué ailleurs (`test_main.AdminTestCase`) ; ce qui
    s'exerce ici est le cloisonnement, pas la réauthentification.
    """
    with main.db() as conn:
        user_id = main.create_user(
            conn,
            organization_id,
            f"admin-{uuid.uuid4().hex[:8]}@example.com",
            security_role=role,
        )
        account = main.user_by_id(conn, user_id)
    session = main.create_admin_session(account, "test")
    return {"Authorization": f"Bearer {session['token']}"}


def clear_packages_and_policies() -> None:
    with main.db() as conn:
        conn.execute("DELETE FROM organization_package_versions")
        conn.execute("DELETE FROM organization_packages")
        conn.execute("DELETE FROM organization_policies")


# ─────────────────────────────────────────────────────────────────────────────
# 1. Autorité sur la nature de l'organisation
# ─────────────────────────────────────────────────────────────────────────────


class OrganizationTypeAuthorityTests(unittest.TestCase):
    """`organizations.org_type` décide, pas `NOVA_ORGANIZATION_TYPE`.

    L'environnement **amorce** la première écriture. Une organisation déjà
    créée n'en dépend plus — et c'est ce qui permet à un même processus de
    servir une école et une entreprise sans que l'une hérite de la nature de
    l'autre.
    """

    def test_both_natures_are_declared(self):
        self.assertEqual(main.ORGANIZATION_TYPES, ("education", "business"))

    def test_an_unknown_nature_falls_back_instead_of_being_invented(self):
        for value in (None, "", "   ", "school", "entreprise", "BUSINESS "):
            with self.subTest(value=value):
                self.assertIn(
                    main.normalize_organization_type(value),
                    main.ORGANIZATION_TYPES,
                )
        # Le seul cas où la valeur est retenue : elle est réellement connue.
        self.assertEqual(main.normalize_organization_type("business"), "business")
        self.assertEqual(main.normalize_organization_type("Education"), "education")

    def test_the_row_is_the_authority_not_the_environment(self):
        business = make_organization("business", f"biz-{uuid.uuid4().hex[:6]}", "Acme")
        education = make_organization("education", f"edu-{uuid.uuid4().hex[:6]}", "École")
        try:
            self.assertEqual(main.organization_type_of(business), "business")
            self.assertEqual(main.organization_type_of(education), "education")
        finally:
            with main.db() as conn:
                conn.execute("DELETE FROM organizations WHERE id IN (?,?)",
                             (business, education))

    def test_an_unknown_organization_does_not_invent_a_nature(self):
        self.assertEqual(
            main.organization_type_of("organisation-inexistante"),
            main.ORGANIZATION_TYPE,
        )


# ─────────────────────────────────────────────────────────────────────────────
# 2. Contrat annoncé au poste
# ─────────────────────────────────────────────────────────────────────────────


class BusinessContractTests(AdminTestCase):
    """Ce qu'un poste apprend d'une organisation Business."""

    def setUp(self):
        super().setUp()
        self.organization = main.nova_organization_id()
        self.addCleanup(set_organization_type, self.organization, "education")

    def test_config_announces_the_business_nature(self):
        set_organization_type(self.organization, "business")
        body = self.client.get("/api/config").json()
        self.assertEqual(body["organization_type"], "business")

    def test_config_still_announces_education_for_a_campus(self):
        set_organization_type(self.organization, "education")
        body = self.client.get("/api/config").json()
        self.assertEqual(body["organization_type"], "education")

    def test_me_announces_the_business_nature_to_its_member(self):
        set_organization_type(self.organization, "business")
        token = self.sign_in("salarie@example.com")
        body = self.client.get("/api/me", headers=self.auth(token)).json()
        self.assertEqual(body["organization_type"], "business")
        # Le contrat historique ne bouge pas : un poste déployé aujourd'hui
        # continue de lire les mêmes champs, aux mêmes types.
        self.assertIsInstance(body["email"], str)
        self.assertIsInstance(body["role"], str)
        self.assertIsInstance(body["cohort"], str)
        self.assertIsInstance(body["organization"], str)

    def test_a_business_member_keeps_the_nova_core(self):
        """Business n'est pas une édition amoindrie : le Core reste ouvert."""
        set_organization_type(self.organization, "business")
        token = self.sign_in("salarie@example.com")
        body = self.client.get("/api/me", headers=self.auth(token)).json()
        for capability in ("dictation", "rewrite", "styles"):
            self.assertIn(capability, body["capabilities"])

    def test_the_desktop_cannot_announce_its_own_nature(self):
        """Le poste lit ; il ne déclare pas.

        Aucun corps, aucun en-tête, aucun paramètre de requête ne doit pouvoir
        transformer une organisation Education en organisation Business : ce
        serait un moyen d'obtenir la configuration d'un autre tenant.
        """
        set_organization_type(self.organization, "education")
        token = self.sign_in("etudiant@example.com")
        body = self.client.get(
            "/api/me?organization_type=business",
            headers={**self.auth(token), "X-Nova-Organization-Type": "business"},
        ).json()
        self.assertEqual(body["organization_type"], "education")


# ─────────────────────────────────────────────────────────────────────────────
# 3. Métiers d'entreprise
# ─────────────────────────────────────────────────────────────────────────────


class BusinessMemberTypeTests(AdminTestCase):
    def setUp(self):
        super().setUp()
        self.organization = main.nova_organization_id()
        self.addCleanup(set_organization_type, self.organization, "education")

    def test_business_roles_are_accepted_metiers(self):
        for role in ("employee", "manager"):
            with self.subTest(role=role):
                self.assertIn(role, main.MEMBER_ROLES)
                self.assertEqual(main.member_type_for(role), role)

    def test_education_roles_are_untouched(self):
        self.assertEqual(main.member_type_for("student"), "student")
        self.assertEqual(main.member_type_for("teacher"), "teacher")
        self.assertEqual(main.member_type_for("staff"), "staff")
        self.assertEqual(main.member_type_for("partner"), "other")

    def test_an_unknown_role_still_produces_no_member_type(self):
        for role in ("ceo", "", "   ", "administrator"):
            with self.subTest(role=role):
                self.assertIsNone(main.member_type_for(role))

    def test_a_new_business_account_is_an_employee_not_a_student(self):
        set_organization_type(self.organization, "business")
        token = self.sign_in("nouveau@example.com")
        body = self.client.get("/api/me", headers=self.auth(token)).json()
        self.assertEqual(body["role"], "employee")
        self.assertEqual(body["membership"]["member_type"], "employee")

    def test_a_new_education_account_is_still_a_student(self):
        set_organization_type(self.organization, "education")
        token = self.sign_in("nouvelle@example.com")
        body = self.client.get("/api/me", headers=self.auth(token)).json()
        self.assertEqual(body["role"], "student")
        self.assertEqual(body["membership"]["member_type"], "student")

    def test_an_administrator_can_set_a_business_metier(self):
        set_organization_type(self.organization, "business")
        self.sign_in("salarie@example.com")
        admin = self.admin_session()
        with main.db() as conn:
            account = main.user_by_email(conn, self.organization, "salarie@example.com")
        response = self.client.post(
            f"/api/control/organizations/{self.organization}/members/"
            f"{account['user_id']}/profile",
            headers=self.auth(admin),
            json={"member_role": "manager"},
        )
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["member"]["member_type"], "manager")
        self.assertEqual(response.json()["member"]["security_role"], "member")

    def test_an_invented_metier_is_refused(self):
        set_organization_type(self.organization, "business")
        self.sign_in("salarie@example.com")
        admin = self.admin_session()
        with main.db() as conn:
            account = main.user_by_email(conn, self.organization, "salarie@example.com")
        response = self.client.post(
            f"/api/control/organizations/{self.organization}/members/"
            f"{account['user_id']}/profile",
            headers=self.auth(admin),
            json={"member_role": "vice_president"},
        )
        self.assertEqual(response.status_code, 400)


# ─────────────────────────────────────────────────────────────────────────────
# 4. Métier ≠ droit
# ─────────────────────────────────────────────────────────────────────────────


class BusinessSecurityRoleTests(AdminTestCase):
    def setUp(self):
        super().setUp()
        self.organization = main.nova_organization_id()
        set_organization_type(self.organization, "business")
        self.addCleanup(set_organization_type, self.organization, "education")

    def test_no_business_metier_ever_grants_administration(self):
        """Le cas dangereux : un `manager` traité comme un administrateur."""
        for role in ("employee", "manager", "staff", "partner"):
            with self.subTest(role=role):
                email = f"{role}@example.com"
                self.sign_in(email)
                with main.db() as conn:
                    conn.execute(
                        "UPDATE users SET role=? WHERE normalized_email=? "
                        "AND organization_id=?",
                        (role, main.normalize_email(email), self.organization),
                    )
                    account = main.user_by_email(conn, self.organization, email)
                self.assertEqual(main.security_role_for(account), "member")

    def test_the_security_roles_are_the_organization_ones(self):
        """Business ne fait pas apparaître de rôle d'administration à lui."""
        self.assertEqual(
            main.SECURITY_ROLES,
            # `content_editor` est un role d'organisation, pas propre a
            # Business : il existe aussi pour une ecole.
            frozenset({"member", "organization_admin", "it_admin", "read_only",
                       "content_editor"}),
        )

    def test_a_business_member_cannot_change_the_policy(self):
        token = self.sign_in("salarie@example.com")
        response = self.client.put(
            "/api/control/organization/policy",
            headers=self.auth(token),
            json={"expected_revision": 0, "settings": {"ai_skills_enabled": False}},
        )
        self.assertIn(response.status_code, {401, 403})

    def test_a_business_member_cannot_publish_content(self):
        token = self.sign_in("salarie@example.com")
        response = self.client.post(
            f"/api/control/organizations/{self.organization}/packages",
            headers=self.auth(token),
            json={"package_type": "style", "name": "Interdit"},
        )
        self.assertIn(response.status_code, {401, 403})

    def test_a_business_member_cannot_read_the_ai_runtime(self):
        token = self.sign_in("salarie@example.com")
        response = self.client.get(
            "/api/control/organization/ai-runtime", headers=self.auth(token)
        )
        self.assertIn(response.status_code, {401, 403})

    def test_a_business_member_cannot_administer_connectors(self):
        token = self.sign_in("salarie@example.com")
        response = self.client.get(
            "/api/control/connectors", headers=self.auth(token)
        )
        self.assertIn(response.status_code, {401, 403})

    def test_a_business_member_cannot_name_administrators(self):
        token = self.sign_in("salarie@example.com")
        with main.db() as conn:
            account = main.user_by_email(conn, self.organization, "salarie@example.com")
        response = self.client.post(
            f"/api/control/organizations/{self.organization}/administrators/"
            f"{account['user_id']}/role",
            headers=self.auth(token),
            json={"security_role": "organization_admin"},
        )
        self.assertIn(response.status_code, {401, 403})


# ─────────────────────────────────────────────────────────────────────────────
# 5. SSO — le même, sans fournisseur Business
# ─────────────────────────────────────────────────────────────────────────────


class BusinessSsoTests(AdminTestCase):
    def setUp(self):
        super().setUp()
        self.organization = main.nova_organization_id()
        set_organization_type(self.organization, "business")
        self.addCleanup(set_organization_type, self.organization, "education")

    def test_the_provider_catalogue_is_the_organization_one(self):
        """Ni « Business SSO », ni « Business OAuth » : les trois fournisseurs
        d'entreprise sont déjà ceux de l'infrastructure Organization."""
        self.assertEqual(
            set(main.ADAPTERS),
            {"microsoft_entra", "google_workspace", "oidc"},
        )

    def test_a_business_organization_declares_the_same_providers(self):
        admin = self.admin_session()
        view = self.client.get(
            f"/api/control/organizations/{self.organization}",
            headers=self.auth(admin),
        )
        self.assertEqual(view.status_code, 200, view.text)
        body = view.json()
        self.assertEqual(body["organization"]["org_type"], "business")
        self.assertIn("providers", json.dumps(body))

    def test_no_password_or_secret_is_ever_returned_to_a_member(self):
        token = self.sign_in("salarie@example.com")
        raw = self.client.get("/api/me", headers=self.auth(token)).text
        for forbidden in ("password", "client_secret", "token_hash", "external_subject"):
            self.assertNotIn(forbidden, raw)


# ─────────────────────────────────────────────────────────────────────────────
# 6. SCIM — le même endpoint, un tenant Business
# ─────────────────────────────────────────────────────────────────────────────


class BusinessScimTests(unittest.TestCase):
    """Un tenant Business est provisionné par le SCIM 2.0 déjà en place."""

    @classmethod
    def setUpClass(cls):
        from fastapi.testclient import TestClient

        cls.client = TestClient(main.app)

    def setUp(self):
        self.organization = make_organization(
            "business", f"scim-biz-{uuid.uuid4().hex[:6]}", "Acme Industries"
        )
        self.admin = admin_of(self.organization)
        response = self.client.post(
            f"/api/control/organizations/{self.organization}/provisioning/credentials",
            headers=self.admin,
            json={"name": "Entra", "provider_context": "microsoft_entra"},
        )
        self.assertEqual(response.status_code, 200, response.text)
        self.scim = {
            **SCIM,
            "Authorization": f"Bearer {response.json()['token']}",
        }

    def tearDown(self):
        with main.db() as conn:
            conn.execute("DELETE FROM organizations WHERE id=?", (self.organization,))

    def user_payload(self, user_name: str, external_id: str) -> dict:
        return {
            "schemas": ["urn:ietf:params:scim:schemas:core:2.0:User"],
            "externalId": external_id,
            "userName": user_name,
            "active": True,
            "name": {"givenName": "Dana", "familyName": "Ops"},
            "displayName": "Dana Ops",
            "emails": [{"value": user_name, "type": "work", "primary": True}],
        }

    def test_a_business_user_is_provisioned_updated_and_deprovisioned(self):
        created = self.client.post(
            "/scim/v2/Users",
            headers=self.scim,
            json=self.user_payload("dana.ops@example.com", "biz-object-1"),
        )
        self.assertIn(created.status_code, {200, 201}, created.text)
        scim_id = created.json()["id"]

        renamed = self.client.patch(
            f"/scim/v2/Users/{scim_id}",
            headers=self.scim,
            json={
                "schemas": ["urn:ietf:params:scim:api:messages:2.0:PatchOp"],
                "Operations": [
                    {"op": "replace", "path": "displayName", "value": "Dana Operations"}
                ],
            },
        )
        self.assertEqual(renamed.status_code, 200, renamed.text)

        disabled = self.client.patch(
            f"/scim/v2/Users/{scim_id}",
            headers=self.scim,
            json={
                "schemas": ["urn:ietf:params:scim:api:messages:2.0:PatchOp"],
                "Operations": [{"op": "replace", "path": "active", "value": False}],
            },
        )
        self.assertEqual(disabled.status_code, 200, disabled.text)
        self.assertFalse(disabled.json()["active"])

        removed = self.client.delete(f"/scim/v2/Users/{scim_id}", headers=self.scim)
        self.assertIn(removed.status_code, {200, 204}, removed.text)

    def test_a_provisioned_business_user_is_never_an_administrator(self):
        created = self.client.post(
            "/scim/v2/Users",
            headers=self.scim,
            json=self.user_payload("sam.ops@example.com", "biz-object-2"),
        )
        self.assertIn(created.status_code, {200, 201}, created.text)
        with main.db() as conn:
            account = main.user_by_email(conn, self.organization, "sam.ops@example.com")
        self.assertEqual(main.security_role_for(account), "member")

    def test_a_directory_group_is_not_a_security_role(self):
        """Le cas dangereux : un groupe « Admins » de l'annuaire d'entreprise
        qui accorderait l'administration de Nova."""
        self.client.post(
            "/scim/v2/Users",
            headers=self.scim,
            json=self.user_payload("lee.ops@example.com", "biz-object-3"),
        )
        group = self.client.post(
            "/scim/v2/Groups",
            headers=self.scim,
            json={
                "schemas": ["urn:ietf:params:scim:schemas:core:2.0:Group"],
                "externalId": "biz-group-admins",
                "displayName": "Admins",
            },
        )
        self.assertIn(group.status_code, {200, 201}, group.text)
        with main.db() as conn:
            account = main.user_by_email(conn, self.organization, "lee.ops@example.com")
        self.assertEqual(main.security_role_for(account), "member")

    def test_scim_cannot_write_a_security_role(self):
        response = self.client.post(
            "/scim/v2/Users",
            headers=self.scim,
            json={
                **self.user_payload("root.ops@example.com", "biz-object-4"),
                "roles": [{"value": "organization_admin", "primary": True}],
            },
        )
        if response.status_code in {200, 201}:
            with main.db() as conn:
                account = main.user_by_email(
                    conn, self.organization, "root.ops@example.com"
                )
            self.assertEqual(main.security_role_for(account), "member")
        else:
            self.assertEqual(response.status_code, 400, response.text)


# ─────────────────────────────────────────────────────────────────────────────
# 7. Policies et packages — les mêmes primitives
# ─────────────────────────────────────────────────────────────────────────────


class BusinessPolicyAndPackageTests(AdminTestCase):
    def setUp(self):
        super().setUp()
        clear_packages_and_policies()
        self.organization = main.nova_organization_id()
        set_organization_type(self.organization, "business")
        self.addCleanup(set_organization_type, self.organization, "education")
        self.addCleanup(clear_packages_and_policies)
        self.token = self.admin_session()

    def base(self) -> str:
        return f"/api/control/organizations/{self.organization}/packages"

    def publish(self, package_type: str, name: str, content: dict) -> str:
        package = self.client.post(
            self.base(), headers=self.auth(self.token),
            json={"package_type": package_type, "name": name})
        self.assertEqual(package.status_code, 200, package.text)
        package_id = package.json()["package_id"]
        versions = self.client.post(
            f"{self.base()}/{package_id}/versions",
            headers=self.auth(self.token), json={"content": content})
        self.assertEqual(versions.status_code, 200, versions.text)
        version_id = versions.json()["versions"][0]["version_id"]
        published = self.client.post(
            f"{self.base()}/{package_id}/versions/{version_id}/publish",
            headers=self.auth(self.token), json={"activate": True})
        self.assertEqual(published.status_code, 200, published.text)
        return package_id

    def delivered(self, member_token: str) -> dict:
        response = self.client.get(
            "/api/organization/packages", headers=self.auth(member_token))
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()

    def test_no_business_specific_package_type_exists(self):
        self.assertEqual(
            main.PACKAGE_TYPES, ("style", "ai_skill", "vocabulary")
        )

    def test_no_business_specific_policy_key_exists(self):
        self.assertEqual(
            set(main.POLICY_SETTINGS),
            {
                "ai_skills_enabled",
                "organization_vocabulary_enabled",
                "voice_commands_enabled",
                "engineering_notes_enabled",
                "file_import_enabled",
                # Cle Core apportee par Learn, pas par Business : elle ferme
                # Learn pour une organisation quelle que soit sa nature. La
                # liste reste exhaustive pour continuer a refuser l'ajout
                # d'une clef propre a l'entreprise.
                "learning_enabled",
                # Cle Core : borne la duree d'une dictee pour proteger le
                # moteur partage, quelle que soit la nature de l'organisation.
                "max_dictation_seconds",
                # Cle Core : plafond de mises au propre par membre et par jour
                # (n° 21 ter), quelle que soit la nature de l'organisation.
                "daily_rewrites_per_member",
                # Categories de l'application, communes a toute organisation.
                "styles_enabled",
                "generated_instructions_enabled",
                "past_dictations_enabled",
                # Mode reunion : cle Core. Elle ferme la capture des autres
                # participants pour une organisation quelle que soit sa
                # nature, et une ecole a les memes raisons de l'encadrer
                # qu'une entreprise.
                "meeting_enabled",
                # Styles desactives un par un : commun a toute organisation.
                "disabled_style_ids",
                # Affichage des groupes au membre : commun a toute organisation.
                "member_groups_visible",
            },
        )

    def test_a_business_employee_receives_styles_skills_and_vocabulary(self):
        self.publish("style", "Professional Email", BUSINESS_STYLE)
        self.publish("ai_skill", "Summarize Meeting Notes", BUSINESS_AI_SKILL)
        self.publish("vocabulary", "Company Terms", BUSINESS_VOCABULARY)

        member = self.sign_in("salarie@example.com")
        packages = self.delivered(member)["packages"]
        self.assertEqual(
            sorted(package["type"] for package in packages),
            ["ai_skill", "style", "vocabulary"],
        )
        style = next(p for p in packages if p["type"] == "style")
        self.assertTrue(style["id"].startswith("org_style_"))

    def test_the_organization_vocabulary_reaches_the_business_member(self):
        self.publish("vocabulary", "Company Terms", BUSINESS_VOCABULARY)
        terms = {
            entry["term"]
            for entry in main.organization_vocabulary(self.organization)
        }
        self.assertEqual(terms, {"ACME-1", "QBR"})

    def test_a_closed_policy_withholds_business_content(self):
        """Le contenu existe, la permission non."""
        self.publish("vocabulary", "Company Terms", BUSINESS_VOCABULARY)
        response = self.client.put(
            "/api/control/organization/policy",
            headers=self.auth(self.token),
            json={"expected_revision": 0,
                  "settings": {"organization_vocabulary_enabled": False}},
        )
        self.assertEqual(response.status_code, 200, response.text)

        member = self.sign_in("salarie@example.com")
        self.assertEqual(self.delivered(member)["packages"], [])

    def test_the_authoritative_skill_instruction_never_reaches_the_desktop(self):
        """La garantie que Business hérite telle quelle.

        Le poste reçoit un **identifiant** de Skill et le texte à traiter ;
        l'instruction reste côté serveur, où le catalogue publié la décide.
        S'il la recevait, il pourrait la remplacer — et le catalogue de
        l'entreprise ne serait plus qu'une suggestion.
        """
        package_id = self.publish(
            "ai_skill", "Summarize Meeting Notes", BUSINESS_AI_SKILL)
        member = self.sign_in("salarie@example.com")
        skill = next(
            package
            for package in self.delivered(member)["packages"]
            if package["package_id"] == package_id
        )
        self.assertNotIn("instruction", skill["content"])
        self.assertNotIn(
            BUSINESS_AI_SKILL["instruction"],
            self.client.get(
                "/api/organization/packages", headers=self.auth(member)).text,
        )
        # Elle est bien là, côté serveur, et c'est elle qui sera exécutée.
        server_side = next(
            package
            for package in main.effective_packages(self.organization)["packages"]
            if package["package_id"] == package_id
        )
        self.assertEqual(
            server_side["content"]["instruction"], BUSINESS_AI_SKILL["instruction"]
        )

    def test_an_organization_style_keeps_its_instruction_for_the_business_member(self):
        """Un Style, lui, garde sa consigne : elle *est* le Style, et le moteur
        local du poste doit pouvoir l'appliquer sans serveur."""
        package_id = self.publish("style", "Professional Email", BUSINESS_STYLE)
        member = self.sign_in("salarie@example.com")
        style = next(
            package
            for package in self.delivered(member)["packages"]
            if package["package_id"] == package_id
        )
        self.assertEqual(
            style["content"]["instruction"], BUSINESS_STYLE["instruction"]
        )


# ─────────────────────────────────────────────────────────────────────────────
# 8. Cloisonnement
# ─────────────────────────────────────────────────────────────────────────────


class BusinessTenantIsolationTests(AdminTestCase):
    """Business A ↔ Business B, puis Education ↔ Business.

    Une entreprise ne doit pas plus voir sa concurrente qu'une école ne voit
    l'autre. Le cloisonnement ne dépend pas de la nature de l'organisation, et
    ces tests sont là pour le prouver dans les deux combinaisons.
    """

    def setUp(self):
        super().setUp()
        clear_packages_and_policies()
        self.addCleanup(clear_packages_and_policies)
        self.business_a = make_organization("business", f"a-{uuid.uuid4().hex[:6]}", "Acme")
        self.business_b = make_organization("business", f"b-{uuid.uuid4().hex[:6]}", "Globex")
        self.education = make_organization("education", f"e-{uuid.uuid4().hex[:6]}", "École")
        self.addCleanup(self.drop_organizations)
        self.admin_a = admin_of(self.business_a)
        self.admin_b = admin_of(self.business_b)
        self.admin_edu = admin_of(self.education)

    def drop_organizations(self):
        with main.db() as conn:
            conn.execute(
                "DELETE FROM organizations WHERE id IN (?,?,?)",
                (self.business_a, self.business_b, self.education),
            )

    def unreachable(self, headers: dict, organization_id: str, path: str):
        response = self.client.get(
            f"/api/control/organizations/{organization_id}{path}", headers=headers
        )
        # 404, jamais 403 : découvrir qu'une organisation existe est déjà une
        # information.
        self.assertEqual(response.status_code, 404, response.text)

    def test_business_a_cannot_reach_business_b(self):
        for path in ("", "/members", "/packages", "/administrators", "/audit"):
            with self.subTest(path=path or "/"):
                self.unreachable(self.admin_a, self.business_b, path)

    def test_business_b_cannot_reach_business_a(self):
        for path in ("", "/members", "/packages"):
            with self.subTest(path=path or "/"):
                self.unreachable(self.admin_b, self.business_a, path)

    def test_an_education_administrator_cannot_reach_a_business_tenant(self):
        for path in ("", "/members", "/packages"):
            with self.subTest(path=path or "/"):
                self.unreachable(self.admin_edu, self.business_a, path)

    def test_a_business_administrator_cannot_reach_an_education_tenant(self):
        for path in ("", "/members", "/packages"):
            with self.subTest(path=path or "/"):
                self.unreachable(self.admin_a, self.education, path)

    def test_each_tenant_keeps_its_own_nature(self):
        self.assertEqual(main.organization_type_of(self.business_a), "business")
        self.assertEqual(main.organization_type_of(self.education), "education")

    def test_a_business_member_never_receives_a_neighbours_content(self):
        with main.db() as conn:
            conn.execute(
                "INSERT INTO organization_packages(package_id, organization_id, "
                "package_type, name, status, active_version_id, revision, "
                "created_at, updated_at) VALUES (?,?,?,?,?,?,?,?,?)",
                ("p-globex", self.business_b, "vocabulary", "Globex", "published",
                 "v-globex", 0, 0.0, 0.0))
            conn.execute(
                "INSERT INTO organization_package_versions(version_id, package_id, "
                "organization_id, version_number, schema_version, content, "
                "content_hash, status, validation_status, revision, created_at, "
                "updated_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                ("v-globex", "p-globex", self.business_b, 1, 1,
                 json.dumps({"entries": [{"term": "CONFIDENTIEL", "replacement": "x"}]}),
                 "h", "published", "valid", 0, 0.0, 0.0))

        delivered = main.effective_packages(self.business_a)
        raw = json.dumps(delivered)
        self.assertNotIn("CONFIDENTIEL", raw)
        self.assertNotIn("p-globex", raw)

    def test_a_policy_written_for_one_tenant_does_not_govern_the_other(self):
        response = self.client.put(
            "/api/control/organization/policy",
            headers=self.admin_a,
            json={"expected_revision": 0,
                  "settings": {"organization_vocabulary_enabled": False}},
        )
        self.assertEqual(response.status_code, 200, response.text)
        self.assertFalse(
            main.public_campus_config(self.business_a)["capabilities"]["dictionary"]
        )
        self.assertTrue(
            main.public_campus_config(self.business_b)["capabilities"]["dictionary"]
        )
        self.assertTrue(
            main.public_campus_config(self.education)["capabilities"]["dictionary"]
        )

    def test_each_tenant_receives_its_own_nature_in_the_same_process(self):
        """Le cas que la variable d'environnement ne savait pas décrire."""
        self.assertEqual(
            main.public_campus_config(self.business_a)["organization_type"],
            "business",
        )
        self.assertEqual(
            main.public_campus_config(self.education)["organization_type"],
            "education",
        )


# ─────────────────────────────────────────────────────────────────────────────
# 9. Runtime privé et Connector — inchangés
# ─────────────────────────────────────────────────────────────────────────────


class BusinessRuntimeAndConnectorTests(AdminTestCase):
    def setUp(self):
        super().setUp()
        self.organization = main.nova_organization_id()
        set_organization_type(self.organization, "business")
        self.addCleanup(set_organization_type, self.organization, "education")
        self.token = self.admin_session()

    def test_the_ai_runtime_configuration_is_the_organization_one(self):
        response = self.client.get(
            "/api/control/organization/ai-runtime", headers=self.auth(self.token)
        )
        self.assertEqual(response.status_code, 200, response.text)
        self.assertNotIn("business", json.dumps(response.json()).lower())

    def test_the_connector_inventory_is_the_organization_one(self):
        response = self.client.get(
            "/api/control/connectors", headers=self.auth(self.token)
        )
        self.assertEqual(response.status_code, 200, response.text)

    def test_the_organization_view_reports_the_business_nature(self):
        view = main.control_organization_view(self.organization)
        self.assertEqual(view["organization"]["org_type"], "business")
        self.assertIn("deployment", view)
        self.assertIn("discovery", view)
        self.assertIn("seats", view)


# ─────────────────────────────────────────────────────────────────────────────
# 10. Confidentialité
# ─────────────────────────────────────────────────────────────────────────────


class BusinessPrivacyTests(AdminTestCase):
    def setUp(self):
        super().setUp()
        self.organization = main.nova_organization_id()
        set_organization_type(self.organization, "business")
        self.addCleanup(set_organization_type, self.organization, "education")

    def test_the_public_configuration_carries_no_member_data(self):
        self.sign_in("salarie@example.com")
        raw = self.client.get("/api/config").text
        self.assertNotIn("salarie@example.com", raw)

    def test_the_member_view_carries_no_secret(self):
        token = self.sign_in("salarie@example.com")
        raw = self.client.get(
            "/api/organization/packages", headers=self.auth(token)
        ).text
        for forbidden in ("token", "secret", "password", "client_secret"):
            self.assertNotIn(forbidden, raw.lower())


if __name__ == "__main__":
    unittest.main()
