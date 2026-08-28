"""Phase 31 — packages distribues par l'organisation.

Un fichier a part, comme les policies : ce qui se joue ici n'est pas une
permission mais du **contenu**, et melanger les deux rendrait les deux plus
difficiles a relire.

La distinction structure tout le fichier :

    POLICY    cette capacite est-elle autorisee ?
    PACKAGE   quel contenu l'organisation distribue-t-elle ?

Un package actif dont la policy est fermee n'est pas distribue. Le contenu
existe, la permission non.
"""

import json
import unittest
from unittest.mock import patch

from test_main import AdminTestCase  # noqa: F401

import main


def clear_packages():
    with main.db() as conn:
        conn.execute("DELETE FROM organization_package_versions")
        conn.execute("DELETE FROM organization_packages")
        conn.execute("DELETE FROM organization_policies")


VOCABULARY = {"entries": [{"term": "Nova", "replacement": "Nova"},
                          {"term": "SSO", "replacement": "single sign-on"}]}
STYLE = {"name": "Example Style", "instruction": "Write in plain sentences."}
AI_SKILL = {"title": "Example Skill", "summary": "An example.",
            "instruction": "Summarise the text in three bullet points.",
            "practice": "Try it once.", "duration_minutes": 4, "steps": ["One"]}


class ContentValidationTests(unittest.TestCase):
    """Valider **structurellement**, pas seulement syntaxiquement.

    « Le JSON se parse » ne veut rien dire : un document vide se parse aussi.
    """

    def refuse(self, package_type, content, field=None):
        with self.assertRaises(main.PackageValidationError) as raised:
            main.validate_package_content(package_type, content)
        if field:
            self.assertEqual(raised.exception.field, field)
        return raised.exception

    def test_a_valid_document_of_each_type_is_normalized(self):
        for package_type, content in (("style", STYLE), ("ai_skill", AI_SKILL),
                                      ("vocabulary", VOCABULARY)):
            with self.subTest(package_type):
                self.assertTrue(main.validate_package_content(package_type, content))

    def test_an_unknown_type_is_refused(self):
        self.refuse("marketplace", {}, "package_type")

    def test_a_missing_required_field_is_refused(self):
        self.refuse("style", {"instruction": "x"}, "name")
        self.refuse("style", {"name": "x"}, "instruction")

    def test_whitespace_is_not_content(self):
        self.refuse("style", {"name": "   ", "instruction": "x"}, "name")

    def test_a_field_beyond_its_limit_is_refused(self):
        error = self.refuse(
            "style", {"name": "a" * 200, "instruction": "x"}, "name")
        self.assertEqual(error.code, "too_long")

    def test_control_characters_are_refused(self):
        """Ils n'ont aucun sens dans un contenu declaratif, et servent surtout
        a masquer du texte a la relecture."""
        self.refuse("style", {"name": "a\x07b", "instruction": "x"}, "name")

    def test_a_vocabulary_needs_at_least_one_entry(self):
        self.refuse("vocabulary", {"entries": []}, "entries")

    def test_duplicate_vocabulary_terms_are_refused(self):
        """Deux lignes pour le meme terme signifient que quelqu'un attend deux
        resultats differents. En choisir un sans le dire produirait un
        comportement que personne ne saurait expliquer."""
        error = self.refuse(
            "vocabulary",
            {"entries": [{"term": "Nova", "replacement": "a"},
                         {"term": "nova", "replacement": "b"}]})
        self.assertEqual(error.code, "duplicate")

    def test_too_many_vocabulary_entries_are_refused(self):
        entries = [{"term": f"t{i}", "replacement": "x"}
                   for i in range(main.VOCABULARY_ENTRY_MAX + 1)]
        self.refuse("vocabulary", {"entries": entries}, "entries")

    def test_an_ai_skill_duration_must_be_a_whole_number(self):
        for value in ("5", 5.5, True, None):
            with self.subTest(value=value):
                self.refuse("ai_skill", {**AI_SKILL, "duration_minutes": value},
                            "duration_minutes")

    def test_an_ai_skill_carries_no_executable_content(self):
        """Un package est du contenu distribue a des postes.

        Lui laisser porter du code executable ferait de la console
        d'administration un moyen d'executer du code chez chaque membre — ce
        qui n'est pas une fonction produit mais une porte.
        """
        normalized = main.validate_package_content("ai_skill", {
            **AI_SKILL, "script": "rm -rf /", "url": "http://x", "eval": "1+1"})
        self.assertEqual(
            set(normalized),
            {"title", "summary", "instruction", "practice", "duration_minutes",
             "steps"})

    def test_an_ai_skill_without_an_instruction_is_refused(self):
        """C'est ce qui distingue une action d'une fiche.

        Publier un Skill inerte ferait croire a une fonction qui ne fera jamais
        rien — et l'utilisateur le decouvrirait en cliquant.
        """
        content = {k: v for k, v in AI_SKILL.items() if k != "instruction"}
        error = self.refuse("ai_skill", content, "instruction")
        self.assertEqual(error.code, "required")

    def test_the_instruction_is_declarative_text_only(self):
        """Le Skill dit au modele quoi faire ; il n'execute rien.

        Le texte peut contenir n'importe quels mots — c'est une consigne en
        langue naturelle — mais aucun champ du schema ne porte de code, et le
        serveur n'en execute aucun.
        """
        normalized = main.validate_package_content("ai_skill", {
            **AI_SKILL, "instruction": "Resume le texte.",
            "handler": "eval", "plugin": "x", "secret_ref": "y"})
        self.assertNotIn("handler", normalized)
        self.assertNotIn("plugin", normalized)
        self.assertNotIn("secret_ref", normalized)

    def test_unknown_fields_are_dropped_not_stored(self):
        normalized = main.validate_package_content(
            "style", {**STYLE, "enabled": True, "secret": "x"})
        self.assertEqual(set(normalized), {"name", "instruction"})

    def test_a_package_never_carries_its_own_permission(self):
        """Package = contenu. Policy = permission.

        Un `enabled` dans un package creerait une seconde source de verite, et
        la question « qui gagne ? » n'aurait pas de bonne reponse.
        """
        for package_type, content in (("style", STYLE), ("ai_skill", AI_SKILL),
                                      ("vocabulary", VOCABULARY)):
            normalized = main.validate_package_content(package_type, content)
            self.assertNotIn("enabled", normalized)

    def test_an_oversized_document_is_refused(self):
        entries = [{"term": f"t{i}", "replacement": "x" * 190} for i in range(1500)]
        error = self.refuse("vocabulary", {"entries": entries}, "content")
        self.assertEqual(error.code, "too_large")

    def test_the_hash_follows_the_content_not_its_key_order(self):
        a = main.package_content_hash({"name": "x", "instruction": "y"})
        b = main.package_content_hash({"instruction": "y", "name": "x"})
        self.assertEqual(a, b)
        self.assertNotEqual(a, main.package_content_hash({"name": "z"}))


