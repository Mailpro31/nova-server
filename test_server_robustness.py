"""Défauts trouvés par le banc de charge et d'entrées limites du RTX (09/10, 7a763ff).

- Saturation : avec une seule transcription à la fois, une file de 4 places et
  3 s d'attente, 10 dictées simultanées donnaient 5 refus 429. Le poste passe
  alors en silence sur son moteur local : la qualité change sans que l'élève
  le sache. Une dictée prend environ 0,5 s sur la carte ; une file plus longue
  sert toute une classe en quelques secondes.
- Audio illisible : un fichier vide, des octets au hasard ou un texte déguisé en
  .wav donnaient 502 AI_RUNTIME_FAILED, qui fait croire à une panne du serveur.
"""

import asyncio
import unittest
from unittest.mock import patch

import httpx

from test_main import AdminTestCase

import main
import ai_runtime
from ai_runtime import AIRuntimeError, SpeechInferenceResult

main.init_db()


class ClassroomLoadTests(unittest.TestCase):
    def test_ten_simultaneous_dictations_are_all_served(self):
        gate = ai_runtime._CapacityGate(1, main.AUTO_MAX_QUEUE)

        async def dictation():
            await gate.acquire()
            try:
                await asyncio.sleep(0.5)
            finally:
                await gate.release()

        async def classroom():
            return await asyncio.gather(
                *(dictation() for _ in range(10)), return_exceptions=True)

        refused = [r for r in asyncio.run(classroom()) if isinstance(r, AIRuntimeError)]
        self.assertEqual(refused, [])

    def test_the_automatic_setting_keeps_a_classroom_queue(self):
        body = main.AIRuntimeConfigUpdateRequest(
            expected_revision=0, profile="efficient", reasoning_allowed=False)
        self.assertGreaterEqual(main.validate_ai_runtime_update(body)["max_queue"], 16)


def wav(frames: bytes = b"") -> bytes:
    return b"RIFF" + (36 + len(frames)).to_bytes(4, "little") + b"WAVEfmt " + bytes(24) + frames


class AudioRecognitionTests(unittest.TestCase):
    def test_known_containers_are_recognised(self):
        for audio in (
            wav(),
            b"OggS" + bytes(20),
            b"fLaC" + bytes(20),
            b"ID3\x04" + bytes(20),
            b"\xff\xfb\x90\x00" + bytes(20),
            bytes(4) + b"ftypM4A " + bytes(12),
            b"\x1a\x45\xdf\xa3" + bytes(20),
            b"FORM" + bytes(4) + b"AIFF" + bytes(12),
            b"#!AMR\n" + bytes(20),
        ):
            with self.subTest(audio=audio[:12]):
                self.assertTrue(main.looks_like_audio(audio))

    def test_empty_or_unreadable_audio_is_not_recognised(self):
        for audio in (b"", b"bonjour, ceci est un texte", bytes(range(7, 64)), b"RIFF"):
            with self.subTest(audio=audio[:12]):
                self.assertFalse(main.looks_like_audio(audio))

    def test_an_engine_refusing_the_audio_says_so(self):
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(422, json={"detail": "could not decode audio"})

        adapter = ai_runtime.WhisperCompatibleSpeechBackend(
            "http://whisper", "large-v3-turbo", transport=httpx.MockTransport(handler))
        request = ai_runtime.SpeechInferenceRequest(
            "r1", ai_runtime.TaskType.TRANSCRIPTION_REALTIME, wav(), "a.wav", "fr", False,
            10.0, None, True)
        with self.assertRaises(AIRuntimeError) as raised:
            asyncio.run(adapter.transcribe(request))
        self.assertEqual(raised.exception.code, ai_runtime.AI_AUDIO_INVALID)


class InvalidAudioRouteTests(AdminTestCase):
    def setUp(self):
        super().setUp()
        self.token = self.sign_in("etudiant@example.com")
        self.engine_called = False

    def post(self, audio: bytes):
        async def transcribe(task_type, audio, filename, **options):
            self.engine_called = True
            return SpeechInferenceResult(text="Le partiel est le 12 mars.", language="fr",
                                         duration_seconds=1.0, latency_ms=1, backend={},
                                         avg_logprob=-0.2)

        with patch.object(main.ORGANIZATION_AI_ROUTER, "transcribe", transcribe):
            return self.client.post(
                "/api/transcribe?language=fr", headers=self.auth(self.token),
                files={"file": ("recording.wav", audio, "audio/wav")})

    def test_unreadable_audio_is_a_clear_400(self):
        for audio in (b"", b"bonjour, ceci est un texte"):
            with self.subTest(audio=audio[:12]):
                response = self.post(audio)
                self.assertEqual(response.status_code, 400, response.text)
                self.assertEqual(response.json()["detail"]["code"], "INVALID_AUDIO")
        self.assertFalse(self.engine_called)

    def test_a_real_recording_is_still_transcribed(self):
        response = self.post(wav(bytes(3200)))
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json(), {"text": "Le partiel est le 12 mars."})

    def test_an_engine_refusal_of_the_audio_is_a_400(self):
        async def transcribe(task_type, audio, filename, **options):
            raise AIRuntimeError(ai_runtime.AI_AUDIO_INVALID)

        with patch.object(main.ORGANIZATION_AI_ROUTER, "transcribe", transcribe):
            response = self.client.post(
                "/api/transcribe?language=fr", headers=self.auth(self.token),
                files={"file": ("recording.wav", wav(bytes(3200)), "audio/wav")})
        self.assertEqual(response.status_code, 400, response.text)


