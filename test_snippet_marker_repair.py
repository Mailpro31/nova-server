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
from unittest.mock import patch

from test_main import AdminTestCase

import main

main.init_db()

ROWS = [{"trigger": "mon numéro étudiant, mon matricule", "content": "21458732"}]


def restored(dictated: str, model_output: str):
    protected, restores = main.protect_snippets(dictated, ROWS)
    return main.restore_snippets(model_output, restores, dictated=protected)


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

    def test_a_signature_dictated_last_comes_back_last(self):
        """N° 1 : « …, signature ipsa » refusé en E-mail, le modèle ayant écrit
        sa propre formule de fin sans les mots du déclencheur."""
        rows = [{"trigger": "signature ipsa", "content": "Sasha\nIPSA Toulouse"}]
        protected, restores = main.protect_snippets(
            "écris au professeur que je serai absent signature ipsa", rows)
        self.assertEqual(
            main.restore_snippets("Bonjour,\n\nJe serai absent.\n\nCordialement,",
                                  restores, dictated=protected),
            "Bonjour,\n\nJe serai absent.\n\nCordialement,\n\nSasha\nIPSA Toulouse")

    def test_a_one_line_value_is_not_moved_to_the_end(self):
        self.assertIsNone(restored("je vous envoie mon matricule", "Je vous envoie mon identifiant."))

    def test_a_kept_marker_is_unchanged(self):
        self.assertEqual(
            restored("voici mon matricule", "Voici mon matricule {{snippet0}}."),
            "Voici mon matricule : 21458732.")


if __name__ == "__main__":
    unittest.main()


class RepairNeverLetsAnInventedNumberThroughTests(AdminTestCase):
    """Mesuré sur le RTX le 05/10, après #85 : avec un Style sans contrôle des
    nombres, le modèle remplaçait le repère par « 25% », #85 remettait le
    repère, et l'élève recevait « …mon numéro étudiant : 21458732 25% pour
    l'inscription au partiel. ». Avant #85, la dictée brute, juste.
    Le repère n'est remis que si la sortie n'invente ni nombre ni date."""

    def reformulate(self, text, answer, style_id):
        token = self.sign_in("etudiant@example.com")
        self.client.post("/api/snippets", headers=self.auth(token),
                         json={"trigger": "mon numéro étudiant, mon matricule",
                               "content": "21458732"})

        async def engine(task_type, instruction, message):
            return answer

        with patch.object(main, "execute_text", engine):
            response = self.client.post(
                "/api/reformulate", headers=self.auth(token),
                json={"text": text, "style_id": style_id, "style_prompt": "Mets au propre."})
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()["text"]

    def test_an_invented_number_sends_back_the_dictation(self):
        dictated = "bonjour je vous envoie mon numéro étudiant pour l'inscription au partiel"
        for style_id in ("perso_notes", "nova_style_everyday"):
            with self.subTest(style_id=style_id):
                text = self.reformulate(
                    dictated,
                    "Bonjour, je vous envoie mon numéro étudiant 25% pour l'inscription au partiel.",
                    style_id)
                self.assertNotIn("25", text)
                self.assertIn("21458732", text)

    def test_a_clean_repair_is_still_kept(self):
        text = self.reformulate(
            "bonjour je vous envoie mon numéro étudiant pour l'inscription au partiel",
            "Bonjour, je vous envoie mon numéro étudiant pour l'inscription au partiel.",
            "perso_notes")
        self.assertEqual(
            text,
            "Bonjour, je vous envoie mon numéro étudiant : 21458732 pour l'inscription au partiel.")