class PackageIdentifierTests(unittest.TestCase):
    def test_an_organization_style_cannot_be_mistaken_for_a_builtin(self):
        """Les presets integres portent `nova_style_…`. Un package qui pourrait
        prendre ce nom masquerait un preset — ou en heriterait les privileges de
        palier."""
        identifier = main.package_identifier("style", "abc")
        self.assertTrue(identifier.startswith("org_"))
        self.assertFalse(identifier.startswith("nova_style_"))

    def test_types_do_not_collide_with_each_other(self):
        identifiers = {main.package_identifier(t, "abc") for t in main.PACKAGE_TYPES}
        self.assertEqual(len(identifiers), len(main.PACKAGE_TYPES))


class PackageLifecycleTests(AdminTestCase):
    def setUp(self):
        super().setUp()
        clear_packages()
        self.organization = main.nova_organization_id()
        self.token = self.admin_session()

    def base(self) -> str:
        return f"/api/control/organizations/{self.organization}/packages"

    def create(self, package_type="vocabulary", name="Example Vocabulary"):
        response = self.client.post(
            self.base(), headers=self.auth(self.token),
            json={"package_type": package_type, "name": name,
                  "description": "An example."})
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()

    def draft(self, package_id, content=None):
        return self.client.post(
            f"{self.base()}/{package_id}/versions", headers=self.auth(self.token),
            json={"content": content or VOCABULARY})

    def publish(self, package_id, version_id, activate=True):
        return self.client.post(
            f"{self.base()}/{package_id}/versions/{version_id}/publish",
            headers=self.auth(self.token), json={"activate": activate})

    def test_a_new_package_has_no_active_version(self):
        """Un package existe avant d'avoir quoi que ce soit a dire."""
        view = self.create()
        self.assertEqual(view["status"], "draft")
        self.assertIsNone(view["active_version_id"])
        self.assertEqual(view["versions"], [])

    def test_an_unknown_type_is_refused(self):
        response = self.client.post(
            self.base(), headers=self.auth(self.token),
            json={"package_type": "marketplace", "name": "x"})
        self.assertEqual(response.status_code, 400)

    def test_a_draft_becomes_version_one(self):
        package = self.create()
        view = self.draft(package["package_id"]).json()
        self.assertEqual(len(view["versions"]), 1)
        self.assertEqual(view["versions"][0]["version_number"], 1)
        self.assertEqual(view["versions"][0]["status"], "draft")
        self.assertIsNone(view["active_version_id"])

    def test_only_one_draft_at_a_time(self):
        """Deux brouillons poseraient la question « lequel devient v4 ? », et
        toute reponse serait arbitraire."""
        package = self.create()
        self.draft(package["package_id"])
        response = self.draft(package["package_id"])
        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.json()["detail"], "PACKAGE_DRAFT_EXISTS")

    def test_publishing_activates_and_distributes(self):
        package = self.create()
        view = self.draft(package["package_id"]).json()
        version = view["versions"][0]["version_id"]
        published = self.publish(package["package_id"], version).json()
        self.assertEqual(published["status"], "published")
        self.assertEqual(published["active_version_id"], version)
        self.assertEqual(published["active_version_number"], 1)

    def test_a_published_version_cannot_be_edited(self):
        """La garantie qui rend le retour arriere fiable : ce qui a ete
        distribue reste exactement ce qui avait ete distribue."""
        package = self.create()
        version = self.draft(package["package_id"]).json()["versions"][0]["version_id"]
        self.publish(package["package_id"], version)
        response = self.client.put(
            f"{self.base()}/{package['package_id']}/versions/{version}",
            headers=self.auth(self.token),
            json={"expected_revision": 0, "content": VOCABULARY})
        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.json()["detail"], "PACKAGE_VERSION_IMMUTABLE")

    def test_a_published_version_cannot_be_deleted(self):
        package = self.create()
        version = self.draft(package["package_id"]).json()["versions"][0]["version_id"]
        self.publish(package["package_id"], version)
        response = self.client.delete(
            f"{self.base()}/{package['package_id']}/versions/{version}",
            headers=self.auth(self.token))
        self.assertEqual(response.status_code, 409)

    def test_a_draft_can_be_discarded(self):
        package = self.create()
        version = self.draft(package["package_id"]).json()["versions"][0]["version_id"]
        view = self.client.delete(
            f"{self.base()}/{package['package_id']}/versions/{version}",
            headers=self.auth(self.token)).json()
        self.assertEqual(view["versions"], [])

    def test_editing_a_draft_requires_the_current_revision(self):
        package = self.create()
        version = self.draft(package["package_id"]).json()["versions"][0]["version_id"]
        path = f"{self.base()}/{package['package_id']}/versions/{version}"
        first = self.client.put(path, headers=self.auth(self.token),
                                json={"expected_revision": 0, "content": VOCABULARY})
        self.assertEqual(first.status_code, 200)
        stale = self.client.put(path, headers=self.auth(self.token),
                                json={"expected_revision": 0, "content": VOCABULARY})
        self.assertEqual(stale.status_code, 409)
        self.assertEqual(stale.json()["detail"], "PACKAGE_REVISION_CONFLICT")

    def test_rollback_reactivates_an_untouched_version(self):
        """Revenir a v1 ne reecrit pas v1 : l'active redesigne une version
        restee intacte."""
        package = self.create()
        pid = package["package_id"]
        v1 = self.draft(pid).json()["versions"][0]["version_id"]
        self.publish(pid, v1)
        first_hash = self.client.get(
            f"{self.base()}/{pid}/versions/{v1}",
            headers=self.auth(self.token)).json()["content_hash"]

        second = {"entries": [{"term": "Autre", "replacement": "autre"}]}
        v2 = [v for v in self.draft(pid, second).json()["versions"]
              if v["status"] == "draft"][0]["version_id"]
        self.publish(pid, v2)

        rolled = self.client.post(
            f"{self.base()}/{pid}/versions/{v1}/activate",
            headers=self.auth(self.token)).json()
        self.assertEqual(rolled["active_version_id"], v1)
        self.assertEqual(rolled["active_version_number"], 1)
        after = self.client.get(f"{self.base()}/{pid}/versions/{v1}",
                                headers=self.auth(self.token)).json()
        self.assertEqual(after["content_hash"], first_hash)

    def test_an_unpublished_version_cannot_be_activated(self):
        package = self.create()
        version = self.draft(package["package_id"]).json()["versions"][0]["version_id"]
        response = self.client.post(
            f"{self.base()}/{package['package_id']}/versions/{version}/activate",
            headers=self.auth(self.token))
        self.assertEqual(response.status_code, 409)

    def test_archiving_stops_distribution_without_erasing(self):
        package = self.create()
        pid = package["package_id"]
        version = self.draft(pid).json()["versions"][0]["version_id"]
        self.publish(pid, version)
        archived = self.client.post(f"{self.base()}/{pid}/archive",
                                    headers=self.auth(self.token)).json()
        self.assertEqual(archived["status"], "archived")
        self.assertIsNone(archived["active_version_id"])
        self.assertEqual(len(archived["versions"]), 1)

    def test_the_list_carries_metadata_not_content(self):
        """Un tableau de packages ne doit pas ramener chaque document."""
        package = self.create()
        self.draft(package["package_id"])
        raw = self.client.get(self.base(), headers=self.auth(self.token)).text
        self.assertNotIn("single sign-on", raw)

    def test_an_unknown_package_is_not_found(self):
        response = self.client.get(f"{self.base()}/inconnu",
                                   headers=self.auth(self.token))
        self.assertEqual(response.status_code, 404)


