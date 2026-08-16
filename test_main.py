import os
import tempfile
import unittest
from unittest.mock import patch


TEST_DIRECTORY = tempfile.TemporaryDirectory()
os.environ["NOVA_DB"] = os.path.join(TEST_DIRECTORY.name, "nova-test.db")
os.environ["NOVA_DOMAINS"] = "example.com"
os.environ["NOVA_ADMIN_TOKEN"] = "test-admin-token"
os.environ["NOVA_ORGANIZATION_NAME"] = "Nova Test School"
os.environ["NOVA_ORGANIZATION_SHORT_NAME"] = "NTS"
os.environ["NOVA_ENTRA_CLIENT_ID"] = ""

from fastapi.testclient import TestClient

import main


class NovaServerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.client = TestClient(main.app)

    def setUp(self):
        with main.db() as conn:
            for table in (
                "auth_codes",
                "tokens",
                "users",
                "usage",
                "personal_dictionary",
                "snippets",
                "formatting_rules",
            ):
                conn.execute(f"DELETE FROM {table}")

    def sign_in(self):
        email = "student@example.com"
        response = self.client.post(
            "/api/auth/request",
            json={"email": email, "machine": "test-laptop"},
        )
        self.assertEqual(response.status_code, 200)
        with main.db() as conn:
            code = conn.execute(
                "SELECT code FROM auth_codes WHERE email=?", (email,)
            ).fetchone()["code"]
        response = self.client.post(
            "/api/auth/verify",
            json={"email": email, "code": code, "machine": "test-laptop"},
        )
        self.assertEqual(response.status_code, 200)
        return {"Authorization": f"Bearer {response.json()['token']}"}

    def test_public_configuration_and_health(self):
        health = self.client.get("/api/health")
        self.assertEqual(health.status_code, 200)
        self.assertEqual(health.json()["status"], "ok")

        config = self.client.get("/api/config")
        self.assertEqual(config.status_code, 200)
        body = config.json()
        self.assertEqual(body["organization"]["shortName"], "NTS")
        self.assertIn("email_code", body["auth_methods"])
        self.assertNotIn("entra", body["auth_methods"])
        self.assertTrue(body["capabilities"]["aiSkills"])
        self.assertEqual(
            body["ai_skills"],
            {"enabled": True, "required": False, "trackProgress": True},
        )

    def test_auth_writing_preferences_ai_skills_and_logout(self):
        headers = self.sign_in()
        self.assertEqual(self.client.get("/api/me", headers=headers).status_code, 200)

        added = self.client.post(
            "/api/dictionary",
            headers=headers,
            json={"term": "IPSA", "replacement": "IPSA"},
        )
        self.assertEqual(added.status_code, 200)
        self.assertGreater(added.json()["id"], 0)

        snippet = self.client.post(
            "/api/snippets",
            headers=headers,
            json={"trigger": "mon école", "content": "Nova Test School"},
        )
        self.assertEqual(snippet.status_code, 200)

        rule = self.client.post(
            "/api/formatting-rules",
            headers=headers,
            json={"rule": "Use SI units"},
        )
        self.assertEqual(rule.status_code, 200)

        vocabulary = self.client.get("/api/vocabulary", headers=headers).json()
        self.assertEqual(vocabulary["personal"][0]["term"], "IPSA")
        self.assertEqual(vocabulary["snippets"][0]["trigger"], "mon école")

        skills = self.client.get("/api/ai-skills", headers=headers)
        self.assertEqual(skills.status_code, 200)
        self.assertEqual(len(skills.json()["skills"]), 6)
        self.assertIn("practice", skills.json()["skills"][0])

        self.assertEqual(
            self.client.post("/api/auth/logout", headers=headers).status_code,
            200,
        )
        self.assertEqual(self.client.get("/api/me", headers=headers).status_code, 401)

    def test_admin_requires_the_configured_token(self):
        self.assertEqual(self.client.get("/api/admin/overview").status_code, 403)
        response = self.client.get(
            "/api/admin/overview",
            headers={"X-Admin-Token": "test-admin-token"},
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["max_seats"], main.MAX_SEATS)

    def test_microsoft_device_flow_creates_a_nova_session(self):
        class Response:
            def __init__(self, status_code, body):
                self.status_code = status_code
                self.body = body

            def json(self):
                return self.body

        class MicrosoftClient:
            async def __aenter__(self):
                return self

            async def __aexit__(self, *_):
                return None

            async def post(self, url, **_):
                if url.endswith("/devicecode"):
                    return Response(
                        200,
                        {
                            "device_code": "device-code",
                            "user_code": "ABCD-EFGH",
                            "verification_uri": "https://microsoft.com/devicelogin",
                            "expires_in": 900,
                            "interval": 1,
                            "message": "Sign in",
                        },
                    )
                return Response(200, {"access_token": "graph-token"})

            async def get(self, *_args, **_kwargs):
                return Response(200, {"mail": "student@example.com"})

        main.ENTRA_FLOWS.clear()
        with (
            patch.object(main, "ENTRA_CLIENT_ID", "public-client-id"),
            patch.object(main.httpx, "AsyncClient", return_value=MicrosoftClient()),
        ):
            started = self.client.post(
                "/api/auth/entra/start", json={"machine": "test-laptop"}
            )
            self.assertEqual(started.status_code, 200)
            self.assertEqual(started.json()["user_code"], "ABCD-EFGH")

            completed = self.client.post(
                "/api/auth/entra/poll",
                json={"flow_id": started.json()["flow_id"]},
            )
            self.assertEqual(completed.status_code, 200)
            self.assertEqual(completed.json()["status"], "complete")
            self.assertEqual(completed.json()["email"], "student@example.com")
            token = completed.json()["token"]
            profile = self.client.get(
                "/api/me", headers={"Authorization": f"Bearer {token}"}
            )
            self.assertEqual(profile.status_code, 200)
            self.assertEqual(profile.json()["email"], "student@example.com")


if __name__ == "__main__":
    unittest.main()
