"""L'historique que la console tracera : mesure, repli, purge.

Ces tests ne lancent aucune boucle et ne dependent d'aucun materiel : le releve
machine est teste sur ses fonctions pures, le reste sur la base.
"""

import json
import time
import unittest
from unittest.mock import patch

from test_main import AdminTestCase  # noqa: F401  — prepare la base de test

import main
import usage_metrics
import usage_worker

main.init_db()


DAY = 86400


class HostSampleTests(unittest.TestCase):
    def test_a_machine_without_a_graphics_card_is_not_a_failure(self):
        """Pas de carte, pas d'erreur : des valeurs vides que la console sait afficher."""
        with patch.object(usage_metrics.shutil, "which", return_value=None):
            sample = usage_metrics.sample_host(0.0)
        self.assertIsNone(sample.gpu_percent)
        self.assertIsNone(sample.vram_total_gb)
        self.assertEqual(sample.sampled_at, "1970-01-01T00:00:00")

    def test_an_unreadable_graphics_card_is_not_a_failure_either(self):
        """`nvidia-smi` present mais muet : on rend vide, on ne leve pas."""
        class Broken:
            returncode = 1
            stdout = ""

        with patch.object(usage_metrics.shutil, "which", return_value="nvidia-smi"), \
             patch.object(usage_metrics.subprocess, "run", return_value=Broken()):
            sample = usage_metrics.sample_host(0.0)
        self.assertIsNone(sample.gpu_percent)

    def test_the_graphics_card_is_read_in_gigabytes(self):
        class Fine:
            returncode = 0
            stdout = "42, 3072, 6144, 61, 74.5\n"

        with patch.object(usage_metrics.shutil, "which", return_value="nvidia-smi"), \
             patch.object(usage_metrics.subprocess, "run", return_value=Fine()):
            sample = usage_metrics.sample_host(0.0)
        self.assertEqual(sample.gpu_percent, 42.0)
        self.assertEqual(sample.vram_used_gb, 3.0)
        self.assertEqual(sample.vram_total_gb, 6.0)
        self.assertEqual(sample.gpu_temperature_c, 61.0)
        self.assertEqual(sample.gpu_power_w, 74.5)


