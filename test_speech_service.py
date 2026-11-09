"""Le service de transcription GGUF parle comme `faster-whisper-server`.

Le serveur Nova doit l'appeler sans changer de code (`WhisperCompatibleSpeechBackend`) :
même route, mêmes champs, même santé, et un audio illisible rendu en 400.
"""

import array
import asyncio
import importlib.util
import io
import math
import os
import sys
import types
import unittest
import wave
from unittest.mock import patch

import httpx
from fastapi.testclient import TestClient

import ai_runtime

_SPEC = importlib.util.spec_from_file_location(
    "speech_service_app", os.path.join(os.path.dirname(__file__), "tools", "speech", "app.py"))
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

    def test_a_header_only_wav_gives_an_empty_text_without_the_engine(self):
        # Relevé par le RTX le 11/10 : transcribe.cpp levait « empty PCM buffer ».
        for seconds in (0.0, 0.02):
            with self.subTest(seconds=seconds):
                response = self.client.post(
                    "/v1/audio/transcriptions", files={"file": ("a.wav", wav(seconds))},
                    data={"response_format": "verbose_json"})
                self.assertEqual(response.status_code, 200, response.text)
                self.assertEqual(response.json()["text"], "")
                self.assertEqual(response.json()["segments"], [])
        self.assertEqual(self.engine.calls, [])

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


class RaiseLevelTests(unittest.TestCase):
    @staticmethod
    def tone(amplitude: float) -> "array.array":
        return array.array("f", (amplitude * math.sin(i / 5) for i in range(16000)))

    def test_off_by_default(self):
        quiet = self.tone(0.01)
        self.assertIs(speech.raise_level(quiet, float("nan")), quiet)

    def test_a_quiet_voice_is_raised_toward_the_target(self):
        raised = speech.raise_level(self.tone(0.01), -20.0)
        self.assertAlmostEqual(speech.loudest_dbfs(raised), -20.0, delta=0.5)

    def test_the_gain_is_capped_and_silence_is_left_alone(self):
        raised = speech.raise_level(self.tone(0.001), -10.0)
        self.assertAlmostEqual(
            speech.loudest_dbfs(raised) - speech.loudest_dbfs(self.tone(0.001)), 30.0, delta=0.5)
        silent = self.tone(0.00001)
        self.assertIs(speech.raise_level(silent, -20.0), silent)

    def test_a_loud_voice_is_never_lowered_nor_clipped(self):
        loud = self.tone(0.9)
        self.assertIs(speech.raise_level(loud, -20.0), loud)
        spiky = self.tone(0.01)
        spiky[100] = 0.5
        self.assertLessEqual(max(speech.raise_level(spiky, -10.0)), 0.99 + 1e-6)


class FakeWhisperModel:
    """Le strict nécessaire de `faster_whisper.WhisperModel`."""

    instances = []

    def __init__(self, model, device, compute_type):
        self.loaded = (model, device, compute_type)
        self.calls = []
        FakeWhisperModel.instances.append(self)

    def transcribe(self, audio, **options):
        self.calls.append(options)
        segment = types.SimpleNamespace(start=0.0, end=1.2, text=" La portance augmente.",
                                        avg_logprob=-0.21)
        return iter([segment]), types.SimpleNamespace(language="fr")


class FasterWhisperEngineTests(unittest.TestCase):
    def setUp(self):
        fake_numpy = types.SimpleNamespace(
            float32="float32", frombuffer=lambda data, dtype: ("pcm", len(data)))
        fake_whisper = types.SimpleNamespace(__version__="1.test", WhisperModel=FakeWhisperModel)
        patcher = patch.dict(sys.modules, {"numpy": fake_numpy, "faster_whisper": fake_whisper})
        patcher.start()
        self.addCleanup(patcher.stop)
        FakeWhisperModel.instances.clear()

    def test_the_engine_is_chosen_by_the_environment(self):
        with patch.dict(os.environ, {"SPEECH_ENGINE": "faster_whisper",
                                     "SPEECH_MODEL": "large-v3-turbo",
                                     "SPEECH_COMPUTE_TYPE": "int8_float16"}):
            engine = speech.engine_from_environment()
        self.assertIsInstance(engine, speech.FasterWhisperEngine)
        engine.load()
        self.assertEqual(FakeWhisperModel.instances[0].loaded,
                         ("large-v3-turbo", "auto", "int8_float16"))

    def test_decoding_keeps_the_beam_and_the_temperature_fallback(self):
        # Le banc du RTX : faisceau 5 et repli de température, jamais une
        # température fixée à 0 comme derrière faster-whisper-server.
        engine = speech.FasterWhisperEngine("large-v3-turbo")
        engine.load()
        transcript = engine.transcribe(speech.pcm16_to_float(bytes(32000), 1), "fr")
        options = FakeWhisperModel.instances[0].calls[0]
        self.assertEqual(options["beam_size"], 5)
        self.assertEqual(options["language"], "fr")
        self.assertNotIn("temperature", options)
        self.assertEqual(transcript.text, "La portance augmente.")

    def test_the_confidence_reaches_the_nova_server(self):
        engine = speech.FasterWhisperEngine("large-v3-turbo")
        engine.load()
        client = TestClient(speech.create_app(engine))
        body = client.post("/v1/audio/transcriptions", files={"file": ("a.wav", wav(1.0))},
                           data={"response_format": "verbose_json", "language": "fr"}).json()
        self.assertAlmostEqual(ai_runtime.segments_confidence(body["segments"]), -0.21)

    def test_transcribe_cpp_stays_the_default(self):
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("SPEECH_ENGINE", None)
            self.assertIsInstance(speech.engine_from_environment(), speech.TranscribeCppEngine)


