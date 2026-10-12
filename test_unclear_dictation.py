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

    def test_a_sentence_repeated_back_to_back_is_a_loop(self):
        # Une phrase reprise juste après elle-même, au bout d'une dictée : avec
        # une confiance faible, c'est le modèle qui boucle ; trois copies le
        # sont toujours.
        text = ("La portance dépend de la vitesse et de la surface de l'aile. "
                "Le coefficient de portance augmente avec l'incidence jusqu'au "
                "décrochage. Merci beaucoup de votre attention merci beaucoup de votre attention.")
        self.assertTrue(main.dictation_unclear(text, -0.63))
        self.assertTrue(main.dictation_unclear(
            text + " merci beaucoup de votre attention.", -0.3))

    def test_a_sentence_repeated_by_a_confident_student_is_kept(self):
        # Banc du RTX sur f28a759 (11/10) : les 21 dictées v2 bout à bout,
        # confiance −0,22, étaient bloquées par une phrase dite deux fois.
        said = ("Le moteur turbofan sépare le flux primaire du flux secondaire. "
                "Le moteur turbofan sépare le flux primaire du flux secondaire. "
                "La matrice est inversible si son déterminant est différent de zéro.")
        self.assertFalse(main.dictation_unclear(said, -0.22))
        # Sans confiance, ou une confiance faible, deux copies restent une boucle.
        self.assertTrue(main.dictation_unclear(said, -0.63))
        self.assertTrue(main.dictation_unclear(said, None))

    def test_a_repeated_syllable_alone_is_not_a_dictation(self):
        # Turbo en GGUF sur la carte du RTX (11/10) : « Lalalala. » sur un « euh ».
        for heard in ("Lalalala.", "nanana", "Tatata !"):
            with self.subTest(heard=heard):
                self.assertTrue(main.only_hesitations(heard))
        for said in ("Papa arrive.", "Le bonbon", "Lalalala, c'est la chanson du TD.", "ananas"):
            with self.subTest(said=said):
                self.assertFalse(main.only_hesitations(said))

    def test_a_dictation_made_of_numbers_is_kept(self):
        # Une réponse d'exercice n'a parfois aucune lettre : elle ressortait
        # vide, comme un « euh » seul.
        for said in ("1 + 1 = 2", "3,14", "42.", "111", "2 222", "1/2"):
            with self.subTest(said=said):
                self.assertFalse(main.only_hesitations(said))
        for heard in ("Euh…", "...", "", "Hum, euh."):
            with self.subTest(heard=heard):
                self.assertTrue(main.only_hesitations(heard))

    def test_a_lone_thanks_heard_on_a_breath_is_unclear(self):
        # Témoin du RTX (11/10) : « Merci. » sur un souffle, confiance −0,46.
        for heard in ("Merci.", "Merci beaucoup !", "merci à vous"):
            with self.subTest(heard=heard):
                self.assertTrue(main.dictation_unclear(heard, -0.46))
        # Dicté pour de vrai, avec assurance, ou sans confiance : gardé.
        self.assertFalse(main.dictation_unclear("Merci.", -0.15))
        self.assertFalse(main.dictation_unclear("Merci.", None))
        # Une phrase qui contient un merci n'est pas visée.
        self.assertFalse(main.dictation_unclear("Merci pour le corrigé du TD.", -0.46))

    def test_a_formula_said_twice_in_a_course_is_not_a_loop(self):
        # Mesuré sur le RTX le 09/10 (défaut 1) : une dictée de cours juste,
        # confiance −0,2, était entièrement bloquée parce que « le nombre de
        # Reynolds vaut » revenait deux fois, loin l'une de l'autre.
        for text in (
            "Le nombre de Reynolds vaut rho V L sur mu. En dessous de 2300 le "
            "régime est laminaire. Au-dessus de 4000 le régime est turbulent, et "
            "entre les deux le nombre de Reynolds vaut une zone de transition.",
            "La vitesse de l'écoulement est mesurée au tube de Pitot. Plus loin, "
            "la vitesse de l'écoulement est mesurée au fil chaud.",
        ):
            with self.subTest(text=text[:40]):
                self.assertFalse(main.dictation_unclear(text, -0.2))

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
