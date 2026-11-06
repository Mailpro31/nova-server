"""Le mode examen (1.8) : un groupe n'a plus que la dictée, jusqu'à une heure.

Pendant l'examen, toutes les fonctions d'IA, la bulle, l'historique et l'import
sont fermés pour les membres du groupe, et la dictée revient telle que Whisper
l'a écrite : aucune mise au propre, aucune relecture. Le vocabulaire reste, il
corrige l'orthographe des mots du cours sans rien écrire à la place de l'élève.
À l'heure prévue, tout rouvre sans que personne n'ait à revenir dans la console.
"""

import time
import unittest
from unittest.mock import AsyncMock, patch

from test_group_policies import GroupPolicyTestCase, create_group  # noqa: F401

import main
from ai_runtime import SpeechInferenceResult

main.init_db()

KEY = "exam_until"
INSIDE, OUTSIDE = "candidat@example.com", "libre@example.com"


class ExamSchemaTests(unittest.TestCase):
    def test_no_exam_by_default(self):
        self.assertEqual(main.DEFAULT_ORGANIZATION_POLICY[KEY], 0)
        self.assertFalse(main.exam_active(main.DEFAULT_ORGANIZATION_POLICY))

    def test_the_latest_end_wins_and_a_group_never_cancels_the_school(self):
        later, sooner = time.time() + 7200, time.time() + 3600
        resolved = main.resolve_group_policy_settings(
            {**main.DEFAULT_ORGANIZATION_POLICY, KEY: int(later)}, [{KEY: 0}, {KEY: int(sooner)}])
        self.assertEqual(resolved[KEY], int(later))


class ExamModeTests(GroupPolicyTestCase):
    def setUp(self):
        super().setUp()
        self.inside, self.outside = self.member(INSIDE), self.member(OUTSIDE)
        self.group = create_group("Partiel de mécanique", [INSIDE])
        self.until = int(time.time()) + 3600
        response = self.set_group(self.group, {KEY: self.until})
        self.assertEqual(response.status_code, 200, response.text)

    def test_only_dictation_is_left_to_the_group(self):
        me = self.client.get("/api/me", headers=self.inside).json()
        self.assertEqual(me["exam"], {"until": self.until})
        for closed in ("styles", "history", "prompts", "learning", "meeting"):
            self.assertIn(closed, me["closed_capabilities"])
        capabilities = self.client.get(
            "/api/organization/policy", headers=self.inside).json()["effective_capabilities"]
        for closed in ("commands", "selectionBubble", "aiSkills", "fileTranscription",
                       "engineeringNotes"):
            self.assertFalse(capabilities[closed], closed)
        self.assertTrue(capabilities["dictionary"])

    def test_the_rest_of_the_school_keeps_everything(self):
        me = self.client.get("/api/me", headers=self.outside).json()
        self.assertEqual(me["exam"], {"until": 0})
        self.assertNotIn("styles", me["closed_capabilities"])

    def test_a_dictation_comes_back_untouched(self):
        response = self.client.post("/api/reformulate", headers=self.inside,
                                    json={"text": "la poussée d'archimède"})
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json(), {"text": "la poussée d'archimède", "exam": True})

    def test_the_audio_is_not_read_again(self):
        async def engine(task_type, audio, filename, **options):
            return SpeechInferenceResult(text="la portance", language="fr",
                                         duration_seconds=1.0, latency_ms=1,
                                         backend={"adapter": "test"})

        refine = AsyncMock(return_value="la portance relue")
        with patch.object(main.ORGANIZATION_AI_ROUTER, "transcribe", engine), \
                patch.object(main, "refined_transcription", refine):
            response = self.client.post(
                "/api/transcribe", headers=self.inside,
                files={"file": ("recording.wav", b"RIFFaudio", "audio/wav")})
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["text"], "la portance")
        refine.assert_not_awaited()

    def test_everything_reopens_at_the_planned_time(self):
        during = self.client.get("/api/organization/changes", headers=self.inside).json()
        with patch.object(main.time, "time", return_value=self.until + 1):
            after = self.client.get("/api/organization/changes", headers=self.inside).json()
            me = self.client.get("/api/me", headers=self.inside).json()
        # Le repère bouge seul : le poste recharge sa policy sans écriture.
        self.assertNotEqual(after["policy_revision"], during["policy_revision"])
        self.assertEqual(me["exam"], {"until": 0})
        self.assertNotIn("styles", me["closed_capabilities"])

    def test_stopping_the_exam_early_moves_the_marker_too(self):
        during = self.client.get("/api/organization/changes", headers=self.inside).json()
        self.assertEqual(self.set_group(self.group, {}, revision=1).status_code, 200)
        after = self.client.get("/api/organization/changes", headers=self.inside).json()
        self.assertNotEqual(after["policy_revision"], during["policy_revision"])


if __name__ == "__main__":
    unittest.main()
