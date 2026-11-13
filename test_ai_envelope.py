"""L'enveloppe commune d'IA (2.3) : un montant en euros fixé au contrat,
découpé par mois avec report, jamais dépassé ; vide, la dictée passe sur le
moteur local et la bulle se met en pause."""

import asyncio
import time
import unittest
from unittest.mock import patch
from unittest import mock

from test_main import AdminTestCase  # noqa: F401  — prépare la base de test

import ai_envelope
import ai_runtime
import licence
import main

main.init_db()

DAY = 86400
# Jeudi 5 novembre 2026, 12 h UTC : troisième mois d'une licence de septembre.
NOW = 1793880000.0
PRICES = {"NOVA_AI_EUR_PER_MTOK_INPUT": "1", "NOVA_AI_EUR_PER_MTOK_OUTPUT": "4"}


def spent(email: str, eur: float, month: str = "2026-11"):
    with main.db() as conn:
        conn.execute("INSERT INTO ai_spend(month, email, eur) VALUES (?,?,?)", (month, email, eur))


class PureTests(unittest.TestCase):
    def test_call_cost_reads_the_tokens(self):
        with mock.patch.dict("os.environ", PRICES):
            cost = ai_envelope.call_cost({"prompt_tokens": 1_000_000, "completion_tokens": 500_000})
        self.assertAlmostEqual(cost, 3.0)

    def test_without_prices_nothing_is_counted(self):
        with mock.patch.dict("os.environ", {}, clear=True):
            self.assertFalse(ai_envelope.priced())

    def test_the_contract_is_split_by_month_with_carry_over(self):
        # Dix mois, 100 € : 10 € par mois ; en novembre, trois mois sont ouverts.
        self.assertAlmostEqual(ai_envelope.allowance(100, "2026-09-01", "2027-06-30", NOW), 30)
        self.assertAlmostEqual(ai_envelope.month_share(100, "2026-09-01", "2027-06-30"), 10)
        # Jamais plus que le contrat, même après l'échéance.
        self.assertAlmostEqual(ai_envelope.allowance(100, "2026-09-01", "2027-06-30", NOW + 400 * DAY), 100)


