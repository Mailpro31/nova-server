"""Une reformulation qui change la personne : « mon matricule » → « votre matricule ».

Mesuré sur le RTX le 05/10 : la dictée « Mon Matricule c'est pour le
dossier de bourse » est devenue « Votre matricule est pour le dossier de
bourse ». Le modèle parle à l'élève au lieu de garder ses mots : ce qui
était à lui devient à quelqu'un d'autre. Un possessif de la première
personne (mon, ma, mes, notre, nos) devant un nom ne peut pas devenir celui
d'une autre personne devant le même nom.
"""

import unittest

from test_main import AdminTestCase  # noqa: F401

import main

main.init_db()


class PersonChangedTests(unittest.TestCase):
    def test_the_measured_case_is_caught(self):
        self.assertTrue(main.person_changed(
            "Mon Matricule c'est pour le dossier de bourse",
            "Votre matricule est pour le dossier de bourse."))

    def test_other_persons_are_caught(self):
        for dictated, written in (
            ("je rends mes notes demain", "Vous rendez vos notes demain."),
            ("ma présentation est prête", "Sa présentation est prête."),
            ("notre projet avance", "Leur projet avance."),
        ):
            with self.subTest(dictated=dictated):
                self.assertTrue(main.person_changed(dictated, written))

    def test_faithful_rewrites_pass(self):
        for dictated, written in (
            ("mon matricule c'est pour le dossier", "Mon matricule, c'est pour le dossier."),
            ("ma présentation est prête", "Ma présentation est prête."),
            # « ton » → « votre » : le tutoiement devient vouvoiement dans un
            # e-mail, sans possessif de l'élève en jeu.
            ("merci pour ton aide", "Merci pour votre aide."),
            ("j'ai lu votre message et mon rapport est prêt",
             "J'ai lu votre message, et mon rapport est prêt."),
        ):
            with self.subTest(dictated=dictated):
                self.assertFalse(main.person_changed(dictated, written))

    def test_the_rewrite_is_refused_for_built_in_styles(self):
        self.assertEqual(
            main.rewrite_refusal("Mon Matricule c'est pour le dossier de bourse",
                                 "Votre matricule est pour le dossier de bourse.",
                                 "nova_style_everyday"),
            "person-changed")


if __name__ == "__main__":
    unittest.main()


class OwnerMovedTests(unittest.TestCase):
    """Mesuré sur le RTX le 05/10, Style par défaut du poste : la dictée « mon
    numéro étudiant c'est 21 45 87 32 » est devenue « le numéro de mon étudiant
    est 21 45 87 32 ». Le « mon » est toujours là, mais il a changé de nom :
    ce n'est plus le numéro de l'élève, c'est celui de « mon étudiant »."""

    def test_a_possessive_moved_to_the_next_word_is_caught(self):
        self.assertTrue(main.person_changed(
            "mon numéro étudiant c'est 21 45 87 32",
            "le numéro de mon étudiant est 21 45 87 32"))
        self.assertEqual(
            main.rewrite_refusal("mon numéro étudiant c'est 21 45 87 32",
                                 "le numéro de mon étudiant est 21 45 87 32",
                                 "default_improve_transcriptions"),
            "person-changed")

    def test_a_kept_owner_with_reordered_words_passes(self):
        for dictated, written in (
            ("mon numéro étudiant c'est 21 45 87 32", "Mon numéro étudiant est 21 45 87 32."),
            ("ma présentation demain est prête", "Demain, ma présentation est prête."),
            ("mon cours de maths commence", "Mon cours de maths commence."),
        ):
            with self.subTest(dictated=dictated):
                self.assertFalse(main.person_changed(dictated, written))
