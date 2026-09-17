"""Le membre voit ses groupes, sauf quand l'organisation le masque.

Un etudiant doit savoir dans quel groupe il est — « Aero 2 » — sur Nova
Desktop. L'administrateur peut masquer cet affichage a quatre niveaux :

- pour toute l'organisation (reglage `member_groups_visible`) ;
- pour les membres d'un groupe (surcharge de groupe du meme reglage ; entre
  deux groupes, le masquage l'emporte) ;
- pour une personne precise, qui l'emporte sur tout le reste ;
- pour un groupe precis, qui n'apparait alors chez aucun de ses membres.

Quand l'affichage est masque, `/api/me` n'envoie aucun nom de groupe : le poste
ne peut pas montrer ce qu'il n'a pas recu. Chaque changement fait bouger
`profile_version`, pour que le poste relise `/api/me` en moins d'une minute.
"""

import unittest

from test_main import AdminTestCase  # noqa: F401

import main
from test_group_policies import create_group

main.init_db()

POLICY = "/api/control/organization/policy"


def clear():
    with main.db() as conn:
        for table in ("organization_group_memberships", "organization_group_policies",
                      "organization_groups", "organization_policies",
                      "organization_member_group_display"):
            conn.execute(f"DELETE FROM {table}")


class MemberGroupsTestCase(AdminTestCase):
    def setUp(self):
        super().setUp()
        clear()
        self.organization = main.nova_organization_id()
        self.admin = self.admin_session()
        self.member = self.auth(self.sign_in("etudiant@example.com"))
        with main.db() as conn:
            self.member_id = main.user_by_email(
                conn, self.organization, "etudiant@example.com")["user_id"]

    def me(self, headers: dict | None = None) -> dict:
        response = self.client.get("/api/me", headers=headers or self.member)
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()["membership"]

    def labels(self, headers: dict | None = None) -> list[str]:
        return [group["label"] for group in self.me(headers)["groups"]]

    def profile_version(self) -> str:
        response = self.client.get("/api/organization/changes", headers=self.member)
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()["profile_version"]

    def set_organization(self, settings: dict):
        current = self.client.get(POLICY, headers=self.auth(self.admin)).json()["revision"]
        response = self.client.put(POLICY, headers=self.auth(self.admin),
                                   json={"expected_revision": current, "settings": settings})
        self.assertEqual(response.status_code, 200, response.text)

    def set_group_policy(self, group_id: str, settings: dict):
        response = self.client.put(f"{POLICY}/groups/{group_id}", headers=self.auth(self.admin),
                                   json={"expected_revision": 0, "settings": settings})
        self.assertEqual(response.status_code, 200, response.text)

    def groups_base(self) -> str:
        return f"/api/control/organizations/{self.organization}/groups"

    def set_group_visibility(self, group_id: str, visible: bool, headers: dict | None = None):
        return self.client.put(f"{self.groups_base()}/{group_id}/visibility",
                               headers=headers or self.auth(self.admin),
                               json={"visible_to_members": visible})

    def member_display_path(self, user_id: str | None = None) -> str:
        return (f"/api/control/organizations/{self.organization}/members/"
                f"{user_id or self.member_id}/group-display")

    def set_member_display(self, visible, headers: dict | None = None):
        return self.client.put(self.member_display_path(),
                               headers=headers or self.auth(self.admin),
                               json={"visible": visible})


