"""Ce qui a change pour le membre, sans rien telecharger.

Un changement fait dans la console doit arriver sur les postes en moins d'une
minute. Recharger policy, packages et catalogue toutes les trente secondes
ferait du serveur le souci de chaque poste allume. Le poste demande donc trois
reperes — la revision de la policy, l'empreinte des packages distribues,
l'empreinte d'Apprendre reglages compris — et ne recharge que ce qui a bouge.

`catalog_version` du catalogue Apprendre ne suffisait pas : il vaut la version
du catalogue livre, et reste le meme quand l'organisation archive une lecon ou
la rend obligatoire.
"""

import time
import unittest

from test_main import AdminTestCase  # noqa: F401

import learning
import main

main.init_db()

PATH = "/api/organization/changes"


class OrganizationChangesTests(AdminTestCase):
    def setUp(self):
        super().setUp()
        learning.reset_cache()
        with main.db() as conn:
            conn.execute("DELETE FROM organization_policies")
            conn.execute("DELETE FROM organization_learning_lesson_settings")
        self.admin = self.admin_session()
        self.member = self.sign_in("etudiant@example.com")

    def changes(self) -> dict:
        response = self.client.get(PATH, headers=self.auth(self.member))
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()

    def test_the_answer_carries_only_the_three_markers(self):
        body = self.changes()
        self.assertEqual(set(body), {"policy_revision", "packages_version", "learning_version"})
        self.assertIsInstance(body["policy_revision"], int)
        self.assertIsInstance(body["packages_version"], str)
        self.assertIsInstance(body["learning_version"], str)

    def test_nothing_changed_means_the_same_markers(self):
        self.assertEqual(self.changes(), self.changes())

    def test_a_policy_change_moves_the_policy_revision(self):
        before = self.changes()["policy_revision"]
        response = self.client.put(
            "/api/control/organization/policy", headers=self.auth(self.admin),
            json={"expected_revision": before,
                  "settings": {"voice_commands_enabled": False}})
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(self.changes()["policy_revision"], before + 1)

    def test_archiving_a_lesson_moves_the_learning_marker(self):
        before = self.changes()
        response = self.client.post(
            "/api/control/organization/learning/lessons/ask-better-questions/archive",
            headers=self.auth(self.admin), json={"expected_revision": 0})
        self.assertEqual(response.status_code, 200, response.text)
        after = self.changes()
        self.assertNotEqual(after["learning_version"], before["learning_version"])
        self.assertEqual(after["packages_version"], before["packages_version"])
        self.assertEqual(after["policy_revision"], before["policy_revision"])

    def test_requiring_a_lesson_moves_the_learning_marker(self):
        before = self.changes()["learning_version"]
        response = self.client.put(
            "/api/control/organization/learning/lessons/ask-better-questions",
            headers=self.auth(self.admin),
            json={"expected_revision": 0, "required": True, "due_at": time.time() + 86400})
        self.assertEqual(response.status_code, 200, response.text)
        self.assertNotEqual(self.changes()["learning_version"], before)

    def test_joining_or_leaving_a_group_moves_the_learning_marker(self):
        """Une lecon peut viser un groupe : entrer dans ce groupe, par la console
        ou par l'annuaire, doit faire recharger Apprendre sur le poste."""
        organization = main.nova_organization_id()
        with main.db() as conn:
            conn.execute("DELETE FROM organization_group_memberships")
            conn.execute("DELETE FROM organization_groups")
            user_id = main.user_by_email(conn, organization, "etudiant@example.com")["user_id"]
        groups = f"/api/control/organizations/{organization}/groups"
        created = self.client.post(groups, headers=self.auth(self.admin),
                                   json={"display_name": "Aero 2"})
        self.assertEqual(created.status_code, 201, created.text)
        group_id = created.json()["group"]["id"]
        before = self.changes()["learning_version"]

        added = self.client.put(f"{groups}/{group_id}/members/{user_id}",
                                headers=self.auth(self.admin))
        self.assertEqual(added.status_code, 200, added.text)
        joined = self.changes()["learning_version"]
        self.assertNotEqual(joined, before)

        self.client.delete(f"{groups}/{group_id}/members/{user_id}",
                           headers=self.auth(self.admin))
        self.assertEqual(self.changes()["learning_version"], before)

    def test_the_packages_marker_is_the_version_the_member_receives(self):
        packages = self.client.get(
            "/api/organization/packages", headers=self.auth(self.member)).json()
        self.assertEqual(self.changes()["packages_version"], packages["catalog_version"])

    def test_an_anonymous_caller_learns_nothing(self):
        self.assertEqual(self.client.get(PATH).status_code, 401)


if __name__ == "__main__":
    unittest.main()
