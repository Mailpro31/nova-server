"""Un seul appel par dictée (n° 39) : l'audio et le Style partent ensemble.

`/api/dictate` enchaîne `/api/transcribe` puis `/api/reformulate` sur le
serveur : mêmes quotas, mêmes garde-fous, un aller-retour de moins pour le
poste. Il rend `relue` (la transcription) et `final` (le texte du Style). Une
erreur du Style ne perd jamais la dictée : `final` est alors la transcription.
"""

from unittest.mock import patch

from test_main import AdminTestCase
from test_dictation_language import engine_saying

import main

main.init_db()

STYLE = {"style_id": "default_improve_transcriptions", "style_prompt": "Corrige le texte."}


class DictateSingleCallTests(AdminTestCase):
    def setUp(self):
        super().setUp()
        self.token = self.sign_in("etudiant@example.com")
        self.seen_texts: list = []

    async def style_engine(self, task_type, instruction, text):
        self.seen_texts.append(text)
        return "Je révise la dérivée d'une constante."

    def dictate(self, heard: str, style=STYLE):
        with patch.object(main.ORGANIZATION_AI_ROUTER, "transcribe", engine_saying(heard, [])), \
                patch.object(main, "execute_text", self.style_engine):
            response = self.client.post(
                "/api/dictate?language=fr", headers=self.auth(self.token), data=style,
                files={"file": ("recording.wav", b"RIFF0000WAVE", "audio/wav")})
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()

    def reformulate(self, text: str):
        with patch.object(main, "execute_text", self.style_engine):
            response = self.client.post(
                "/api/reformulate", headers=self.auth(self.token), json={"text": text, **STYLE})
        return response.json()["text"]

    def test_one_call_gives_the_same_text_as_two(self):
        heard = "je révise la dérivée d'une constante"
        body = self.dictate(heard)
        self.assertEqual(body["relue"], heard)
        self.assertEqual(len(self.seen_texts), 1)
        self.assertIn(heard, self.seen_texts[0])
        self.assertEqual(body["final"], self.reformulate(heard))

    def test_an_exam_returns_the_dictation_untouched(self):
        heard = "je révise la dérivée d'une constante"
        with patch.object(main, "exam_active", lambda settings: True):
            body = self.dictate(heard)
        self.assertEqual((body["final"], body.get("exam")), (heard, True))
        self.assertEqual(self.seen_texts, [])

    def test_an_empty_dictation_never_reaches_the_style(self):
        body = self.dictate("Euh...")
        self.assertEqual((body["relue"], body["final"]), ("", ""))
        self.assertEqual(self.seen_texts, [])

    def test_a_style_error_keeps_the_dictation(self):
        heard = "je révise la dérivée d'une constante"
        body = self.dictate(heard, {"style_id": main.ORGANIZATION_STYLE_PREFIX + "inconnu"})
        self.assertEqual(body["final"], heard)
        self.assertEqual(body["style_error"], "STYLE_NOT_AVAILABLE")


class FakeCombinedRefiner:
    """La relecture audio qui rend aussi le Style (n° 39) : un seul appel."""

    def __init__(self, relue: str, final: str | None):
        self.answer = (relue, final)
        self.prompts: list = []

    async def refine(self, audio, audio_format, transcript, vocabulary):
        return self.answer[0]

    async def refine_with_style(self, audio, audio_format, transcript, vocabulary, style_prompt):
        self.prompts.append(style_prompt)
        return self.answer


class OneGeminiCallTests(DictateSingleCallTests):
    """Avec la relecture audio, la même réponse Gemini donne la relue et le Style."""

    heard = "je revise la derivee d'une constante"
    relue = "je révise la dérivée d'une constante"

    def combined(self, final: str | None):
        refiner = FakeCombinedRefiner(self.relue, final)
        with patch.object(main, "AUDIO_REFINER", refiner):
            return self.dictate(self.heard), refiner

    def test_the_style_comes_from_the_same_gemini_call(self):
        body, refiner = self.combined("Je révise la dérivée d'une constante.")
        self.assertEqual(body["relue"], self.relue)
        self.assertEqual(body["final"], "Je révise la dérivée d'une constante.")
        self.assertEqual(self.seen_texts, [], "aucun second appel pour le Style")
        self.assertIn("Corrige le texte.", refiner.prompts[0])

    def test_a_refused_style_falls_back_to_the_separate_call(self):
        # Le 15 disparaît : les contrôles refusent, le Style est redemandé seul.
        self.relue = "rendez-vous à 15 heures en salle A112"
        body, _ = self.combined("Rendez-vous en salle.")
        self.assertEqual(len(self.seen_texts), 1)
        self.assertNotEqual(body["final"], "Rendez-vous en salle.")

    def test_without_a_style_answer_the_separate_call_runs(self):
        body, _ = self.combined(None)
        self.assertEqual(len(self.seen_texts), 1)
        self.assertEqual(body["final"], self.reformulate(self.relue))

    @staticmethod
    def forget_policies():
        with main.db() as conn:
            conn.execute("DELETE FROM organization_policies")

    def test_an_exam_never_asks_gemini_for_the_style(self):
        """En examen, ni relecture ni Style : Gemini n'est jamais appelé."""
        with patch.object(main, "exam_active", lambda settings: True):
            body, refiner = self.combined("Je révise la dérivée d'une constante.")
        self.assertEqual((body["relue"], body["final"], body.get("exam")),
                         (self.heard, self.heard, True))
        self.assertEqual(refiner.prompts, [])

    def test_the_daily_cap_never_asks_gemini_for_the_style(self):
        self.forget_policies()
        with main.db() as conn:
            conn.execute(
                "INSERT INTO organization_policies(organization_id, schema_version, "
                "revision, settings, updated_at, updated_by_user_id) VALUES (?,?,?,?,?,?)",
                (main.nova_organization_id(), main.POLICY_SCHEMA_VERSION, 1,
                 '{"daily_rewrites_per_member": 1}', 0.0, None))
        self.addCleanup(self.forget_policies)
        with patch.object(main, "rewrites_today", lambda user: 1):
            body, refiner = self.combined("Je révise la dérivée d'une constante.")
        self.assertEqual((body["final"], body.get("capped")), (self.relue, True))
        self.assertEqual(refiner.prompts, [])

    def test_plain_transcription_never_asks_for_a_style(self):
        refiner = FakeCombinedRefiner(self.relue, "x")
        with patch.object(main, "AUDIO_REFINER", refiner), \
                patch.object(main.ORGANIZATION_AI_ROUTER, "transcribe", engine_saying(self.heard, [])):
            response = self.client.post(
                "/api/transcribe?language=fr", headers=self.auth(self.token),
                files={"file": ("recording.wav", b"RIFF0000WAVE", "audio/wav")})
        self.assertEqual(response.json()["text"], self.relue)
        self.assertEqual(refiner.prompts, [])


class RelueFinalParsingTests(AdminTestCase):
    def test_json_with_or_without_fences(self):
        from ai_runtime import relue_and_final

        self.assertEqual(relue_and_final('{"relue": " a ", "final": "A."}'), ("a", "A."))
        self.assertEqual(relue_and_final('```json\n{"relue": "a", "final": "A."}\n```'), ("a", "A."))
        self.assertEqual(relue_and_final('{"relue": "a"}'), ("a", None))

    def test_anything_else_is_a_runtime_error(self):
        from ai_runtime import AIRuntimeError, relue_and_final

        for answer in ("a", '{"final": "A."}', "[1]", '{"relue": 3}'):
            with self.subTest(answer=answer), self.assertRaises(AIRuntimeError):
                relue_and_final(answer)