class UnsupportedRequest(Exception):
    """Le nom de l'erreur de transcribe_cpp quand un modèle refuse une option."""


class FakeGgufSession:
    def __init__(self, calls, refuse_segments):
        self.calls, self.refuse_segments = calls, refuse_segments

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def run(self, pcm, timestamps, language):
        self.calls.append(timestamps)
        if timestamps == "segment" and self.refuse_segments:
            raise UnsupportedRequest("unsupported timestamp granularity")
        return types.SimpleNamespace(text=" La portance augmente.", language=language,
                                     segments=[])


class TranscribeCppEngineTests(unittest.TestCase):
    """Mesuré sur la carte du RTX le 11/10 : Cohere refuse les horodatages par
    passage, et le service répondait 502 à toutes les dictées."""

    def engine(self, refuse_segments):
        engine = speech.TranscribeCppEngine("/models/speech.gguf")
        calls = []
        engine._model = types.SimpleNamespace(
            session=lambda: FakeGgufSession(calls, refuse_segments))
        return engine, calls

    def test_a_model_without_segment_timestamps_still_transcribes(self):
        engine, calls = self.engine(refuse_segments=True)
        pcm = speech.pcm16_to_float(bytes(3200), 1)
        self.assertEqual(engine.transcribe(pcm, "fr").text, "La portance augmente.")
        self.assertEqual(engine.transcribe(pcm, "fr").text, "La portance augmente.")
        # Le refus n'est payé qu'une fois.
        self.assertEqual(calls, ["segment", "none", "none"])

    def test_a_model_with_segments_keeps_them(self):
        engine, calls = self.engine(refuse_segments=False)
        engine.transcribe(speech.pcm16_to_float(bytes(3200), 1), "fr")
        self.assertEqual(calls, ["segment"])

    def test_each_passage_gets_its_confidence_from_its_tokens(self):
        token = lambda text, p: types.SimpleNamespace(text=text, p=p)  # noqa: E731
        tokens = (token("[_BEG_]", 0.01), token(" La", 0.9), token(" portance", 0.5),
                  token(" Merci", 0.1), token("<|endoftext|>", 0.01))
        self.assertAlmostEqual(speech.average_logprob(tokens[:3]),
                               (math.log(0.9) + math.log(0.5)) / 2)
        self.assertIsNone(speech.average_logprob(tokens[:1]))
        segments = [types.SimpleNamespace(text=" La portance", t0_ms=0, t1_ms=900,
                                          first_token=0, n_tokens=3),
                    types.SimpleNamespace(text=" Merci", t0_ms=900, t1_ms=1200,
                                          first_token=3, n_tokens=2)]
        engine = speech.TranscribeCppEngine("/models/speech.gguf")
        session = FakeGgufSession([], refuse_segments=False)
        session.run = lambda pcm, timestamps, language: types.SimpleNamespace(
            text=" La portance Merci", language="fr", segments=segments, tokens=tokens)
        engine._model = types.SimpleNamespace(session=lambda: session)
        found = engine.transcribe(speech.pcm16_to_float(bytes(3200), 1), "fr").segments
        self.assertAlmostEqual(found[0].avg_logprob, (math.log(0.9) + math.log(0.5)) / 2)
        self.assertAlmostEqual(found[1].avg_logprob, math.log(0.1))


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
