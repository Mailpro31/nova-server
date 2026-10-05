"""Un repère de snippet perdu par le modèle, remis derrière son déclencheur.

Mesuré sur le RTX le 05/10 (snippet « mon numéro étudiant, mon matricule ») :
dans 6 cas sur 9, le modèle a reformulé la phrase en perdant le repère
`{{snippetN}}`. Nova jetait alors toute la reformulation et collait la
dictée brute, sans majuscule ni ponctuation : « bonjour je vous envoie mon
numéro étudiant : 21458732 pour l'inscription au partiel ».

Quand les mots du déclencheur sont restés dans la reformulation, une seule
fois, le repère y est remis juste derrière : l'élève garde la phrase mise
au propre et la valeur exacte. Sinon, rien ne change : la dictée brute.
"""

import unittest

from test_main import AdminTestCase  # noqa: F401

import main

main.init_db()

ROWS = [{"trigger": "mon numéro étudiant, mon matricule", "content": "21458732"}]


def restored(dictated: str, model_output: str):
    _, restores = main.protect_snippets(dictated, ROWS)
    return main.restore_snippets(model_output, restores)


class SnippetMarkerRepairTests(unittest.TestCase):
    def test_a_lost_marker_comes_back_behind_its_trigger(self):
        self.assertEqual(
            restored("bonjour je vous envoie mon numéro étudiant pour l'inscription au partiel",
                     "Bonjour, je vous envoie mon numéro étudiant pour l'inscription au partiel."),
            "Bonjour, je vous envoie mon numéro étudiant : 21458732 pour l'inscription au partiel.")

    def test_the_trigger_keeps_the_case_the_model_gave_at_a_sentence_start(self):
        self.assertEqual(
            restored("mon matricule", "Mon matricule."),
            "Mon matricule : 21458732.")

    def test_without_the_trigger_words_the_dictation_is_used(self):
        self.assertIsNone(
            restored("je vous envoie mon matricule", "Je vous envoie mon identifiant."))

    def test_a_trigger_written_twice_is_ambiguous(self):
        self.assertIsNone(
            restored("mon matricule", "Mon matricule, je répète, mon matricule."))

    def test_a_kept_marker_is_unchanged(self):
        self.assertEqual(
            restored("voici mon matricule", "Voici mon matricule {{snippet0}}."),
            "Voici mon matricule : 21458732.")


if __name__ == "__main__":
    unittest.main()
