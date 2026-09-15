"""Reglages par groupe.

Les reglages de l'organisation valent pour tout le monde. Une ecole veut
pourtant fermer les AI Skills a la promo en examen, ou ouvrir les commandes
vocales au seul personnel. Un groupe peut donc surcharger des reglages :

- une surcharge de groupe l'emporte sur le reglage de l'organisation, dans les
  deux sens — elle ferme comme elle ouvre ;
- quand deux groupes d'un membre ne sont pas d'accord, la valeur la plus
  restrictive l'emporte : ferme plutot qu'ouvert, la duree la plus courte ;
- un groupe supprime ne s'applique plus ;
- chaque ecriture porte une revision, laisse une trace avant/apres, et fait
  bouger le repere de policy du membre pour arriver sur son poste en moins
  d'une minute.
"""

import json
import time
import unittest
import uuid

from test_main import AdminTestCase, grant_admin_session  # noqa: F401

import learning
import main

main.init_db()

POLICY = "/api/control/organization/policy"
GROUPS = f"{POLICY}/groups"


def create_group(name: str, emails: list[str]) -> str:
    organization_id = main.nova_organization_id()
    group_id = str(uuid.uuid4())
    now = time.time()
    with main.db() as conn:
        conn.execute(
            "INSERT INTO organization_groups(organization_id, group_id, external_id, "
            "display_name, created_at, updated_at) VALUES (?,?,?,?,?,?)",
            (organization_id, group_id, None, name, now, now))
        for email in emails:
            user_id = conn.execute(
                "SELECT user_id FROM users WHERE organization_id=? AND email=?",
                (organization_id, email)).fetchone()["user_id"]
            conn.execute(
                "INSERT INTO organization_group_memberships(organization_id, group_id, "
                "user_id, created_at) VALUES (?,?,?,?)",
                (organization_id, group_id, user_id, now))
    return group_id


class GroupPolicyTestCase(AdminTestCase):
    def setUp(self):
        super().setUp()
        learning.reset_cache()
        with main.db() as conn:
            conn.execute("DELETE FROM organization_policies")
            conn.execute("DELETE FROM organization_group_policies")
        self.admin = self.admin_session()

    def member(self, email: str) -> dict:
        return self.auth(self.sign_in(email))

    def set_group(self, group_id: str, settings: dict, revision: int = 0,
                  headers: dict | None = None):
        return self.client.put(
            f"{GROUPS}/{group_id}", headers=headers or self.auth(self.admin),
            json={"expected_revision": revision, "settings": settings})

    def set_organization(self, settings: dict):
        current = self.client.get(POLICY, headers=self.auth(self.admin)).json()["revision"]
        response = self.client.put(POLICY, headers=self.auth(self.admin),
                                   json={"expected_revision": current, "settings": settings})
        self.assertEqual(response.status_code, 200, response.text)

    def member_settings(self, headers: dict) -> dict:
        response = self.client.get("/api/organization/policy", headers=headers)
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()["settings"]

    def catalog_status(self, headers: dict) -> int:
        return self.client.get("/api/learning/catalog", headers=headers).status_code


class ResolutionTests(GroupPolicyTestCase):
    def test_a_group_closes_a_feature_only_for_its_members(self):
        inside, outside = self.member("dedans@example.com"), self.member("dehors@example.com")
        group_id = create_group("Promo en examen", ["dedans@example.com"])
        response = self.set_group(group_id, {"learning_enabled": False})
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["revision"], 1)

        self.assertEqual(self.catalog_status(inside), 403)
        self.assertEqual(self.catalog_status(outside), 200)
        self.assertIn("learning", self.client.get("/api/me", headers=inside).json()
                      ["closed_capabilities"])
        self.assertNotIn("learning", self.client.get("/api/me", headers=outside).json()
                         ["closed_capabilities"])

    def test_a_group_opens_what_the_organization_closed(self):
        inside, outside = self.member("dedans@example.com"), self.member("dehors@example.com")
        group_id = create_group("Personnel", ["dedans@example.com"])
        self.set_organization({"learning_enabled": False})
        self.assertEqual(self.set_group(group_id, {"learning_enabled": True}).status_code, 200)

        self.assertEqual(self.catalog_status(inside), 200)
        self.assertEqual(self.catalog_status(outside), 403)

    def test_between_two_groups_the_restrictive_value_wins(self):
        inside = self.member("dedans@example.com")
        relaxed = create_group("Recherche", ["dedans@example.com"])
        strict = create_group("Examen", ["dedans@example.com"])
        self.set_group(relaxed, {"ai_skills_enabled": True, "max_dictation_seconds": 1200})
        self.set_group(strict, {"ai_skills_enabled": False, "max_dictation_seconds": 300})

        settings = self.member_settings(inside)
        self.assertFalse(settings["ai_skills_enabled"])
        self.assertEqual(settings["max_dictation_seconds"], 300)

    def test_the_member_policy_route_announces_the_effective_capabilities(self):
        inside = self.member("dedans@example.com")
        group_id = create_group("Examen", ["dedans@example.com"])
        self.set_group(group_id, {"ai_skills_enabled": False})
        body = self.client.get("/api/organization/policy", headers=inside).json()
        self.assertFalse(body["effective_capabilities"]["aiSkills"])

    def test_a_deleted_group_no_longer_applies(self):
        inside = self.member("dedans@example.com")
        group_id = create_group("Ancienne promo", ["dedans@example.com"])
        self.set_group(group_id, {"learning_enabled": False})
        with main.db() as conn:
            conn.execute("UPDATE organization_groups SET deleted_at=? WHERE group_id=?",
                         (time.time(), group_id))
        self.assertEqual(self.catalog_status(inside), 200)

    def test_clearing_the_overrides_returns_the_group_to_the_organization(self):
        inside = self.member("dedans@example.com")
        group_id = create_group("Examen", ["dedans@example.com"])
        self.set_group(group_id, {"learning_enabled": False})
        response = self.set_group(group_id, {}, revision=1)
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["overrides"], {})
        self.assertEqual(self.catalog_status(inside), 200)

    def test_the_member_marker_moves_when_one_of_their_groups_changes(self):
        inside = self.member("dedans@example.com")
        group_id = create_group("Examen", ["dedans@example.com"])
        before = self.client.get("/api/organization/changes", headers=inside).json()
        self.set_group(group_id, {"voice_commands_enabled": False})
        after = self.client.get("/api/organization/changes", headers=inside).json()
        self.assertNotEqual(after["policy_revision"], before["policy_revision"])


