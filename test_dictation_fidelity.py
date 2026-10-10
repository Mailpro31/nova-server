"""Une dictée reformulée garde ce que l'élève a dit : test de Sash du 07/10.

Vingt phrases de cours dictées dans Word avec Nova 1.0.58, Style
« Automatique ». Trois défauts venaient de la reformulation, pas de Whisper :

- « La dérivée de x au carré est égale à deux x » → « La dérivée de \\( x^2 \\)
  est égale à \\( 2x \\) » : du LaTeX, illisible dans Word ;
- « Je dois rendre le rapport de projet avant vendredi midi » → « Vous devez
  rendre le rapport… » : la personne a changé ;
- « On se voit mardi, non attends, mercredi » : la reprise n'a pas été résolue.
"""

import unittest

from test_main import AdminTestCase  # noqa: F401

import main

main.init_db()


class LatexTests(unittest.TestCase):
    def test_latex_the_student_did_not_dictate_is_refused(self):
        self.assertEqual(
            main.rewrite_refusal(
                "La dérivée de x au carré est égale à deux x.",
                "La dérivée de \\( x^2 \\) est égale à \\( 2x \\).",
                "nova_style_everyday"),
            "latex-added")
        for answer in ("La dérivée de $x^2$ vaut $2x$.", "Δ = \\frac{1}{2} ρ v²",
                       "x^{2} + y_{1}"):
            with self.subTest(answer=answer):
                self.assertTrue(main.latex_added("la dérivée de x au carré", answer))

    def test_unicode_symbols_and_dictated_latex_pass(self):
        self.assertFalse(main.latex_added("x au carré", "La dérivée de x² est égale à 2x."))
        self.assertFalse(main.latex_added("Écris \\frac{a}{b} dans Overleaf.",
                                          "Écris \\frac{a}{b} dans Overleaf."))
        self.assertFalse(main.latex_added("Ça coûte 5 $.", "Ça coûte 5 $."))


class PersonTests(unittest.TestCase):
    def test_je_dois_becoming_vous_devez_is_refused(self):
        self.assertEqual(
            main.rewrite_refusal(
                "Je dois rendre le rapport de projet avant vendredi midi.",
                "Vous devez rendre le rapport de projet avant vendredi midi.",
                "nova_style_everyday"),
            "person-changed")

    def test_a_faithful_correction_keeps_the_person(self):
        self.assertIsNone(main.rewrite_refusal(
            "je dois rendre le rapport de projet avant vendredi midi",
            "Je dois rendre le rapport de projet avant vendredi midi.",
            "nova_style_everyday"))

    def test_course_notes_may_depersonalise(self):
        # « Je vais vous montrer » devient « On montre » dans des notes : c'est
        # le travail du Style, pas un changement de personne à refuser.
        self.assertNotEqual(
            main.rewrite_refusal(
                "je vais vous montrer que la dérivée de x au carré vaut deux x",
                "On montre que la dérivée de x au carré vaut deux x.",
                main.COURSE_NOTES_STYLE),
            "person-changed")


class SimpleRepairTests(unittest.TestCase):
    def test_a_weekday_month_or_number_repair_is_resolved_without_a_model(self):
        for said, kept in (
            ("On se voit mardi, non attend, mercredi à 10h à la bibliothèque.",
             "On se voit mercredi à 10h à la bibliothèque."),
            ("rendez-vous lundi non attends jeudi à 10 heures",
             "rendez-vous jeudi à 10 heures"),
            ("On se retrouve à 10 h, non attends, 11 h devant l'amphi.",
             "On se retrouve à 11 h devant l'amphi."),
            ("Rendez-vous en mars, plutôt avril.", "Rendez-vous en avril."),
            ("Le TD est jeudi, enfin vendredi.", "Le TD est vendredi."),
            # Banc du RTX sur d6c929f (09/10) : l'article répété empêchait la
            # reprise d'une date ou d'une heure.
            ("Le partiel est le 12 mars, pardon, le 13 mars.", "Le partiel est le 13 mars."),
            ("Le cours finit à 10 h, pardon, à 11 h.", "Le cours finit à 11 h."),
            ("Rendu le 1er avril, enfin le 2 avril.", "Rendu le 2 avril."),
        ):
            with self.subTest(said=said):
                self.assertEqual(main.resolve_simple_repairs(said), kept)

    def test_a_no_that_is_an_answer_or_a_content_word_stays(self):
        for said in (
            "Je viens mardi, non, mercredi aussi.",
            "Il a dit non au voyage de mardi.",
            "Le chapitre 2, non attends, le chapitre 5.",
            "Le partiel dure 2 heures et demie.",
            # Une date reprise par un simple nombre perdrait son mois : le
            # modèle s'en charge.
            "Le partiel est le 12 mars, pardon, le 13.",
            "Fais 20 pompes, attends 30 secondes, puis recommence.",
        ):
            with self.subTest(said=said):
                self.assertEqual(main.resolve_simple_repairs(said), said)


if __name__ == "__main__":
    unittest.main()
