"""Une dictée que Whisper n'a pas comprise ne se colle pas : elle est signalée.

Test de Sash le 05/10 : sans même parler, Nova a collé « D'accord, un peu
d'alcool pour ma prosse en PIX » cinq fois de suite, et une dictée faite à
voix cassée est devenue « Donc calme pas le signal pour ma croix en x… ».
Mesuré sur ses enregistrements avec le modèle du serveur :

| dictée                                   | confiance (avg_logprob) | phrase répétée |
| « Est-ce qu'il ne faudrait pas… » juste  | −0,20                   | non            |
| « important demain j'irai… » presque     | −0,47                   | non            |
| « Il y a que il se stomme… » charabia    | −1,18                   | non            |
| « …pour ma proposant X » ×2, inventé     | −0,63                   | oui            |

Une confiance sous −1,0 (le seuil où Whisper lui-même rejette un passage) ou
une phrase reprise en boucle : le serveur rend un texte vide et
`"unclear": true`. Le poste ne colle rien et dit à l'élève de parler plus
fort ou plus près du micro.
"""

import asyncio
import unittest
from unittest.mock import patch

import httpx

from test_main import AdminTestCase

import main
import ai_runtime
from ai_runtime import SpeechInferenceResult

main.init_db()


class ConfidenceFromWhisperTests(unittest.TestCase):
    def transcribe(self, body: dict):
        def handler(request: httpx.Request) -> httpx.Response:
            self.sent = request.content
            return httpx.Response(200, json=body)

        adapter = ai_runtime.WhisperCompatibleSpeechBackend(
            "http://whisper", "large-v3-turbo", transport=httpx.MockTransport(handler))
        request = ai_runtime.SpeechInferenceRequest(
            "r1", ai_runtime.TaskType.TRANSCRIPTION_REALTIME, b"RIFF", "a.wav", "fr", False,
            10.0, None, True)
        return asyncio.run(adapter.transcribe(request))

    def test_the_confidence_is_read_from_the_segments(self):
        result = self.transcribe({"text": "bonjour à tous", "segments": [
            {"text": "bonjour", "avg_logprob": -0.2},
            {"text": " à tous", "avg_logprob": -1.4},
        ]})
        self.assertIn(b"verbose_json", self.sent)
        self.assertAlmostEqual(result.avg_logprob, (-0.2 * 7 + -1.4 * 7) / 14, places=2)

    def test_without_segments_there_is_no_confidence(self):
        self.assertIsNone(self.transcribe({"text": "bonjour"}).avg_logprob)


class UnclearDecisionTests(unittest.TestCase):
    def test_low_confidence_is_unclear(self):
        self.assertTrue(main.dictation_unclear("Il y a que il se stomme un enfant.", -1.18))
        self.assertFalse(main.dictation_unclear("important demain j'irai voir", -0.47))
        self.assertFalse(main.dictation_unclear("bonjour", None))

    def test_a_phrase_repeated_in_a_loop_is_unclear(self):
        looped = ", ".join(["D'accord, un peu d'alcool pour ma prosse en PIX"] * 5)
        self.assertTrue(main.dictation_unclear(looped, -0.3))
        self.assertTrue(main.dictation_unclear(
            "Donc, on passe à l'alphao et à l'alphao pour ma proposant X. "
            "Au moins, on passe à l'alphao et à l'alphao pour ma proposant X.", -0.63))

    def test_ordinary_repetitions_are_not_a_loop(self):
        for text in (
            "La fonction f et la fonction g sont continues.",
            "Premier point, deuxième point, troisième point.",
            "Je répète : le partiel est le 12 mars, le partiel est important.",
        ):
            with self.subTest(text=text):
                self.assertFalse(main.dictation_unclear(text, -0.3))


class UnclearRouteTests(AdminTestCase):
    def setUp(self):
        super().setUp()
        self.token = self.sign_in("etudiant@example.com")

    def dictate(self, text: str, avg_logprob):
        async def transcribe(task_type, audio, filename, **options):
            self.options = options
            return SpeechInferenceResult(text=text, language="fr", duration_seconds=1.0,
                                         latency_ms=1, backend={}, avg_logprob=avg_logprob)

        with patch.object(main.ORGANIZATION_AI_ROUTER, "transcribe", transcribe):
            response = self.client.post(
                "/api/transcribe?language=fr", headers=self.auth(self.token),
                files={"file": ("recording.wav", b"RIFF0000WAVE", "audio/wav")})
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()

    def test_an_unclear_dictation_returns_nothing_and_says_so(self):
        self.assertEqual(self.dictate("Il y a que il se stomme un enfant.", -1.18),
                         {"text": "", "unclear": True})
        self.assertTrue(self.options["confidence"])

    def test_a_clear_dictation_is_unchanged(self):
        self.assertEqual(self.dictate("Le partiel est le 12 mars.", -0.2),
                         {"text": "Le partiel est le 12 mars."})


if __name__ == "__main__":
    unittest.main()


class SubtitleCreditTests(unittest.TestCase):
    """Mesuré sur le RTX le 05/10 (615a4ce) : sur du bruit, sans un mot dit,
    Whisper écrit des crédits de sous-titres appris sur des vidéos, avec une
    confiance qui paraît normale (−0,63, −0,66) : « Sous-titres réalisés par
    l'Amara.org », « Sous-titrage ST' 501 ». Une dictée qui n'est QUE cela est
    incomprise : rien n'est collé."""

    def test_a_dictation_made_only_of_credits_is_unclear(self):
        for heard in (
            "Sous-titres réalisés par l'Amara.org",
            "Sous-titres réalisés par la communauté d'Amara.org",
            "Sous-titrage ST' 501",
            "Sous-titrage Société Radio-Canada",
            "Merci d'avoir regardé cette vidéo !",
            "Subtitles by the Amara.org community",
            "Thanks for watching!",
        ):
            with self.subTest(heard=heard):
                self.assertTrue(main.dictation_unclear(heard, -0.63))

    def test_real_dictations_that_mention_these_words_are_kept(self):
        for said in (
            "Les sous-titres de la vidéo sont en anglais.",
            "Merci d'avoir regardé mon rapport.",
            "Le site Amara.org propose des sous-titres participatifs pour les cours.",
        ):
            with self.subTest(said=said):
                self.assertFalse(main.dictation_unclear(said, -0.3))
