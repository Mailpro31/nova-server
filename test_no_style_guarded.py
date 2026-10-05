"""Une reformulation sans Style passe les mêmes contrôles que le Style par défaut.

Relevé sur le RTX le 05/10 : une requête `/api/reformulate` sans
`style_id` (un ancien poste, un Style désactivé par l'organisation, un appel
direct à l'API) n'avait ni le contrôle des nombres ni celui de la personne.
C'est par là qu'un « 25% » inventé passait. Le poste, lui, envoie
`default_improve_transcriptions` : « sans Style » est désormais contrôlé
exactement comme lui.
"""

import unittest

from test_main import AdminTestCase  # noqa: F401

import main

main.init_db()


class NoStyleGuardedTests(unittest.TestCase):
    def test_an_invented_number_is_refused_without_a_style(self):
        for style_id in ("", None):
            with self.subTest(style_id=style_id):
                self.assertEqual(
                    main.rewrite_refusal(
                        "je vous envoie mon numéro étudiant pour le partiel",
                        "Je vous envoie mon numéro étudiant 25% pour le partiel.",
                        style_id),
                    "number-changed")

    def test_a_changed_person_is_refused_without_a_style(self):
        self.assertEqual(
            main.rewrite_refusal("mon matricule c'est pour le dossier",
                                 "Votre matricule est pour le dossier.", ""),
            "person-changed")

    def test_a_faithful_rewrite_still_passes_without_a_style(self):
        self.assertIsNone(main.rewrite_refusal(
            "le partiel est le 12 mars", "Le partiel est le 12 mars.", ""))


if __name__ == "__main__":
    unittest.main()
