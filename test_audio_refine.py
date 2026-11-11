"""La dictée relue avec l'audio et le vocabulaire (Gemini via une API au format
OpenAI). Banc v2 du RTX (17/10) : 41,5 % → 80,7 % de transcriptions complètes."""

import asyncio
import json
import unittest
from unittest.mock import patch

import httpx

# `test_main` doit être importé en premier : il redirige la base de test.
from test_main import AdminTestCase  # noqa: F401

import ai_runtime
import main
from ai_runtime import AI_RUNTIME_FAILED, AIRuntimeError, AudioRefineBackend

main.init_db()


def backend(handler):
    return AudioRefineBackend("http://refine:8000/v1", "google/gemini-2.5-flash",
                              token="t", transport=httpx.MockTransport(handler))


class AudioRefineBackendTests(unittest.TestCase):
    def test_the_audio_the_transcript_and_the_vocabulary_are_sent(self):
        seen = {}

        def handler(request):
            seen.update(json.loads(request.content))
            seen["auth"] = request.headers.get("authorization")
            return httpx.Response(200, json={"choices": [{"message": {
                "content": " Le nombre de Mach 1. "}}]})

        text = asyncio.run(backend(handler).refine(b"RIFFaudio", "wav", "le nombre de à Maquin",
                                                   ["Mach 1", "portance"]))
        self.assertEqual(text, "Le nombre de Mach 1.")
        self.assertEqual(seen["auth"], "Bearer t")
        self.assertEqual(seen["temperature"], 0)
        system, user = seen["messages"]
        self.assertEqual(system["content"], ai_runtime.AUDIO_REFINE_INSTRUCTION)
        audio, context = user["content"]
        self.assertEqual(audio["input_audio"]["format"], "wav")
        self.assertIn("le nombre de à Maquin", context["text"])
        self.assertIn("Mach 1, portance", context["text"])
        # Les reprises restent dans la transcription : le Style les résout ensuite.
        self.assertIn("écris le mot de reprise ET les deux valeurs", system["content"])

    def test_the_reasoning_effort_is_sent_only_when_configured(self):
        bodies = []

        def handler(request):
            bodies.append(json.loads(request.content))
            return httpx.Response(200, json={"choices": [{"message": {"content": "ok"}}]})

        for effort in ("minimal", ""):
            asyncio.run(AudioRefineBackend("http://refine:8000/v1", "m", reasoning_effort=effort,
                                           transport=httpx.MockTransport(handler)).refine(b"x", "wav", "t", []))
        self.assertEqual(bodies[0]["reasoning_effort"], "minimal")
        self.assertNotIn("reasoning_effort", bodies[1])

    def test_the_tokens_of_a_reading_are_logged_without_text(self):
        def handler(request):
            return httpx.Response(200, json={"choices": [{"message": {"content": "ok"}}], "usage": {
                "prompt_tokens": 805, "completion_tokens": 216,
                "completion_tokens_details": {"reasoning_tokens": 0}}})

        # Le journal ET le compteur Prometheus : sans lui, le tableau coût du
        # panel omettait le poste le plus lourd (PC fixe, 11/11).
        with patch.object(ai_runtime.observability, "log") as log, \
                patch.object(ai_runtime.observability.METRICS, "add_tokens") as count:
            asyncio.run(backend(handler).refine(b"x", "wav", "t", []))
        count.assert_called_once_with("audio_refine", {"prompt_tokens": 805, "completion_tokens": 216,
                                                       "completion_tokens_details": {"reasoning_tokens": 0}})
        log.assert_any_call("ai.usage", task="audio_refine", prompt_tokens=805,
                            completion_tokens=216, reasoning_tokens=0)

    def test_an_engine_error_is_a_runtime_error(self):
        with self.assertRaises(AIRuntimeError) as raised:
            asyncio.run(backend(lambda request: httpx.Response(500)).refine(b"x", "wav", "t", []))
        self.assertEqual(raised.exception.code, AI_RUNTIME_FAILED)

    def test_it_is_off_unless_the_deployment_configures_it(self):
        with patch.dict("os.environ", {"NOVA_AI_SPEECH_REFINE_BASE_URL": "",
                                       "NOVA_AI_SPEECH_REFINE_MODEL": ""}):
            self.assertIsNone(ai_runtime.audio_refiner_from_environment())


class FakeRefiner:
    def __init__(self, answer=None, error=None):
        self.answer, self.error = answer, error

    async def refine(self, audio, audio_format, transcript, vocabulary):
        if self.error:
            raise AIRuntimeError(self.error)
        return self.answer


