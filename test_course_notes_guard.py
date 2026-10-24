"""Des notes de cours n'ajoutent rien, ne perdent rien, ne changent aucun nombre.

Le poste a ce controle depuis nova-desktop#199 (`rewrite::guard`). Le serveur
doit refuser exactement la meme chose, pour la meme raison : un membre dont la
reformulation passe par l'organisation ne doit pas recevoir ce que le poste
aurait refuse. Les cas ci-dessous sont ceux des tests du poste, a l'identique.

Mesure a l'origine (29 et 30/09) : des notes de cours generees avaient perdu
la date d'un partiel, invente des exemples, inverse une formule ; « le vingt
mars » est sorti « 23 mars » et 1793 est devenu 1790. Sans chiffre dans la
dictee, rien n'etait compare.
"""

import unittest
from unittest.mock import patch

from test_main import AdminTestCase  # noqa: F401

import main

main.init_db()

COURSE = "nova_style_course_notes"


class CourseNotesRefusalTests(unittest.TestCase):
    def refusal(self, dictated, answer):
        return main.rewrite_refusal(dictated, answer, COURSE)

    def test_an_added_example_is_refused(self):
        self.assertEqual(self.refusal(
            "la portance augmente avec l'angle d'incidence jusqu'au décrochage",
            "La portance augmente avec l'angle d'incidence jusqu'au décrochage.\n"
            "Par exemple, un avion au décollage cabre pour gagner de la portance "
            "avant de risquer le décrochage.",
        ), "content-added")

    def test_an_explanation_instead_of_notes_is_refused(self):
        self.assertEqual(self.refusal(
            "la dérivée de x carré c'est deux x",
            "Dérivée de x² : 2x. Cela signifie que la pente de la parabole double "
            "quand x double, ce qui explique sa courbure.",
        ), "content-added")

    def test_a_truncated_lecture_is_refused(self):
        self.assertEqual(self.refusal(
            "le partiel aura lieu le douze mars, il portera sur les chapitres trois "
            "et quatre, les calculatrices sont interdites et il faudra justifier "
            "chaque résultat",
            "Partiel le 12 mars : chapitres 3 et 4.",
        ), "dictation-not-kept")

    def test_a_lost_figure_is_refused(self):
        self.assertEqual(self.refusal(
            "au-delà de 12 % d'humidité le composite perd 30 % de sa résistance",
            "Au-delà d'un certain taux d'humidité, le composite perd 30 % de sa résistance.",
        ), "number-lost")

    def test_a_changed_date_is_refused(self):
        self.assertEqual(self.refusal(
            "pour lundi il faut faire les exercices douze et treize page quarante-cinq "
            "et relire le chapitre cinq, important le partiel c'est le vingt mars",
            "pour lundi il faut faire les exercices 12 et 13 page 45 et relire le "
            "chapitre 5.\nÀ retenir : le partiel est le 23 mars.",
        ), "number-changed")
        self.assertEqual(self.refusal(
            "partie deux la révolution française, elle commence en mille sept cent "
            "quatre-vingt-neuf, le quatorze juillet c'est la prise de la bastille, en "
            "mille sept cent quatre-vingt-douze non pardon en mille sept cent "
            "quatre-vingt-treize louis seize est exécuté",
            "Partie 2 la révolution française, elle commence en 1789, le 14 juillet "
            "c'est la prise de la bastille, en 1789 non pardon en 1790 louis seize est "
            "exécuté.",
        ), "number-changed")

    def test_a_runaway_output_is_refused(self):
        dictated = (
            "partie deux la révolution française, elle commence en mille sept cent "
            "quatre-vingt-neuf, le quatorze juillet c'est la prise de la bastille, en "
            "mille sept cent quatre-vingt-douze non pardon en mille sept cent "
            "quatre-vingt-treize louis seize est exécuté"
        )
        answer = (
            "Partie deux, la Révolution française, elle commence en 1789, le 14 "
            "juillet c'est la prise de la Bastille, en 1789 non" + ", en 1789" * 80
        )
        self.assertEqual(self.refusal(dictated, answer), "output-runaway")

    def test_a_power_written_as_a_symbol_is_the_same_number(self):
        self.assertIsNone(self.refusal(
            "à retenir la dérivée de x au carré c'est deux x et la dérivée de x au "
            "cube c'est trois x au carré",
            "À retenir : la dérivée de x² est 2x et la dérivée de x³ est 3x².",
        ))

    def test_faithful_course_notes_pass(self):
        for dictated, answer in [
            (
                "chapitre trois la photosynthèse, définition la photosynthèse c'est la "
                "transformation du dioxyde de carbone en glucose grâce à la lumière, à "
                "retenir elle produit du dioxygène",
                "Chapitre 3 : la photosynthèse\n\nPhotosynthèse : transformation du "
                "dioxyde de carbone en glucose grâce à la lumière.\nÀ retenir : elle "
                "produit du dioxygène.",
            ),
            (
                "la Révolution commence en mille sept cent quatre-vingt-neuf non pardon "
                "la prise de la Bastille c'est le quatorze juillet mille sept cent "
                "quatre-vingt-neuf",
                "La prise de la Bastille : 14 juillet 1789.",
            ),
            (
                "le present perfect se forme avec have plus participe passé par exemple "
                "I have finished my homework",
                "Present perfect : have + participe passé.\nExemple : « I have "
                "finished my homework ».",
            ),
            (
                "les trois états de la matière sont solide liquide et gazeux",
                "Les trois états de la matière :\n- solide\n- liquide\n- gazeux",
            ),
        ]:
            with self.subTest(answer=answer):
                self.assertIsNone(self.refusal(dictated, answer))

    def test_other_styles_keep_their_own_rules(self):
        """Le controle strict ne s'applique qu'aux notes de cours : un e-mail
        peut ajouter une formule de politesse."""
        self.assertIsNone(main.rewrite_refusal(
            "dis-lui que le dossier est complet",
            "Bonjour,\n\nLe dossier est complet.\n\nBien cordialement,",
            "nova_style_email",
        ))


class SpokenNumbersTests(unittest.TestCase):
    def test_numbers_said_in_words_are_read_like_digits(self):
        self.assertEqual(main.spoken_numbers("mille sept cent quatre-vingt-treize"), ["1793"])
        self.assertEqual(
            main.spoken_numbers("les exercices douze et treize page quarante-cinq"),
            ["12", "13", "45"])
        self.assertEqual(
            main.spoken_numbers("soixante et onze, quatre-vingt-dix-neuf, deux mille vingt-six"),
            ["71", "99", "2026"])
        self.assertEqual(
            main.spoken_numbers("six virgule zéro deux fois dix puissance vingt-trois"),
            ["6,02", "10", "23"])
        # « un » est d'abord un article : seul, il ne compte pas.
        self.assertIn("2", main.spoken_numbers("un accord entre une ou deux personnes"))
        self.assertNotIn("1", main.spoken_numbers("un accord entre une personne"))


class CourseNotesRouteTests(AdminTestCase):
    def test_notes_with_a_changed_date_come_back_as_the_dictation(self):
        dictated = "important le partiel c'est le vingt mars"

        async def engine(task_type, instruction, message):
            return "À retenir : le partiel est le 23 mars."

        token = self.sign_in("etudiant@example.com")
        with patch.object(main, "execute_text", engine):
            response = self.client.post(
                "/api/reformulate", headers=self.auth(token),
                json={"text": dictated, "style_id": COURSE,
                      "style_prompt": "Mets en forme les notes de cours."})
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["text"], main.presentable_fallback(dictated))


if __name__ == "__main__":
    unittest.main()
