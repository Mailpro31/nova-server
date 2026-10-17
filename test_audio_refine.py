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

        with patch.object(main, "_audio_refine_down_until", 0.0):
            self.assertEqual(self.refined(Down()), "le nombre de à Maquin")
            self.assertEqual(self.refined(Down()), "le nombre de à Maquin")
        self.assertEqual(len(calls), 1)

    def test_a_copied_vocabulary_or_an_invented_text_is_rejected(self):
        # RTX, 17/10 : le glossaire recopié en entier à la place de la dictée.
        dump = "Mach 1, portance, traînée, finesse, Bernoulli, Reynolds."
        self.assertEqual(self.refined(FakeRefiner(dump)), "le nombre de à Maquin")
        self.assertEqual(self.refined(FakeRefiner("")), "le nombre de à Maquin")
        long_answer = " ".join(["mot"] * 30)
        self.assertEqual(self.refined(FakeRefiner(long_answer)), "le nombre de à Maquin")


if __name__ == "__main__":
    unittest.main()
