"""La voix naturelle (1.1) : le serveur de l'école lit le texte, rien ne sort.

Les voix Windows du poste sont robotiques ; les voix naturelles d'Edge sont
en ligne, chez Microsoft. `/api/speech` fait lire le texte par un moteur de
synthèse hébergé avec le serveur (Kokoro, voix française `ff_siwis`), au
format OpenAI `/v1/audio/speech`. Pas de moteur configuré : 503, et le poste
garde la voix Windows.
"""

import unittest
from unittest.mock import patch

import httpx

from test_main import AdminTestCase

import main
from ai_runtime import AIRuntimeError, VoiceBackend

main.init_db()


def engine(seen: list, status: int = 200):
    def handle(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(status, content=b"ID3-audio")
    return httpx.MockTransport(handle)


class VoiceBackendTests(unittest.IsolatedAsyncioTestCase):
    async def test_the_engine_receives_the_text_voice_and_speed(self):
        seen: list = []
        voice = VoiceBackend("http://voice:8880/v1", "kokoro", "ff_siwis",
                             transport=engine(seen))
        self.assertEqual(await voice.speak("Bonjour.", 1.2), b"ID3-audio")
        request = seen[0]
        self.assertEqual(request.url.path, "/v1/audio/speech")
        body = __import__("json").loads(request.content)
        self.assertEqual(body, {"model": "kokoro", "input": "Bonjour.", "voice": "ff_siwis",
                                "response_format": "mp3", "speed": 1.2})

    async def test_an_engine_error_is_a_runtime_error(self):
        voice = VoiceBackend("http://voice:8880/v1", "kokoro", "ff_siwis",
                             transport=engine([], status=500))
        with self.assertRaises(AIRuntimeError):
            await voice.speak("Bonjour.", 1.0)


class SpeechRouteTests(AdminTestCase):
    def setUp(self):
        super().setUp()
        self.token = self.sign_in("etudiant@example.com")

    def speak(self, body: dict, backend=None):
        with patch.object(main, "VOICE_BACKEND", backend):
            return self.client.post("/api/speech", headers=self.auth(self.token), json=body)

    def test_the_school_engine_reads_the_text(self):
        seen: list = []
        backend = VoiceBackend("http://voice:8880/v1", "kokoro", "ff_siwis",
                               transport=engine(seen))
        response = self.speak({"text": "La dérivée d'une constante est nulle.", "speed": 1.0},
                              backend)
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.headers["content-type"], "audio/mpeg")
        self.assertEqual(response.content, b"ID3-audio")

    def test_without_an_engine_the_post_keeps_its_windows_voice(self):
        response = self.speak({"text": "Bonjour.", "speed": 1.0})
        self.assertEqual(response.status_code, 503)

    def test_too_long_or_odd_requests_are_refused(self):
        backend = VoiceBackend("http://voice:8880/v1", "kokoro", "ff_siwis",
                               transport=engine([]))
        for body in ({"text": "a" * (main.MAX_SPEECH_CHARS + 1), "speed": 1.0},
                     {"text": "Bonjour.", "speed": 9},
                     {"text": "   ", "speed": 1.0}):
            with self.subTest(body=str(body)[:40]):
                self.assertEqual(self.speak(body, backend).status_code, 422)

    def test_a_signed_out_post_cannot_use_the_engine(self):
        with patch.object(main, "VOICE_BACKEND", None):
            response = self.client.post("/api/speech", json={"text": "Bonjour.", "speed": 1.0})
        self.assertEqual(response.status_code, 401)


if __name__ == "__main__":
    unittest.main()