class DefaultTests(MemberGroupsTestCase):
    def test_a_member_sees_their_groups_by_default(self):
        create_group("Aero 2", ["etudiant@example.com"])
        membership = self.me()
        self.assertTrue(membership["groups_visible"])
        self.assertEqual(self.labels(), ["Aero 2"])
        group = membership["groups"][0]
        self.assertEqual(set(group), {"id", "label", "source", "external_group_id"})
        # Une source que le poste connait deja : une valeur inconnue ferait
        # rejeter toute la reponse par un poste installe.
        self.assertIn(group["source"], {"scim", "manual"})
        self.assertIsNone(group["external_group_id"])

    def test_groups_are_sorted_and_deleted_groups_never_show(self):
        create_group("Promo B", ["etudiant@example.com"])
        create_group("Promo A", ["etudiant@example.com"])
        gone = create_group("Ancien", ["etudiant@example.com"])
        with main.db() as conn:
            conn.execute("UPDATE organization_groups SET deleted_at=1 WHERE group_id=?", (gone,))
        self.assertEqual(self.labels(), ["Promo A", "Promo B"])

    def test_the_member_never_sees_someone_elses_groups(self):
        self.sign_in("autre@example.com")
        create_group("Aero 3", ["autre@example.com"])
        self.assertEqual(self.labels(), [])


class OrganizationAndGroupLevelTests(MemberGroupsTestCase):
    def test_the_organization_hides_groups_for_everyone(self):
        create_group("Aero 2", ["etudiant@example.com"])
        self.set_organization({"member_groups_visible": False})
        membership = self.me()
        self.assertFalse(membership["groups_visible"])
        self.assertEqual(membership["groups"], [])

    def test_a_group_hides_groups_for_its_members_only(self):
        self.sign_in("autre@example.com")
        exam = create_group("Examen", ["etudiant@example.com"])
        create_group("Aero 3", ["autre@example.com"])
        self.set_group_policy(exam, {"member_groups_visible": False})
        self.assertFalse(self.me()["groups_visible"])
        other = self.auth(self.sign_in("autre@example.com"))
        self.assertEqual(self.labels(other), ["Aero 3"])

    def test_between_two_groups_hiding_wins(self):
        open_group = create_group("Ouvert", ["etudiant@example.com"])
        hidden = create_group("Masque", ["etudiant@example.com"])
        self.set_group_policy(open_group, {"member_groups_visible": True})
        self.set_group_policy(hidden, {"member_groups_visible": False})
        self.assertFalse(self.me()["groups_visible"])

    def test_a_group_reopens_what_the_organization_hid(self):
        staff = create_group("Personnel", ["etudiant@example.com"])
        self.set_organization({"member_groups_visible": False})
        self.set_group_policy(staff, {"member_groups_visible": True})
        self.assertEqual(self.labels(), ["Personnel"])


class GroupVisibilityTests(MemberGroupsTestCase):
    def test_a_hidden_group_disappears_but_the_others_stay(self):
        create_group("Aero 2", ["etudiant@example.com"])
        secret = create_group("Suivi particulier", ["etudiant@example.com"])
        response = self.set_group_visibility(secret, False)
        self.assertEqual(response.status_code, 200, response.text)
        self.assertFalse(response.json()["group"]["visible_to_members"])
        self.assertTrue(self.me()["groups_visible"])
        self.assertEqual(self.labels(), ["Aero 2"])

    def test_a_directory_group_can_be_hidden_too(self):
        scim = create_group("Annuaire", ["etudiant@example.com"])
        with main.db() as conn:
            conn.execute("UPDATE organization_groups SET source='scim' WHERE group_id=?", (scim,))
        self.assertEqual(self.set_group_visibility(scim, False).status_code, 200)
        self.assertEqual(self.labels(), [])

    def test_the_group_list_says_whether_members_see_it(self):
        group = create_group("Aero 2", [])
        self.set_group_visibility(group, False)
        listed = self.client.get(self.groups_base(), headers=self.auth(self.admin)).json()
        [row] = [row for row in listed["groups"] if row["id"] == group]
        self.assertFalse(row["visible_to_members"])

    def test_visibility_needs_identity_management_and_is_audited(self):
        group = create_group("Aero 2", [])
        self.assertIn(self.set_group_visibility(group, False, headers=self.member).status_code,
                      (401, 403))
        self.assertEqual(self.set_group_visibility("inconnu", False).status_code, 404)
        self.set_group_visibility(group, False)
        with main.db() as conn:
            actions = [row["action"] for row in conn.execute(
                "SELECT action FROM admin_audit_log WHERE target_id=?", (group,))]
        self.assertIn("group.visibility", actions)

    def test_the_body_must_be_a_boolean(self):
        group = create_group("Aero 2", [])
        response = self.client.put(f"{self.groups_base()}/{group}/visibility",
                                   headers=self.auth(self.admin),
                                   json={"visible_to_members": "non"})
        self.assertEqual(response.status_code, 422)


