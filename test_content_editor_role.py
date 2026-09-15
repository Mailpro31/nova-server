"""Le role « editeur de contenu ».

Une ecole ou une entreprise confie souvent la preparation des contenus —
lecons d'Apprendre, Styles, Prompts, vocabulaire — a des personnes qui ne
doivent toucher ni aux membres, ni a la securite, ni a ce que l'organisation
autorise. Leur donner `organization_admin` les laissait tout faire.

`content_editor` porte donc exactement deux capacites : lire l'organisation,
et gerer le contenu. La matrice fait le reste, sans controle special.
"""

import unittest

from test_main import AdminTestCase, grant_admin_session  # noqa: F401

import learning
import main

main.init_db()


class ContentEditorMatrixTests(unittest.TestCase):
    def test_the_role_exists_and_administers(self):
        self.assertIn("content_editor", main.SECURITY_ROLES)
        self.assertIn("content_editor", main.ADMIN_SECURITY_ROLES)

    def test_it_reads_the_organization_and_manages_content_only(self):
        self.assertEqual(
            main.capabilities_for_role("content_editor"),
            frozenset({main.CAPABILITY_ORGANIZATION_READ, main.CAPABILITY_CONTENT_MANAGE}),
        )


class ContentEditorRouteTests(AdminTestCase):
    def setUp(self):
        super().setUp()
        learning.reset_cache()
        with main.db() as conn:
            conn.execute("DELETE FROM organization_learning_lesson_settings")
            conn.execute("DELETE FROM organization_policies")
        self.editor = grant_admin_session("redaction@example.com", "content_editor")

    def test_it_can_archive_a_learn_lesson(self):
        response = self.client.post(
            "/api/control/organization/learning/lessons/ask-better-questions/archive",
            headers=self.editor, json={"expected_revision": 0})
        self.assertEqual(response.status_code, 200, response.text)

    def test_it_can_read_the_learn_settings(self):
        response = self.client.get("/api/control/organization/learning", headers=self.editor)
        self.assertEqual(response.status_code, 200, response.text)

    def test_it_cannot_change_what_the_organization_allows(self):
        response = self.client.put(
            "/api/control/organization/policy", headers=self.editor,
            json={"expected_revision": 0, "settings": {"voice_commands_enabled": False}})
        self.assertEqual(response.status_code, 403, response.text)

    def test_it_cannot_give_itself_or_anyone_a_role(self):
        self.sign_in("etudiant@example.com")
        with main.db() as conn:
            user_id = conn.execute(
                "SELECT user_id FROM users WHERE email=?", ("etudiant@example.com",)
            ).fetchone()["user_id"]
        response = self.client.post(
            f"/api/control/organizations/{main.nova_organization_id()}"
            f"/administrators/{user_id}/role",
            headers=self.editor, json={"security_role": "organization_admin"})
        self.assertEqual(response.status_code, 403, response.text)

    def test_an_organization_admin_can_grant_the_role(self):
        admin = self.auth(self.admin_session())
        self.sign_in("redacteur@example.com")
        with main.db() as conn:
            user_id = conn.execute(
                "SELECT user_id FROM users WHERE email=?", ("redacteur@example.com",)
            ).fetchone()["user_id"]
        response = self.client.post(
            f"/api/control/organizations/{main.nova_organization_id()}"
            f"/administrators/{user_id}/role",
            headers=admin, json={"security_role": "content_editor"})
        self.assertEqual(response.status_code, 200, response.text)
        with main.db() as conn:
            row = conn.execute(
                "SELECT * FROM users WHERE user_id=?", (user_id,)).fetchone()
        self.assertEqual(main.security_role_for(row), "content_editor")


if __name__ == "__main__":
    unittest.main()