class ConsoleTests(GroupPolicyTestCase):
    def test_the_console_lists_every_group_with_its_overrides(self):
        self.member("dedans@example.com")
        group_id = create_group("Examen", ["dedans@example.com"])
        self.set_group(group_id, {"learning_enabled": False})
        response = self.client.get(GROUPS, headers=self.auth(self.admin))
        self.assertEqual(response.status_code, 200, response.text)
        listed = {group["id"]: group for group in response.json()["groups"]}
        self.assertEqual(listed[group_id]["label"], "Examen")
        self.assertEqual(listed[group_id]["overrides"], {"learning_enabled": False})
        self.assertEqual(listed[group_id]["revision"], 1)

    def test_invalid_writes_are_refused_without_writing(self):
        self.member("dedans@example.com")
        group_id = create_group("Examen", ["dedans@example.com"])
        for settings in ({"invente": True}, {"learning_enabled": "non"},
                         {"max_dictation_seconds": 5}, {"learning_enabled": 0}):
            with self.subTest(settings=settings):
                self.assertEqual(self.set_group(group_id, settings).status_code, 400)
        self.assertEqual(self.set_group("groupe-inconnu", {"learning_enabled": False})
                         .status_code, 404)
        self.assertEqual(self.set_group(group_id, {"learning_enabled": False}).status_code, 200)
        self.assertEqual(self.set_group(group_id, {"learning_enabled": True}).status_code, 409)

    def test_every_change_is_audited_with_before_and_after(self):
        self.member("dedans@example.com")
        group_id = create_group("Examen", ["dedans@example.com"])
        self.set_group(group_id, {"learning_enabled": False})
        self.set_group(group_id, {"learning_enabled": False, "max_dictation_seconds": 300},
                       revision=1)
        with main.db() as conn:
            rows = conn.execute(
                "SELECT target_id, metadata FROM admin_audit_log "
                "WHERE action='group_policy.update' ORDER BY created_at").fetchall()
        self.assertEqual([row["target_id"] for row in rows], [group_id, group_id])
        last = json.loads(rows[-1]["metadata"])
        self.assertEqual(last["before"], {"learning_enabled": False})
        self.assertEqual(last["after"], {"learning_enabled": False, "max_dictation_seconds": 300})
        self.assertEqual(last["new_revision"], 2)

    def test_saving_the_same_overrides_writes_nothing(self):
        self.member("dedans@example.com")
        group_id = create_group("Examen", ["dedans@example.com"])
        self.set_group(group_id, {"learning_enabled": False})
        response = self.set_group(group_id, {"learning_enabled": False}, revision=1)
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["revision"], 1)

    def test_a_content_editor_cannot_change_group_settings_but_a_reader_can_read(self):
        self.member("dedans@example.com")
        group_id = create_group("Examen", ["dedans@example.com"])
        editor = grant_admin_session("redaction@example.com", "content_editor")
        self.assertEqual(self.set_group(group_id, {"learning_enabled": False},
                                        headers=editor).status_code, 403)
        reader = grant_admin_session("lecture@example.com", "read_only")
        self.assertEqual(self.client.get(GROUPS, headers=reader).status_code, 200)


if __name__ == "__main__":
    unittest.main()
