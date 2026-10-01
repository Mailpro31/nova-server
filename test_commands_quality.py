"""Une commande fait ce qu'elle annonce, dans la langue du texte.

Banc d'essai du 2026-09-22, Qwen3 4B servi comme le serveur le sert, sur un
cours de mecanique des fluides (831 caracteres) :

- « Expliquer » a rendu le cours **traduit en anglais** : la consigne commune
  demandait de « garder la langue d'origine » tout en « reecrivant la
  formulation », et le texte arrivait sans rien qui le distingue d'une
  demande ;
- « Resumer » rendait 752 caracteres sur 831 : une paraphrase, pas un resume ;
- les notes structurees d'une reunion en francais sortaient avec des titres
  en anglais (« Summary », « Decisions »), malgre « write in the same
  language ».

La consigne commune dit desormais que le texte est une matiere a traiter,
qu'il ne faut pas se contenter de le repeter, et dans quelle langue repondre.
Le texte arrive delimite. Les titres des notes sont donnes au modele dans la
langue des notes, plutot que de lui demander de les traduire.
"""

import unittest
from unittest.mock import patch

from test_main import AdminTestCase  # noqa: F401

import main
from ai_runtime import FakeSpeechInferenceBackend, FakeTextInferenceBackend, OrganizationAIRouter

main.init_db()


class CommandPromptTests(unittest.TestCase):
    def test_the_command_is_not_merely_a_rewrite(self):
        prompt = main.command_prompt("Summarize the following text.", "")
        self.assertIn("do not merely repeat or rephrase the text", prompt)
        # « garder la langue d'origine » contredisait « Traduire ».
        self.assertNotIn("original language unchanged", prompt)
        self.assertIn("Unless the instruction names a language", prompt)
        self.assertIn("between the <text> tags", prompt)

    def test_the_selected_text_is_delimited(self):
        self.assertEqual(main.command_text("Bonjour."), "<text>\nBonjour.\n</text>")


class CommandRouteTests(AdminTestCase):
    def test_a_command_receives_the_text_delimited(self):
        token = self.sign_in("etudiant@example.com")
        seen = {}

        async def engine(task_type, instruction, text):
            seen["text"] = text
            return "resultat"

        with patch.object(main, "execute_text", engine):
            response = self.client.post(
                "/api/command", headers=self.auth(token),
                json={"instruction": "Resume ce texte.", "text": "Le cours de ce matin."})
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(seen["text"], "<text>\nLe cours de ce matin.\n</text>")


class StructuredNotesLanguageTests(AdminTestCase):
    FRENCH_NOTES = ("reunion projet drone. Léa dit que la batterie tient 18 minutes, il faut 25. "
                    "décision : on passe à la batterie 6S. Hugo commande avant vendredi.")

    def post(self, text, note_type="meeting"):
        main.reset_rate_limiter()
        self.addCleanup(main.reset_rate_limiter)
        router = OrganizationAIRouter(FakeTextInferenceBackend(), FakeSpeechInferenceBackend())
        token = self.sign_in("member@example.com")
        with patch.object(main, "ORGANIZATION_AI_ROUTER", router):
            response = self.client.post("/api/structured-notes", headers=self.auth(token),
                                        json={"note_type": note_type, "text": text})
        self.assertEqual(response.status_code, 200, response.text)
        return router.text_backend.requests[0].instruction

    def test_french_notes_get_french_headings(self):
        instruction = self.post(self.FRENCH_NOTES)
        self.assertIn("Décisions", instruction)
        self.assertIn("Actions", instruction)
        self.assertNotIn("Action items", instruction)

    def test_every_note_type_has_french_headings(self):
        for note_type in main.STRUCTURED_NOTE_TYPES:
            with self.subTest(note_type=note_type):
                self.assertEqual(len(main.STRUCTURED_NOTE_TYPES_FR[note_type]),
                                 len(main.STRUCTURED_NOTE_TYPES[note_type]))

    def test_english_notes_keep_english_headings(self):
        instruction = self.post("meeting about the drone. Lea says the battery lasts 18 minutes "
                                "and we need 25. Hugo orders the new one before Friday.")
        self.assertIn("Action items", instruction)


if __name__ == "__main__":
    unittest.main()