class PackagePermissionTests(AdminTestCase):
    def setUp(self):
        super().setUp()
        clear_packages()
        self.organization = main.nova_organization_id()

    def base(self) -> str:
        return f"/api/control/organizations/{self.organization}/packages"

    def test_content_manage_is_its_own_capability(self):
        self.assertIn(main.CAPABILITY_CONTENT_MANAGE,
                      main.capabilities_for_role(main.SECURITY_ROLE_ORGANIZATION_ADMIN))
        for role in (main.SECURITY_ROLE_IT_ADMIN, main.SECURITY_ROLE_READ_ONLY,
                     main.SECURITY_ROLE_MEMBER):
            with self.subTest(role):
                self.assertNotIn(main.CAPABILITY_CONTENT_MANAGE,
                                 main.capabilities_for_role(role))

    def test_read_only_may_list_but_not_create(self):
        token = self.admin_session("lecture@example.com",
                                   main.SECURITY_ROLE_READ_ONLY, subject="oid-ro")
        self.assertEqual(
            self.client.get(self.base(), headers=self.auth(token)).status_code, 200)
        response = self.client.post(
            self.base(), headers=self.auth(token),
            json={"package_type": "style", "name": "x"})
        self.assertEqual(response.status_code, 403)

    def test_it_admin_may_list_but_not_create(self):
        """Publier du contenu aux membres est une decision d'organisation, pas
        d'exploitation — la frontiere de la Phase 22 tient."""
        token = self.admin_session("it@example.com", main.SECURITY_ROLE_IT_ADMIN,
                                   subject="oid-it")
        self.assertEqual(
            self.client.get(self.base(), headers=self.auth(token)).status_code, 200)
        self.assertEqual(
            self.client.post(self.base(), headers=self.auth(token),
                             json={"package_type": "style",
                                   "name": "x"}).status_code, 403)

    def test_a_member_session_opens_nothing(self):
        user = self.sign_in("membre@example.com")
        self.assertEqual(
            self.client.get(self.base(), headers=self.auth(user)).status_code, 401)


class PackageTenantIsolationTests(AdminTestCase):
    OTHER = "org-voisine-pkg"

    def setUp(self):
        super().setUp()
        clear_packages()
        self.organization = main.nova_organization_id()
        self.token = self.admin_session()
        with main.db() as conn:
            conn.execute(
                "INSERT OR REPLACE INTO organizations(id, slug, display_name, "
                "org_type, status, created_at) VALUES (?,?,?,?,?,?)",
                (self.OTHER, "voisine-pkg", "Voisine", "education", "active", 0.0))

    def tearDown(self):
        with main.db() as conn:
            conn.execute("DELETE FROM organizations WHERE id=?", (self.OTHER,))

    def test_another_organization_is_not_reachable(self):
        """Une organisation qu'on n'administre pas ne se distingue pas d'une
        organisation qui n'existe pas."""
        response = self.client.get(
            f"/api/control/organizations/{self.OTHER}/packages",
            headers=self.auth(self.token))
        self.assertEqual(response.status_code, 404)

    def test_a_package_id_from_another_organization_is_not_found(self):
        with main.db() as conn:
            conn.execute(
                "INSERT INTO organization_packages(package_id, organization_id, "
                "package_type, name, status, revision, created_at, updated_at) "
                "VALUES (?,?,?,?,?,?,?,?)",
                ("etranger", self.OTHER, "style", "Voisin", "draft", 0, 0.0, 0.0))
        response = self.client.get(
            f"/api/control/organizations/{self.organization}/packages/etranger",
            headers=self.auth(self.token))
        self.assertEqual(response.status_code, 404)

    def test_a_member_never_receives_another_organizations_content(self):
        with main.db() as conn:
            conn.execute(
                "INSERT INTO organization_packages(package_id, organization_id, "
                "package_type, name, status, active_version_id, revision, "
                "created_at, updated_at) VALUES (?,?,?,?,?,?,?,?,?)",
                ("p-voisin", self.OTHER, "vocabulary", "Voisin", "published",
                 "v-voisin", 0, 0.0, 0.0))
            conn.execute(
                "INSERT INTO organization_package_versions(version_id, package_id, "
                "organization_id, version_number, schema_version, content, "
                "content_hash, status, validation_status, revision, created_at, "
                "updated_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                ("v-voisin", "p-voisin", self.OTHER, 1, 1,
                 json.dumps({"entries": [{"term": "SECRET", "replacement": "x"}]}),
                 "h", "published", "valid", 0, 0.0, 0.0))
        user = self.sign_in("etudiant@example.com")
        raw = self.client.get("/api/organization/packages",
                              headers=self.auth(user)).text
        self.assertNotIn("SECRET", raw)
        self.assertNotIn("p-voisin", raw)


