"""Le texte rendu quand un Style est refusé se lit comme une phrase.

Test réel des Styles du 20/10 (BUGS-A-CORRIGER n° 3) : après un refus,
l'élève recevait « écris un mail… », « envoie le rapport… », « donc le
partiel… » — en minuscule, avec le « donc » de l'oral en tête.
"""

import unittest

from test_main import AdminTestCase  # noqa: F401
import main


class PresentableFallbackTests(unittest.TestCase):
    def test_the_first_letter_is_capitalised(self):
        self.assertEqual(
            main.presentable_fallback("écris un mail au professeur Dupont"),
            "Écris un mail au professeur Dupont",
        )
        self.assertEqual(
            main.presentable_fallback("envoie le rapport à monsieur Martin"),
            "Envoie le rapport à monsieur Martin",
        )

    def test_a_leading_donc_from_speech_is_dropped(self):
        self.assertEqual(
            main.presentable_fallback("donc le partiel de mécanique est le mardi 12 mars"),
            "Le partiel de mécanique est le mardi 12 mars",
        )
        self.assertEqual(
            main.presentable_fallback("euh donc, le partiel est jeudi"),
            "Le partiel est jeudi",
        )

    def test_donc_inside_a_sentence_is_kept(self):
        self.assertEqual(
            main.presentable_fallback("il faut donc réviser le chapitre 3"),
            "Il faut donc réviser le chapitre 3",
        )

    def test_a_dictation_starting_with_a_number_is_unchanged(self):
        self.assertEqual(
            main.presentable_fallback("12 rue de la Paix, 75002 Paris"),
            "12 rue de la Paix, 75002 Paris",
        )

    def test_empty_text_stays_empty(self):
        self.assertEqual(main.presentable_fallback(""), "")


if __name__ == "__main__":
    unittest.main()
