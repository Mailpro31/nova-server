"""La langue de la dictée, transmise à Whisper ; un « Euh… » seul ne s'écrit pas.

Mesuré le 05/10 sur les dictées de Sash (voix cassée, ventilateurs), avec le
modèle du serveur (large-v3-turbo) : laisser Whisper deviner la langue de
chaque clip donnait 0,47 d'erreur de mots ; la lui imposer, 0,27. Sur un
« Hum » de 1,6 s, la langue devinée était l'anglais (« Mm-hmm »). Le poste
envoie donc la langue de dictée choisie par l'élève (`?language=fr`), et le
serveur la passe au moteur. Un code invalide est ignoré : la dictée part
comme avant.

Sur ces clips presque vides, Whisper rend « Euh... », « ... »,
« Lééééééé... » : du texte que l'élève n'a pas dit. Une transcription faite
seulement d'hésitations ou de ponctuation est rendue vide, et rien n'est
collé.
"""

import unittest
from unittest.mock import patch

from test_main import AdminTestCase

import main
from ai_runtime import SpeechInferenceResult

main.init_db()


def engine_saying(text: str, seen: list):
    async def transcribe(task_type, audio, filename, **options):
        seen.append(options)
        return SpeechInferenceResult(text=text, language="fr", duration_seconds=1.0,
                                     latency_ms=1, backend={"adapter": "fake"})
    return transcribe


class DictationLanguageTests(AdminTestCase):
    def setUp(self):
        super().setUp()
        self.token = self.sign_in("etudiant@example.com")

    def dictate(self, text: str, query: str = ""):
        seen: list = []
        token = self.token
        with patch.object(main.ORGANIZATION_AI_ROUTER, "transcribe", engine_saying(text, seen)):
            response = self.client.post(
                f"/api/transcribe{query}", headers=self.auth(token),
                files={"file": ("recording.wav", b"RIFF0000WAVE", "audio/wav")})
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()["text"], seen[0]

    def test_the_dictation_language_reaches_the_engine(self):
        text, options = self.dictate("Question", "?language=fr")
        self.assertEqual(text, "Question")
        self.assertEqual(options["language"], "fr")

    def test_without_a_language_whisper_still_guesses(self):
        _, options = self.dictate("Question")
        self.assertIsNone(options.get("language"))

    def test_an_invalid_language_is_ignored(self):
        for query in ("?language=français", "?language=../x", "?language=auto",
                      "?language=", "?language=FRA1"):
            with self.subTest(query=query):
                _, options = self.dictate("Question", query)
                self.assertIsNone(options.get("language"))

    def test_hesitations_alone_are_not_written(self):
        for heard in ("Euh...", "...", "Lééééééé...", "Mm-hmm", "Hum.", "euh, hum…", " "):
            with self.subTest(heard=heard):
                text, _ = self.dictate(heard, "?language=fr")
                self.assertEqual(text, "")

    def test_real_words_are_kept(self):
        for heard in ("Question", "Ah bon, d'accord.", "Euh, important : demain.", "Oh !"):
            with self.subTest(heard=heard):
                text, _ = self.dictate(heard, "?language=fr")
                self.assertEqual(text, heard)


if __name__ == "__main__":
    unittest.main()