class PackageDeliveryTests(AdminTestCase):
    def setUp(self):
        super().setUp()
        clear_packages()
        self.organization = main.nova_organization_id()
        self.token = self.admin_session()

    def base(self) -> str:
        return f"/api/control/organizations/{self.organization}/packages"

    def published_package(self, package_type="vocabulary", content=None):
        package = self.client.post(
            self.base(), headers=self.auth(self.token),
            json={"package_type": package_type, "name": "Example"}).json()
        pid = package["package_id"]
        version = self.client.post(
            f"{self.base()}/{pid}/versions", headers=self.auth(self.token),
            json={"content": content or VOCABULARY}).json()["versions"][0]["version_id"]
        self.client.post(f"{self.base()}/{pid}/versions/{version}/publish",
                         headers=self.auth(self.token), json={"activate": True})
        return pid, version

    def delivered(self, user_token, headers=None):
        return self.client.get("/api/organization/packages",
                               headers={**self.auth(user_token), **(headers or {})})

    def test_an_active_published_version_is_delivered(self):
        self.published_package()
        user = self.sign_in("etudiant@example.com")
        body = self.delivered(user).json()
        self.assertEqual(len(body["packages"]), 1)
        self.assertEqual(body["packages"][0]["type"], "vocabulary")
        self.assertEqual(body["packages"][0]["version"], 1)

    def test_a_draft_is_never_delivered(self):
        """Ce que l'organisation n'a pas decide de distribuer ne quitte pas la
        console."""
        package = self.client.post(
            self.base(), headers=self.auth(self.token),
            json={"package_type": "vocabulary", "name": "Brouillon"}).json()
        self.client.post(f"{self.base()}/{package['package_id']}/versions",
                         headers=self.auth(self.token), json={"content": VOCABULARY})
        user = self.sign_in("etudiant@example.com")
        self.assertEqual(self.delivered(user).json()["packages"], [])

    def test_a_published_but_inactive_version_is_not_delivered(self):
        pid, first = self.published_package()
        second = {"entries": [{"term": "Autre", "replacement": "autre"}]}
        versions = self.client.post(
            f"{self.base()}/{pid}/versions", headers=self.auth(self.token),
            json={"content": second}).json()["versions"]
        draft = [v for v in versions if v["status"] == "draft"][0]["version_id"]
        self.client.post(f"{self.base()}/{pid}/versions/{draft}/publish",
                         headers=self.auth(self.token), json={"activate": False})
        user = self.sign_in("etudiant@example.com")
        delivered = self.delivered(user).json()["packages"]
        self.assertEqual(len(delivered), 1)
        self.assertEqual(delivered[0]["version"], 1)

    def test_an_archived_package_is_not_delivered(self):
        pid, _ = self.published_package()
        self.client.post(f"{self.base()}/{pid}/archive", headers=self.auth(self.token))
        user = self.sign_in("etudiant@example.com")
        self.assertEqual(self.delivered(user).json()["packages"], [])

    def test_a_closed_policy_withholds_the_content(self):
        """Package actif, policy fermee : le contenu existe, la permission non."""
        self.published_package()
        self.client.put("/api/control/organization/policy",
                        headers=self.auth(self.token),
                        json={"expected_revision": 0,
                              "settings": {"organization_vocabulary_enabled": False}})
        user = self.sign_in("etudiant@example.com")
        self.assertEqual(self.delivered(user).json()["packages"], [])

    def test_reopening_the_policy_restores_the_content(self):
        self.published_package()
        self.client.put("/api/control/organization/policy",
                        headers=self.auth(self.token),
                        json={"expected_revision": 0,
                              "settings": {"organization_vocabulary_enabled": False}})
        self.client.put("/api/control/organization/policy",
                        headers=self.auth(self.token),
                        json={"expected_revision": 1,
                              "settings": {"organization_vocabulary_enabled": True}})
        user = self.sign_in("etudiant@example.com")
        self.assertEqual(len(self.delivered(user).json()["packages"]), 1)

    def test_the_catalog_carries_no_administrative_detail(self):
        self.published_package()
        user = self.sign_in("etudiant@example.com")
        package = self.delivered(user).json()["packages"][0]
        for forbidden in ("created_by_user_id", "updated_by_user_id", "status",
                          "validation_status", "revision"):
            self.assertNotIn(forbidden, package)

    def test_an_unchanged_catalog_answers_304(self):
        self.published_package()
        user = self.sign_in("etudiant@example.com")
        first = self.delivered(user)
        etag = first.headers["ETag"]
        again = self.delivered(user, {"If-None-Match": etag})
        self.assertEqual(again.status_code, 304)

    def test_publishing_changes_the_catalog_version(self):
        pid, _ = self.published_package()
        user = self.sign_in("etudiant@example.com")
        before = self.delivered(user).headers["ETag"]
        second = {"entries": [{"term": "Autre", "replacement": "autre"}]}
        versions = self.client.post(
            f"{self.base()}/{pid}/versions", headers=self.auth(self.token),
            json={"content": second}).json()["versions"]
        draft = [v for v in versions if v["status"] == "draft"][0]["version_id"]
        self.client.post(f"{self.base()}/{pid}/versions/{draft}/publish",
                         headers=self.auth(self.token), json={"activate": True})
        self.assertNotEqual(self.delivered(user).headers["ETag"], before)

    def test_an_anonymous_call_is_refused(self):
        self.assertEqual(self.client.get("/api/organization/packages").status_code, 401)

    def test_organization_styles_carry_a_distinct_identifier(self):
        self.published_package("style", STYLE)
        user = self.sign_in("etudiant@example.com")
        package = self.delivered(user).json()["packages"][0]
        self.assertTrue(package["id"].startswith("org_style_"))

    def test_a_style_keeps_its_instruction_and_a_skill_does_not(self):
        """La frontiere n'est pas la meme pour les deux.

        La consigne d'un Style *est* le Style : le poste doit pouvoir
        l'appliquer avec son moteur local, sans serveur. La retirer viderait
        les Styles d'organisation. La consigne d'un AI Skill, elle, reste au
        serveur : le poste n'envoie qu'un identifiant, et la publier ici
        rendrait ce controle decoratif.
        """
        self.published_package("style", STYLE)
        self.published_package("ai_skill", {**AI_SKILL, "instruction": "Consigne."})
        user = self.sign_in("etudiant@example.com")
        delivered = {p["type"]: p["content"] for p in self.delivered(user).json()["packages"]}
        self.assertEqual(delivered["style"]["instruction"], STYLE["instruction"])
        self.assertNotIn("instruction", delivered["ai_skill"])
        self.assertEqual(delivered["ai_skill"]["title"], AI_SKILL["title"])


class PolicyCapabilityMappingTests(unittest.TestCase):
    """Le nom de capacite doit exister cote **serveur**.

    Ne du defaut trouve en Phase 31 : le mapping portait `writingStyles`, le
    nom du poste, alors que le serveur nomme cette capacite `styles`. Aucun
    package de Style n'etait distribue — et le `.get()` faisait passer la faute
    de frappe pour un refus de policy, donc pour un comportement voulu.
    """

    def test_every_mapped_capability_exists(self):
        capabilities = main.public_campus_config()["capabilities"]
        for package_type, capability in main.POLICY_BY_PACKAGE_TYPE.items():
            with self.subTest(package_type):
                self.assertIn(capability, capabilities)

    def test_every_package_type_is_mapped(self):
        self.assertEqual(set(main.POLICY_BY_PACKAGE_TYPE), set(main.PACKAGE_TYPES))


