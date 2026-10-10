"""Un enregistrement presque muet n'est pas envoyé au moteur.

Whisper écrit des crédits de sous-titres sur du silence (05/10), Canary a
inventé « Je ne sais pas. » sur un « euh » (banc du RTX, 11/10), et Cohere
Transcribe, candidat au remplacement de Whisper, ne rend pas de confiance
(`avg_logprob`) qui permettrait de repérer ces sorties. Un silence ne doit
jamais devenir du texte : le serveur le reconnaît avant le moteur.
"""

import io
import math
import struct
import unittest
import wave
from unittest.mock import patch

from test_main import AdminTestCase

import main
from ai_runtime import SpeechInferenceResult

main.init_db()


def tone(dbfs: float, seconds: float = 1.0, rate: int = 16000) -> bytes:
    """Un son de 440 Hz dont le niveau efficace vaut `dbfs`."""
    amplitude = 32767 * math.sqrt(2) * 10 ** (dbfs / 20)
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as out:
        out.setnchannels(1)
        out.setsampwidth(2)
        out.setframerate(rate)
        out.writeframes(b"".join(
            struct.pack("<h", max(-32768, min(32767, round(amplitude * math.sin(2 * math.pi * 440 * i / rate)))))
            for i in range(int(rate * seconds))))
    return buffer.getvalue()


class LevelTests(unittest.TestCase):
    def test_the_loudest_moment_is_measured(self):
        self.assertAlmostEqual(main.wav_peak_dbfs(tone(-20)), -20, delta=0.5)
        self.assertAlmostEqual(main.wav_peak_dbfs(tone(-45)), -45, delta=0.5)
        self.assertEqual(main.wav_peak_dbfs(tone(-200)), -math.inf)

    def test_what_is_not_a_readable_wav_is_not_judged(self):
        for audio in (b"RIFF0000WAVE", b"OggS" + bytes(40), b""):
            with self.subTest(audio=audio[:8]):
                self.assertIsNone(main.wav_peak_dbfs(audio))


class SilenceRouteTests(AdminTestCase):
    def setUp(self):
        super().setUp()
        self.token = self.sign_in("etudiant@example.com")
        self.engine_called = False

    def dictate(self, audio: bytes):
        async def transcribe(task_type, audio, filename, **options):
            self.engine_called = True
            return SpeechInferenceResult(text="Sous-titres réalisés par la communauté.",
                                         language="fr", duration_seconds=1.0, latency_ms=1,
                                         backend={}, avg_logprob=None)

        with patch.object(main.ORGANIZATION_AI_ROUTER, "transcribe", transcribe):
            response = self.client.post(
                "/api/transcribe?language=fr", headers=self.auth(self.token),
                files={"file": ("recording.wav", audio, "audio/wav")})
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()

    def test_a_silent_recording_never_reaches_the_engine(self):
        for audio in (tone(-200), tone(-70)):
            with self.subTest(audio=len(audio)):
                self.engine_called = False
                self.assertEqual(self.dictate(audio), {"text": "", "unclear": True})
                self.assertFalse(self.engine_called)

    def test_a_quiet_voice_still_reaches_the_engine(self):
        # Une voix basse, en classe, reste bien au-dessus du seuil.
        self.dictate(tone(-45))
        self.assertTrue(self.engine_called)


if __name__ == "__main__":
    unittest.main()
