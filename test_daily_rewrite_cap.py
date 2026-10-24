"""Un plafond de mises au propre par élève et par jour (n° 21 ter).

Demande de Sash (21/10), avec l'option API : chaque mise au propre coûte. Le
plafond se règle dans la console, pour l'école ou pour un groupe. Atteint, la
dictée est rendue telle quelle : rien n'est perdu, seule la mise au propre
manque.
"""

import json
import time
import unittest
from unittest.mock import patch

from test_main import AdminTestCase

import main

main.init_db()

EMAIL = "etudiant@example.com"


class DailyRewriteCapTests(AdminTestCase):
    def setUp(self):
        super().setUp()
        with main.db() as conn:
            conn.execute("DELETE FROM organization_policies")
            conn.execute("DELETE FROM usage")

    def cap(self, value: int):
        with main.db() as conn:
            conn.execute(
                "INSERT INTO organization_policies(organization_id, schema_version, "
                "revision, settings, updated_at, updated_by_user_id) VALUES (?,?,?,?,?,?)",
                (main.nova_organization_id(), main.POLICY_SCHEMA_VERSION, 1,
                 json.dumps({"daily_rewrites_per_member": value}), 0.0, None))

    def used_today(self, count: int):
        with main.db() as conn:
            conn.execute(
                "INSERT INTO usage(email, day, reformulations) VALUES (?,?,?)",
                (EMAIL, time.strftime("%Y-%m-%d"), count))

    def reformulate(self):
        token = self.sign_in(EMAIL)
        calls = []

        async def engine(task_type, instruction, message):
            calls.append(message)
            return "Le partiel est mardi."

        with patch.object(main, "execute_text", engine):
            response = self.client.post(
                "/api/reformulate", headers=self.auth(token),
                json={"text": "le partiel est mardi", "style_id": "nova_style_messages",
                      "style_prompt": "Message court."})
        self.assertEqual(response.status_code, 200, response.text)
        return response.json(), calls

    def test_the_cap_returns_the_dictation_without_calling_the_model(self):
        self.cap(3)
        self.used_today(3)
        body, calls = self.reformulate()
        self.assertEqual(calls, [])
        self.assertEqual(body["text"], "le partiel est mardi")
        self.assertTrue(body["capped"])

    def test_below_the_cap_the_dictation_is_rewritten(self):
        self.cap(3)
        self.used_today(2)
        body, calls = self.reformulate()
        self.assertEqual(len(calls), 1)
        self.assertEqual(body["text"], "Le partiel est mardi.")

    def test_the_school_can_close_personal_styles(self):
        """n° 64 : la règle ferme la création de Styles personnels sur le poste."""
        with main.db() as conn:
            conn.execute(
                "INSERT INTO organization_policies(organization_id, schema_version, "
                "revision, settings, updated_at, updated_by_user_id) VALUES (?,?,?,?,?,?)",
                (main.nova_organization_id(), main.POLICY_SCHEMA_VERSION, 1,
                 json.dumps({"personal_styles_enabled": False}), 0.0, None))
        token = self.sign_in(EMAIL)
        me = self.client.get("/api/me", headers=self.auth(token)).json()
        self.assertIn("personalStyles", me["closed_capabilities"])

    def test_no_cap_by_default(self):
        self.assertEqual(main.POLICY_SETTINGS["daily_rewrites_per_member"].default, 0)
        self.used_today(10_000)
        _, calls = self.reformulate()
        self.assertEqual(len(calls), 1)


if __name__ == "__main__":
    unittest.main()
