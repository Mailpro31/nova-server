"""Chasse aux bugs du 05/10, pendant serveur de nova-desktop#226.

- Le refus des phrases d'assistant refusait de vrais e-mails d'élève :
  « est-ce que vous pourriez préciser » → « Pourriez-vous préciser ». L'élève
  l'a dit si tous les mots sont dans sa dictée, dans n'importe quel ordre,
  accents ignorés ; « N'hésitez pas à me contacter », formule d'e-mail
  ordinaire, n'est plus refusé.
- Un déclencheur de snippet avec une virgule (« Cordialement, Sasha Martin »)
  était coupé : « cordialement » seul déclenchait la signature. Une virgule
  ne sépare que des phrases d'au moins deux mots.
"""

import unittest

from test_main import AdminTestCase  # noqa: F401

import main

main.init_db()


class AssistantSentenceOrderTests(unittest.TestCase):
    def test_a_student_who_says_it_in_another_order_keeps_it(self):
        for dictated, written in (
            ("est-ce que vous pourriez préciser la date du partiel",
             "Pourriez-vous préciser la date du partiel ?"),
            ("vous voulez que je vous envoie le rapport",
             "Voulez-vous que je vous envoie le rapport ?"),
            ("pourriez vous preciser la date", "Pourriez-vous préciser la date ?"),
            ("si vous avez des questions dites le moi",
             "N'hésitez pas à me contacter si vous avez des questions."),
        ):
            with self.subTest(dictated=dictated):
                self.assertFalse(main.assistant_sentence(dictated, written))

    def test_the_measured_reply_is_still_refused(self):
        self.assertTrue(main.assistant_sentence(
            "on se voit mardi non attends mercredi à 10 heures",
            "Mercredi à 10 heures. Pourriez-vous préciser quel rendez-vous ?"))


class SnippetCommaTests(unittest.TestCase):
    def test_a_comma_inside_a_phrase_does_not_split_it(self):
        self.assertEqual(main.snippet_triggers("Cordialement, Sasha Martin"),
                         ["Cordialement, Sasha Martin"])
        self.assertEqual(main.snippet_triggers("bonjour, merci | ma signature"),
                         ["bonjour, merci", "ma signature"])
        self.assertEqual(main.snippet_triggers("mon matricule, mon numéro étudiant"),
                         ["mon matricule", "mon numéro étudiant"])

    def test_cordialement_alone_no_longer_inserts_the_signature(self):
        rows = [{"trigger": "Cordialement, Sasha Martin", "content": "Sasha Martin, IPSA"}]
        self.assertEqual(main.expand_snippets("merci, cordialement", rows),
                         "merci, cordialement")


if __name__ == "__main__":
    unittest.main()
