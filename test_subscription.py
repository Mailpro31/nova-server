"""L'abonnement dans la console (2.1) et les utilisateurs actifs anonymes (2.2)."""

import time
import unittest
from unittest import mock

from test_main import AdminTestCase  # noqa: F401  — prépare la base de test

import licence
import main

main.init_db()

DAY = 86400
# Jeudi 5 novembre 2026, 12 h UTC.
NOW = 1793880000.0


def dictated(email: str, stamp: float):
    with main.db() as conn:
        conn.execute(
            "INSERT INTO usage_hourly(email, hour, transcriptions) VALUES (?,?,1)",
            (email, time.strftime("%Y-%m-%dT%H", time.gmtime(stamp))),
        )


class SubscriptionTests(unittest.TestCase):
    def setUp(self):
        with main.db() as conn:
            conn.execute("DELETE FROM usage_hourly")
            conn.execute("DELETE FROM usage_monthly")
        self.private_pem, self.public_pem = licence.new_key_pair()

    def report(self, **fields):
        token = licence.issue(self.private_pem, **{
            "organization": "IPSA", "tier": "1-4", "kind": "pilot",
            "starts": "2026-09-01", "ends": "2027-06-30", **fields})
        with mock.patch.object(licence, "PUBLIC_KEY_PEM", self.public_pem), \
                mock.patch.object(main, "NOVA_LICENCE", token):
            return main.subscription_report(NOW)

    def test_active_people_are_counted_without_names(self):
        dictated("a@example.com", NOW)
        dictated("a@example.com", NOW - DAY)
        dictated("b@example.com", NOW - 2 * DAY)
        dictated("c@example.com", NOW - 40 * DAY)  # octobre : l'année, pas le mois
        report = self.report()
        self.assertEqual(report["active_month"], 2)
        self.assertEqual(report["active_year"], 3)
        self.assertEqual(report["year_from"], "2026-09-01")
        self.assertNotIn("example.com", repr(report))

    def test_the_gauge_warns_near_the_tier_without_blocking(self):
        for name in "abcd":
            dictated(f"{name}@example.com", NOW)
        report = self.report()
        self.assertEqual(report["seats"], 4)
        self.assertTrue(report["near_limit"])
        self.assertFalse(report["over_limit"])
        dictated("e@example.com", NOW)
        self.assertTrue(self.report()["over_limit"])

    def test_renewal_reminder_before_the_end(self):
        self.assertFalse(self.report()["renewal_reminder"])
        self.assertTrue(self.report(ends="2026-11-20")["renewal_reminder"])

    def test_without_licence_the_year_is_the_calendar_year(self):
        dictated("a@example.com", NOW)
        report = main.subscription_report(NOW)
        self.assertEqual(report["licence"]["status"], "none")
        self.assertIsNone(report["seats"])
        self.assertEqual(report["year_from"], "2026-01-01")
        self.assertEqual(report["active_month"], 1)

    def test_tier_seats(self):
        self.assertEqual(main.tier_seats("151-250"), 250)
        self.assertEqual(main.tier_seats("50"), 50)
        self.assertIsNone(main.tier_seats("sur devis"))


if __name__ == "__main__":
    unittest.main()
