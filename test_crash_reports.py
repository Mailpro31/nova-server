"""Les rapports de plantage du poste (5.2) : ouverts par défaut, l'école les coupe.

Le poste lit `crash_reports` dans `/api/me` et n'envoie plus rien quand
l'école a fermé la case.
"""

from test_group_policies import GroupPolicyTestCase, POLICY  # noqa: F401
from test_main import AdminTestCase  # noqa: F401

import main

main.init_db()

KEY = "crash_reports_enabled"


class CrashReportsTests(AdminTestCase):
    def setUp(self):
        super().setUp()
        with main.db() as conn:
            conn.execute("DELETE FROM organization_policies")
            conn.execute("DELETE FROM organization_group_policies")

    def crash_reports(self):
        token = self.sign_in("etudiant@example.com")
        return self.client.get("/api/me", headers=self.auth(token)).json()["crash_reports"]

    def test_reports_are_on_by_default(self):
        self.assertIs(main.POLICY_SETTINGS[KEY].default, True)
        self.assertEqual(self.crash_reports(), {"enabled": True})

    def test_the_school_turns_them_off_for_its_members(self):
        token = self.admin_session()
        revision = self.client.get(POLICY, headers=self.auth(token)).json()["revision"]
        response = self.client.put(
            POLICY, headers=self.auth(token),
            json={"expected_revision": revision, "settings": {KEY: False}})
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(self.crash_reports(), {"enabled": False})
