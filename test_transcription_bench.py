"""Le banc de transcription : un juge juste, et un tour complet sur un faux moteur."""

import asyncio
import json
import os
import sys
import tempfile
import unittest
from unittest.mock import patch

from test_main import AdminTestCase  # noqa: F401

import main
from ai_runtime import SpeechInferenceResult

main.init_db()
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "scripts"))
import bench_transcription  # noqa: E402


class TranscriptionBenchTests(unittest.TestCase):
    def test_the_judge_counts_word_errors(self):
        self.assertEqual(bench_transcription.wer("un triangle rectangle", "un triangle rectangle"), 0)
        self.assertAlmostEqual(
            bench_transcription.wer("un triangle rectangle", "un train grec tendu"), 1.0)
        self.assertEqual(bench_transcription.wer("L’hypoténuse.", "l'hypoténuse"), 0)

    def test_both_languages_are_measured_on_the_real_router_path(self):
        seen = []

        async def engine(task_type, audio, filename, **options):
            seen.append(options.get("language"))
            text = "Question" if options.get("language") else "Mm-hmm"
            return SpeechInferenceResult(text=text, language="fr", duration_seconds=1.0,
                                         latency_ms=1, backend={})

        with tempfile.TemporaryDirectory() as folder:
            open(os.path.join(folder, "a.wav"), "wb").write(b"RIFF")
            json.dump({"a.wav": "Question."}, open(os.path.join(folder, "attendu.json"), "w"))
            with patch.object(main.ORGANIZATION_AI_ROUTER, "transcribe", engine):
                totals = asyncio.run(bench_transcription.measure(folder, "fr"))
        self.assertEqual(seen, [None, "fr"])
        self.assertEqual(totals["langue imposée (fr)"], 0)
        self.assertEqual(totals["langue devinée"], 1.0)


if __name__ == "__main__":
    unittest.main()
