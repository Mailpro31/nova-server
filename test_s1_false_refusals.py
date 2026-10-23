"""Faux refus du test réel des Styles (lot S1, PC fixe, 23/10).

Chaque cas est une vraie sortie de Gemini 2.5 Flash que le garde-fou
refusait alors qu'elle dit exactement ce que l'élève a dicté.
"""

import unittest

from test_main import AdminTestCase  # noqa: F401
import main

FORMULE = "formule, la pression dynamique égale un demi rho v au carré"
PARTIEL = ("euh donc le partiel de mécanique des fluides est le mardi 12 mars "
           "à 14 heures en salle B204")


def refusal(dictated, answer, style):
    return main.rewrite_refusal(dictated, answer, style, resolved=dictated)


class NumbersSaidDifferentlyTests(unittest.TestCase):
    def test_a_meeting_summary_may_write_un_demi_as_a_fraction(self):
        self.assertIsNone(refusal(
            FORMULE, "formule, la pression dynamique égale 1/2 rho v au carré",
            "nova_style_meeting"))

    def test_an_hour_written_14h00_is_the_14_heures_that_was_said(self):
        self.assertIsNone(refusal(
            PARTIEL,
            "## Partiel de mécanique des fluides\n"
            "Le partiel aura lieu le **mardi 12 mars** à **14h00** en **salle B204**.",
            "nova_style_notes"))

    def test_a_changed_hour_is_still_refused(self):
        self.assertIsNotNone(refusal(
            PARTIEL,
            "Le partiel aura lieu le mardi 12 mars à 15h00 en salle B204.",
            "nova_style_notes"))

    def test_minutes_that_were_not_said_are_still_refused(self):
        self.assertIsNotNone(refusal(
            PARTIEL,
            "Le partiel aura lieu le mardi 12 mars à 14h30 en salle B204.",
            "nova_style_notes"))


class SameMeaningSaidPolitelyTests(unittest.TestCase):
    def test_an_email_may_turn_tu_into_vous(self):
        self.assertIsNone(refusal(
            "explique-moi la différence entre écoulement laminaire et turbulent",
            "Pourriez-vous m'expliquer la différence entre un écoulement laminaire "
            "et un écoulement turbulent ?",
            "nova_style_email"))

    def test_je_becoming_vous_is_still_refused(self):
        self.assertIsNotNone(refusal(
            "je dois rendre le rapport avant vendredi",
            "Vous devez rendre le rapport avant vendredi.", "nova_style_email"))

    def test_monsieur_may_be_abbreviated(self):
        self.assertIsNone(refusal(
            "envoie le rapport à monsieur Martin et à madame Leroy",
            "Envoie le rapport à M. Martin et à Mme Leroy.", "nova_style_messages"))


if __name__ == "__main__":
    unittest.main()