class VocabularyRuntimeTests(AdminTestCase):
    """Le vocabulaire publie arrive dans le **vrai** pipeline de reformulation.

    Publier sans que rien ne consomme produirait un panneau d'administration
    sans effet — exactement ce que la Phase 31 laissait ouvert.
    """

    def setUp(self):
        super().setUp()
        clear_packages()
        with main.db() as conn:
            conn.execute("DELETE FROM shared_dictionary")
            conn.execute("DELETE FROM personal_dictionary")
        self.organization = main.nova_organization_id()
        self.token = self.admin_session()

    def base(self) -> str:
        return f"/api/control/organizations/{self.organization}/packages"

    def publish_vocabulary(self, entries):
        package = self.client.post(
            self.base(), headers=self.auth(self.token),
            json={"package_type": "vocabulary", "name": "Example"}).json()
        pid = package["package_id"]
        version = self.client.post(
            f"{self.base()}/{pid}/versions", headers=self.auth(self.token),
            json={"content": {"entries": entries}}).json()["versions"][0]["version_id"]
        self.client.post(f"{self.base()}/{pid}/versions/{version}/publish",
                         headers=self.auth(self.token), json={"activate": True})
        return pid, version

    def context(self, user="etudiant@example.com"):
        return main.writing_context(user)[0]

    def test_a_published_vocabulary_reaches_the_prompt(self):
        self.publish_vocabulary([{"term": "SSO", "replacement": "single sign-on"}])
        self.assertIn("single sign-on", self.context())

    def test_a_draft_never_reaches_the_prompt(self):
        package = self.client.post(
            self.base(), headers=self.auth(self.token),
            json={"package_type": "vocabulary", "name": "Brouillon"}).json()
        self.client.post(
            f"{self.base()}/{package['package_id']}/versions",
            headers=self.auth(self.token),
            json={"content": {"entries": [{"term": "X", "replacement": "brouillon"}]}})
        self.assertNotIn("brouillon", self.context())

    def test_a_closed_policy_withholds_it_from_the_prompt(self):
        """La composition tient jusqu'au bout de la chaine, pas seulement dans
        la reponse de l'API de distribution."""
        self.publish_vocabulary([{"term": "SSO", "replacement": "single sign-on"}])
        self.client.put("/api/control/organization/policy",
                        headers=self.auth(self.token),
                        json={"expected_revision": 0,
                              "settings": {"organization_vocabulary_enabled": False}})
        self.assertNotIn("single sign-on", self.context())

    def test_a_personal_entry_wins_over_the_organization(self):
        """Un membre qui a corrige un terme l'a fait en connaissance de cause."""
        self.publish_vocabulary([{"term": "SSO", "replacement": "single sign-on"}])
        with main.db() as conn:
            conn.execute(
                "INSERT INTO personal_dictionary(email, term, replacement) "
                "VALUES (?,?,?)",
                ("etudiant@example.com", "sso", "authentification unique"))
        context = self.context()
        self.assertIn("authentification unique", context)
        self.assertNotIn("single sign-on", context)

    def test_a_term_never_appears_twice(self):
        """Deux lignes pour un terme laisseraient le modele choisir."""
        self.publish_vocabulary([{"term": "SSO", "replacement": "single sign-on"}])
        with main.db() as conn:
            conn.execute(
                "INSERT INTO personal_dictionary(email, term, replacement) "
                "VALUES (?,?,?)", ("etudiant@example.com", "SSO", "autre"))
        self.assertEqual(self.context().lower().count("- sso ->"), 1)

    def test_a_new_version_replaces_the_previous_content(self):
        pid, _ = self.publish_vocabulary(
            [{"term": "SSO", "replacement": "single sign-on"}])
        versions = self.client.post(
            f"{self.base()}/{pid}/versions", headers=self.auth(self.token),
            json={"content": {"entries": [{"term": "SSO",
                                           "replacement": "federated sign-on"}]}}
        ).json()["versions"]
        draft = [v for v in versions if v["status"] == "draft"][0]["version_id"]
        self.client.post(f"{self.base()}/{pid}/versions/{draft}/publish",
                         headers=self.auth(self.token), json={"activate": True})
        context = self.context()
        self.assertIn("federated sign-on", context)
        self.assertNotIn("single sign-on", context)

    def test_a_rollback_restores_the_earlier_content(self):
        pid, v1 = self.publish_vocabulary(
            [{"term": "SSO", "replacement": "single sign-on"}])
        versions = self.client.post(
            f"{self.base()}/{pid}/versions", headers=self.auth(self.token),
            json={"content": {"entries": [{"term": "SSO",
                                           "replacement": "federated sign-on"}]}}
        ).json()["versions"]
        draft = [v for v in versions if v["status"] == "draft"][0]["version_id"]
        self.client.post(f"{self.base()}/{pid}/versions/{draft}/publish",
                         headers=self.auth(self.token), json={"activate": True})
        self.client.post(f"{self.base()}/{pid}/versions/{v1}/activate",
                         headers=self.auth(self.token))
        self.assertIn("single sign-on", self.context())

    def test_an_archived_package_leaves_the_prompt(self):
        pid, _ = self.publish_vocabulary(
            [{"term": "SSO", "replacement": "single sign-on"}])
        self.client.post(f"{self.base()}/{pid}/archive", headers=self.auth(self.token))
        self.assertNotIn("single sign-on", self.context())


class LegacySharedVocabularyTests(AdminTestCase):
    """`shared_dictionary` n'a jamais porte de colonne `organization_id`.

    En mode dedie, l'instance sert une seule organisation : la table lui
    appartient entierement. En Control Plane, elle appartiendrait a tout le
    monde a la fois — chaque organisation recevrait le vocabulaire de ses
    voisines, et la fuite serait d'autant plus difficile a voir qu'elle
    produirait seulement des remplacements inattendus.
    """

    def setUp(self):
        super().setUp()
        clear_packages()
        with main.db() as conn:
            conn.execute("DELETE FROM shared_dictionary")
            conn.execute("DELETE FROM personal_dictionary")
            conn.execute(
                "INSERT INTO shared_dictionary(term, replacement) VALUES (?,?)",
                ("HERITAGE", "heritage partage"))

    def test_dedicated_mode_still_reads_the_legacy_table(self):
        self.assertIn("heritage partage",
                      main.writing_context("etudiant@example.com")[0])

    def test_control_plane_mode_never_reads_it(self):
        """Le refus est explicite plutot qu'une migration : deplacer ces
        entrees changerait leur comportement sans que personne ne l'ait
        demande."""
        with patch.object(main, "NOVA_SERVER_MODE",
                          main.SERVER_MODE_CONTROL_PLANE):
            context = main.writing_context("etudiant@example.com")[0]
        self.assertNotIn("heritage partage", context)

    def test_a_package_replaces_it_cleanly_in_control_plane(self):
        organization = main.nova_organization_id()
        token = self.admin_session()
        base = f"/api/control/organizations/{organization}/packages"
        package = self.client.post(
            base, headers=self.auth(token),
            json={"package_type": "vocabulary", "name": "Example"}).json()
        pid = package["package_id"]
        version = self.client.post(
            f"{base}/{pid}/versions", headers=self.auth(token),
            json={"content": {"entries": [{"term": "HERITAGE",
                                           "replacement": "publie"}]}}
        ).json()["versions"][0]["version_id"]
        self.client.post(f"{base}/{pid}/versions/{version}/publish",
                         headers=self.auth(token), json={"activate": True})
        with patch.object(main, "NOVA_SERVER_MODE",
                          main.SERVER_MODE_CONTROL_PLANE):
            context = main.writing_context("etudiant@example.com")[0]
        self.assertIn("publie", context)
        self.assertNotIn("heritage partage", context)


