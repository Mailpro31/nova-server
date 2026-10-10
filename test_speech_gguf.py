"""Le service de transcription GGUF parle comme `faster-whisper-server`.

Le serveur Nova doit l'appeler sans changer de code (`WhisperCompatibleSpeechBackend`) :
même route, mêmes champs, même santé, et un audio illisible rendu en 400.
"""

import asyncio
import importlib.util
import io
import os
import sys
import unittest
import wave
from unittest.mock import patch

import httpx
from fastapi.testclient import TestClient

import ai_runtime

_SPEC = importlib.util.spec_from_file_location(
    "speech_gguf_app", os.path.join(os.path.dirname(__file__), "tools", "speech-gguf", "app.py"))
speech = importlib.util.module_from_spec(_SPEC)
sys.modules[_SPEC.name] = speech
_SPEC.loader.exec_module(speech)


def wav(seconds: float = 1.0, rate: int = 16000, channels: int = 1) -> bytes:
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as out:
        out.setnchannels(channels)
        out.setsampwidth(2)
        out.setframerate(rate)
        out.writeframes(bytes(2 * channels * int(rate * seconds)))
    return buffer.getvalue()


class FakeEngine:
    model_path = "/models/cohere-transcribe-03-2026-Q5_K_M.gguf"
    version = "transcribe.cpp test"
    error = None

    def __init__(self, ready=True):
        self.ready = ready
        self.calls = []

    def load(self):
        self.ready = True

    def transcribe(self, pcm, language):
        self.calls.append((len(pcm), language))
        return speech.Transcript(
            "Le nombre de Reynolds compare les forces.", language,
            [speech.Segment(0.0, 1.0, "Le nombre de Reynolds compare les forces.")])


class SpeechServiceTests(unittest.TestCase):
    def setUp(self):
        self.engine = FakeEngine()
        self.client = TestClient(speech.create_app(self.engine))

    def test_a_wav_dictation_is_transcribed_in_french(self):
        response = self.client.post(
            "/v1/audio/transcriptions", files={"file": ("a.wav", wav(1.0))},
            data={"model": "cohere", "language": "fr"})
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json(), {"text": "Le nombre de Reynolds compare les forces."})
        self.assertEqual(self.engine.calls, [(16000, "fr")])

    def test_without_a_language_french_is_imposed(self):
        self.client.post("/v1/audio/transcriptions", files={"file": ("a.wav", wav(0.5))})
        self.assertEqual(self.engine.calls[0][1], "fr")

    def test_verbose_json_has_what_the_nova_server_reads(self):
        body = self.client.post(
            "/v1/audio/transcriptions", files={"file": ("a.wav", wav(2.0))},
            data={"response_format": "verbose_json"}).json()
        self.assertAlmostEqual(body["duration"], 2.0)
        self.assertEqual(body["segments"][0]["text"], "Le nombre de Reynolds compare les forces.")
        # Pas de confiance inventée : le serveur la lit comme absente.
        self.assertIsNone(ai_runtime.segments_confidence(body["segments"]))

    def test_unreadable_audio_is_a_400(self):
        with patch.object(speech, "decode_with_ffmpeg", side_effect=speech.AudioUnreadable("x")):
            for audio in (b"", b"bonjour, ceci est un texte"):
                with self.subTest(audio=audio[:10]):
                    response = self.client.post(
                        "/v1/audio/transcriptions", files={"file": ("a.wav", audio)})
                    self.assertEqual(response.status_code, 400)
        self.assertEqual(self.engine.calls, [])

    def test_a_stereo_or_other_rate_recording_goes_through_ffmpeg(self):
        converted = speech.pcm16_to_float(bytes(2 * 8000), 1)
        with patch.object(speech, "decode_with_ffmpeg", return_value=converted) as ffmpeg:
            self.client.post("/v1/audio/transcriptions", files={"file": ("a.wav", wav(1.0, 44100))})
        ffmpeg.assert_called_once()
        self.assertEqual(self.engine.calls, [(8000, "fr")])

    def test_stereo_16k_is_downmixed(self):
        pcm = speech.decode_audio(wav(1.0, 16000, channels=2))
        self.assertEqual(len(pcm), 16000)

    def test_health_says_loading_then_ready(self):
        loading = TestClient(speech.create_app(FakeEngine(ready=False)))
        # Le chargement démarre au lancement ; avant, la santé dit `loading`.
        app = speech.create_app(FakeEngine(ready=False))
        self.assertEqual(TestClient(app).get("/health").status_code, 503)
        del loading
        self.assertEqual(self.client.get("/health").json()["status"], "ready")

    def test_a_dictation_while_loading_asks_to_retry(self):
        engine = FakeEngine(ready=False)
        engine.load = lambda: None  # reste en chargement
        client = TestClient(speech.create_app(engine))
        response = client.post("/v1/audio/transcriptions", files={"file": ("a.wav", wav(0.5))})
        self.assertEqual(response.status_code, 425)


class NovaServerCompatibilityTests(unittest.TestCase):
    """Le vrai adaptateur du serveur Nova, branché sur ce service."""

    def test_the_nova_backend_reads_this_service(self):
        client = TestClient(speech.create_app(FakeEngine()))

        def handler(request: httpx.Request) -> httpx.Response:
            sent = {"content-type": request.headers["content-type"]} if request.content else {}
            response = client.request(
                request.method, request.url.path, content=request.content, headers=sent)
            return httpx.Response(response.status_code, content=response.content,
                                  headers={"content-type": response.headers["content-type"]})

        adapter = ai_runtime.WhisperCompatibleSpeechBackend(
            "http://speech-gguf:8000", "cohere-transcribe-03-2026",
            transport=httpx.MockTransport(handler))
        request = ai_runtime.SpeechInferenceRequest(
            "r1", ai_runtime.TaskType.TRANSCRIPTION_REALTIME, wav(1.0), "recording.wav", "fr",
            False, 10.0, None, True)
        result = asyncio.run(adapter.transcribe(request))
        self.assertEqual(result.text, "Le nombre de Reynolds compare les forces.")
        self.assertIsNone(result.avg_logprob)
        health = asyncio.run(adapter.health())
        self.assertEqual(health.status, ai_runtime.EngineStatus.READY)

    def test_unreadable_audio_becomes_invalid_audio_for_the_nova_server(self):
        client = TestClient(speech.create_app(FakeEngine()))

        def handler(request: httpx.Request) -> httpx.Response:
            with patch.object(speech, "decode_with_ffmpeg",
                              side_effect=speech.AudioUnreadable("x")):
                response = client.request(
                    request.method, request.url.path, content=request.content,
                    headers={"content-type": request.headers["content-type"]})
            return httpx.Response(response.status_code, content=response.content)

        adapter = ai_runtime.WhisperCompatibleSpeechBackend(
            "http://speech-gguf:8000", "cohere", transport=httpx.MockTransport(handler))
        request = ai_runtime.SpeechInferenceRequest(
            "r1", ai_runtime.TaskType.TRANSCRIPTION_REALTIME, b"pas de son", "a.wav", "fr",
            False, 10.0, None, True)
        with self.assertRaises(ai_runtime.AIRuntimeError) as raised:
            asyncio.run(adapter.transcribe(request))
        self.assertEqual(raised.exception.code, ai_runtime.AI_AUDIO_INVALID)


if __name__ == "__main__":
    unittest.main()