class RefinedTranscriptionTests(unittest.TestCase):
    vocabulary = [("mach", "Mach 1"), ("portance", ""), ("traînée", ""), ("finesse", ""),
                  ("Bernoulli", ""), ("Reynolds", "")]

    def refined(self, refiner, transcript="le nombre de à Maquin", filename="audio.wav"):
        with patch.object(main, "AUDIO_REFINER", refiner):
            return asyncio.run(main.refined_transcription(b"RIFF", filename, transcript,
                                                          self.vocabulary))

    def test_a_plausible_reading_replaces_whisper(self):
        self.assertEqual(self.refined(FakeRefiner("Le nombre de Mach 1.")), "Le nombre de Mach 1.")

    def test_whisper_stays_when_the_reading_fails_or_is_off(self):
        self.assertEqual(self.refined(FakeRefiner(error=AI_RUNTIME_FAILED)), "le nombre de à Maquin")
        self.assertEqual(self.refined(None), "le nombre de à Maquin")
        # Un format que l'API n'accepte pas : pas de relecture.
        self.assertEqual(self.refined(FakeRefiner("autre"), filename="audio.webm"),
                         "le nombre de à Maquin")

    def test_a_refiner_down_is_set_aside_for_a_minute(self):
        calls = []

        class Down:
            async def refine(self, *args):
                calls.append(1)
                raise AIRuntimeError(ai_runtime.AI_RUNTIME_UNAVAILABLE)

        with patch.object(main, "_audio_refine_down_until", 0.0), \
                patch.object(main, "_audio_refine_failures", 0):
            for _ in range(4):
                self.assertEqual(self.refined(Down()), "le nombre de à Maquin")
        # 3 échecs de suite, puis la relecture est mise de côté.
        self.assertEqual(len(calls), 3)

    def test_a_copied_vocabulary_or_an_invented_text_is_rejected(self):
        # RTX, 17/10 : le glossaire recopié en entier à la place de la dictée.
        dump = "Mach 1, portance, traînée, finesse, Bernoulli, Reynolds."
        self.assertEqual(self.refined(FakeRefiner(dump)), "le nombre de à Maquin")
        self.assertEqual(self.refined(FakeRefiner("")), "le nombre de à Maquin")
        long_answer = " ".join(["mot"] * 30)
        self.assertEqual(self.refined(FakeRefiner(long_answer)), "le nombre de à Maquin")

    def test_a_reading_that_drops_most_of_the_dictation_is_rejected(self):
        # Banc 0.1 du PC fixe (02/11) : une dictée relue « i ».
        self.assertEqual(self.refined(FakeRefiner("I")), "le nombre de à Maquin")
        self.assertEqual(self.refined(FakeRefiner("Le nombre de Mach.")), "Le nombre de Mach.")
        # Une dictée très courte peut légitimement perdre un mot.
        self.assertEqual(self.refined(FakeRefiner("Oui."), transcript="euh oui"), "Oui.")

    def test_good_readings_that_shorten_a_lot_stay_accepted(self):
        # Banc des 600 relues (PC fixe, 02/11) : refusées à tort avec un seuil à 50 %.
        for heard, read in (
            ("U or Mule U Aigle R I.", "U = RI."),
            ("Et euh c'est la formule X égale moins B plus ou moins Ressin de Delta Sur de XA.",
             "Et euh c'est la formule x = (-b ± √Δ)/2a."),
            ("Lua, Trapich, S. Privolu, Makridi, 1 octobre, G.E. V. Daya, Makridi, "
             "11 december, A.H.M. 9-8-30, Itibola.", "mercredi 11 décembre à 9h30"),
        ):
            with self.subTest(read=read):
                self.assertEqual(self.refined(FakeRefiner(read), transcript=heard), read)


if __name__ == "__main__":
    unittest.main()


class DoubtfulWordsTests(AdminTestCase):
    """1.2 : les mots que la relecture a changés sont les mots à vérifier.

    Banc du 06/11 (600 dictées) : précision 88,9 %, rappel 54,6 %.
    """

    def test_only_the_words_the_reading_changed_are_doubtful(self):
        self.assertEqual(main.changed_words("le nombre de à Maquin", "Le nombre de Mach."),
                         ["Mach"])
        self.assertEqual(main.changed_words("la portance augmente", "La portance augmente."), [])
        self.assertEqual(main.changed_words("", "Bonjour."), ["Bonjour"])

    def test_the_dictation_route_returns_them(self):
        from test_dictation_language import engine_saying
        token = self.sign_in("etudiant@example.com")
        with patch.object(main.ORGANIZATION_AI_ROUTER, "transcribe",
                          engine_saying("le nombre de à Maquin", [])), \
                patch.object(main, "AUDIO_REFINER", FakeRefiner("Le nombre de Mach.")):
            body = self.client.post(
                "/api/transcribe?language=fr", headers=self.auth(token),
                files={"file": ("recording.wav", b"RIFF0000WAVE", "audio/wav")}).json()
        self.assertEqual((body["text"], body["doubtful"]), ("Le nombre de Mach.", ["Mach"]))