class SkillExecutionTests(AdminTestCase):
    """Un AI Skill publie s'execute reellement, sur le moteur existant.

    Publier une fiche que rien ne peut declencher serait un catalogue, pas une
    fonction. Ce qui est verifie ici est la chaine complete : package actif →
    catalogue → instruction → moteur.
    """

    PATH = "/api/skills/run"

    def setUp(self):
        super().setUp()
        clear_packages()
        self.organization = main.nova_organization_id()
        self.token = self.admin_session()

    def base(self) -> str:
        return f"/api/control/organizations/{self.organization}/packages"

    def publish_skill(self, instruction="Summarise the text.", activate=True):
        package = self.client.post(
            self.base(), headers=self.auth(self.token),
            json={"package_type": "ai_skill", "name": "Example Skill"}).json()
        pid = package["package_id"]
        version = self.client.post(
            f"{self.base()}/{pid}/versions", headers=self.auth(self.token),
            json={"content": {**AI_SKILL, "instruction": instruction}}
        ).json()["versions"][0]["version_id"]
        self.client.post(f"{self.base()}/{pid}/versions/{version}/publish",
                         headers=self.auth(self.token), json={"activate": activate})
        return pid, version, main.package_identifier("ai_skill", pid)

    def execute(self, skill_id, token, text="bonjour"):
        seen = {}

        async def engine(task_type, system_prompt, source):
            seen["task_type"] = task_type
            seen["prompt"] = system_prompt
            seen["text"] = source
            return "resultat"

        with patch.object(main, "execute_text", engine):
            response = self.client.post(
                self.PATH, headers=self.auth(token),
                json={"skill_id": skill_id, "text": text})
        return response, seen

    def test_a_published_skill_runs_with_its_own_instruction(self):
        _, _, skill_id = self.publish_skill("Summarise in three bullets.")
        user = self.sign_in("etudiant@example.com")
        response, seen = self.execute(skill_id, user)
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["text"], "resultat")
        self.assertIn("Summarise in three bullets.", seen["prompt"])
        self.assertEqual(seen["text"], "bonjour")

    def test_the_instruction_never_comes_from_the_client(self):
        """Sinon n'importe qui ferait executer n'importe quoi au modele en se
        reclamant d'un Skill, et le catalogue publie ne serait qu'une
        suggestion."""
        _, _, skill_id = self.publish_skill("Consigne publiee.")
        user = self.sign_in("etudiant@example.com")

        async def engine(task_type, system_prompt, source):
            self.assertIn("Consigne publiee.", system_prompt)
            self.assertNotIn("Consigne injectee.", system_prompt)
            return "resultat"

        with patch.object(main, "execute_text", engine):
            response = self.client.post(
                self.PATH, headers=self.auth(user),
                json={"skill_id": skill_id, "text": "x",
                      "instruction": "Consigne injectee."})
        self.assertEqual(response.status_code, 200)

    def test_a_draft_skill_cannot_be_run(self):
        package = self.client.post(
            self.base(), headers=self.auth(self.token),
            json={"package_type": "ai_skill", "name": "Brouillon"}).json()
        self.client.post(f"{self.base()}/{package['package_id']}/versions",
                         headers=self.auth(self.token), json={"content": AI_SKILL})
        user = self.sign_in("etudiant@example.com")
        response, _ = self.execute(
            main.package_identifier("ai_skill", package["package_id"]), user)
        self.assertEqual(response.status_code, 404)

    def test_an_unknown_skill_is_indistinguishable_from_an_unpublished_one(self):
        """Un membre n'a pas a decouvrir ce que son organisation prepare."""
        user = self.sign_in("etudiant@example.com")
        response, _ = self.execute("org_ai_skill_inexistant", user)
        self.assertEqual(response.status_code, 404)
        self.assertEqual(response.json()["detail"], "SKILL_NOT_AVAILABLE")

    def test_a_closed_policy_refuses_execution(self):
        """Un package publie ne contourne jamais la policy."""
        _, _, skill_id = self.publish_skill()
        self.client.put("/api/control/organization/policy",
                        headers=self.auth(self.token),
                        json={"expected_revision": 0,
                              "settings": {"ai_skills_enabled": False}})
        user = self.sign_in("etudiant@example.com")
        response, _ = self.execute(skill_id, user)
        self.assertEqual(response.status_code, 403)
        self.assertEqual(response.json()["detail"]["code"],
                         "ORGANIZATION_POLICY_DISABLED")

    def test_the_commands_policy_does_not_govern_skills(self):
        """Fermer les commandes vocales ne doit pas fermer les Skills : ce
        serait coupler deux policies que personne n'a demande de lier."""
        _, _, skill_id = self.publish_skill()
        self.client.put("/api/control/organization/policy",
                        headers=self.auth(self.token),
                        json={"expected_revision": 0,
                              "settings": {"voice_commands_enabled": False}})
        user = self.sign_in("etudiant@example.com")
        response, _ = self.execute(skill_id, user)
        self.assertEqual(response.status_code, 200)

    def test_a_new_version_changes_the_instruction(self):
        pid, _, skill_id = self.publish_skill("Consigne v1.")
        versions = self.client.post(
            f"{self.base()}/{pid}/versions", headers=self.auth(self.token),
            json={"content": {**AI_SKILL, "instruction": "Consigne v2."}}
        ).json()["versions"]
        draft = [v for v in versions if v["status"] == "draft"][0]["version_id"]
        self.client.post(f"{self.base()}/{pid}/versions/{draft}/publish",
                         headers=self.auth(self.token), json={"activate": True})
        user = self.sign_in("etudiant@example.com")
        _, seen = self.execute(skill_id, user)
        self.assertIn("Consigne v2.", seen["prompt"])
        self.assertNotIn("Consigne v1.", seen["prompt"])

    def test_a_rollback_restores_the_earlier_instruction(self):
        pid, v1, skill_id = self.publish_skill("Consigne v1.")
        versions = self.client.post(
            f"{self.base()}/{pid}/versions", headers=self.auth(self.token),
            json={"content": {**AI_SKILL, "instruction": "Consigne v2."}}
        ).json()["versions"]
        draft = [v for v in versions if v["status"] == "draft"][0]["version_id"]
        self.client.post(f"{self.base()}/{pid}/versions/{draft}/publish",
                         headers=self.auth(self.token), json={"activate": True})
        self.client.post(f"{self.base()}/{pid}/versions/{v1}/activate",
                         headers=self.auth(self.token))
        user = self.sign_in("etudiant@example.com")
        _, seen = self.execute(skill_id, user)
        self.assertIn("Consigne v1.", seen["prompt"])

    def test_an_archived_package_stops_being_runnable(self):
        pid, _, skill_id = self.publish_skill()
        self.client.post(f"{self.base()}/{pid}/archive", headers=self.auth(self.token))
        user = self.sign_in("etudiant@example.com")
        response, _ = self.execute(skill_id, user)
        self.assertEqual(response.status_code, 404)

    def test_an_anonymous_call_is_refused(self):
        response = self.client.post(self.PATH,
                                    json={"skill_id": "x", "text": "y"})
        self.assertEqual(response.status_code, 401)

    def test_a_skill_of_another_organization_is_not_runnable(self):
        with main.db() as conn:
            conn.execute(
                "INSERT OR REPLACE INTO organizations(id, slug, display_name, "
                "org_type, status, created_at) VALUES (?,?,?,?,?,?)",
                ("org-voisine-run", "voisine-run", "Voisine", "education",
                 "active", 0.0))
            conn.execute(
                "INSERT INTO organization_packages(package_id, organization_id, "
                "package_type, name, status, active_version_id, revision, "
                "created_at, updated_at) VALUES (?,?,?,?,?,?,?,?,?)",
                ("p-voisin", "org-voisine-run", "ai_skill", "Voisin", "published",
                 "v-voisin", 0, 0.0, 0.0))
            conn.execute(
                "INSERT INTO organization_package_versions(version_id, package_id, "
                "organization_id, version_number, schema_version, content, "
                "content_hash, status, validation_status, revision, created_at, "
                "updated_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                ("v-voisin", "p-voisin", "org-voisine-run", 1,
                 main.PACKAGE_CONTENT_SCHEMA_VERSION,
                 json.dumps({**AI_SKILL, "instruction": "Consigne voisine."}),
                 "h", "published", "valid", 0, 0.0, 0.0))
        user = self.sign_in("etudiant@example.com")
        response, _ = self.execute(main.package_identifier("ai_skill", "p-voisin"), user)
        self.assertEqual(response.status_code, 404)
        with main.db() as conn:
            conn.execute("DELETE FROM organizations WHERE id=?", ("org-voisine-run",))


