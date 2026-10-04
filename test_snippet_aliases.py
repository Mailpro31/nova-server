"""Un snippet, plusieurs façons de le dire.

Un élève dys ne formule pas pareil d'un jour à l'autre : « mon numéro
étudiant », « mon matricule », « mon numéro IPSA ». Un snippet accepte donc
plusieurs déclencheurs, séparés par une virgule ou une barre verticale :
`mon numéro étudiant, mon matricule | mon numéro IPSA`. Chacun insère le même
contenu, le plus long l'emporte toujours, et le contenu n'atteint jamais le
modèle.
"""

import unittest

from test_main import AdminTestCase  # noqa: F401

import main

main.init_db()


class SnippetAliasTests(unittest.TestCase):
    ROWS = [
        {"trigger": "mon numéro étudiant, mon matricule | mon numéro IPSA",
         "content": "21458732"},
        {"trigger": "mon adresse", "content": "12 rue des Lilas"},
    ]

    def test_each_way_of_saying_it_inserts_the_same_content(self):
        for said in ("mon numéro étudiant", "mon matricule", "Mon numéro IPSA"):
            with self.subTest(said=said):
                self.assertEqual(
                    main.expand_snippets(f"voici {said}", self.ROWS),
                    f"voici {said} : 21458732")

    def test_the_content_never_reaches_the_model(self):
        protected, restores = main.protect_snippets(
            "mon matricule et mon adresse", self.ROWS)
        self.assertNotIn("21458732", protected)
        self.assertEqual(len(restores), 2)

    def test_the_longest_way_wins(self):
        rows = [{"trigger": "mon numéro, mon numéro étudiant", "content": "21458732"},
                {"trigger": "mon numéro de téléphone", "content": "06 12 34 56 78"}]
        self.assertEqual(
            main.expand_snippets("mon numéro de téléphone", rows),
            "mon numéro de téléphone : 06 12 34 56 78")
        self.assertEqual(
            main.expand_snippets("mon numéro étudiant", rows),
            "mon numéro étudiant : 21458732")

    def test_the_ways_of_saying_it_are_listed_once_each(self):
        self.assertEqual(
            main.snippet_triggers(" a , b|  c,,a "), ["a", "b", "c"])
        self.assertEqual(main.snippet_triggers("mon lien visio"), ["mon lien visio"])

    def test_an_empty_way_is_ignored(self):
        rows = [{"trigger": " , | ", "content": "rien"}]
        self.assertEqual(main.expand_snippets("texte", rows), "texte")


if __name__ == "__main__":
    unittest.main()
