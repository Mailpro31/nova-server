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

    def test_it_finds_a_person_to_add_but_sees_nothing_of_the_file(self):
        # Avant : la recherche exigeait `identity_manage`, donc la gestion des
        # acces ne pouvait pas retrouver la personne a ajouter (403).
        self.member_id("etudiant@example.com")
        response = self.client.get(
            f"/api/control/organizations/{self.organization}/members"
            "?query=etudiant&limit=8", headers=self.manager)
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual([set(m) for m in response.json()["members"]], [{"user_id", "email"}])


class MemberAddressesStayWithTheirOwnersTests(ConsoleGroupsTestCase):
    """Les adresses ne sortent que vers un role qui gere les personnes ou les groupes."""

    def test_content_editor_and_read_only_cannot_list_group_members(self):
        group = self.created("Accompagnement")
        for role in ("content_editor", "read_only", "it_admin"):
            with self.subTest(role=role):
                token = grant_admin_session(f"{role}@example.com", role)
                response = self.client.get(f"{self.base()}/{group['id']}/members", headers=token)
                self.assertEqual(response.status_code, 403, response.text)

    def test_a_group_rule_preview_does_not_name_members_to_those_roles(self):
        self.member_id("etudiant@example.com")
        for role in ("content_editor", "read_only", "it_admin", "access_manager"):
            with self.subTest(role=role):
                token = grant_admin_session(f"{role}@example.com", role)
                response = self.client.post(
                    f"/api/control/organizations/{self.organization}/group-rules/preview",
                    headers=token,
                    json={"group_id": "x", "field": "department", "operator": "contains",
                          "value": "a"})
                self.assertEqual(response.status_code, 403, response.text)


if __name__ == "__main__":
    unittest.main()
