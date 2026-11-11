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

    def test_days_left_and_status_follow_the_end_date(self):
        """Abonnement : « jours restants ». La licence couvre son dernier jour,
        donc au 5 novembre midi : 30 juin 2027 -> 237,5 j (238), 20 novembre -> 15,5 j (16)."""
        cases = (
            ("2027-06-30", "valid", 238),
            ("2026-11-20", "warning", 16),
            ("2026-11-04", "grace", 0),    # échue depuis une demi-journée
            ("2026-10-01", "expired", -34),  # 34,5 j de retard : la console affiche 0
        )
        for ends, status, days in cases:
            with self.subTest(ends=ends):
                licence_view = self.report(ends=ends)["licence"]
                self.assertEqual((licence_view["status"], licence_view["days_left"]),
                                 (status, days))
                self.assertEqual(licence_view["grace_days"], 30)

    def test_the_gauge_flags_exactly_at_ninety_percent_of_the_tier(self):
        for count, near, over in ((8, False, False), (9, True, False),
                                  (10, True, False), (11, True, True)):
            with self.subTest(active=count):
                with main.db() as conn:
                    conn.execute("DELETE FROM usage_hourly")
                for index in range(count):
                    dictated(f"p{index}@example.com", NOW)
                report = self.report(tier="1-10")
                self.assertEqual((report["seats"], report["active_month"]), (10, count))
                self.assertEqual((report["near_limit"], report["over_limit"]), (near, over))

    def test_tier_seats(self):
        self.assertEqual(main.tier_seats("151-250"), 250)
        self.assertEqual(main.tier_seats("50"), 50)
        self.assertIsNone(main.tier_seats("sur devis"))


class SubscriptionApiTests(AdminTestCase):
    """Ce que la page Abonnement lit réellement, par la route."""

    ROUTE = "/api/control/organization/subscription"

    def test_the_route_serves_the_figures_the_page_displays(self):
        with main.db() as conn:
            conn.execute("DELETE FROM usage_hourly")
            conn.execute("DELETE FROM usage_monthly")
        for name in "abc":
            dictated(f"{name}@example.com", time.time())
        today = time.gmtime()
        ends = time.strftime("%Y-%m-%d", time.gmtime(time.time() + 99 * DAY))
        private_pem, public_pem = licence.new_key_pair()
        token = licence.issue(private_pem, organization="IPSA", tier="151-250",
                              kind="subscription", starts=time.strftime("%Y-01-01", today),
                              ends=ends)
        admin = self.admin_session(role=main.SECURITY_ROLE_READ_ONLY)
        with mock.patch.object(licence, "PUBLIC_KEY_PEM", public_pem),                 mock.patch.object(main, "NOVA_LICENCE", token):
            body = self.client.get(self.ROUTE, headers=self.auth(admin)).json()
        self.assertEqual(body["active_month"], 3)
        self.assertEqual(body["active_year"], 3)
        self.assertEqual(body["seats"], 250)
        self.assertEqual(body["year_from"], time.strftime("%Y-01-01", today))
        self.assertEqual(body["licence"]["tier"], "151-250")
        # Fin = minuit après `ends`, soit 99 j plus la fraction de jour restante : 100 j.
        self.assertEqual(body["licence"]["days_left"], 100)
        self.assertFalse(body["near_limit"])
        self.assertFalse(body["over_limit"])


if __name__ == "__main__":
    unittest.main()