class DraftLeniencyTests(AdminTestCase):
    """Un brouillon a le droit d'etre incomplet.

    Ne du defaut trouve par la recette de la Phase 31B : le document de depart
    est vide, et lui appliquer la validation de publication rendait la creation
    impossible — il aurait fallu l'avoir rempli pour avoir le droit de
    commencer a le remplir.

    Aucun test ne pouvait le voir : tous partaient d'un document deja complet.
    Il a fallu qu'un humain clique sur « Create draft ».

    La regle desormais : on refuse ce qui est **faux**, on tolere ce qui est
    **inacheve**.
    """

    def setUp(self):
        super().setUp()
        clear_packages()
        self.organization = main.nova_organization_id()
        self.token = self.admin_session()

    def base(self) -> str:
        return f"/api/control/organizations/{self.organization}/packages"

    def create(self, package_type):
        return self.client.post(
            self.base(), headers=self.auth(self.token),
            json={"package_type": package_type, "name": "Example"}).json()

    EMPTY = {
        "vocabulary": {"entries": [{"term": "", "replacement": ""}]},
        "style": {"name": "", "instruction": ""},
        "ai_skill": {"title": "", "summary": "", "instruction": "",
                     "practice": "", "duration_minutes": 5, "steps": []},
    }

    def test_an_empty_draft_can_be_created_for_every_type(self):
        for package_type, content in self.EMPTY.items():
            with self.subTest(package_type):
                clear_packages()
                package = self.create(package_type)
                response = self.client.post(
                    f"{self.base()}/{package['package_id']}/versions",
                    headers=self.auth(self.token), json={"content": content})
                self.assertEqual(response.status_code, 200, response.text)
                version = response.json()["versions"][0]
                self.assertEqual(version["status"], "draft")
                # Creable, mais annonce comme non publiable.
                self.assertEqual(version["validation_status"], "invalid")

    def test_an_empty_draft_can_be_edited(self):
        package = self.create("vocabulary")
        version = self.client.post(
            f"{self.base()}/{package['package_id']}/versions",
            headers=self.auth(self.token),
            json={"content": self.EMPTY["vocabulary"]}
        ).json()["versions"][0]["version_id"]
        response = self.client.put(
            f"{self.base()}/{package['package_id']}/versions/{version}",
            headers=self.auth(self.token),
            json={"expected_revision": 0, "content": self.EMPTY["vocabulary"]})
        self.assertEqual(response.status_code, 200, response.text)

    def test_completing_a_draft_makes_it_valid(self):
        package = self.create("vocabulary")
        version = self.client.post(
            f"{self.base()}/{package['package_id']}/versions",
            headers=self.auth(self.token),
            json={"content": self.EMPTY["vocabulary"]}
        ).json()["versions"][0]["version_id"]
        view = self.client.put(
            f"{self.base()}/{package['package_id']}/versions/{version}",
            headers=self.auth(self.token),
            json={"expected_revision": 0, "content": VOCABULARY}).json()
        self.assertEqual(view["versions"][0]["validation_status"], "valid")

    def test_an_incomplete_draft_cannot_be_published(self):
        """Tolere en brouillon ne veut pas dire tolere en publication."""
        package = self.create("vocabulary")
        version = self.client.post(
            f"{self.base()}/{package['package_id']}/versions",
            headers=self.auth(self.token),
            json={"content": self.EMPTY["vocabulary"]}
        ).json()["versions"][0]["version_id"]
        response = self.client.post(
            f"{self.base()}/{package['package_id']}/versions/{version}/publish",
            headers=self.auth(self.token), json={"activate": True})
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json()["detail"]["code"],
                         "PACKAGE_VALIDATION_FAILED")

    def test_a_wrong_type_is_still_refused_in_a_draft(self):
        """On tolere l'inacheve, jamais le faux."""
        package = self.create("vocabulary")
        response = self.client.post(
            f"{self.base()}/{package['package_id']}/versions",
            headers=self.auth(self.token), json={"content": {"entries": "texte"}})
        self.assertEqual(response.status_code, 400)

    def test_an_unknown_field_is_still_refused_in_a_draft(self):
        package = self.create("style")
        response = self.client.post(
            f"{self.base()}/{package['package_id']}/versions",
            headers=self.auth(self.token),
            json={"content": {"name": "", "instruction": "", "script": "x"}})
        self.assertEqual(response.status_code, 400)

    def test_an_oversized_draft_is_still_refused(self):
        package = self.create("vocabulary")
        entries = [{"term": f"t{i}", "replacement": "x" * 190} for i in range(1500)]
        response = self.client.post(
            f"{self.base()}/{package['package_id']}/versions",
            headers=self.auth(self.token), json={"content": {"entries": entries}})
        self.assertEqual(response.status_code, 400)


