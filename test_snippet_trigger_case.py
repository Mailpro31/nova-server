"""Le déclencheur d'un snippet garde la casse dictée en milieu de phrase.

Test de Sash le 05/10 : « …ne pas confondre avec mon adresse » est devenu
« …ne pas confondre avec Mon adresse : 7 impasse des bons voisins ». Le
modèle met une majuscule au déclencheur, qu'il prend pour une étiquette, et
la restauration du snippet la gardait. En début de phrase, la majuscule est
juste et reste.
"""

import unittest

from test_main import AdminTestCase  # noqa: F401

import main

main.init_db()

ROWS = [{"trigger": "mon adresse", "content": "7 impasse des bons voisins"}]


def restored(dictated: str, model_output: str) -> str:
    _, restores = main.protect_snippets(dictated, ROWS)
    return main.restore_snippets(model_output, restores)


class SnippetTriggerCaseTests(unittest.TestCase):
    def test_a_capital_in_the_middle_of_a_sentence_is_undone(self):
        self.assertEqual(
            restored("ne pas confondre avec mon adresse",
                     "Ne pas confondre avec Mon adresse {{snippet0}}."),
            "Ne pas confondre avec mon adresse : 7 impasse des bons voisins.")

    def test_a_capital_at_the_start_of_a_sentence_stays(self):
        self.assertEqual(
            restored("mon adresse est la suivante",
                     "Mon adresse {{snippet0}} est la suivante."),
            "Mon adresse : 7 impasse des bons voisins est la suivante.")
        self.assertEqual(
            restored("voici. mon adresse",
                     "Voici.\nMon adresse {{snippet0}}"),
            "Voici.\nMon adresse : 7 impasse des bons voisins")

    def test_a_trigger_the_model_moved_is_left_alone(self):
        self.assertEqual(
            restored("avec mon adresse", "Avec {{snippet0}}."),
            "Avec 7 impasse des bons voisins.")


if __name__ == "__main__":
    unittest.main()
