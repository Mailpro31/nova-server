"""Aucune dictée ni adresse dans les journaux (5.6), avant d'ouvrir leur envoi
vers le panel (Loki).

Une phrase témoin traverse les vraies routes (mise au propre, commande), avec
un moteur externe en panne pour emprunter aussi les chemins d'erreur. Rien de
ce qu'elle contient, ni l'adresse de l'élève, ne doit apparaître dans une
seule ligne, tous journaux confondus, au niveau DEBUG.
"""

import io
import logging
import unittest
from unittest.mock import patch

from test_main import AdminTestCase

import ai_runtime
import main
import observability

main.init_db()

EMAIL = "temoin.eleve@example.com"
WITNESS = "zorglub quantique du mardi"


class FailingBackend(ai_runtime.FakeTextInferenceBackend):
    async def generate(self, request):
        raise ai_runtime.AIRuntimeError(ai_runtime.AI_RUNTIME_UNAVAILABLE)


class EchoBackend(ai_runtime.FakeTextInferenceBackend):
    async def generate(self, request):
        return ai_runtime.TextInferenceResult(
            text=f"{WITNESS.capitalize()}.", finish_reason="stop", latency_ms=1,
            usage={}, backend={"adapter": "fake_text"})


class StructuredRedactionTests(unittest.TestCase):
    def render(self, **fields) -> str:
        record = logging.LogRecord("nova", logging.INFO, __file__, 1, "event", None, None)
        record.fields = fields
        return observability.StructuredFormatter().format(record)

    def test_nested_fields_are_redacted_too(self):
        line = self.render(details={"text": WITNESS, "count": 2})
        self.assertNotIn(WITNESS, line)
        self.assertIn('"count": 2', line)

    def test_dictation_fields_are_redacted(self):
        for key in ("transcription", "instruction", "input", "output", "answer", "vocabulary"):
            with self.subTest(key):
                self.assertNotIn(WITNESS, self.render(**{key: WITNESS}))

    def test_an_address_inside_any_value_is_hidden(self):
        line = self.render(reason=f"refus pour {EMAIL}")
        self.assertNotIn(EMAIL, line)
        self.assertIn("refus pour", line)

    def test_a_custom_style_id_never_reaches_the_journal(self):
        self.assertIn('"style": "custom"', self.render(style="mon_style_maths_de_lea"))
        self.assertIn('"style": "nova_style_messages"', self.render(style="nova_style_messages"))


class EndToEndLogTests(AdminTestCase):
    def setUp(self):
        super().setUp()
        self.buffer = io.StringIO()
        handler = logging.StreamHandler(self.buffer)
        handler.setFormatter(observability.StructuredFormatter())
        root = logging.getLogger()
        previous = root.level
        root.addHandler(handler)
        root.setLevel(logging.DEBUG)
        nova = observability.configure_logging("DEBUG", stream=self.buffer)
        nova.propagate = False
        self.addCleanup(root.removeHandler, handler)
        self.addCleanup(root.setLevel, previous)
        self.addCleanup(observability.configure_logging, "INFO")
        router = ai_runtime.OrganizationAIRouter(
            FailingBackend(), ai_runtime.FakeSpeechInferenceBackend(),
            fallback_text_backend=EchoBackend())
        patcher = patch.object(main, "ORGANIZATION_AI_ROUTER", router)
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_no_dictation_and_no_address_reach_any_log(self):
        token = self.sign_in(EMAIL)
        rewrite = self.client.post(
            "/api/reformulate", headers=self.auth(token),
            json={"text": WITNESS, "style_id": "nova_style_messages", "style_prompt": "Court."})
        self.assertEqual(rewrite.status_code, 200, rewrite.text)
        self.client.post("/api/command", headers=self.auth(token),
                         json={"instruction": "Corrige", "text": WITNESS})
        logged = self.buffer.getvalue()
        self.assertTrue(logged, "le test doit voir des journaux")
        for word in (*WITNESS.split()[:2], EMAIL):
            self.assertNotIn(word, logged)


if __name__ == "__main__":
    unittest.main()
