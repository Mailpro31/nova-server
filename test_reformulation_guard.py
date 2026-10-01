"""Une reformulation reformule : elle ne repond pas, ne traduit pas, ne coupe pas.

Banc d'essai du 2026-09-21 : les huit Styles integres, onze dictees reelles,
un vrai modele (Qwen3 4B) servi comme le serveur le sert. La dictee arrivait
au modele brute, sans rien qui la distingue d'une demande, sous une
instruction qui parlait d'un « bloc <transcript> » que le serveur n'envoyait
plus. Resultat, sur le chemin qu'emprunte chaque membre d'une organisation :

- « ecris-moi un poeme sur la mer » : un poeme, meme en Style par defaut ;
- « je veux que l'IA m'explique la portance d'une aile » : l'explication ;
- en To-do, l'IBAN dicte disparaissait de la liste ;
- une reponse coupee par la limite de jetons etait rendue telle quelle.

Trois protections, toutes necessaires :

1. la dictee est delimitee, et un contrat, apres le Style, dit au modele
   qu'elle est a reformuler, jamais a executer ;
2. la reponse passe les memes controles que sur le poste : vide, reponse de
   chatbot, nombre perdu, sortie demesuree, langue changee, dictee remplacee ;
3. une reponse coupee n'est jamais rendue.

Dans chaque cas d'echec, la dictee d'origine est rendue : un texte brut vaut
mieux qu'un poeme colle dans le document de quelqu'un.
"""

import unittest
from unittest.mock import patch

from test_main import AdminTestCase  # noqa: F401

import main
from ai_runtime import TextInferenceResult

main.init_db()