class ArchiveRestoreTests(AdminTestCase):
    """Archiver doit pouvoir se defaire.

    Ne de la recette de la Phase 31B : archiver etait presente comme reversible
    — « archiver plutot que supprimer » — sans qu'aucun chemin de retour
    n'existe. Un clic malencontreux devenait definitif, et la promesse etait
    fausse.
    """

    def setUp(self):
        super().setUp()
        clear_packages()
        self.organization = main.nova_organization_id()
        self.token = self.admin_session()

    def base(self) -> str:
        return f"/api/control/organizations/{self.organization}/packages"

    def published_package(self):
        package = self.client.post(
            self.base(), headers=self.auth(self.token),
            json={"package_type": "vocabulary", "name": "Example"}).json()
        pid = package["package_id"]
        version = self.client.post(
            f"{self.base()}/{pid}/versions", headers=self.auth(self.token),
            json={"content": VOCABULARY}).json()["versions"][0]["version_id"]
        self.client.post(f"{self.base()}/{pid}/versions/{version}/publish",
                         headers=self.auth(self.token), json={"activate": True})
        return pid, version

    def test_an_archived_package_can_be_restored(self):
        pid, _ = self.published_package()
        self.client.post(f"{self.base()}/{pid}/archive", headers=self.auth(self.token))
        view = self.client.post(f"{self.base()}/{pid}/restore",
                                headers=self.auth(self.token)).json()
        self.assertEqual(view["status"], "draft")

    def test_restoring_does_not_redistribute_on_its_own(self):
        """Sortir de l'archive ne doit pas remettre du contenu en circulation a
        l'improviste : reactiver une version est un geste separe."""
        pid, version = self.published_package()
        self.client.post(f"{self.base()}/{pid}/archive", headers=self.auth(self.token))
        view = self.client.post(f"{self.base()}/{pid}/restore",
                                headers=self.auth(self.token)).json()
        self.assertIsNone(view["active_version_id"])
        user = self.sign_in("etudiant@example.com")
        self.assertEqual(
            self.client.get("/api/organization/packages",
                            headers=self.auth(user)).json()["packages"], [])

    def test_the_versions_survive_the_round_trip(self):
        """Rien n'est efface : c'est tout l'interet d'archiver."""
        pid, version = self.published_package()
        self.client.post(f"{self.base()}/{pid}/archive", headers=self.auth(self.token))
        self.client.post(f"{self.base()}/{pid}/restore", headers=self.auth(self.token))
        detail = self.client.get(f"{self.base()}/{pid}/versions/{version}",
                                 headers=self.auth(self.token)).json()
        self.assertEqual(detail["content"], VOCABULARY)

    def test_reactivating_after_restore_redistributes(self):
        pid, version = self.published_package()
        self.client.post(f"{self.base()}/{pid}/archive", headers=self.auth(self.token))
        self.client.post(f"{self.base()}/{pid}/restore", headers=self.auth(self.token))
        view = self.client.post(f"{self.base()}/{pid}/versions/{version}/activate",
                                headers=self.auth(self.token)).json()
        self.assertEqual(view["status"], "published")
        user = self.sign_in("etudiant@example.com")
        self.assertEqual(
            len(self.client.get("/api/organization/packages",
                                headers=self.auth(user)).json()["packages"]), 1)

    def test_restoring_a_package_that_is_not_archived_is_refused(self):
        pid, _ = self.published_package()
        response = self.client.post(f"{self.base()}/{pid}/restore",
                                    headers=self.auth(self.token))
        self.assertEqual(response.status_code, 409)

    def test_the_round_trip_is_recorded(self):
        pid, _ = self.published_package()
        self.client.post(f"{self.base()}/{pid}/archive", headers=self.auth(self.token))
        self.client.post(f"{self.base()}/{pid}/restore", headers=self.auth(self.token))
        with main.db() as conn:
            actions = [row["action"] for row in conn.execute(
                "SELECT action FROM admin_audit_log WHERE action LIKE 'package.%' "
                "ORDER BY created_at")]
        self.assertIn("package.archive", actions)
        self.assertIn("package.restore", actions)

    def test_read_only_cannot_restore(self):
        pid, _ = self.published_package()
        self.client.post(f"{self.base()}/{pid}/archive", headers=self.auth(self.token))
        token = self.admin_session("lecture@example.com",
                                   main.SECURITY_ROLE_READ_ONLY, subject="oid-ro")
        self.assertEqual(
            self.client.post(f"{self.base()}/{pid}/restore",
                             headers=self.auth(token)).status_code, 403)


class PackageAuditTests(AdminTestCase):
    def setUp(self):
        super().setUp()
        clear_packages()
        self.organization = main.nova_organization_id()
        self.token = self.admin_session()

    def base(self) -> str:
        return f"/api/control/organizations/{self.organization}/packages"

    def actions(self):
        with main.db() as conn:
            return [
                (row["action"], json.loads(row["metadata"]))
                for row in conn.execute(
                    "SELECT action, metadata FROM admin_audit_log "
                    "WHERE action LIKE 'package.%' ORDER BY created_at")
            ]

    def test_the_lifecycle_is_recorded(self):
        package = self.client.post(
            self.base(), headers=self.auth(self.token),
            json={"package_type": "vocabulary", "name": "Example"}).json()
        pid = package["package_id"]
        version = self.client.post(
            f"{self.base()}/{pid}/versions", headers=self.auth(self.token),
            json={"content": VOCABULARY}).json()["versions"][0]["version_id"]
        self.client.post(f"{self.base()}/{pid}/versions/{version}/publish",
                         headers=self.auth(self.token), json={"activate": True})
        recorded = [action for action, _ in self.actions()]
        self.assertEqual(recorded, ["package.create", "package.draft_create",
                                    "package.version_publish"])

    def test_the_audit_never_carries_the_content(self):
        """Un journal qui recopierait un dictionnaire entier a chaque
        publication cesserait d'etre lisible, et porterait du contenu qu'il n'a
        pas a porter."""
        package = self.client.post(
            self.base(), headers=self.auth(self.token),
            json={"package_type": "vocabulary", "name": "Example"}).json()
        self.client.post(f"{self.base()}/{package['package_id']}/versions",
                         headers=self.auth(self.token), json={"content": VOCABULARY})
        with main.db() as conn:
            raw = " ".join(
                row["metadata"] or "" for row in conn.execute(
                    "SELECT metadata FROM admin_audit_log WHERE action LIKE 'package.%'"))
        self.assertNotIn("single sign-on", raw)
        self.assertNotIn("entries", raw)

    def test_a_rollback_is_named_as_such(self):
        package = self.client.post(
            self.base(), headers=self.auth(self.token),
            json={"package_type": "vocabulary", "name": "Example"}).json()
        pid = package["package_id"]
        v1 = self.client.post(
            f"{self.base()}/{pid}/versions", headers=self.auth(self.token),
            json={"content": VOCABULARY}).json()["versions"][0]["version_id"]
        self.client.post(f"{self.base()}/{pid}/versions/{v1}/publish",
                         headers=self.auth(self.token), json={"activate": True})
        second = {"entries": [{"term": "Autre", "replacement": "autre"}]}
        versions = self.client.post(
            f"{self.base()}/{pid}/versions", headers=self.auth(self.token),
            json={"content": second}).json()["versions"]
        v2 = [v for v in versions if v["status"] == "draft"][0]["version_id"]
        self.client.post(f"{self.base()}/{pid}/versions/{v2}/publish",
                         headers=self.auth(self.token), json={"activate": True})
        self.client.post(f"{self.base()}/{pid}/versions/{v1}/activate",
                         headers=self.auth(self.token))
        action, metadata = self.actions()[-1]
        self.assertEqual(action, "package.version_rollback")
        self.assertEqual(metadata["old_version"], 2)
        self.assertEqual(metadata["new_version"], 1)

    def test_a_refused_publish_records_nothing(self):
        package = self.client.post(
            self.base(), headers=self.auth(self.token),
            json={"package_type": "vocabulary", "name": "Example"}).json()
        before = len(self.actions())
        self.client.post(
            f"{self.base()}/{package['package_id']}/versions/inconnu/publish",
            headers=self.auth(self.token), json={"activate": True})
        self.assertEqual(len(self.actions()), before)


if __name__ == "__main__":
    unittest.main()
