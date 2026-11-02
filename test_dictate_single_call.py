"""Un seul appel par dictée (n° 39) : l'audio et le Style partent ensemble.

`/api/dictate` enchaîne `/api/transcribe` puis `/api/reformulate` sur le
serveur : mêmes quotas, mêmes garde-fous, un aller-retour de moins pour le
poste. Il rend `relue` (la transcription) et `final` (le texte du Style). Une
erreur du Style ne perd jamais la dictée : `final` est alors la transcription.
"""

from unittest.mock import patch

from test_main import AdminTestCase
from test_dictation_language import engine_saying

import main

main.init_db()

STYLE = {"style_id": "default_improve_transcriptions", "style_prompt": "Corrige le texte."}


class DictateSingleCallTests(AdminTestCase):
    def setUp(self):
        super().setUp()
        self.token = self.sign_in("etudiant@example.com")
        self.seen_texts: list = []

    async def style_engine(self, task_type, instruction, text):
        self.seen_texts.append(text)
        return "Je révise la dérivée d'une constante."

    def dictate(self, heard: str, style=STYLE):
        with patch.object(main.ORGANIZATION_AI_ROUTER, "transcribe", engine_saying(heard, [])), \
                patch.object(main, "execute_text", self.style_engine):
            response = self.client.post(
                "/api/dictate?language=fr", headers=self.auth(self.token), data=style,
                files={"file": ("recording.wav", b"RIFF0000WAVE", "audio/wav")})
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()

    def reformulate(self, text: str):
        with patch.object(main, "execute_text", self.style_engine):
            response = self.client.post(
                "/api/reformulate", headers=self.auth(self.token), json={"text": text, **STYLE})
        return response.json()["text"]

    def test_one_call_gives_the_same_text_as_two(self):
        heard = "je révise la dérivée d'une constante"
        body = self.dictate(heard)
        self.assertEqual(body["relue"], heard)
        self.assertEqual(len(self.seen_texts), 1)
        self.assertIn(heard, self.seen_texts[0])
        self.assertEqual(body["final"], self.reformulate(heard))

    def test_an_empty_dictation_never_reaches_the_style(self):
        body = self.dictate("Euh...")
        self.assertEqual((body["relue"], body["final"]), ("", ""))
        self.assertEqual(self.seen_texts, [])

    def test_a_style_error_keeps_the_dictation(self):
        heard = "je révise la dérivée d'une constante"
        body = self.dictate(heard, {"style_id": main.ORGANIZATION_STYLE_PREFIX + "inconnu"})
        self.assertEqual(body["final"], heard)
        self.assertEqual(body["style_error"], "STYLE_NOT_AVAILABLE")
