"""Groupes geres depuis la console.

Les groupes ne venaient que de l'annuaire, par SCIM. Une ecole sans SCIM ne
pouvait donc ni viser une lecon, ni surcharger un reglage pour une promo. La
console cree maintenant ses propres groupes :

- creer, renommer, supprimer, ajouter et retirer des membres ;
- un groupe venu de l'annuaire reste en lecture seule dans la console, et un
  groupe de la console n'apparait jamais au client SCIM, qui ne doit ni le
  modifier ni le supprimer ;
- chaque ecriture laisse une trace, et un groupe supprime ne s'applique plus.
"""

import unittest
import uuid

from test_main import AdminTestCase  # noqa: F401

import main

main.init_db()


def clear_groups():
    with main.db() as conn:
        for table in ("organization_group_memberships", "organization_group_policies",
                      "organization_groups", "scim_credentials"):
            conn.execute(f"DELETE FROM {table}")


class ConsoleGroupsTestCase(AdminTestCase):
    def setUp(self):
        super().setUp()
        clear_groups()
        self.organization = main.nova_organization_id()
        self.admin = self.admin_session()

    def base(self) -> str:
        return f"/api/control/organizations/{self.organization}/groups"

    def create(self, name: str = "Promo 2027", token: str | None = None):
        return self.client.post(self.base(), headers=self.auth(token or self.admin),
                                json={"display_name": name})

    def created(self, name: str = "Promo 2027") -> dict:
        response = self.create(name)
        self.assertEqual(response.status_code, 201, response.text)
        return response.json()["group"]

    def listed(self) -> dict[str, dict]:
        response = self.client.get(self.base(), headers=self.auth(self.admin))
        self.assertEqual(response.status_code, 200, response.text)
        return {group["id"]: group for group in response.json()["groups"]}

    def member_id(self, email: str) -> str:
        self.sign_in(email)
        with main.db() as conn:
            return main.user_by_email(conn, self.organization, email)["user_id"]

    def scim_group(self, name: str = "Annuaire") -> str:
        group_id = str(uuid.uuid4())
        with main.db() as conn:
            conn.execute(
                "INSERT INTO organization_groups(organization_id, group_id, external_id, "
                "display_name, created_at, updated_at) VALUES (?,?,?,?,?,?)",
                (self.organization, group_id, f"ext-{group_id}", name, 1.0, 1.0))
        return group_id


class CreateRenameDeleteTests(ConsoleGroupsTestCase):
    def test_a_group_is_created_listed_and_offered_for_targeting(self):
        group = self.created("Promo 2027")
        listed = self.listed()[group["id"]]
        self.assertEqual(listed["label"], "Promo 2027")
        self.assertEqual(listed["source"], "console")
        self.assertEqual(listed["member_count"], 0)
        options = self.client.get("/api/control/organization/learning",
                                  headers=self.auth(self.admin)).json()["groups"]
        self.assertIn(group["id"], [option["id"] for option in options])

    def test_a_group_is_renamed(self):
        group = self.created("Promo 2027")
        response = self.client.put(f"{self.base()}/{group['id']}",
                                   headers=self.auth(self.admin),
                                   json={"display_name": "Promo 2027 — Aéro"})
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(self.listed()[group["id"]]["label"], "Promo 2027 — Aéro")

    def test_a_name_must_be_present_short_and_unique(self):
        self.created("Promo 2027")
        for name, code in (("   ", "GROUP_NAME_INVALID"), ("x" * 121, "GROUP_NAME_INVALID"),
                           ("promo 2027", "GROUP_NAME_TAKEN")):
            with self.subTest(name=name[:12]):
                response = self.create(name)
                self.assertIn(response.status_code, (400, 409), response.text)
                self.assertEqual(response.json()["detail"], code)

    def test_a_deleted_group_disappears_and_stops_applying(self):
        group = self.created()
        user_id = self.member_id("dedans@example.com")
        self.client.put(f"{self.base()}/{group['id']}/members/{user_id}",
                        headers=self.auth(self.admin))
        response = self.client.delete(f"{self.base()}/{group['id']}",
                                      headers=self.auth(self.admin))
        self.assertEqual(response.status_code, 204, response.text)
        self.assertNotIn(group["id"], self.listed())
        with main.db() as conn:
            memberships = conn.execute(
                "SELECT COUNT(*) AS n FROM organization_group_memberships WHERE group_id=?",
                (group["id"],)).fetchone()["n"]
        self.assertEqual(memberships, 0)

    def test_every_change_is_audited(self):
        group = self.created()
        user_id = self.member_id("dedans@example.com")
        self.client.put(f"{self.base()}/{group['id']}", headers=self.auth(self.admin),
                        json={"display_name": "Renamed"})
        self.client.put(f"{self.base()}/{group['id']}/members/{user_id}",
                        headers=self.auth(self.admin))
        self.client.delete(f"{self.base()}/{group['id']}/members/{user_id}",
                           headers=self.auth(self.admin))
        self.client.delete(f"{self.base()}/{group['id']}", headers=self.auth(self.admin))
        with main.db() as conn:
            actions = {row["action"] for row in conn.execute(
                "SELECT action FROM admin_audit_log WHERE action LIKE 'group.%'").fetchall()}
        self.assertEqual(actions, {"group.create", "group.rename", "group.member_add",
                                   "group.member_remove", "group.delete"})
        for action in actions:
            self.assertIn(action, main.AUDIT_ACTION_LABELS)

    def test_read_only_may_list_but_not_change(self):
        reader = self.admin_session("lecture@example.com", main.SECURITY_ROLE_READ_ONLY,
                                    subject="oid-ro")
        self.assertEqual(self.client.get(self.base(), headers=self.auth(reader)).status_code, 200)
        self.assertEqual(self.create("Nope", token=reader).status_code, 403)


