"""Le gabarit d'un Style ne doit jamais sortir a la place de la dictee.

Signale le 2026-09-20 : une dictee dans Word a produit le **texte du Style
lui-meme** — son exemple (« projet Atlas », « 45 000 € »), ses regles, et
jusqu'au marqueur `<transcript>${output}</transcript>` non remplace. La dictee
reelle n'est jamais apparue.

La cause tient au chemin : le poste envoie le prompt persiste de son Style, qui
porte ce marqueur parce que le poste, lui, le remplace avant d'appeler son
moteur local. Le serveur, lui, place la dictee dans le **message** et le Style
dans l'**instruction** : le marqueur reste alors dans l'instruction, le modele
lit un gabarit avec un emplacement vide, et continue l'exemple.

Deux protections, parce qu'une seule ne suffirait pas :

1. le marqueur et son bloc sont retires de l'instruction, comme le poste le
   fait deja pour son moteur local ;
2. si le modele renvoie malgre tout le gabarit, la dictee d'origine est rendue
   telle quelle — une dictee brute vaut mieux qu'un prompt colle dans Word.
"""

import unittest
from unittest.mock import patch

from test_main import AdminTestCase  # noqa: F401

import main

main.init_db()

# Le gabarit tel que le poste le persiste, raccourci.
TEMPLATE = (
    "<transcript>\n${output}\n</transcript>\n\n"
    "Exemple attendu :\n## Reunion — projet Atlas\nBudget : 45 000 €\n\n"
    "Avant d'ecrire, lis TOUTE la dictee et degage l'intention reelle."
)


class StylePromptPlaceholderTests(AdminTestCase):
    def reformulate(self, body: dict, answer: str = "Texte reformule.", text: str = "bonjour"):
        token = self.sign_in("etudiant@example.com")
        seen = {}

        async def engine(task_type, instruction, message):
            seen["instruction"] = instruction
            seen["text"] = message
            return answer

        with patch.object(main, "execute_text", engine):
            response = self.client.post("/api/reformulate", headers=self.auth(token),
                                        json={"text": text, **body})
        return response, seen

    def test_the_transcript_marker_never_reaches_the_model(self):
        response, seen = self.reformulate(
            {"style_id": "nova_style_notes", "style_prompt": TEMPLATE})
        self.assertEqual(response.status_code, 200, response.text)
        self.assertNotIn("${output}", seen["instruction"])
        self.assertNotIn("<transcript>", seen["instruction"])
        # Le reste du Style est conserve : c'est lui qui fait le travail.
        self.assertIn("degage l'intention reelle", seen["instruction"])
        # La dictee va dans le message, delimitee -- jamais dans l'instruction.
        self.assertEqual(seen["text"], main.transcript_message("bonjour"))

    def test_a_style_made_only_of_the_marker_falls_back_to_the_default(self):
        response, seen = self.reformulate(
            {"style_id": "nova_style_notes",
             "style_prompt": "<transcript>\n${output}\n</transcript>"})
        self.assertEqual(response.status_code, 200, response.text)
        self.assertNotIn("${output}", seen["instruction"])
        self.assertTrue(seen["instruction"].strip())

    def test_an_answer_that_repeats_the_template_is_refused(self):
        # Le modele a recrache le gabarit : la dictee d'origine est rendue.
        response, _ = self.reformulate(
            {"style_id": "nova_style_notes", "style_prompt": TEMPLATE},
            answer="## Reunion — projet Atlas\nBudget : 45 000 €\n<transcript>\n${output}\n</transcript>",
            text="voici mon iban FR76 3000 4000 5000")
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["text"], "Voici mon iban FR76 3000 4000 5000")

    def test_a_normal_answer_is_returned_untouched(self):
        response, _ = self.reformulate(
            {"style_id": "nova_style_notes", "style_prompt": TEMPLATE},
            answer="Voici mon IBAN FR76 3000 4000 5000.",
            text="voici mon iban FR76 3000 4000 5000")
        self.assertEqual(response.json()["text"], "Voici mon IBAN FR76 3000 4000 5000.")

    def test_the_guard_reads_the_instruction_not_a_fixed_list(self):
        # Une reponse qui reprend l'instruction du Style, sans marqueur.
        response, _ = self.reformulate(
            {"style_id": "nova_style_notes", "style_prompt": TEMPLATE},
            answer="Avant d'ecrire, lis TOUTE la dictee et degage l'intention reelle.",
            text="reunion demain a 9 h")
        self.assertEqual(response.json()["text"], "Reunion demain a 9 h")


class SanitizerTests(unittest.TestCase):
    def test_the_marker_is_removed_wherever_it_sits(self):
        for raw in (
            "Regles.\n<transcript>\n${output}\n</transcript>",
            "<transcript>${output}</transcript>\nRegles.",
            "Corrige ceci : ${output}\nRegles.",
        ):
            with self.subTest(raw=raw):
                cleaned = main.style_instruction(raw)
                self.assertNotIn("${output}", cleaned)
                self.assertNotIn("transcript>", cleaned)
                self.assertIn("Regles.", cleaned)

    def test_an_answer_is_compared_to_the_instruction_it_came_from(self):
        instruction = "Ecris des notes structurees. Exemple : projet Atlas."
        self.assertTrue(main.answer_repeats_instruction(instruction, instruction))
        self.assertTrue(main.answer_repeats_instruction(
            instruction, "Exemple : projet Atlas."))
        self.assertTrue(main.answer_repeats_instruction(
            instruction, "<transcript>\n${output}\n</transcript>"))
        self.assertFalse(main.answer_repeats_instruction(
            instruction, "Reunion de lundi, budget valide."))
        self.assertFalse(main.answer_repeats_instruction(instruction, ""))


if __name__ == "__main__":
    unittest.main()