class StoppedTextEngineTests(unittest.TestCase):
    """Test 8 du RTX sur 472bc3a (11/10) : modèle de texte arrêté, la dictée
    revenait au bout de 11,8 s. Un conteneur arrêté ne se résout plus, et
    chaque appel attendait avant d'échouer ; une reformulation en fait deux."""

    def router(self):
        backend = ai_runtime.FakeTextInferenceBackend(status=ai_runtime.EngineStatus.UNAVAILABLE)
        router = ai_runtime.OrganizationAIRouter(
            backend, ai_runtime.FakeSpeechInferenceBackend())
        return router, backend

    def generate(self, router):
        with self.assertRaises(AIRuntimeError) as raised:
            asyncio.run(router.generate(ai_runtime.TaskType.REFORMULATION, "bonjour", "Corrige."))
        return raised.exception.code

    def test_a_stopped_engine_is_not_called_again_right_away(self):
        router, backend = self.router()
        self.assertEqual(self.generate(router), ai_runtime.AI_RUNTIME_UNAVAILABLE)
        self.assertEqual(self.generate(router), ai_runtime.AI_RUNTIME_UNAVAILABLE)
        self.assertEqual(len(backend.requests), 1)

    def test_the_engine_is_tried_again_after_the_pause(self):
        router, backend = self.router()
        self.generate(router)
        backend.status = ai_runtime.EngineStatus.READY
        with patch.object(ai_runtime.time, "monotonic",
                          return_value=router._text_down_until + 0.1):
            result = asyncio.run(
                router.generate(ai_runtime.TaskType.REFORMULATION, "bonjour", "Corrige."))
        self.assertEqual(result.text, "fake:reformulation")

    def test_a_real_connection_failure_arms_the_pause(self):
        # Test 8 du RTX sur 3ebfec9 (11/10) : vLLM arrêté, la connexion bornée
        # levait ConnectTimeout, compté comme TIMEOUT : la pause ne s'armait
        # jamais, et chaque reformulation attendait 6,1 s.
        for failure in (httpx.ConnectTimeout("dns"), httpx.ConnectError("refused")):
            with self.subTest(failure=type(failure).__name__):
                calls = []

                def handler(request: httpx.Request) -> httpx.Response:
                    calls.append(request.url.path)
                    raise failure

                backend = ai_runtime.OpenAICompatibleTextBackend(
                    "http://llm:8000/v1", "qwen", transport=httpx.MockTransport(handler))
                router = ai_runtime.OrganizationAIRouter(
                    backend, ai_runtime.FakeSpeechInferenceBackend())
                self.assertEqual(self.generate(router), ai_runtime.AI_RUNTIME_UNAVAILABLE)
                reached = len(calls)
                self.assertEqual(self.generate(router), ai_runtime.AI_RUNTIME_UNAVAILABLE)
                self.assertEqual(len(calls), reached, "le moteur arrêté a été rappelé")

    def test_a_slow_answer_stays_a_timeout(self):
        def handler(request: httpx.Request) -> httpx.Response:
            raise httpx.ReadTimeout("slow")

        backend = ai_runtime.OpenAICompatibleTextBackend(
            "http://llm:8000/v1", "qwen", transport=httpx.MockTransport(handler))
        router = ai_runtime.OrganizationAIRouter(backend, ai_runtime.FakeSpeechInferenceBackend())
        self.assertEqual(self.generate(router), ai_runtime.AI_RUNTIME_TIMEOUT)
        self.assertEqual(router._text_down_until, 0.0)

    def test_connecting_to_an_engine_is_bounded(self):
        seen = {}

        def handler(request: httpx.Request) -> httpx.Response:
            seen["timeout"] = request.extensions.get("timeout", {})
            return httpx.Response(200, json={"text": "bonjour", "segments": []})

        adapter = ai_runtime.WhisperCompatibleSpeechBackend(
            "http://whisper", "large-v3-turbo", transport=httpx.MockTransport(handler))
        request = ai_runtime.SpeechInferenceRequest(
            "r1", ai_runtime.TaskType.TRANSCRIPTION_REALTIME, wav(), "a.wav", "fr", False,
            10.0, None, True)
        asyncio.run(adapter.transcribe(request))
        self.assertEqual(seen["timeout"].get("connect"), ai_runtime.ENGINE_CONNECT_TIMEOUT_SECONDS)
        self.assertEqual(seen["timeout"].get("read"), 10.0)


if __name__ == "__main__":
    unittest.main()
