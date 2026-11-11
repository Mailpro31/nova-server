"""Gemini via Vertex AI (3.1) : le jeton du compte de service de l'école.

Le fichier de clé vit dans la configuration du serveur, jamais dans la
console. Le serveur en tire un jeton d'une heure, le garde, et le renouvelle
avant qu'il expire.
"""

import asyncio
import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import httpx
import jwt
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa

import ai_runtime
import vertex_auth


def service_account_file(directory: str) -> tuple[Path, object]:
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    pem = key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                            serialization.NoEncryption()).decode()
    path = Path(directory) / "sa.json"
    path.write_text(json.dumps({
        "type": "service_account", "client_email": "nova@nova-ipsa.iam.gserviceaccount.com",
        "private_key": pem, "token_uri": "https://oauth2.googleapis.com/token"}))
    return path, key.public_key()


class VertexTokenTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.path, self.public_key = service_account_file(self.directory.name)
        self.assertions = []

        def google(request: httpx.Request) -> httpx.Response:
            form = dict(httpx.QueryParams(request.content.decode()))
            self.assertions.append(form["assertion"])
            return httpx.Response(200, json={"access_token": f"ya29.{len(self.assertions)}",
                                             "expires_in": 3600})

        self.transport = httpx.MockTransport(google)

    def test_the_signed_request_names_the_account_and_the_scope(self):
        token = vertex_auth.ServiceAccountToken(self.path, transport=self.transport)
        self.assertEqual(asyncio.run(token.bearer()), "ya29.1")
        claims = jwt.decode(self.assertions[0], self.public_key, algorithms=["RS256"],
                            audience="https://oauth2.googleapis.com/token")
        self.assertEqual(claims["iss"], "nova@nova-ipsa.iam.gserviceaccount.com")
        self.assertEqual(claims["scope"], "https://www.googleapis.com/auth/cloud-platform")

    def test_the_token_is_kept_then_renewed_before_it_expires(self):
        token = vertex_auth.ServiceAccountToken(self.path, transport=self.transport)
        with mock.patch.object(vertex_auth.time, "time", return_value=1000.0):
            self.assertEqual(asyncio.run(token.bearer()), "ya29.1")
            self.assertEqual(asyncio.run(token.bearer()), "ya29.1")
        # Cinq minutes avant l'heure, un nouveau jeton.
        with mock.patch.object(vertex_auth.time, "time", return_value=1000.0 + 3600 - 200):
            self.assertEqual(asyncio.run(token.bearer()), "ya29.2")

    def test_a_refused_key_is_an_unavailable_engine_not_a_crash(self):
        refused = httpx.MockTransport(lambda request: httpx.Response(400, json={"error": "invalid_grant"}))
        token = vertex_auth.ServiceAccountToken(self.path, transport=refused)
        with self.assertRaises(ai_runtime.AIRuntimeError) as raised:
            asyncio.run(token.bearer())
        self.assertEqual(raised.exception.code, ai_runtime.AI_RUNTIME_UNAVAILABLE)

    def test_the_text_engine_sends_the_vertex_token(self):
        token = vertex_auth.ServiceAccountToken(self.path, transport=self.transport)
        seen = []

        def vertex(request: httpx.Request) -> httpx.Response:
            seen.append(request.headers.get("authorization"))
            return httpx.Response(200, json={"choices": [{"message": {"content": "ok"},
                                                          "finish_reason": "stop"}]})

        with mock.patch.dict("os.environ", {"NOVA_AI_ALLOW_PUBLIC_ENDPOINT": "1"}):
            backend = ai_runtime.OpenAICompatibleTextBackend(
                "https://europe-west1-aiplatform.googleapis.com/v1/projects/nova-ipsa/"
                "locations/europe-west1/endpoints/openapi",
                "google/gemini-2.5-flash-lite", token=token,
                transport=httpx.MockTransport(vertex))
        request = ai_runtime.TextInferenceRequest(
            "r1", ai_runtime.TaskType.REFORMULATION, "texte", "consigne",
            ai_runtime.TextInferenceOptions(64, 0.0, ai_runtime.ReasoningMode.OFF), 5.0)
        self.assertEqual(asyncio.run(backend.generate(request)).text, "ok")
        # Chaque requête porte le jeton ; Google n'est sollicité qu'une fois.
        self.assertEqual(set(seen), {"Bearer ya29.1"})
        self.assertEqual(len(self.assertions), 1)

    def vertex_backend(self, transport, token_transport=None):
        token = vertex_auth.ServiceAccountToken(self.path, transport=token_transport or self.transport)
        with mock.patch.dict("os.environ", {"NOVA_AI_ALLOW_PUBLIC_ENDPOINT": "1"}):
            return ai_runtime.OpenAICompatibleTextBackend(
                "https://aiplatform.eu.rep.googleapis.com/v1/projects/novaecole/"
                "locations/eu/endpoints/openapi",
                "google/gemini-3.5-flash", token=token, transport=transport)

    def test_vertex_without_a_models_route_is_ready_once_the_token_is_obtained(self):
        # PC fixe, 11/11 : GET /models rend 404 sur Vertex ; la console affichait
        # « Indisponible » alors que les dictées passaient.
        backend = self.vertex_backend(httpx.MockTransport(lambda request: httpx.Response(404)))
        health = asyncio.run(backend.health())
        self.assertEqual((health.status, health.model),
                         (ai_runtime.EngineStatus.READY, "google/gemini-3.5-flash"))

    def test_a_refused_vertex_key_makes_the_health_unavailable_not_a_crash(self):
        refused = httpx.MockTransport(lambda request: httpx.Response(400, json={"error": "invalid_grant"}))
        backend = self.vertex_backend(httpx.MockTransport(lambda request: httpx.Response(404)), refused)
        self.assertEqual(asyncio.run(backend.health()).status, ai_runtime.EngineStatus.UNAVAILABLE)

    def test_the_environment_uses_the_key_file_for_the_external_engines(self):
        env = {"NOVA_VERTEX_CREDENTIALS_FILE": str(self.path)}
        with mock.patch.dict("os.environ", env):
            self.assertIsInstance(vertex_auth.token_from_environment(), vertex_auth.ServiceAccountToken)
        with mock.patch.dict("os.environ", {}, clear=True):
            self.assertIsNone(vertex_auth.token_from_environment())


if __name__ == "__main__":
    unittest.main()
