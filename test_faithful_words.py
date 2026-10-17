"""Un Style fidèle ne fait pas disparaître un mot dicté.

Banc v2 du RTX (11/10) : le texte collé par Nova 1.0.58 avait plus d'erreurs
(27,9 %) que la transcription brute du même moteur (23,5 %). La reformulation
perdait des mots que l'élève avait bien dits : « dans un triangle rectangle »
devenait « dans un rectangle ». Le contrôle acceptait une sortie qui ne gardait
qu'un tiers des mots (`MIN_KEPT_WORDS`).

Un Style fidèle peut corriger l'orthographe, la ponctuation et la casse,
écrire les nombres et les formules en chiffres et en symboles, et retirer les
hésitations et ce qu'une reprise abandonne. Il ne retire pas un mot de contenu.
"""

import unittest

from test_main import AdminTestCase  # noqa: F401  (base de test)

import main

main.init_db()


class FaithfulWordTests(unittest.TestCase):
    def test_a_dropped_content_word_is_refused(self):
        for said, written in (
            ("Définition, l'hypoténuse est le côté opposé à l'angle droit dans un triangle rectangle.",
             "Définition : l'hypoténuse est le côté opposé à l'angle droit dans un rectangle."),
            ("La conclusion de ce chapitre porte sur la stabilité du vol longitudinal.",
             "La conclusion de ce chapitre porte sur la stabilité."),
        ):
            with self.subTest(written=written):
                self.assertTrue(main.faithful_word_dropped(said, written))

    def test_corrections_numbers_and_formulas_are_not_drops(self):
        for said, written in (
            ("un avion vol a 250 metres par seconde", "Un avion vole à 250 mètres par seconde."),
            ("la transformée de la place permet de résoudre les équations",
             "La transformée de Laplace permet de résoudre les équations."),
            ("la viscosité s'exprime en pascal secondes", "La viscosité s'exprime en pascals seconde."),
            ("alors euh voilà donc la masse volumique diminue", "La masse volumique diminue."),
            ("le théorème de Pythagore", "Le théorème de Pythagore."),
        ):
            with self.subTest(written=written):
                self.assertFalse(main.faithful_word_dropped(said, written))

    def test_a_formula_is_left_to_the_desktop(self):
        # Banc #105 du RTX sur 472bc3a (11/10) : « la dérivée de x au carré est
        # égale à deux x » acceptée en « La dérivée du x² est égale à 2x ». Le
        # poste écrit les formules lui-même, de façon déterministe
        # (`spoken_maths`) : un Style fidèle garde « au carré ».
        self.assertTrue(main.faithful_word_dropped(
            "la dérivée de x au carré est égale à deux x", "La dérivée du x² est égale à 2x."))

    def test_an_invented_letter_closing_or_blank_field_is_refused(self):
        # Banc du RTX sur 472bc3a (11/10), 3 fois sur 3 avec le Style par défaut.
        said = "bonjour je vous envoie mon adresse pour l'inscription au partiel"
        written = ("Bonjour,\n\nJe vous envoie mon adresse pour l'inscription au partiel."
                   "\n\nMerci,\n[Votre nom]")
        self.assertEqual(main.rewrite_refusal(said, written, "default_improve_transcriptions"),
                         "content-added")
        # Une formule ou un champ dicté reste.
        self.assertIsNone(main.rewrite_refusal(
            "bonjour je vous envoie mon adresse merci", "Bonjour, je vous envoie mon adresse. Merci.",
            "default_improve_transcriptions"))
        # Un champ à remplir n'est jamais acceptable, même dans un e-mail.
        self.assertEqual(main.rewrite_refusal(
            "écris à madame dupont que je serai absent demain",
            "Bonjour Madame Dupont,\n\nJe serai absent demain.\n\nCordialement,\n[Votre nom]",
            "nova_style_email"), "content-added")

    def test_a_dictated_acronym_must_stay(self):
        # Banc de non-régression du RTX (11/10) : « TD » développé en une
        # expression inventée, et accepté.
        self.assertEqual(main.rewrite_refusal(
            "Le TD de mardi, pardon, de jeudi est annulé.",
            "Le délégué territorial de jeudi est annulé.",
            "default_improve_transcriptions"), "content-dropped")
        self.assertIsNone(main.rewrite_refusal(
            "le TD de jeudi est annulé", "Le TD de jeudi est annulé.",
            "default_improve_transcriptions"))
        # Un sigle abandonné par la reprise peut partir.
        self.assertIsNone(main.rewrite_refusal(
            "Rendez-vous en salle B204, pardon, en salle C112.",
            "Rendez-vous en salle C112.", "default_improve_transcriptions",
            resolved="Rendez-vous en salle C112."))

    def test_a_dictated_negation_must_stay(self):
        # Banc de non-régression du RTX (11/10) : « non » retiré sans signal.
        self.assertEqual(main.rewrite_refusal(
            "Rendez-vous à 14 heures, non à temps, à 15 heures.",
            "Rendez-vous à 14 heures, à temps, à 15 heures.",
            "default_improve_transcriptions"), "negation-dropped")
        self.assertEqual(main.rewrite_refusal(
            "le partiel n'est pas reporté", "Le partiel est reporté.",
            "default_improve_transcriptions"), "negation-dropped")
        # Un « ne » ajouté par la correction est bienvenu.
        self.assertIsNone(main.rewrite_refusal(
            "j'ai pas compris la démonstration", "Je n'ai pas compris la démonstration.",
            "default_improve_transcriptions"))

    def test_on_and_nous_are_the_same_person(self):
        # Banc de non-régression du RTX (11/10) : refusé à tort en person-changed.
        said = "On se retrouve le 12 octobre, enfin le 19 octobre, pour la soutenance."
        self.assertIsNone(main.rewrite_refusal(
            said, "Nous nous retrouvons le 19 octobre pour la soutenance.",
            "default_improve_transcriptions"))
        self.assertEqual(main.rewrite_refusal(
            said, "Vous vous retrouvez le 19 octobre pour la soutenance.",
            "default_improve_transcriptions"), "person-changed")
        # La valeur retenue, elle, doit rester.
        self.assertEqual(main.rewrite_refusal(
            said, "On se retrouve le 20 octobre pour la soutenance.",
            "default_improve_transcriptions"), "explicit-number-lost")

    def test_a_number_or_a_unit_written_the_french_way_is_kept(self):
        style = "default_improve_transcriptions"
        self.assertIsNone(main.rewrite_refusal(
            "le budget est de 1500 euros", "Le budget est de 1 500 euros.", style))
        self.assertIsNone(main.rewrite_refusal(
            "rendez-vous à 14 heures 30", "Rendez-vous à 14 h 30.", style))
        self.assertIsNone(main.rewrite_refusal(
            "la pièce mesure 3 mètres", "La pièce mesure 3 m.", style))
        # Collé à son nombre, comme le modèle du serveur l'écrit (vrai modèle
        # sur le RTX, 3ec5e50, 12/10).
        self.assertIsNone(main.rewrite_refusal(
            "rendez-vous à 14 heures 30 en salle B204",
            "Rendez-vous à 14h30 en salle B204.", style))
        self.assertIsNone(main.rewrite_refusal(
            "la piste fait 2 kilomètres", "La piste fait 2km.", style))
        # Les symboles d'une mise au propre (sondage du 12/10).
        for said, wrote in (
                ("la vitesse est de 340 mètres par seconde", "La vitesse est de 340 m/s."),
                ("la remise est de 20 pour cent", "La remise est de 20 %."),
                ("le TD numéro 3 est annulé", "Le TD n° 3 est annulé."),
                ("ça coûte 15 euros", "Ça coûte 15 €."),
                ("le rendu est pour le premier novembre", "Le rendu est pour le 1er novembre.")):
            with self.subTest(said=said):
                self.assertIsNone(main.rewrite_refusal(said, wrote, style))
        self.assertIsNotNone(main.rewrite_refusal(
            "la vitesse est de 340 mètres par seconde", "La vitesse est de 340 km/h.", style))
        self.assertIsNotNone(main.rewrite_refusal(
            "le rendu est pour le premier novembre", "Le rendu est pour le 2 novembre.", style))
        self.assertIsNotNone(main.rewrite_refusal(
            "ça coûte 15 euros", "Ça coûte 15 $.", style))
        # Un autre nombre, ou l'unité perdue, restent refusés.
        self.assertEqual(main.rewrite_refusal(
            "le budget est de 1500 euros", "Le budget est de 1 600 euros.", style),
            "explicit-number-lost")
        self.assertEqual(main.rewrite_refusal(
            "la pièce mesure 3 mètres", "La pièce mesure 3.", style), "content-dropped")
        self.assertEqual(main.rewrite_refusal(
            "on se voit dans 2 heures", "On se voit dans 2 minutes.", style),
            "content-dropped")

    def test_the_tics_of_speech_never_count_in_the_checks(self):
        # Banc v2 du RTX (17/10, 45 refus tirés au hasard) : la moitié venaient
        # des contrôles, pas du modèle.
        style = "default_improve_transcriptions"
        for said, wrote in (
                # « coup » de « du coup » compté comme un mot perdu (0933).
                ("Du coup, l'intégrale de 0 à 1 de x au carré vaut un tiers.",
                 "L'intégrale de 0 à 1 de x au carré vaut un tiers."),
                # « tu vois » retiré : ce n'est pas un changement de personne (0408).
                ("le correcteur tu vois combine trois actions",
                 "Le correcteur combine trois actions."),
                # « tu vois » rendu « vous voyez » (0533).
                ("le correcteur, tu vois, est stable", "Le correcteur, vous voyez, est stable."),
                # Un pourcentage recopié tel quel (0447).
                ("Le projet compte pour 50% de la note finale.",
                 "Le projet compte pour 50% de la note finale.")):
            with self.subTest(said=said):
                self.assertIsNone(main.rewrite_refusal(said, wrote, style))
        # Une vraie personne changée, un vrai mot perdu, un pourcentage changé : refusés.
        self.assertEqual(main.rewrite_refusal(
            "je dois rendre le rapport", "Vous devez rendre le rapport.", style), "person-changed")
        self.assertEqual(main.rewrite_refusal(
            "du coup dans un triangle rectangle", "Dans un rectangle.", style), "content-dropped")
        self.assertIsNotNone(main.rewrite_refusal(
            "Le projet compte pour 50% de la note.", "Le projet compte pour 40% de la note.", style))

    def test_a_formula_written_in_symbols_is_kept(self):
        # Banc v2 du RTX sur Gemini (17/10) : la formule bien écrite était refusée.
        style = "default_improve_transcriptions"
        for said, wrote in (
                ("Formule P égale rho GH.", "Formule : P = ρGH."),
                ("formule delta égale b au carré moins 4 a c", "Formule : Δ = b² - 4ac."),
                ("R égale rho V L divisé par mu", "R = ρVL / μ."),
                ("la tension U égale R I, comment dire, c'est la loi d'Ohm",
                 "La tension U = RI, c'est la loi d'Ohm."),
                ("le rendu est en salle 312, non, je me suis trompé, 315",
                 "Le rendu est en salle 315.")):
            with self.subTest(said=said):
                self.assertIsNone(main.rewrite_refusal(said, wrote, style, resolved=wrote))
        # Une autre formule reste refusée.
        self.assertIsNotNone(main.rewrite_refusal(
            "formule P égale rho GH", "Formule : P = mv².", style))

    def test_the_rule_applies_to_faithful_styles_only(self):
        said = "Définition, l'hypoténuse est le côté opposé à l'angle droit dans un triangle rectangle."
        written = "Définition : l'hypoténuse est le côté opposé à l'angle droit dans un rectangle."
        self.assertEqual(main.rewrite_refusal(said, written, "nova_style_everyday"), "content-dropped")
        self.assertNotEqual(main.rewrite_refusal(said, written, "nova_style_email"), "content-dropped")


if __name__ == "__main__":
    unittest.main()