class MembershipTests(ConsoleGroupsTestCase):
    def test_members_are_added_listed_and_removed(self):
        group = self.created()
        user_id = self.member_id("dedans@example.com")
        added = self.client.put(f"{self.base()}/{group['id']}/members/{user_id}",
                                headers=self.auth(self.admin))
        self.assertEqual(added.status_code, 200, added.text)
        members = self.client.get(f"{self.base()}/{group['id']}/members",
                                  headers=self.auth(self.admin)).json()["members"]
        self.assertEqual([member["email"] for member in members], ["dedans@example.com"])
        self.assertEqual(self.listed()[group["id"]]["member_count"], 1)
        removed = self.client.delete(f"{self.base()}/{group['id']}/members/{user_id}",
                                     headers=self.auth(self.admin))
        self.assertEqual(removed.status_code, 200, removed.text)
        self.assertEqual(self.listed()[group["id"]]["member_count"], 0)

    def test_a_group_membership_applies_the_group_settings(self):
        group = self.created("Examen")
        headers = self.auth(self.sign_in("dedans@example.com"))
        with main.db() as conn:
            user_id = main.user_by_email(conn, self.organization, "dedans@example.com")["user_id"]
        self.client.put(f"/api/control/organization/policy/groups/{group['id']}",
                        headers=self.auth(self.admin),
                        json={"expected_revision": 0, "settings": {"learning_enabled": False}})
        before = self.client.get("/api/me", headers=headers).json()["closed_capabilities"]
        self.client.put(f"{self.base()}/{group['id']}/members/{user_id}",
                        headers=self.auth(self.admin))
        after = self.client.get("/api/me", headers=headers).json()["closed_capabilities"]
        self.assertNotIn("learning", before)
        self.assertIn("learning", after)

    def test_an_unknown_member_or_group_is_not_found(self):
        group = self.created()
        self.assertEqual(self.client.put(f"{self.base()}/{group['id']}/members/inconnu",
                                         headers=self.auth(self.admin)).status_code, 404)
        user_id = self.member_id("dedans@example.com")
        self.assertEqual(self.client.put(f"{self.base()}/inconnu/members/{user_id}",
                                         headers=self.auth(self.admin)).status_code, 404)


class DirectoryBoundaryTests(ConsoleGroupsTestCase):
    def test_a_directory_group_is_listed_but_read_only_in_the_console(self):
        group_id = self.scim_group("Annuaire")
        self.assertEqual(self.listed()[group_id]["source"], "scim")
        user_id = self.member_id("dedans@example.com")
        for response in (
            self.client.put(f"{self.base()}/{group_id}", headers=self.auth(self.admin),
                            json={"display_name": "Changed"}),
            self.client.delete(f"{self.base()}/{group_id}", headers=self.auth(self.admin)),
            self.client.put(f"{self.base()}/{group_id}/members/{user_id}",
                            headers=self.auth(self.admin)),
        ):
            with self.subTest(status=response.status_code):
                self.assertEqual(response.status_code, 409, response.text)
                self.assertEqual(response.json()["detail"], "GROUP_MANAGED_BY_DIRECTORY")

    def test_the_directory_never_sees_a_console_group(self):
        group = self.created("Console only")
        credential = self.client.post(
            f"/api/control/organizations/{self.organization}/provisioning/credentials",
            headers=self.auth(self.admin), json={"name": "C1", "provider_context": "generic"})
        self.assertEqual(credential.status_code, 200, credential.text)
        scim = {"Authorization": f"Bearer {credential.json()['token']}",
                "Content-Type": "application/scim+json"}
        listed = self.client.get("/scim/v2/Groups", headers=scim).json()
        self.assertNotIn(group["id"], [resource["id"] for resource in listed["Resources"]])
        self.assertEqual(self.client.get(f"/scim/v2/Groups/{group['id']}",
                                         headers=scim).status_code, 404)
        self.assertEqual(self.client.delete(f"/scim/v2/Groups/{group['id']}",
                                            headers=scim).status_code, 404)
        self.assertIn(group["id"], self.listed())


if __name__ == "__main__":
    unittest.main()
