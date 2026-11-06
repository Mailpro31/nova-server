"""L'adoption de Nova, telle que la direction et la CTI la lisent.

Le suivi d'usage de la console sert au diagnostic : 24 heures, 7 ou 31 jours,
heure par heure. Une direction qui rend compte d'un semestre, ou une mission
handicap qui prépare un bilan, a besoin d'autre chose : combien de personnes
se servent de Nova chaque semaine, combien y reviennent, et combien elles
dictent. Aucun nom : ce sont des comptes.

Une semaine où moins de cinq personnes ont dicté est masquée (2.4). Quand on
sait qui fait partie du pilote, un compte d'une personne est l'usage de cette
personne.
"""

import json
import time
import unittest

from test_main import AdminTestCase  # noqa: F401  — prépare la base de test

import main

main.init_db()

DAY = 86400
HOUR = 3600
# Mercredi 7 octobre 2026, 12 h UTC : la semaine commence le lundi 5.
NOW = 1791374400.0
# Le seuil de masquage : cinq personnes (2.4).
PEOPLE = tuple(f"{name}@ecole.example" for name in "abcde")


def hour_key(stamp: float) -> str:
    return time.strftime("%Y-%m-%dT%H", time.gmtime(stamp))


def insert_hour(email: str, stamp: float, transcriptions=1, reformulations=0, audio=30.0):
    with main.db() as conn:
        conn.execute(
            """INSERT INTO usage_hourly(email, hour, transcriptions, reformulations,
                   audio_seconds) VALUES (?,?,?,?,?)""",
            (email, hour_key(stamp), transcriptions, reformulations, audio),
        )


class AdoptionReportTests(unittest.TestCase):
    def setUp(self):
        with main.db() as conn:
            conn.execute("DELETE FROM usage_hourly")
            conn.execute("DELETE FROM usage_monthly")

    def test_twelve_weeks_are_always_listed_even_without_any_dictation(self):
        report = main.adoption_report(NOW)
        self.assertEqual(len(report["weeks"]), 12)
        self.assertEqual(report["weeks"][-1]["week_start"], "2026-10-05")
        self.assertEqual(report["weeks"][0]["week_start"], "2026-07-20")
        # Rien n'a encore été mesuré : on ne sait pas dire « personne ».
        self.assertFalse(report["weeks"][-1]["measured"])
        self.assertIsNone(report["weeks"][-1]["people"])
        self.assertEqual(report["months"], [])
        self.assertEqual(report["min_people"], 5)

    def test_weeks_before_the_measure_began_are_not_zero(self):
        """RTX, b855ac6 : le relevé horaire a commencé le 29/09 ; les semaines
        d'avant affichaient « 0 personne » alors qu'il y avait eu des dictées."""
        start = NOW - 7 * DAY  # mercredi 30 septembre, semaine du 28
        for email in PEOPLE:
            insert_hour(email, start)
        weeks = {week["week_start"]: week for week in main.adoption_report(NOW)["weeks"]}
        before = weeks["2026-09-21"]
        self.assertFalse(before["measured"])
        self.assertIsNone(before["people"])
        self.assertIsNone(before["dictations"])
        first = weeks["2026-09-28"]
        self.assertTrue(first["measured"])
        self.assertTrue(first["partial"])
        self.assertEqual(first["people"], 5)
        current = weeks["2026-10-05"]
        self.assertTrue(current["measured"])
        self.assertFalse(current["partial"])
        # Mesurée et vide : là, zéro est la vérité.
        self.assertEqual(current["people"], 0)

    def test_a_partial_week_with_one_person_is_also_masked(self):
        insert_hour("a@ecole.example", NOW - 7 * DAY)
        week = {w["week_start"]: w for w in main.adoption_report(NOW)["weeks"]}["2026-09-28"]
        self.assertTrue(week["masked"])
        self.assertTrue(week["partial"])
        self.assertIsNone(week["people"])

    def test_the_month_in_which_the_measure_began_is_partial(self):
        for email in PEOPLE:
            insert_hour(email, NOW - 7 * DAY)
            insert_hour(email, NOW - HOUR)
        months = {m["month"]: m for m in main.adoption_report(NOW)["months"]}
        self.assertTrue(months["2026-09"]["partial"])
        self.assertFalse(months["2026-10"]["partial"])

    def test_a_week_counts_people_regular_people_and_dictations(self):
        monday = NOW - 2 * DAY
        # Trois jours différents : une personne régulière.
        for offset in (0, DAY, 2 * DAY):
            insert_hour("a@ecole.example", monday + offset, transcriptions=2, audio=60.0)
        insert_hour("b@ecole.example", monday, transcriptions=1, reformulations=1)
        insert_hour("c@ecole.example", monday + DAY, transcriptions=3)
        insert_hour("d@ecole.example", monday)
        insert_hour("e@ecole.example", monday)
        week = main.adoption_report(NOW)["weeks"][-1]
        self.assertEqual(week["people"], 5)
        self.assertEqual(week["regular_people"], 1)
        # Comme le résumé du suivi d'usage : transcriptions et reformulations.
        self.assertEqual(week["dictations"], 6 + 2 + 3 + 1 + 1)
        self.assertEqual(week["reformulations"], 1)
        self.assertEqual(week["audio_minutes"], round((180 + 30 * 4) / 60, 1))
        self.assertFalse(week["masked"])

    def test_a_week_with_fewer_than_five_people_is_masked(self):
        for email in PEOPLE[:4]:
            insert_hour(email, NOW - HOUR, transcriptions=7)
        week = main.adoption_report(NOW)["weeks"][-1]
        self.assertTrue(week["masked"])
        for key in ("people", "regular_people", "dictations", "reformulations",
                    "audio_minutes"):
            self.assertIsNone(week[key], key)

    def test_months_merge_the_monthly_trace_and_the_recent_hours(self):
        with main.db() as conn:
            for email in PEOPLE:
                conn.execute(
                    """INSERT INTO usage_monthly(email, month, transcriptions,
                           audio_seconds) VALUES (?,?,?,?)""",
                    (email, "2026-06", 4, 120.0),
                )
        for email in (*PEOPLE[:4], "f@ecole.example"):
            insert_hour(email, NOW - HOUR, transcriptions=2)
        months = {m["month"]: m for m in main.adoption_report(NOW)["months"]}
        self.assertEqual(months["2026-06"]["people"], 5)
        self.assertEqual(months["2026-06"]["dictations"], 20)
        self.assertEqual(months["2026-06"]["audio_minutes"], 10.0)
        self.assertEqual(months["2026-10"]["people"], 5)
        self.assertEqual(months["2026-10"]["dictations"], 10)

    def test_the_report_never_names_anyone(self):
        for email in PEOPLE:
            insert_hour(email, NOW - HOUR)
        dumped = json.dumps(main.adoption_report(NOW))
        self.assertNotIn("@", dumped)
        self.assertNotIn("email", dumped)