class PersonLevelTests(MemberGroupsTestCase):
    def test_a_person_can_be_hidden_while_the_organization_shows(self):
        create_group("Aero 2", ["etudiant@example.com"])
        response = self.set_member_display(False)
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json(), {"visible": False, "effective": False})
        self.assertFalse(self.me()["groups_visible"])
        self.assertEqual(self.me()["groups"], [])

    def test_a_person_wins_over_their_groups_and_the_organization(self):
        exam = create_group("Examen", ["etudiant@example.com"])
        self.set_organization({"member_groups_visible": False})
        self.set_group_policy(exam, {"member_groups_visible": False})
        self.set_member_display(True)
        self.assertEqual(self.labels(), ["Examen"])

    def test_a_person_can_go_back_to_inheriting(self):
        create_group("Aero 2", ["etudiant@example.com"])
        self.set_member_display(False)
        response = self.set_member_display(None)
        self.assertEqual(response.json(), {"visible": None, "effective": True})
        self.assertEqual(self.labels(), ["Aero 2"])

    def test_a_hidden_group_stays_hidden_even_for_a_person_shown(self):
        secret = create_group("Suivi particulier", ["etudiant@example.com"])
        self.set_group_visibility(secret, False)
        self.set_member_display(True)
        self.assertEqual(self.labels(), [])

    def test_the_setting_is_read_back_and_audited(self):
        self.set_member_display(False)
        response = self.client.get(self.member_display_path(), headers=self.auth(self.admin))
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json(), {"visible": False, "effective": False})
        with main.db() as conn:
            actions = [row["action"] for row in conn.execute(
                "SELECT action FROM admin_audit_log WHERE target_id=?", (self.member_id,))]
        self.assertIn("member.group_display", actions)

    def test_only_identity_managers_and_known_members(self):
        self.assertIn(self.set_member_display(False, headers=self.member).status_code, (401, 403))
        response = self.client.put(self.member_display_path("inconnu"),
                                   headers=self.auth(self.admin), json={"visible": False})
        self.assertEqual(response.status_code, 404)
        response = self.client.put(self.member_display_path(), headers=self.auth(self.admin),
                                   json={"visible": "oui"})
        self.assertEqual(response.status_code, 422)


class ProfileMarkerTests(MemberGroupsTestCase):
    def test_every_change_moves_the_profile_marker(self):
        start = self.profile_version()
        group = create_group("Aero 2", ["etudiant@example.com"])
        joined = self.profile_version()
        self.assertNotEqual(joined, start)

        self.set_group_visibility(group, False)
        hidden = self.profile_version()
        self.assertNotEqual(hidden, joined)
        self.set_group_visibility(group, True)
        self.assertEqual(self.profile_version(), joined)

        self.set_member_display(False)
        self.assertNotEqual(self.profile_version(), joined)
        self.set_member_display(None)

        with main.db() as conn:
            conn.execute("UPDATE organization_groups SET display_name='Aero 3' WHERE group_id=?",
                         (group,))
        self.assertNotEqual(self.profile_version(), joined)

    def test_nothing_changed_means_the_same_marker(self):
        create_group("Aero 2", ["etudiant@example.com"])
        self.assertEqual(self.profile_version(), self.profile_version())


if __name__ == "__main__":
    unittest.main()
