"""La version de Nova Desktop de chaque poste, telle que la console la compte."""

import json

from test_main import AdminTestCase  # noqa: F401  — prepare la base de test
import main

main.init_db()


class ClientVersionTests(AdminTestCase):
    ROUTE = "/api/control/organization/client-versions"

    def setUp(self):
        super().setUp()
        with main.db() as conn:
            conn.execute("DELETE FROM client_versions")

    def member_headers(self, email):
        self.client.post("/api/auth/request", json={"email": email, "machine": "poste-1"})
        with main.db() as conn:
            code = conn.execute(
                "SELECT code FROM auth_codes WHERE email=?", (email,)
            ).fetchone()["code"]
        response = self.client.post(
            "/api/auth/verify", json={"email": email, "code": code, "machine": "poste-1"}
        )
        self.assertEqual(response.status_code, 200)
        return {"Authorization": f"Bearer {response.json()['token']}"}

    def me(self, email, version):
        headers = {**self.member_headers(email), "X-Nova-Version": version}
        self.assertEqual(self.client.get("/api/me", headers=headers).status_code, 200)

    def read(self, role=main.SECURITY_ROLE_ORGANIZATION_ADMIN):
        token = self.admin_session(role=role)
        return self.client.get(self.ROUTE, headers=self.auth(token))

    def test_postes_are_counted_by_version_newest_first(self):
        self.me("a@example.com", "1.0.9")
        self.me("b@example.com", "1.0.10")
        self.me("c@example.com", "1.0.10")
        response = self.read()
        self.assertEqual(response.status_code, 200, response.text)
        versions = response.json()["versions"]
        self.assertEqual([(v["version"], v["members"]) for v in versions],
                         [("1.0.10", 2), ("1.0.9", 1)])
        self.assertNotIn("example.com", json.dumps(response.json()))

    def test_a_member_counts_once_with_the_last_version_seen(self):
        self.me("a@example.com", "1.0.70")
        self.me("a@example.com", "1.0.71")
        versions = self.read().json()["versions"]
        self.assertEqual([(v["version"], v["members"]) for v in versions], [("1.0.71", 1)])

    def test_an_odd_header_is_ignored_not_stored(self):
        self.me("a@example.com", "<script>")
        self.me("b@example.com", "1" * 40)
        self.assertEqual(self.read().json()["versions"], [])

    def test_a_read_only_role_may_read_and_a_content_editor_may_not(self):
        self.assertEqual(self.read(role=main.SECURITY_ROLE_READ_ONLY).status_code, 200)
        self.assertEqual(self.read(role=main.SECURITY_ROLE_CONTENT_EDITOR).status_code, 403)