class UsageHistoryTests(unittest.TestCase):
    def setUp(self):
        with main.db() as conn:
            for table in ("usage_hourly", "usage_monthly", "host_samples", "usage"):
                conn.execute(f"DELETE FROM {table}")
        self.addCleanup(self.clear)

    def clear(self):
        with main.db() as conn:
            for table in ("usage_hourly", "usage_monthly", "host_samples", "usage"):
                conn.execute(f"DELETE FROM {table}")

    def hourly(self):
        with main.db() as conn:
            return conn.execute(
                "SELECT email, hour, reformulations, latency_ms_sum, "
                "latency_ms_count, latency_ms_max FROM usage_hourly ORDER BY hour"
            ).fetchall()

    def test_a_dictation_is_counted_by_the_hour_with_its_latency(self):
        main.count_usage("a@ecole.example", "reformulation", latency_ms=1200.0)
        main.count_usage("a@ecole.example", "reformulation", latency_ms=800.0)
        rows = self.hourly()
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0][2], 2)
        self.assertEqual(rows[0][3], 2000.0)
        self.assertEqual(rows[0][4], 2)
        # Le pire cas, pas le dernier.
        self.assertEqual(rows[0][5], 1200.0)

    def test_a_dictation_without_a_measured_latency_does_not_skew_the_average(self):
        """Compter une latence absente comme zero ferait mentir la moyenne."""
        main.count_usage("a@ecole.example", "reformulation", latency_ms=1000.0)
        main.count_usage("a@ecole.example", "reformulation")
        rows = self.hourly()
        self.assertEqual(rows[0][2], 2)
        self.assertEqual(rows[0][4], 1, "une seule latence mesuree")
        self.assertEqual(rows[0][3] / rows[0][4], 1000.0)

    def insert_hour(self, email, hour, *, reformulations=1, latency=500.0):
        with main.db() as conn:
            conn.execute(
                """INSERT INTO usage_hourly(email, hour, reformulations, audio_seconds,
                       latency_ms_sum, latency_ms_count, latency_ms_max)
                   VALUES (?,?,?,?,?,?,?)""",
                (email, hour, reformulations, 0.0, latency, 1, latency),
            )

    def test_hours_outside_the_window_become_a_monthly_trace(self):
        old = time.strftime("%Y-%m-%dT%H", time.gmtime(time.time() - 120 * DAY))
        self.insert_hour("a@ecole.example", old, reformulations=3, latency=900.0)
        recent = time.strftime("%Y-%m-%dT%H", time.gmtime())
        self.insert_hour("a@ecole.example", recent)

        self.assertEqual(main.rollup_usage(), 1)

        with main.db() as conn:
            monthly = conn.execute(
                "SELECT month, reformulations, latency_ms_count, latency_ms_max "
                "FROM usage_monthly"
            ).fetchall()
            remaining = conn.execute("SELECT hour FROM usage_hourly").fetchall()
        self.assertEqual(monthly[0][0], old[:7])
        self.assertEqual(monthly[0][1], 3)
        self.assertEqual(monthly[0][3], 900.0)
        # L'heure recente reste : seule la fenetre expiree est repliee.
        self.assertEqual([r[0] for r in remaining], [recent])

    def test_the_rollup_can_be_run_twice_without_counting_twice(self):
        old = time.strftime("%Y-%m-%dT%H", time.gmtime(time.time() - 120 * DAY))
        self.insert_hour("a@ecole.example", old, reformulations=2)
        main.rollup_usage()
        main.rollup_usage()
        with main.db() as conn:
            total = conn.execute(
                "SELECT SUM(reformulations) FROM usage_monthly"
            ).fetchone()[0]
        self.assertEqual(total, 2)

    def test_the_purge_removes_only_what_is_past_its_retention(self):
        with main.db() as conn:
            for offset, stamp in ((1, "recent"), (200, "vieux")):
                conn.execute(
                    "INSERT INTO host_samples(sampled_at, cpu_percent) VALUES (?,?)",
                    (
                        time.strftime(
                            "%Y-%m-%dT%H:%M:%S", time.gmtime(time.time() - offset * DAY)
                        ),
                        1.0,
                    ),
                )
            conn.execute(
                "INSERT INTO usage_monthly(email, month, reformulations) VALUES (?,?,?)",
                ("a@ecole.example", "2000-01", 1),
            )

        removed = main.purge_usage()

        with main.db() as conn:
            samples = conn.execute("SELECT COUNT(*) FROM host_samples").fetchone()[0]
            months = conn.execute("SELECT COUNT(*) FROM usage_monthly").fetchone()[0]
        self.assertEqual(samples, 1, "le releve recent reste")
        self.assertEqual(months, 0, "un mois de l'an 2000 ne se conserve pas")
        self.assertEqual(removed, 2)

    def test_two_samples_in_the_same_second_do_not_break_the_round(self):
        sample = usage_metrics.sample_host(0.0)
        main.record_host_sample(sample)
        main.record_host_sample(sample)
        with main.db() as conn:
            count = conn.execute("SELECT COUNT(*) FROM host_samples").fetchone()[0]
        self.assertEqual(count, 1)


class UsageWorkerTests(unittest.TestCase):
    def test_a_round_never_raises_even_when_everything_fails(self):
        """Un tour qui leve arreterait la boucle et perdrait tous les suivants."""
        with patch.object(main, "record_host_sample", side_effect=OSError("boom")), \
             patch.object(main, "rollup_usage", side_effect=OSError("boom")), \
             patch.object(main, "purge_usage", side_effect=OSError("boom")):
            outcome = usage_worker.run_once(0.0, maintain=True)
        self.assertEqual(outcome["failed"], 3)
        self.assertEqual(outcome["sampled"], 0)

    def test_maintenance_is_skipped_on_an_ordinary_round(self):
        with patch.object(main, "record_host_sample") as record, \
             patch.object(main, "rollup_usage") as rollup, \
             patch.object(main, "purge_usage") as purge:
            usage_worker.run_once(0.0)
        record.assert_called_once()
        rollup.assert_not_called()
        purge.assert_not_called()


