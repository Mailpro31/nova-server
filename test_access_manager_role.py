"""Le rôle « gestion des accès » (2.5) : la mission handicap ouvre Nova.

Une école confie souvent l'accès aux outils d'aide à la mission handicap. Lui
donner `organization_admin` lui ouvrait la sécurité, les réglages et les
administrateurs. `access_manager` ne fait qu'une chose : mettre une personne
dans un groupe de la console, ou l'en retirer.
"""

import unittest

from test_console_groups import ConsoleGroupsTestCase
from test_main import grant_admin_session

import main

main.init_db()


class AccessManagerMatrixTests(unittest.TestCase):
    def test_it_reads_the_organization_and_manages_group_members_only(self):
        self.assertIn("access_manager", main.ADMIN_SECURITY_ROLES)
        self.assertEqual(
            main.capabilities_for_role("access_manager"),
            frozenset({main.CAPABILITY_ORGANIZATION_READ,
                       main.CAPABILITY_GROUP_MEMBERS_MANAGE}),
        )

    def test_an_organization_admin_keeps_managing_group_members(self):
        self.assertIn(main.CAPABILITY_GROUP_MEMBERS_MANAGE,
                      main.capabilities_for_role("organization_admin"))


class AccessManagerRouteTests(ConsoleGroupsTestCase):
    def setUp(self):
        super().setUp()
        self.manager = grant_admin_session("mission.handicap@example.com", "access_manager")

    def test_it_adds_and_removes_a_member(self):
        group = self.created("Accompagnement")
        user_id = self.member_id("etudiant@example.com")
        member = f"{self.base()}/{group['id']}/members/{user_id}"
        added = self.client.put(member, headers=self.manager)
        self.assertEqual(added.status_code, 200, added.text)
        removed = self.client.delete(member, headers=self.manager)
        self.assertEqual(removed.status_code, 200, removed.text)

    def test_it_cannot_create_rename_or_delete_a_group(self):
        self.assertEqual(self.client.post(self.base(), headers=self.manager,
                                          json={"display_name": "Nope"}).status_code, 403)
        group = self.created("Accompagnement")
        self.assertEqual(self.client.delete(f"{self.base()}/{group['id']}",
                                            headers=self.manager).status_code, 403)

    def test_it_cannot_change_what_the_organization_allows(self):
        response = self.client.put(
            "/api/control/organization/policy", headers=self.manager,
            json={"expected_revision": 0, "settings": {"voice_commands_enabled": False}})
        self.assertEqual(response.status_code, 403, response.text)


if __name__ == "__main__":
    unittest.main()
