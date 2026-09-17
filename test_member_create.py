"""Ajouter un membre depuis la console.

Un compte naissait seulement a la premiere connexion, ou par la ligne de
commande. Une ecole veut pourtant preparer ses comptes a l'avance : les ranger
dans leur metier, les viser par une policy ou une lecon avant la rentree.

- l'adresse doit appartenir aux domaines de l'organisation ;
- le compte cree est celui que la premiere connexion reprendra, pas un double ;
- ajouter ne donne aucun privilege, et laisse une trace dans le journal.
"""

import json
import unittest

from test_main import AdminTestCase  # noqa: F401

import main

main.init_db()


class MemberCreateTests(AdminTestCase):
    def setUp(self):
        super().setUp()
        self.organization = main.nova_organization_id()
        self.admin = self.admin_session()

    def add(self, body: dict, token: str | None = None):
        return self.client.post(
            f"/api/control/organizations/{self.organization}/members",
            headers=self.auth(token or self.admin), json=body)

    def test_a_member_is_added_ready_for_their_first_sign_in(self):
        response = self.add({"email": "Nouvelle.Etudiante@example.com",
                             "member_role": "student", "cohort": "AERO 1"})
        self.assertEqual(response.status_code, 201, response.text)
        member = response.json()["member"]
        self.assertEqual(member["email"], "Nouvelle.Etudiante@example.com")
        with main.db() as conn:
            row = main.user_by_email(conn, self.organization, "nouvelle.etudiante@example.com")
        self.assertIsNotNone(row)
        self.assertEqual(row["role"], "student")
        self.assertEqual(row["cohort"], "AERO 1")
        self.assertEqual(main.security_role_for(row), main.SECURITY_ROLE_MEMBER)

    def test_the_first_sign_in_reuses_the_added_account(self):
        created = self.add({"email": "prof@example.com", "member_role": "teacher"}).json()
        self.sign_in("prof@example.com")
        with main.db() as conn:
            count = conn.execute(
                "SELECT COUNT(*) AS n FROM users WHERE organization_id=? AND normalized_email=?",
                (self.organization, "prof@example.com")).fetchone()["n"]
            row = main.user_by_email(conn, self.organization, "prof@example.com")
        self.assertEqual(count, 1)
        self.assertEqual(row["user_id"], created["member"]["user_id"])

    def test_an_address_outside_the_organization_domains_is_refused(self):
        response = self.add({"email": "intrus@ailleurs.org"})
        self.assertEqual(response.status_code, 400, response.text)
        self.assertEqual(response.json()["detail"], "MEMBER_EMAIL_OUTSIDE_DOMAINS")

    def test_a_malformed_address_or_unknown_type_is_refused(self):
        self.assertEqual(self.add({"email": "pas-une-adresse"}).json()["detail"],
                         "MEMBER_EMAIL_INVALID")
        self.assertEqual(self.add({"email": "x@example.com", "member_role": "roi"})
                         .json()["detail"], "MEMBER_ROLE_INVALID")

    def test_an_existing_account_is_not_duplicated(self):
        self.sign_in("deja@example.com")
        response = self.add({"email": "DEJA@example.com"})
        self.assertEqual(response.status_code, 409, response.text)
        self.assertEqual(response.json()["detail"], "MEMBER_ALREADY_EXISTS")

    def test_read_only_cannot_add(self):
        reader = self.admin_session("lecture@example.com", main.SECURITY_ROLE_READ_ONLY,
                                    subject="oid-ro")
        self.assertEqual(self.add({"email": "x@example.com"}, reader).status_code, 403)

    def test_adding_is_audited_without_privilege(self):
        created = self.add({"email": "audit@example.com", "member_role": "staff"}).json()
        with main.db() as conn:
            row = conn.execute(
                "SELECT target_id, metadata FROM admin_audit_log "
                "WHERE action='member.create'").fetchone()
        self.assertEqual(row["target_id"], created["member"]["user_id"])
        self.assertEqual(json.loads(row["metadata"])["member_role"], "staff")
        self.assertIn("member.create", main.AUDIT_ACTION_LABELS)


if __name__ == "__main__":
    unittest.main()