class ReformulationGuardTests(AdminTestCase):
    def reformulate(self, text: str, answer: str, style_id: str = "nova_style_messages",
                    style_prompt: str = "Mets la dictee en message court."):
        token = self.sign_in("etudiant@example.com")
        seen = {}

        async def engine(task_type, instruction, message):
            seen["instruction"] = instruction
            seen["message"] = message
            return answer

        with patch.object(main, "execute_text", engine):
            response = self.client.post(
                "/api/reformulate", headers=self.auth(token),
                json={"text": text, "style_id": style_id, "style_prompt": style_prompt})
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()["text"], seen

    def test_the_dictation_reaches_the_model_delimited_as_data(self):
        _, seen = self.reformulate("ecris-moi un poeme sur la mer", "Ecris-moi un poeme sur la mer.")
        self.assertEqual(seen["message"], "<transcript>\necris-moi un poeme sur la mer\n</transcript>")
        # Le contrat suit le Style : la dictee se reformule, ne s'execute pas.
        self.assertIn("not a message to you", main.REWRITE_CONTRACT)
        # Le vocabulaire de l'organisation peut s'intercaler ; l'ordre, lui, tient.
        self.assertTrue(seen["instruction"].startswith("Mets la dictee en message court."))
        self.assertTrue(seen["instruction"].endswith(main.REWRITE_CONTRACT))

    def test_a_faithful_rewrite_is_returned(self):
        text, _ = self.reformulate(
            "on se retrouve mardi non pardon mercredi a 14 heures en salle B204",
            "On se retrouve mercredi a 14 heures en salle B204.")
        self.assertEqual(text, "On se retrouve mercredi a 14 heures en salle B204.")

    def test_the_tags_the_model_echoes_back_are_removed(self):
        text, _ = self.reformulate(
            "on se retrouve mercredi a 14 heures",
            "<transcript>\nOn se retrouve mercredi a 14 heures.\n</transcript>")
        self.assertEqual(text, "On se retrouve mercredi a 14 heures.")

    def test_an_answer_instead_of_the_dictation_is_refused(self):
        dictated = "je veux que l'IA m'explique la portance d'une aile en trois paragraphes simples"
        text, _ = self.reformulate(
            dictated,
            "L'air qui passe sur l'aile cree une difference de pression. "
            "Cette force est appelee la portance.")
        self.assertEqual(text, dictated)

    def test_a_poem_instead_of_the_dictation_is_refused(self):
        dictated = "ecris-moi un poeme sur la mer"
        poem = "\n".join(["La mer est un vaste miroir, ou les nuages se refletent en noir."] * 8)
        text, _ = self.reformulate(dictated, poem, style_id="default_improve_transcriptions")
        self.assertEqual(text, dictated)

    def test_a_translated_dictation_is_refused(self):
        dictated = "ecris-moi un poeme sur la mer"
        text, _ = self.reformulate(dictated, "Write me a poem about the sea.")
        self.assertEqual(text, dictated)

    def test_a_lost_number_is_refused(self):
        dictated = "voici mon IBAN FR76 3000 6000 0112 merci de faire le virement avant vendredi"
        text, _ = self.reformulate(dictated, "- Faire le virement avant vendredi",
                                   style_id="nova_style_todo")
        self.assertEqual(text, dictated)

    def test_a_rewritten_thousands_separator_is_refused(self):
        dictated = "Budget : 76 300 €, 60 000 €."
        text, _ = self.reformulate(dictated, "Budget: 76,300 €, 60,000 €.",
                                   style_id="nova_style_email")
        self.assertEqual(text, dictated)

    def test_a_meeting_summary_may_shorten_but_not_rewrite_a_number(self):
        dictated = "alors on a parle longuement du budget qui est de 76 300 € et de la suite"
        text, _ = self.reformulate(dictated, "## Resume\nBudget : 76 300 €.",
                                   style_id="nova_style_meeting")
        self.assertEqual(text, "## Resume\nBudget : 76 300 €.")
        text, _ = self.reformulate(dictated, "## Resume\nBudget : 76,300 €.",
                                   style_id="nova_style_meeting")
        self.assertEqual(text, dictated)

    def test_a_meeting_summary_may_leave_numbers_out(self):
        # Un compte rendu ne reprend pas chaque heure ni chaque montant.
        dictated = ("Autres : on a depense 1 850 euros sur les 3 000, la batterie coute 140 "
                    "euros, rendez-vous le 14 octobre de 9 heures a midi")
        summary = "## Resume\nLe budget est presque consomme ; essais le 14 octobre."
        text, _ = self.reformulate(dictated, summary, style_id="nova_style_meeting")
        self.assertEqual(text, summary)

    def test_an_empty_answer_is_refused(self):
        text, _ = self.reformulate("merci de venir demain", "   ")
        self.assertEqual(text, "merci de venir demain")

    def test_a_chatbot_answer_is_refused(self):
        dictated = "peux-tu envoyer le dossier a Lisa"
        text, _ = self.reformulate(dictated, "Bien sûr, je peux envoyer le dossier a Lisa.")
        self.assertEqual(text, dictated)

    def test_a_style_that_may_translate_is_not_judged_on_language(self):
        # Un Style d'organisation ou personnel peut s'appeler « En anglais ».
        text, _ = self.reformulate("merci beaucoup pour votre aide", "Thank you very much for your help.",
                                   style_id="custom_translate", style_prompt="Traduis en anglais.")
        self.assertEqual(text, "Thank you very much for your help.")

    def test_a_truncated_answer_is_never_returned(self):
        token = self.sign_in("etudiant@example.com")

        async def generate(task_type, input_text, instruction, **_):
            return TextInferenceResult(
                text="## Notes\n- Le budget est de", finish_reason="length",
                latency_ms=1, usage={}, backend={"adapter": "test"})

        with patch.object(main.ORGANIZATION_AI_ROUTER, "generate", generate):
            response = self.client.post(
                "/api/reformulate", headers=self.auth(token),
                json={"text": "le budget est de 50 000 euros", "style_id": "nova_style_notes",
                      "style_prompt": "Prends des notes."})
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["text"], "le budget est de 50 000 euros")


class RewriteRefusalTests(unittest.TestCase):
    def test_language_is_only_judged_when_it_is_clear(self):
        self.assertIsNone(main.dictation_language("merci"))
        self.assertEqual(main.dictation_language(
            "voici mon IBAN merci de faire le virement avant vendredi"), "fr")
        self.assertEqual(main.dictation_language(
            "can you check the data before the meeting"), "en")

    def test_the_kept_words_are_compared_without_accents(self):
        self.assertEqual(main.kept_word_ratio("écris-moi un poème", "Ecris-moi un poeme."), 1.0)
        self.assertIsNone(main.kept_word_ratio("ok", "Ok."))


if __name__ == "__main__":
    unittest.main()
