"""Durée de conservation des dictées passées, fixée par l'école (2.7).

Les dictées vivent sur le poste. L'école peut demander qu'elles n'y restent pas
plus de N jours ; 0, la valeur par défaut, laisse le choix à chacun. Le serveur
n'a rien à effacer : il annonce la durée, le poste l'applique.
"""

import unittest

from test_dictation_limit import clear_policies
from test_main import AdminTestCase  # noqa: F401

import main

main.init_db()

PATH = "/api/control/organization/policy"


class RetentionSettingTests(unittest.TestCase):
    def test_no_limit_by_default_and_bounded(self):
        self.assertEqual(main.DEFAULT_ORGANIZATION_POLICY["past_dictations_days"], 0)
        self.assertEqual(main.validate_policy_settings({"past_dictations_days": 30}),
                         {"past_dictations_days": 30})
        for value in (-1, 3651, True, "30"):
            with self.subTest(value=value), self.assertRaises(ValueError):
                main.validate_policy_settings({"past_dictations_days": value})

    def test_the_duration_closes_nothing(self):
        self.assertIsNone(main.POLICY_SETTINGS["past_dictations_days"].capability)


class RetentionDeliveryTests(AdminTestCase):
    def setUp(self):
        super().setUp()
        clear_policies()

    def test_the_workstation_learns_the_duration(self):
        self.assertEqual(main.public_campus_config()["past_dictations"], {"days": 0})
        token = self.admin_session()
        revision = self.client.get(PATH, headers=self.auth(token)).json()["revision"]
        response = self.client.put(PATH, headers=self.auth(token), json={
            "expected_revision": revision, "settings": {"past_dictations_days": 30}})
        self.assertEqual(response.status_code, 200, response.text)
        member = self.sign_in("etudiant@example.com")
        me = self.client.get("/api/me", headers=self.auth(member)).json()
        self.assertEqual(me["past_dictations"], {"days": 30})


if __name__ == "__main__":
    unittest.main()
