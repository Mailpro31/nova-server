"""Faux refus du test réel des Styles (lot S1, PC fixe, 23/10).

Chaque cas est une vraie sortie de Gemini 2.5 Flash que le garde-fou
refusait alors qu'elle dit exactement ce que l'élève a dicté.
"""

import unittest

from test_main import AdminTestCase  # noqa: F401
import main

main.init_db()

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


class InventedMarkersTests(unittest.TestCase):
    """n° 18 : le modèle met un mot entre accolades (« {{marge du coût}} »)."""

    def test_an_invented_marker_becomes_its_word(self):
        self.assertEqual(
            main.without_invented_markers(
                "on calcule la marge du coût", "On calcule la {{marge du coût}}."),
            "On calcule la marge du coût.")

    def test_a_real_snippet_marker_is_kept(self):
        self.assertEqual(
            main.without_invented_markers(
                "voici mon adresse {{snippet0}}", "Voici mon adresse {{snippet0}}."),
            "Voici mon adresse {{snippet0}}.")


class RoomCodeTests(unittest.TestCase):
    """n° 7 : la relecture audio écrivait « A 112 » au lieu de « A112 »."""

    def test_a_room_code_whisper_wrote_joined_stays_joined(self):
        self.assertEqual(
            main.joined_room_codes("rendez-vous en A112 demain", "Rendez-vous en A 112 demain."),
            "Rendez-vous en A112 demain.")

    def test_a_code_after_salle_is_joined(self):
        self.assertEqual(
            main.joined_room_codes("cours en salle b 204", "Cours en salle B 204."),
            "Cours en salle B204.")

    def test_a_letter_and_a_number_elsewhere_are_left_alone(self):
        self.assertEqual(
            main.joined_room_codes("le vecteur a 12 composantes",
                                   "Le vecteur A 12 composantes."),
            "Le vecteur A 12 composantes.")


class RoomRepairTests(unittest.TestCase):
    def test_a_filler_between_the_code_and_its_repair(self):
        """n° 15 : « Tu vois, euh non » gardait l'ancien code."""
        self.assertEqual(
            main.resolve_simple_repairs("Le TD est en salle C122. Tu vois, euh non, B119."),
            "Le TD est en salle B119.")

    def test_a_after_salle_is_the_letter_a(self):
        """n° 13 : Whisper écrit « à 119 » pour la salle A119."""
        self.assertEqual(main.resolve_simple_repairs("Rendez-vous en salle à 119."),
                         "Rendez-vous en salle A119.")

    def test_an_hour_after_salle_is_left_alone(self):
        self.assertEqual(main.resolve_simple_repairs("On libère la salle à 14 h."),
                         "On libère la salle à 14 h.")


class BlockSnippetTests(unittest.TestCase):
    """n° 2 : « signature ipsa » restait devant la signature (« Signature : Ipsa. : Sasha »)."""

    SNIPPETS = [{"trigger": "signature ipsa", "content": "Sasha Martin\nÉtudiant IPSA"},
                {"trigger": "mon adresse", "content": "7 impasse des Bons Voisins"}]

    def test_a_block_replaces_its_trigger(self):
        text, restores = main.protect_snippets("merci, signature ipsa", self.SNIPPETS)
        self.assertEqual(text, "merci, {{snippet0}}")
        self.assertEqual(main.restore_snippets(text, restores),
                         "merci, Sasha Martin\nÉtudiant IPSA")

    def test_a_one_line_value_keeps_its_trigger(self):
        text, _ = main.protect_snippets("voici mon adresse", self.SNIPPETS)
        self.assertEqual(text, "voici mon adresse {{snippet0}}")


if __name__ == "__main__":
    unittest.main()
