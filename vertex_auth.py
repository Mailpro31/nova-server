"""Le jeton d'accès à Vertex AI (3.1), tiré du compte de service de l'école.

Vertex AI répond au format OpenAI (`…/endpoints/openapi/chat/completions`) :
les moteurs existants s'en servent tels quels, seule la clé change. Elle n'est
pas fixe : un jeton OAuth d'une heure, obtenu en signant une demande avec la
clé privée du compte de service (RFC 7523). PyJWT suffit, sans la
bibliothèque Google.

Le fichier de clé vient de la configuration du serveur
(`NOVA_VERTEX_CREDENTIALS_FILE`, secret Docker monté), jamais de la console.
"""

import asyncio
import json
import os
import time
from pathlib import Path

import httpx
import jwt

SCOPE = "https://www.googleapis.com/auth/cloud-platform"
TOKEN_URI = "https://oauth2.googleapis.com/token"
# Renouvelé cinq minutes avant l'heure : une dictée en cours ne tombe jamais
# sur un jeton expiré.
RENEW_BEFORE_SECONDS = 300


class ServiceAccountToken:
    def __init__(self, path: Path | str, *, transport: httpx.AsyncBaseTransport | None = None):
        self.path = Path(path)
        self.transport = transport
        self._token = ""
        self._expires_at = 0.0
        self._lock = asyncio.Lock()

    def _assertion(self, account: dict, now: float) -> str:
        claims = {"iss": account["client_email"], "scope": SCOPE,
                  "aud": account.get("token_uri", TOKEN_URI),
                  "iat": int(now), "exp": int(now) + 3600}
        return jwt.encode(claims, account["private_key"], algorithm="RS256")

    async def bearer(self) -> str:
        from ai_runtime import AI_RUNTIME_UNAVAILABLE, AIRuntimeError

        async with self._lock:
            now = time.time()
            if self._token and now < self._expires_at - RENEW_BEFORE_SECONDS:
                return self._token
            try:
                # Relu à chaque renouvellement : une clé remplacée prend effet
                # sans redémarrer le service.
                account = json.loads(self.path.read_text(encoding="utf-8"))
                async with httpx.AsyncClient(timeout=10, transport=self.transport) as client:
                    response = await client.post(account.get("token_uri", TOKEN_URI), data={
                        "grant_type": "urn:ietf:params:oauth:grant-type:jwt-bearer",
                        "assertion": self._assertion(account, now)})
                response.raise_for_status()
                body = response.json()
                self._token = str(body["access_token"])
                self._expires_at = now + float(body.get("expires_in", 3600))
            except (OSError, ValueError, KeyError, TypeError, AttributeError,
                    jwt.PyJWTError, httpx.HTTPError) as error:
                # Renouvelé en avance : une panne passagère de Google laisse
                # servir le jeton tant qu'il vaut encore.
                if self._token and now < self._expires_at:
                    return self._token
                # Une clé refusée, absente ou mal formée : le moteur externe est
                # indisponible, et le routeur passe au moteur local.
                raise AIRuntimeError(AI_RUNTIME_UNAVAILABLE) from error
            return self._token


def token_from_environment() -> ServiceAccountToken | None:
    path = os.getenv("NOVA_VERTEX_CREDENTIALS_FILE", "").strip()
    return ServiceAccountToken(path) if path else None