class EnvelopeTests(unittest.TestCase):
    def setUp(self):
        with main.db() as conn:
            conn.execute("DELETE FROM ai_spend")
        private_pem, public_pem = licence.new_key_pair()
        token = licence.issue(private_pem, organization="IPSA", tier="1-4", kind="pilot",
                              starts="2026-09-01", ends="2027-06-30", ai_envelope_eur=100)
        for patcher in (mock.patch.object(licence, "PUBLIC_KEY_PEM", public_pem),
                        mock.patch.object(main, "NOVA_LICENCE", token),
                        mock.patch.dict("os.environ", PRICES)):
            patcher.start()
            self.addCleanup(patcher.stop)

    def test_the_report_shows_the_month_and_the_rest(self):
        spent("a@example.com", 12, "2026-10")
        spent("a@example.com", 3)
        state = main.ai_envelope_state(NOW)
        self.assertAlmostEqual(state["allowance_eur"], 30)
        self.assertAlmostEqual(state["spent_eur"], 15)
        self.assertAlmostEqual(state["month_eur"], 3)
        self.assertFalse(state["empty"])
        self.assertNotIn("example.com", repr(state))

    def test_an_empty_envelope_sends_text_to_the_local_engine(self):
        spent("a@example.com", 30)
        self.assertTrue(main.ai_envelope_state(NOW)["empty"])
        primary = ai_runtime.FakeTextInferenceBackend()
        local = ai_runtime.FakeTextInferenceBackend()
        router = ai_runtime.OrganizationAIRouter(
            primary, ai_runtime.FakeSpeechInferenceBackend(), fallback_text_backend=local)
        with mock.patch.object(main, "ORGANIZATION_AI_ROUTER", router), \
                mock.patch.object(time, "time", return_value=NOW):
            main.AI_PAYER.set("b@example.com")
            asyncio.run(main.execute_text(ai_runtime.TaskType.REFORMULATION, "x", "y"))
        self.assertEqual((len(primary.requests), len(local.requests)), (0, 1))

    def test_the_envelope_fallback_is_logged_with_its_reason(self):
        primary = ai_runtime.FakeTextInferenceBackend()
        router = ai_runtime.OrganizationAIRouter(
            primary, ai_runtime.FakeSpeechInferenceBackend(),
            fallback_text_backend=ai_runtime.FakeTextInferenceBackend())
        with mock.patch.object(ai_runtime.observability, "log") as log:
            asyncio.run(router.generate(ai_runtime.TaskType.REFORMULATION, "x", "y",
                                        local_only=True))
        log.assert_any_call("ai.text_fallback", level="warning", reason="envelope")

    def test_one_student_cannot_empty_the_month(self):
        spent("a@example.com", 0.2 * 10)
        with mock.patch.object(time, "time", return_value=NOW):
            self.assertTrue(main.ai_local_only("a@example.com"))
            self.assertFalse(main.ai_local_only("b@example.com"))

    def test_spend_is_recorded_only_for_the_paid_engine(self):
        with mock.patch.object(time, "time", return_value=NOW):
            main.record_ai_spend("a@example.com", {"prompt_tokens": 1000, "completion_tokens": 0},
                                 {"adapter": "openai_compatible"})
            main.record_ai_spend("a@example.com", {"prompt_tokens": 1000, "completion_tokens": 0},
                                 {"adapter": "openai_compatible", "fallback": True})
        self.assertAlmostEqual(main.ai_envelope_state(NOW)["month_eur"], 0.001)

    def test_alerts_at_80_and_100_percent(self):
        spent("a@example.com", 23.9)
        with mock.patch.object(main.observability, "log") as log, \
                mock.patch.object(time, "time", return_value=NOW):
            main.record_ai_spend("b@example.com", {"completion_tokens": 50_000}, {"adapter": "x"})
            main.record_ai_spend("b@example.com", {"completion_tokens": 1_500_000}, {"adapter": "x"})
        alerts = [call.kwargs["percent"] for call in log.call_args_list
                  if call.args and call.args[0] == "ai.envelope_alert"]
        self.assertEqual(alerts, [80, 100])

    def test_the_80_percent_alert_is_not_lost_to_rounding(self):
        # 80 % de 30 € = 24 €. 23,993 € s'affichent 23,99 € puis 24,00 € :
        # un seuil lu sur le chiffre arrondi n'était jamais franchi.
        spent("a@example.com", 23.993)
        with mock.patch.object(main.observability, "log") as log, \
                mock.patch.object(time, "time", return_value=NOW):
            main.record_ai_spend("b@example.com", {"completion_tokens": 750}, {"adapter": "x"})
            main.record_ai_spend("b@example.com", {"completion_tokens": 1250}, {"adapter": "x"})
        alerts = [call.kwargs["percent"] for call in log.call_args_list
                  if call.args and call.args[0] == "ai.envelope_alert"]
        self.assertEqual(alerts, [80])

    def test_the_bubble_pauses_when_the_envelope_is_empty(self):
        spent("a@example.com", 30)
        with mock.patch.object(time, "time", return_value=NOW):
            with self.assertRaises(main.HTTPException) as refused:
                main.refuse_when_ai_envelope_empty(("commands", "selectionBubble"), "voice_commands")
            main.refuse_when_ai_envelope_empty("engineeringNotes", "engineering_notes")
        self.assertEqual(refused.exception.detail["code"], "AI_ENVELOPE_EMPTY")

    def test_every_figure_of_the_envelope_gauge_is_exact(self):
        """Abonnement : 100 € au contrat sur dix mois, 30 € ouverts en novembre,
        15 € dépensés dont 3 € ce mois-ci : la jauge doit dire 50 %."""
        spent("a@example.com", 12, "2026-10")
        spent("b@example.com", 3)
        envelope = main.subscription_report(NOW)["ai_envelope"]
        self.assertEqual(
            envelope,
            {"annual_eur": 100.0, "allowance_eur": 30.0, "spent_eur": 15.0,
             "month_eur": 3.0, "percent": 50, "empty": False})

    def test_the_gauge_reaches_80_then_100_percent_and_empties(self):
        spent("a@example.com", 24)
        near = main.ai_envelope_state(NOW)
        self.assertEqual((near["percent"], near["empty"]), (80, False))
        spent("b@example.com", 6)
        full = main.ai_envelope_state(NOW)
        self.assertEqual((full["spent_eur"], full["percent"], full["empty"]), (30.0, 100, True))

    def test_the_subscription_page_shows_the_envelope(self):
        spent("a@example.com", 3)
        envelope = main.subscription_report(NOW)["ai_envelope"]
        self.assertEqual(envelope["month_eur"], 3)
        self.assertNotIn("mine_over", envelope)

    def test_without_envelope_in_the_licence_nothing_changes(self):
        with mock.patch.object(main, "NOVA_LICENCE", ""):
            self.assertIsNone(main.ai_envelope_state(NOW))
            self.assertFalse(main.ai_local_only("a@example.com"))


if __name__ == "__main__":
    unittest.main()


class BenchSpendTests(unittest.TestCase):
    def test_a_bench_account_never_spends_the_school_envelope(self):
        import main
        with patch.dict("os.environ", {"NOVA_AI_REFINE_EUR_PER_CALL": "0.003"}),                 patch.object(main, "ai_envelope_state", return_value=None):
            with main.db() as conn:
                conn.execute("DELETE FROM ai_spend")
            main.record_ai_spend("banc@nova.invalid", {}, {}, eur=0.003)
            main.record_ai_spend("eleve@ecole.example", {}, {}, eur=0.003)
            with main.db() as conn:
                rows = conn.execute("SELECT email FROM ai_spend").fetchall()
        self.assertEqual([r["email"] for r in rows], ["eleve@ecole.example"])