class UsageMetricsApiTests(AdminTestCase):
    """Ce que la console lit, et ce qu'elle n'a pas le droit de lire."""

    ROUTE = "/api/control/organization/usage-metrics"

    def setUp(self):
        super().setUp()
        with main.db() as conn:
            conn.execute("DELETE FROM usage_hourly")
            conn.execute("DELETE FROM host_samples")

    def read(self, query="", role=main.SECURITY_ROLE_ORGANIZATION_ADMIN):
        token = self.admin_session(role=role)
        return self.client.get(f"{self.ROUTE}{query}", headers=self.auth(token))

    def seed(self):
        hour = time.strftime("%Y-%m-%dT%H", time.gmtime())
        with main.db() as conn:
            for email, reformulations, latency in (
                ("a@ecole.example", 5, 1200.0),
                ("b@ecole.example", 2, 6200.0),
            ):
                conn.execute(
                    """INSERT INTO usage_hourly(email, hour, transcriptions,
                           reformulations, audio_seconds, latency_ms_sum,
                           latency_ms_count, latency_ms_max)
                       VALUES (?,?,?,?,?,?,?,?)""",
                    (email, hour, 1, reformulations, 12.5,
                     latency * reformulations, reformulations, latency),
                )
        return hour

    def test_the_totals_never_name_anyone(self):
        self.seed()
        response = self.read()
        self.assertEqual(response.status_code, 200, response.text)
        payload = response.json()
        self.assertNotIn("members", payload)
        self.assertNotIn("a@ecole.example", json.dumps(payload))

    def test_the_summary_counts_the_hours_above_the_objective(self):
        hour = self.seed()
        summary = self.read().json()["summary"]
        self.assertEqual(summary["dictations"], 9)
        self.assertEqual(summary["people_peak"], 2)
        self.assertEqual(summary["busiest_hour"], hour)
        self.assertEqual(summary["latency_ms_max"], 6200)
        self.assertEqual(summary["over_five_seconds"], 1)

    def test_the_named_detail_is_journalled(self):
        self.seed()
        payload = self.read("?members=true").json()
        self.assertEqual(payload["members"][0]["email"], "a@ecole.example")
        with main.db() as conn:
            actions = [
                row["action"]
                for row in conn.execute("SELECT action FROM admin_audit_log").fetchall()
            ]
        self.assertIn("usage_metrics.read_members", actions)

    def test_reading_the_totals_is_not_journalled(self):
        """Une page ouverte toute la journee remplirait le journal pour rien."""
        self.seed()
        self.read()
        with main.db() as conn:
            count = conn.execute(
                "SELECT COUNT(*) AS n FROM admin_audit_log "
                "WHERE action LIKE 'usage_metrics%'"
            ).fetchone()["n"]
        self.assertEqual(count, 0)

    def test_a_read_only_role_sees_the_totals_but_never_the_names(self):
        """`read_only` porte `diagnostics_read`. Il ne gere pas les personnes."""
        self.seed()
        self.assertEqual(self.read(role=main.SECURITY_ROLE_READ_ONLY).status_code, 200)
        refused = self.read("?members=true", role=main.SECURITY_ROLE_READ_ONLY)
        self.assertEqual(refused.status_code, 403)

    def test_a_content_editor_sees_nothing_at_all(self):
        self.seed()
        refused = self.read(role=main.SECURITY_ROLE_CONTENT_EDITOR)
        self.assertEqual(refused.status_code, 403)

    def test_the_window_is_bounded(self):
        self.seed()
        payload = self.read("?hours=100000").json()
        span = time.mktime(time.strptime(payload["until"], "%Y-%m-%dT%H")) - \
            time.mktime(time.strptime(payload["since"], "%Y-%m-%dT%H"))
        self.assertLessEqual(span / 3600, main.USAGE_WINDOW_MAX_HOURS + 1)

    def test_an_empty_window_answers_zero_and_not_an_error(self):
        payload = self.read("?hours=1").json()
        self.assertEqual(payload["summary"]["dictations"], 0)
        self.assertIsNone(payload["summary"]["busiest_hour"])
        self.assertEqual(payload["timeline"], [])


if __name__ == "__main__":
    unittest.main()
