"""Notes structurees — des notes brutes rangees selon leur nature.

Les « notes d'ingenierie » ne servaient qu'un metier : Observation, Hypothese,
Methode, Resultat, Action. Nova les ouvre au grand public sous le nom « Notes
structurees », avec un type de note choisi par le membre. Chaque type a sa
propre structure : un compte rendu de reunion ne se range pas comme un cours.

`/api/engineering-notes` reste en place : les postes deja installes
l'appellent encore.
"""

import json
from unittest.mock import patch

from test_main import AdminTestCase  # noqa: F401

import main
from ai_runtime import (
    FakeSpeechInferenceBackend,
    FakeTextInferenceBackend,
    OrganizationAIRouter,
    TaskType,
)

main.init_db()


class StructuredNotesTests(AdminTestCase):
    def setUp(self):
        super().setUp()
        with main.db() as conn:
            conn.execute("DELETE FROM usage")
            conn.execute("DELETE FROM organization_policies")

    def auth(self, token):
        return {"Authorization": f"Bearer {token}"}

    def post(self, payload, token=None):
        # Ces tests parlent de structure, pas de quotas : la rafale autorisee
        # par membre ne doit pas decider du resultat a la cinquieme note.
        main.reset_rate_limiter()
        self.addCleanup(main.reset_rate_limiter)
        router = OrganizationAIRouter(
            FakeTextInferenceBackend(), FakeSpeechInferenceBackend()
        )
        token = token or self.sign_in("member@example.com")
        with patch.object(main, "ORGANIZATION_AI_ROUTER", router):
            response = self.client.post(
                "/api/structured-notes", headers=self.auth(token), json=payload
            )
        return response, router.text_backend.requests

    def test_every_note_type_has_its_own_structure(self):
        expected = {
            "meeting": ["Decisions", "Action items"],
            "lecture": ["Key concepts", "Questions to review"],
            "observation": ["Observation", "Result"],
            "ideas": ["Ideas", "Next steps"],
            "free": ["Summary", "Key points"],
        }
        instructions = {}
        for note_type, sections in expected.items():
            with self.subTest(note_type=note_type):
                response, requests = self.post(
                    {"note_type": note_type, "text": "notes brutes"}
                )
                self.assertEqual(response.status_code, 200, response.text)
                self.assertEqual(response.json()["note_type"], note_type)
                self.assertEqual(len(requests), 1)
                self.assertEqual(requests[0].task_type, TaskType.ENGINEERING_NOTES)
                for section in sections:
                    self.assertIn(section, requests[0].instruction)
                instructions[note_type] = requests[0].instruction
        self.assertEqual(len(set(instructions.values())), len(expected))

    def test_every_structure_keeps_the_facts_and_the_language(self):
        for note_type in main.STRUCTURED_NOTE_TYPES:
            with self.subTest(note_type=note_type):
                _, requests = self.post({"note_type": note_type, "text": "x"})
                instruction = requests[0].instruction
                self.assertIn("never invent", instruction)
                self.assertIn("same language", instruction)

    def test_without_a_type_the_note_is_free(self):
        response, requests = self.post({"text": "notes brutes"})
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["note_type"], "free")
        self.assertIn("Summary", requests[0].instruction)

    def test_an_unknown_type_is_refused_before_inference(self):
        response, requests = self.post({"note_type": "poem", "text": "x"})
        self.assertEqual(response.status_code, 422)
        self.assertEqual(requests, [])

    def test_the_member_instruction_is_added_not_substituted(self):
        _, requests = self.post(
            {"note_type": "meeting", "text": "x", "instruction": "Mets les dates en gras"}
        )
        instruction = requests[0].instruction
        self.assertIn("Decisions", instruction)
        self.assertIn("Mets les dates en gras", instruction)

    def test_the_text_stays_bounded(self):
        response, requests = self.post(
            {"note_type": "free", "text": "A" * (main.MAX_ENGINEERING_NOTES_CHARS + 1)}
        )
        self.assertEqual(response.status_code, 422)
        self.assertEqual(requests, [])

    def test_the_organization_policy_still_closes_the_feature(self):
        with main.db() as conn:
            conn.execute(
                "INSERT INTO organization_policies(organization_id,schema_version,revision,settings,updated_at) "
                "VALUES (?,?,?,?,?)",
                (main.nova_organization_id(), main.POLICY_SCHEMA_VERSION, 1,
                 json.dumps({"engineering_notes_enabled": False}), 1),
            )
        response, requests = self.post({"note_type": "free", "text": "x"})
        self.assertEqual(response.status_code, 403)
        self.assertEqual(requests, [])

    def test_installed_workstations_keep_their_route(self):
        router = OrganizationAIRouter(
            FakeTextInferenceBackend(), FakeSpeechInferenceBackend()
        )
        token = self.sign_in("member@example.com")
        with patch.object(main, "ORGANIZATION_AI_ROUTER", router):
            response = self.client.post(
                "/api/engineering-notes", headers=self.auth(token),
                json={"instruction": "", "text": "mesure 12 mm"},
            )
        self.assertEqual(response.status_code, 200, response.text)
        self.assertIn("Observation", router.text_backend.requests[0].instruction)


if __name__ == "__main__":
    import unittest

    unittest.main()


class LongStructuredNotesTests(StructuredNotesTests):
    """Une transcription de vidéo ou de cours entier ne tenait pas dans la
    fenêtre du petit modèle du serveur : « Nova n'a pas pu structurer ces
    notes », sans plus d'explication (vu par Sash le 01/10). Le texte long est
    désormais structuré par morceaux qui tiennent, puis remis bout à bout."""

    sentences = [f"Phrase numéro {i} du cours sur les volcans et leurs éruptions." for i in range(400)]

    def test_a_long_text_is_structured_in_pieces_that_fit(self):
        text = " ".join(self.sentences)
        self.assertGreater(len(text), main.STRUCTURED_NOTES_CHUNK_CHARS * 3)
        response, requests = self.post({"note_type": "lecture", "text": text})
        self.assertEqual(response.status_code, 200, response.text)
        self.assertGreater(len(requests), 1)
        for request in requests:
            self.assertLessEqual(len(request.input), main.STRUCTURED_NOTES_CHUNK_CHARS)
        sent = " ".join(request.input for request in requests)
        for sentence in self.sentences:
            self.assertIn(sentence, sent)
        self.assertEqual(response.json()["text"].count("fake:engineering_notes"), len(requests))

    def test_a_short_text_stays_a_single_request(self):
        response, requests = self.post({"note_type": "lecture", "text": self.sentences[0]})
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(len(requests), 1)

    def test_pieces_follow_sentences_and_lose_nothing(self):
        text = " ".join(self.sentences)
        pieces = main.split_for_structuring(text, 500)
        self.assertTrue(all(len(piece) <= 500 for piece in pieces))
        self.assertEqual(" ".join(pieces).split(), text.split())
        self.assertTrue(all(piece.endswith(".") for piece in pieces))

    def test_a_sentence_longer_than_a_piece_is_cut_between_words(self):
        text = " ".join(["mot"] * 400)
        pieces = main.split_for_structuring(text, 100)
        self.assertTrue(all(len(piece) <= 100 for piece in pieces))
        self.assertEqual(" ".join(pieces).split(), text.split())
