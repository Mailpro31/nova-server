"""Une reformulation qui répond à l'élève au lieu de reformuler sa dictée.

Mesuré sur le RTX (banc de reformulation, Style E-mail, 04/10) :

    « Bonjour, / mercredi à 10 heures, je vous verrai. / Je suis désolé, mais
    votre demande semble incomplète. Pourriez-vous préciser quel événement ou
    rendez-vous vous attendiez ? »

Le modèle s'adresse à l'élève. Le contrôle `chatbot-answer` ne regardait que
le début de la sortie : la phrase parasite, en fin de texte, passait, et se
serait collée dans l'e-mail de l'élève. Une phrase d'assistant est refusée
où qu'elle soit, sauf si l'élève l'a dictée lui-même.
"""

import unittest

from test_main import AdminTestCase  # noqa: F401

import main

main.init_db()

DICTATED = "on se voit mardi non attends mercredi à 10 heures"
MEASURED = ("Bonjour,\nmercredi à 10 heures, je vous verrai.\nJe suis désolé, mais votre "
            "demande semble incomplète. Pourriez-vous préciser quel événement ou "
            "rendez-vous vous attendiez ?")


class AssistantReplyTests(unittest.TestCase):
    def test_the_measured_email_is_refused(self):
        self.assertEqual(
            main.rewrite_refusal(DICTATED, MEASURED, "nova_style_email"), "chatbot-answer")

    def test_an_assistant_sentence_anywhere_is_refused(self):
        for answer in (
            "On se voit mercredi à 10 heures. Voulez-vous que je reformule autrement ?",
            "On se voit mercredi à 10 heures.\n\nEn tant qu'assistant, je ne peux pas savoir.",
            "See you Wednesday at 10. Would you like me to make it more formal?",
            "Le texte fourni est incomplet : mercredi à 10 heures.",
        ):
            with self.subTest(answer=answer):
                self.assertEqual(
                    main.rewrite_refusal(DICTATED, answer, "nova_style_everyday"),
                    "chatbot-answer")

    def test_a_student_who_says_it_keeps_it(self):
        dictated = ("bonjour madame pourriez vous préciser la date du partiel "
                    "merci")
        answer = "Bonjour Madame,\n\nPourriez-vous préciser la date du partiel ?\n\nMerci."
        self.assertIsNone(main.rewrite_refusal(dictated, answer, "nova_style_email"))

    def test_ordinary_words_are_not_assistant_sentences(self):
        dictated = "je suis désolé mais je ne pourrai pas venir mercredi à 10 heures"
        answer = "Je suis désolé, mais je ne pourrai pas venir mercredi à 10 heures."
        self.assertIsNone(main.rewrite_refusal(dictated, answer, "nova_style_everyday"))


if __name__ == "__main__":
    unittest.main()