class PilotReportTests(unittest.TestCase):
    """Le bilan du pilote (2.4) : qui continue après un mois, et le sondage."""

    def setUp(self):
        with main.db() as conn:
            for table in ("usage_hourly", "usage_monthly", "survey_scores", "survey_answered"):
                conn.execute(f"DELETE FROM {table}")

    def test_people_who_keep_using_nova_after_a_month(self):
        # Cinq ont commencé il y a plus d'un mois ; quatre dictent encore.
        for email in PEOPLE:
            insert_hour(email, NOW - 40 * DAY)
        for email in PEOPLE[:4]:
            insert_hour(email, NOW - 2 * DAY)
        insert_hour("nouveau@ecole.example", NOW - DAY)  # trop récent pour compter
        retention = main.adoption_report(NOW)["retention"]
        self.assertEqual(retention, {"days": 30, "started": 5, "continuing": 4, "masked": False})

    def test_retention_is_masked_under_five_people(self):
        for email in PEOPLE[:4]:
            insert_hour(email, NOW - 40 * DAY)
        retention = main.adoption_report(NOW)["retention"]
        self.assertTrue(retention["masked"])
        self.assertIsNone(retention["continuing"])

    def test_the_survey_keeps_an_average_and_never_a_name(self):
        for index, email in enumerate(PEOPLE):
            main.record_survey_answer(email, 3 + index % 3, NOW)
        # Une seconde réponse le même mois ne compte pas.
        main.record_survey_answer(PEOPLE[0], 1, NOW)
        survey = main.adoption_report(NOW)["survey"]
        self.assertEqual(survey, [{"month": "2026-10", "answers": 5, "average": 3.8}])
        with main.db() as conn:
            stored = repr([tuple(row) for row in conn.execute("SELECT * FROM survey_scores")])
        self.assertNotIn("@", stored)

    def test_the_survey_average_is_hidden_under_five_answers(self):
        for email in PEOPLE[:4]:
            main.record_survey_answer(email, 5, NOW)
        self.assertEqual(main.adoption_report(NOW)["survey"],
                         [{"month": "2026-10", "answers": None, "average": None}])


class AdoptionApiTests(AdminTestCase):
    ROUTE = "/api/control/organization/adoption"

    def test_the_route_needs_an_administrator(self):
        self.assertIn(self.client.get(self.ROUTE).status_code, (401, 403))

    def test_a_read_only_administrator_reads_the_counts(self):
        token = self.admin_session(role=main.SECURITY_ROLE_READ_ONLY)
        response = self.client.get(self.ROUTE, headers=self.auth(token))
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(len(response.json()["weeks"]), 12)

    def test_a_member_answers_the_survey_once_a_month(self):
        token = main.issue_token("eleve@ecole.example", "poste")["token"]
        for expected in (200, 200):
            response = self.client.post("/api/survey", headers=self.auth(token), json={"score": 4})
            self.assertEqual(response.status_code, expected, response.text)
        self.assertEqual(self.client.post("/api/survey", headers=self.auth(token),
                                          json={"score": 9}).status_code, 422)


if __name__ == "__main__":
    unittest.main()
