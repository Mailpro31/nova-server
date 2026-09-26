"""Private, model-agnostic AI runtime contracts for Nova organizations.

This module is deliberately infrastructure-only. Product services compose
instructions and context; the router decides where and with which bounded
runtime settings they execute. It never logs request input or output.
"""

from __future__ import annotations

import asyncio
import ipaddress
import json
import os
import platform
import re
import socket
import subprocess
import time
import uuid
from dataclasses import asdict, dataclass
from enum import Enum
from pathlib import Path
from typing import Any, Protocol
from urllib.parse import urlparse

import httpx

import observability


class TaskType(str, Enum):
    REFORMULATION = "reformulation"
    STYLE_TRANSFORM = "style_transform"
    VOICE_COMMAND = "voice_command"
    AI_SKILL = "ai_skill"
    LEARNING_FEEDBACK = "learning_feedback"
    ENGINEERING_NOTES = "engineering_notes"
    TRANSCRIPTION_REALTIME = "transcription_realtime"
    TRANSCRIPTION_FILE = "transcription_file"


TEXT_TASKS = frozenset(
    {
        TaskType.REFORMULATION,
        TaskType.STYLE_TRANSFORM,
        TaskType.VOICE_COMMAND,
        TaskType.AI_SKILL,
        TaskType.LEARNING_FEEDBACK,
        TaskType.ENGINEERING_NOTES,
    }
)
SPEECH_TASKS = frozenset(
    {TaskType.TRANSCRIPTION_REALTIME, TaskType.TRANSCRIPTION_FILE}
)
# Les taches qui restituent un texte que l'utilisateur a lui-meme dicte. Elles
# n'ont rien a inventer : leur sortie est jugee sur sa fidelite, jamais sur sa
# variete. Le raisonnement leur est deja retire pour cette raison.
FIDELITY_TASKS = frozenset(
    {TaskType.REFORMULATION, TaskType.STYLE_TRANSFORM, TaskType.VOICE_COMMAND}
)


class ModelProfile(str, Enum):
    EFFICIENT = "efficient"
    STANDARD = "standard"
    ADVANCED = "advanced"
    CUSTOM = "custom"


class ReasoningMode(str, Enum):
    OFF = "off"
    AUTO = "auto"
    ON = "on"


class RuntimeStatus(str, Enum):
    UNAVAILABLE = "unavailable"
    STARTING = "starting"
    HEALTHY = "healthy"
    DEGRADED = "degraded"


class EngineStatus(str, Enum):
    READY = "ready"
    LOADING = "loading"
    UNAVAILABLE = "unavailable"


AI_RUNTIME_UNAVAILABLE = "AI_RUNTIME_UNAVAILABLE"
AI_RUNTIME_STARTING = "AI_RUNTIME_STARTING"
AI_RUNTIME_BUSY = "AI_RUNTIME_BUSY"
AI_RUNTIME_TIMEOUT = "AI_RUNTIME_TIMEOUT"
AI_RUNTIME_FAILED = "AI_RUNTIME_FAILED"
AI_MODEL_NOT_READY = "AI_MODEL_NOT_READY"
AI_RUNTIME_CONFIG_INVALID = "AI_RUNTIME_CONFIG_INVALID"
AI_PROMPT_TOO_LONG = "AI_PROMPT_TOO_LONG"

# Un moteur borne `prompt + max_tokens` par sa fenetre. Demander la sortie
# du profil sans regarder ce que le prompt a deja consomme produit une
# requete invalide que le moteur refuse en 400, quelle que soit sa taille.
MIN_OUTPUT_TOKENS = 64
CONTEXT_SAFETY_MARGIN_TOKENS = 8

# Repli utilise seulement si le moteur n'expose pas `/tokenize`. Il doit
# surestimer le cout du prompt : sous-estimer rendrait la requete invalide,
# ce que ce correctif existe precisement pour empecher.
_FALLBACK_CHARS_PER_TOKEN = 2.0
_FALLBACK_MESSAGE_OVERHEAD_TOKENS = 8
# Combien de temps une requete accepte d'attendre sa place avant d'etre refusee.
#
# Une seconde -- la valeur d'origine -- refusait une requete que le budget de
# l'utilisateur laissait tout le temps de servir : sur un profil a une seule
# place, la seconde personne qui dicte en meme temps recevait AI_RUNTIME_BUSY
# apres une seconde, et son poste retombait sur le moteur local. La qualite
# changeait sans qu'elle ait rien fait, et sans que rien ne le lui dise.
#
# Trois secondes laissent servir la quasi-totalite des attentes reelles tout en
# gardant deux secondes de marge sous l'objectif de cinq secondes de bout en
# bout. Le plafond existe pour qu'un deploiement ne puisse pas transformer la
# porte en salle d'attente sans fin : au-dela, mieux vaut un refus franc, que le
# poste sait traiter, qu'une requete qui pend.
DEFAULT_QUEUE_WAIT_SECONDS = 3.0
MIN_QUEUE_WAIT_SECONDS = 0.05
MAX_QUEUE_WAIT_SECONDS = 15.0


class AIRuntimeError(RuntimeError):
    """An error safe to expose without host, model path, stack or content."""

    def __init__(self, code: str, *, retry_after: int | None = None):
        super().__init__(code)
        self.code = code
        self.retry_after = retry_after


@dataclass(frozen=True)
class TextInferenceOptions:
    max_output_tokens: int
    temperature: float
    reasoning: ReasoningMode


@dataclass(frozen=True)
class TextInferenceRequest:
    request_id: str
    task_type: TaskType
    input: str
    instruction: str
    options: TextInferenceOptions
    timeout_seconds: float


@dataclass(frozen=True)
class TextInferenceResult:
    text: str
    finish_reason: str
    latency_ms: int
    usage: dict[str, int]
    backend: dict[str, Any]


@dataclass(frozen=True)
class SpeechInferenceRequest:
    request_id: str
    task_type: TaskType
    audio: bytes
    filename: str
    language: str | None
    timestamps: bool
    timeout_seconds: float


@dataclass(frozen=True)
class SpeechInferenceResult:
    text: str
    language: str | None
    duration_seconds: float | None
    latency_ms: int
    backend: dict[str, Any]


@dataclass(frozen=True)
class EngineHealth:
    status: EngineStatus
    adapter: str
    latency_ms: int | None
    runtime_version: str | None = None
    model: str | None = None
    last_error_code: str | None = None


class TextInferenceBackend(Protocol):
    async def generate(self, request: TextInferenceRequest) -> TextInferenceResult: ...

    async def health(self) -> EngineHealth: ...


class SpeechInferenceBackend(Protocol):
    async def transcribe(
        self, request: SpeechInferenceRequest
    ) -> SpeechInferenceResult: ...

    async def health(self) -> EngineHealth: ...


def _safe_request_id(value: str | None) -> str:
    value = (value or "").strip()
    if value and len(value) <= 128 and all(c.isalnum() or c in "-_." for c in value):
        return value
    return str(uuid.uuid4())


def validate_private_runtime_url(value: str) -> str:
    """Accept only operator-controlled private/loopback HTTP destinations.

    Single-label names are allowed for Docker/service discovery. Public IPs,
    link-local metadata ranges, credentials, query strings and fragments fail
    closed. This policy is intentionally different from public OIDC discovery.
    """

    parsed = urlparse((value or "").strip())
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise AIRuntimeError(AI_RUNTIME_CONFIG_INVALID)
    if parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise AIRuntimeError(AI_RUNTIME_CONFIG_INVALID)
    host = parsed.hostname.rstrip(".").lower()
    if host in {"localhost", "host.docker.internal"} or host.endswith(".localhost"):
        return value.rstrip("/")

    def allowed_ip(raw: str) -> bool:
        address = ipaddress.ip_address(raw)
        return (address.is_private or address.is_loopback) and not address.is_link_local

    try:
        if allowed_ip(host):
            return value.rstrip("/")
    except ValueError:
        pass

    # Docker Compose and internal service discovery commonly use one label.
    if "." not in host:
        return value.rstrip("/")

    try:
        addresses = {item[4][0] for item in socket.getaddrinfo(host, parsed.port)}
    except OSError as error:
        raise AIRuntimeError(AI_RUNTIME_CONFIG_INVALID) from error
    if not addresses or not all(allowed_ip(address) for address in addresses):
        raise AIRuntimeError(AI_RUNTIME_CONFIG_INVALID)
    return value.rstrip("/")


def _auth_headers(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"} if token else {}


_ENGINE_ERROR_TYPE = re.compile(r"^[A-Za-z0-9_.-]{1,64}$")


def _log_engine_refusal(adapter: str, response: httpx.Response) -> None:
    """Code HTTP et type d'erreur du moteur, jamais son message.

    Sans cette ligne, un refus du moteur arrive au client en
    `AI_RUNTIME_FAILED` sans rien qui permette d'en trouver la cause.
    """
    error_type = None
    try:
        body = response.json()
        if isinstance(body, dict):
            error = body.get("error")
            candidate = error.get("type") if isinstance(error, dict) else None
            if isinstance(candidate, str) and _ENGINE_ERROR_TYPE.match(candidate):
                error_type = candidate
    except (ValueError, json.JSONDecodeError):
        error_type = None
    observability.log(
        "ai.engine_refused",
        level="error",
        adapter=adapter,
        status=response.status_code,
        error_type=error_type or "unknown",
    )


class OpenAICompatibleTextBackend:
    adapter_name = "openai_compatible"

    def __init__(
        self,
        base_url: str,
        model: str,
        *,
        token: str = "",
        health_timeout: float = 3.0,
        reasoning_supported: bool = False,
        transport: httpx.AsyncBaseTransport | None = None,
    ):
        self.base_url = validate_private_runtime_url(base_url) if base_url else ""
        self.model = model.strip()
        self.token = token
        self.health_timeout = health_timeout
        self.reasoning_supported = reasoning_supported
        self.transport = transport
        self._context_window: int | None = None

    @property
    def _engine_root(self) -> str:
        """Racine du moteur. `/tokenize` est un voisin de `/v1`, pas un enfant."""
        if self.base_url.endswith("/v1"):
            return self.base_url[:-3].rstrip("/")
        return self.base_url

    async def _tokenize(
        self, client: httpx.AsyncClient, messages: list[dict[str, str]]
    ) -> tuple[int | None, int | None]:
        """Cout reel du prompt, et fenetre si le moteur l'annonce ici.

        Retourne `(None, None)` si l'endpoint est absent ou illisible : c'est
        au repli conservateur de prendre le relais, jamais a une supposition.
        """
        try:
            response = await client.post(
                f"{self._engine_root}/tokenize",
                headers=_auth_headers(self.token),
                json={
                    "model": self.model,
                    "messages": messages,
                    "add_generation_prompt": True,
                },
            )
            if response.status_code != 200:
                return None, None
            body = response.json()
        except (httpx.HTTPError, ValueError, json.JSONDecodeError):
            return None, None
        count = body.get("count")
        window = body.get("max_model_len")
        return (
            count if isinstance(count, int) and count >= 0 else None,
            window if isinstance(window, int) and window > 0 else None,
        )

    async def _advertised_window(self, client: httpx.AsyncClient) -> int | None:
        """Fenetre annoncee par `/models`, mise en cache une fois connue."""
        if self._context_window is not None:
            return self._context_window
        try:
            response = await client.get(
                f"{self.base_url}/models", headers=_auth_headers(self.token)
            )
            if response.status_code != 200:
                return None
            body = response.json()
        except (httpx.HTTPError, ValueError, json.JSONDecodeError):
            return None
        for item in body.get("data", []):
            if not isinstance(item, dict):
                continue
            window = item.get("max_model_len")
            if item.get("id") == self.model and isinstance(window, int) and window > 0:
                self._context_window = window
                return window
        return None

    @staticmethod
    def _fallback_prompt_tokens(messages: list[dict[str, str]]) -> int:
        characters = sum(len(message.get("content") or "") for message in messages)
        return (
            int(characters / _FALLBACK_CHARS_PER_TOKEN)
            + _FALLBACK_MESSAGE_OVERHEAD_TOKENS * len(messages)
            + _FALLBACK_MESSAGE_OVERHEAD_TOKENS
        )

    async def _bounded_output_tokens(
        self,
        client: httpx.AsyncClient,
        messages: list[dict[str, str]],
        requested: int,
    ) -> int:
        """Sortie demandee, ramenee a ce que la fenetre laisse reellement.

        Sans fenetre connue, on n'invente rien : la valeur demandee part
        telle quelle, comme avant ce correctif.
        """
        prompt_tokens, window = await self._tokenize(client, messages)
        if prompt_tokens is None:
            prompt_tokens = self._fallback_prompt_tokens(messages)
        if window is None:
            window = await self._advertised_window(client)
        if window is None:
            return requested
        self._context_window = window
        remaining = window - prompt_tokens - CONTEXT_SAFETY_MARGIN_TOKENS
        if remaining < MIN_OUTPUT_TOKENS:
            raise AIRuntimeError(AI_PROMPT_TOO_LONG)
        return min(requested, remaining)

    async def generate(self, request: TextInferenceRequest) -> TextInferenceResult:
        if not self.base_url:
            raise AIRuntimeError(AI_RUNTIME_UNAVAILABLE)
        if not self.model:
            raise AIRuntimeError(AI_MODEL_NOT_READY)
        payload: dict[str, Any] = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": request.instruction},
                {"role": "user", "content": request.input},
            ],
            "temperature": request.options.temperature,
            "max_tokens": request.options.max_output_tokens,
            "stream": False,
        }
        if self.reasoning_supported and request.options.reasoning != ReasoningMode.OFF:
            payload["reasoning_effort"] = (
                "high" if request.options.reasoning == ReasoningMode.ON else "medium"
            )
        started = time.monotonic()
        try:
            async with httpx.AsyncClient(
                timeout=request.timeout_seconds, transport=self.transport
            ) as client:
                payload["max_tokens"] = await self._bounded_output_tokens(
                    client, payload["messages"], request.options.max_output_tokens
                )
                response = await client.post(
                    f"{self.base_url}/chat/completions",
                    headers=_auth_headers(self.token),
                    json=payload,
                )
        except httpx.TimeoutException as error:
            raise AIRuntimeError(AI_RUNTIME_TIMEOUT) from error
        except httpx.HTTPError as error:
            raise AIRuntimeError(AI_RUNTIME_UNAVAILABLE) from error
        if response.status_code in {408, 504}:
            raise AIRuntimeError(AI_RUNTIME_TIMEOUT)
        if response.status_code in {429, 503}:
            raise AIRuntimeError(AI_RUNTIME_BUSY, retry_after=1)
        if response.status_code == 425:
            raise AIRuntimeError(AI_RUNTIME_STARTING, retry_after=2)
        if response.status_code != 200:
            _log_engine_refusal(self.adapter_name, response)
            raise AIRuntimeError(AI_RUNTIME_FAILED)
        try:
            body = response.json()
            choice = body["choices"][0]
            text = choice["message"]["content"]
            if not isinstance(text, str):
                raise ValueError("text")
            raw_usage = body.get("usage") or {}
            usage = {
                key: int(raw_usage[key])
                for key in ("prompt_tokens", "completion_tokens", "total_tokens")
                if isinstance(raw_usage.get(key), int)
            }
            return TextInferenceResult(
                text=text,
                finish_reason=str(choice.get("finish_reason") or "unknown"),
                latency_ms=round((time.monotonic() - started) * 1000),
                usage=usage,
                backend={"adapter": self.adapter_name},
            )
        except (KeyError, IndexError, TypeError, ValueError, json.JSONDecodeError) as error:
            raise AIRuntimeError(AI_RUNTIME_FAILED) from error

    async def health(self) -> EngineHealth:
        if not self.base_url:
            return EngineHealth(EngineStatus.UNAVAILABLE, self.adapter_name, None)
        if not self.model:
            return EngineHealth(
                EngineStatus.UNAVAILABLE,
                self.adapter_name,
                None,
                last_error_code=AI_MODEL_NOT_READY,
            )
        started = time.monotonic()
        try:
            async with httpx.AsyncClient(
                timeout=self.health_timeout, transport=self.transport
            ) as client:
                response = await client.get(
                    f"{self.base_url}/models", headers=_auth_headers(self.token)
                )
            latency = round((time.monotonic() - started) * 1000)
            if response.status_code in {425, 503}:
                return EngineHealth(EngineStatus.LOADING, self.adapter_name, latency)
            if response.status_code != 200:
                return EngineHealth(EngineStatus.UNAVAILABLE, self.adapter_name, latency)
            body = response.json()
            identifiers = {
                item.get("id") for item in body.get("data", []) if isinstance(item, dict)
            }
            if identifiers and self.model not in identifiers:
                return EngineHealth(
                    EngineStatus.LOADING,
                    self.adapter_name,
                    latency,
                    model=self.model,
                    last_error_code=AI_MODEL_NOT_READY,
                )
            return EngineHealth(
                EngineStatus.READY,
                self.adapter_name,
                latency,
                runtime_version=response.headers.get("x-runtime-version"),
                model=self.model,
            )
        except (httpx.HTTPError, ValueError, json.JSONDecodeError):
            return EngineHealth(EngineStatus.UNAVAILABLE, self.adapter_name, None)


class WhisperCompatibleSpeechBackend:
    adapter_name = "whisper_compatible"

    def __init__(
        self,
        base_url: str,
        model: str,
        *,
        token: str = "",
        health_timeout: float = 3.0,
        transport: httpx.AsyncBaseTransport | None = None,
    ):
        self.base_url = validate_private_runtime_url(base_url) if base_url else ""
        self.model = model.strip()
        self.token = token
        self.health_timeout = health_timeout
        self.transport = transport

    async def transcribe(self, request: SpeechInferenceRequest) -> SpeechInferenceResult:
        if not self.base_url:
            raise AIRuntimeError(AI_RUNTIME_UNAVAILABLE)
        if not self.model:
            raise AIRuntimeError(AI_MODEL_NOT_READY)
        data: dict[str, str] = {"model": self.model}
        if request.language:
            data["language"] = request.language
        if request.timestamps:
            data["response_format"] = "verbose_json"
        started = time.monotonic()
        try:
            async with httpx.AsyncClient(
                timeout=request.timeout_seconds, transport=self.transport
            ) as client:
                response = await client.post(
                    f"{self.base_url}/v1/audio/transcriptions",
                    headers=_auth_headers(self.token),
                    files={"file": (request.filename, request.audio)},
                    data=data,
                )
        except httpx.TimeoutException as error:
            raise AIRuntimeError(AI_RUNTIME_TIMEOUT) from error
        except httpx.HTTPError as error:
            raise AIRuntimeError(AI_RUNTIME_UNAVAILABLE) from error
        if response.status_code in {408, 504}:
            raise AIRuntimeError(AI_RUNTIME_TIMEOUT)
        if response.status_code in {429, 503}:
            raise AIRuntimeError(AI_RUNTIME_BUSY, retry_after=1)
        if response.status_code == 425:
            raise AIRuntimeError(AI_RUNTIME_STARTING, retry_after=2)
        if response.status_code != 200:
            _log_engine_refusal(self.adapter_name, response)
            raise AIRuntimeError(AI_RUNTIME_FAILED)
        try:
            body = response.json()
            text = body.get("text")
            if not isinstance(text, str):
                raise ValueError("text")
            duration = body.get("duration")
            return SpeechInferenceResult(
                text=text,
                language=body.get("language") if isinstance(body.get("language"), str) else None,
                duration_seconds=float(duration) if isinstance(duration, (int, float)) else None,
                latency_ms=round((time.monotonic() - started) * 1000),
                backend={"adapter": self.adapter_name},
            )
        except (ValueError, json.JSONDecodeError) as error:
            raise AIRuntimeError(AI_RUNTIME_FAILED) from error

    async def health(self) -> EngineHealth:
        if not self.base_url:
            return EngineHealth(EngineStatus.UNAVAILABLE, self.adapter_name, None)
        if not self.model:
            return EngineHealth(
                EngineStatus.UNAVAILABLE,
                self.adapter_name,
                None,
                last_error_code=AI_MODEL_NOT_READY,
            )
        started = time.monotonic()
        try:
            async with httpx.AsyncClient(
                timeout=self.health_timeout, transport=self.transport
            ) as client:
                response = await client.get(
                    f"{self.base_url}/health", headers=_auth_headers(self.token)
                )
            latency = round((time.monotonic() - started) * 1000)
            if response.status_code in {425, 503}:
                return EngineHealth(EngineStatus.LOADING, self.adapter_name, latency)
            if response.status_code != 200:
                return EngineHealth(EngineStatus.UNAVAILABLE, self.adapter_name, latency)
            version = response.headers.get("x-runtime-version")
            try:
                body = response.json()
                if isinstance(body, dict):
                    version = body.get("version") or version
                    state = str(body.get("status") or "").lower()
                    if state in {"loading", "starting"}:
                        return EngineHealth(
                            EngineStatus.LOADING, self.adapter_name, latency, version, self.model
                        )
            except (ValueError, json.JSONDecodeError):
                pass
            return EngineHealth(
                EngineStatus.READY, self.adapter_name, latency, version, self.model
            )
        except httpx.HTTPError:
            return EngineHealth(EngineStatus.UNAVAILABLE, self.adapter_name, None)


class FakeTextInferenceBackend:
    """Deterministic backend for routing tests; never selected by production env."""

    adapter_name = "fake_text"

    def __init__(self, *, status: EngineStatus = EngineStatus.READY, delay: float = 0):
        self.status = status
        self.delay = delay
        self.requests: list[TextInferenceRequest] = []

    async def generate(self, request: TextInferenceRequest) -> TextInferenceResult:
        self.requests.append(request)
        if self.delay:
            await asyncio.sleep(self.delay)
        if self.status != EngineStatus.READY:
            code = AI_RUNTIME_STARTING if self.status == EngineStatus.LOADING else AI_RUNTIME_UNAVAILABLE
            raise AIRuntimeError(code)
        return TextInferenceResult(
            text=f"fake:{request.task_type.value}",
            finish_reason="stop",
            latency_ms=0,
            usage={"total_tokens": 1},
            backend={"adapter": self.adapter_name},
        )

    async def health(self) -> EngineHealth:
        return EngineHealth(self.status, self.adapter_name, 0, "fake-1")


class FakeSpeechInferenceBackend:
    adapter_name = "fake_speech"

    def __init__(self, *, status: EngineStatus = EngineStatus.READY, delay: float = 0):
        self.status = status
        self.delay = delay
        self.requests: list[SpeechInferenceRequest] = []

    async def transcribe(self, request: SpeechInferenceRequest) -> SpeechInferenceResult:
        self.requests.append(request)
        if self.delay:
            await asyncio.sleep(self.delay)
        if self.status != EngineStatus.READY:
            code = AI_RUNTIME_STARTING if self.status == EngineStatus.LOADING else AI_RUNTIME_UNAVAILABLE
            raise AIRuntimeError(code)
        return SpeechInferenceResult(
            text=f"fake:{request.task_type.value}",
            language=request.language,
            duration_seconds=None,
            latency_ms=0,
            backend={"adapter": self.adapter_name},
        )

    async def health(self) -> EngineHealth:
        return EngineHealth(self.status, self.adapter_name, 0, "fake-1")


@dataclass
class CapacitySnapshot:
    max_concurrent: int
    active: int
    queue_length: int
    max_queue: int


class _CapacityGate:
    def __init__(
        self,
        limit: int,
        max_queue: int,
        queue_wait_seconds: float = DEFAULT_QUEUE_WAIT_SECONDS,
    ):
        self.limit = max(1, limit)
        self.max_queue = max(0, max_queue)
        self.queue_wait_seconds = min(
            MAX_QUEUE_WAIT_SECONDS,
            max(MIN_QUEUE_WAIT_SECONDS, queue_wait_seconds),
        )
        self._semaphore = asyncio.Semaphore(self.limit)
        self._lock = asyncio.Lock()
        self._active = 0
        self._waiting = 0

    async def acquire(self):
        async with self._lock:
            if self._semaphore.locked() and self._waiting >= self.max_queue:
                raise AIRuntimeError(AI_RUNTIME_BUSY, retry_after=1)
            queued = self._semaphore.locked()
            if queued:
                self._waiting += 1
        try:
            await asyncio.wait_for(self._semaphore.acquire(), self.queue_wait_seconds)
        except asyncio.TimeoutError as error:
            raise AIRuntimeError(AI_RUNTIME_BUSY, retry_after=1) from error
        finally:
            if queued:
                async with self._lock:
                    self._waiting -= 1
        async with self._lock:
            self._active += 1

    async def release(self):
        async with self._lock:
            self._active = max(0, self._active - 1)
        self._semaphore.release()

    def snapshot(self) -> CapacitySnapshot:
        return CapacitySnapshot(self.limit, self._active, self._waiting, self.max_queue)


TEXT_TIMEOUTS = {
    TaskType.REFORMULATION: 30.0,
    TaskType.STYLE_TRANSFORM: 45.0,
    TaskType.VOICE_COMMAND: 45.0,
    TaskType.AI_SKILL: 90.0,
    # Un retour pedagogique tient en quatre phrases. Lui laisser le budget
    # d'un AI Skill ferait attendre quelqu'un qui pratique un exercice de
    # trois minutes.
    TaskType.LEARNING_FEEDBACK: 45.0,
    TaskType.ENGINEERING_NOTES: 60.0,
}
SPEECH_TIMEOUTS = {
    TaskType.TRANSCRIPTION_REALTIME: 45.0,
    TaskType.TRANSCRIPTION_FILE: 300.0,
}


# Les trois profils que Nova definit lui-meme. `custom` n'y figure pas : ses
# valeurs viennent du deploiement, et lui donner un defaut ici en ferait une
# copie de `standard` sous un autre nom — une option qui ne fait rien.
PROFILE_OPTIONS = {
    ModelProfile.EFFICIENT: TextInferenceOptions(1024, 0.2, ReasoningMode.OFF),
    ModelProfile.STANDARD: TextInferenceOptions(2048, 0.3, ReasoningMode.AUTO),
    ModelProfile.ADVANCED: TextInferenceOptions(4096, 0.3, ReasoningMode.ON),
}

MAX_OUTPUT_TOKENS_CEILING = 16384
TEMPERATURE_CEILING = 2.0



class OrganizationAIRouter:
    """One fail-closed routing point for every organization inference task."""

    def __init__(
        self,
        text_backend: TextInferenceBackend,
        speech_backend: SpeechInferenceBackend,
        *,
        profile: ModelProfile = ModelProfile.STANDARD,
        reasoning_allowed: bool = False,
        max_concurrent_text: int = 2,
        max_concurrent_speech: int = 1,
        max_queue: int = 4,
        queue_wait_seconds: float = DEFAULT_QUEUE_WAIT_SECONDS,
        custom_options: TextInferenceOptions | None = None,
    ):
        self.text_backend = text_backend
        self.speech_backend = speech_backend
        # Les valeurs de `custom`, si et seulement si le deploiement les a
        # declarees. Elles ne sont pas modifiables par une API : ce sont des
        # parametres d'exploitation, au meme titre que le modele charge.
        self.custom_options = custom_options
        self.profile = profile
        self.reasoning_allowed = reasoning_allowed
        self.text_capacity = _CapacityGate(
            max_concurrent_text, max_queue, queue_wait_seconds
        )
        self.speech_capacity = _CapacityGate(
            max_concurrent_speech, max_queue, queue_wait_seconds
        )
        self.fallback_policy = "disabled"
        self.applied_revision = 0
        self.queue_wait_seconds = queue_wait_seconds

    def apply_local_config(
        self,
        *,
        profile: ModelProfile,
        reasoning_allowed: bool,
        max_concurrent_text: int,
        max_concurrent_speech: int,
        max_queue: int,
        revision: int,
    ) -> None:
        if profile == ModelProfile.CUSTOM and self.custom_options is None:
            raise AIRuntimeError(AI_RUNTIME_CONFIG_INVALID)
        self.profile = profile
        self.reasoning_allowed = reasoning_allowed
        # La patience vient du deploiement, pas de la console : elle decoule de
        # l'objectif de latence, que l'exploitant fixe une fois.
        self.text_capacity = _CapacityGate(
            max_concurrent_text, max_queue, self.queue_wait_seconds
        )
        self.speech_capacity = _CapacityGate(
            max_concurrent_speech, max_queue, self.queue_wait_seconds
        )
        self.applied_revision = revision

    def _options(
        self, task_type: TaskType, requested: TextInferenceOptions | None
    ) -> TextInferenceOptions:
        options = requested or self.profile_options()
        reasoning = options.reasoning
        temperature = max(0.0, min(options.temperature, TEMPERATURE_CEILING))
        if not self.reasoning_allowed or task_type in FIDELITY_TASKS:
            reasoning = ReasoningMode.OFF
        if task_type in FIDELITY_TASKS:
            # Mesure sur le modele du lab (Qwen2.5-1.5B), deux textes porteurs
            # de dates, d'heures, de nombres et de noms propres, vingt tirages
            # par configuration : a temperature 0,3 le modele place un fait qui
            # n'existe pas dans onze sorties sur quatre-vingts ; a temperature
            # nulle, aucune sur cent vingt.
            #
            # Le prix est un peu plus d'omission -- un fait qui disparait. Ce
            # n'est pas le meme risque : une omission se voit, l'utilisateur
            # relit son propre texte et constate le manque. Une invention se lit
            # avec la meme assurance que le reste, et deplace un rendez-vous
            # sans que rien ne le signale.
            #
            # La variete d'echantillonnage n'achete rien ici : ces trois taches
            # rendent un texte que l'utilisateur a dicte, pas une proposition.
            temperature = 0.0
        return TextInferenceOptions(
            max(1, min(options.max_output_tokens, MAX_OUTPUT_TOKENS_CEILING)),
            temperature,
            reasoning,
        )

    @property
    def custom_available(self) -> bool:
        """Le deploiement a-t-il declare un profil Custom exploitable ?

        La console s'en sert pour ne pas proposer un choix qui n'existe pas.
        Sans cette question, `custom` serait selectionnable partout et ne se
        distinguerait de `standard` sur aucun deploiement.
        """
        return self.custom_options is not None

    def profile_options(self) -> TextInferenceOptions:
        if self.profile == ModelProfile.CUSTOM:
            if self.custom_options is None:
                raise AIRuntimeError(AI_RUNTIME_CONFIG_INVALID)
            return self.custom_options
        return PROFILE_OPTIONS[self.profile]

    async def generate(
        self,
        task_type: TaskType,
        input_text: str,
        instruction: str,
        *,
        request_id: str | None = None,
        options: TextInferenceOptions | None = None,
    ) -> TextInferenceResult:
        if task_type not in TEXT_TASKS:
            raise AIRuntimeError(AI_RUNTIME_CONFIG_INVALID)
        timeout = TEXT_TIMEOUTS[task_type]
        request = TextInferenceRequest(
            _safe_request_id(request_id),
            task_type,
            input_text,
            instruction,
            self._options(task_type, options),
            timeout,
        )
        await self.text_capacity.acquire()
        try:
            try:
                return await asyncio.wait_for(
                    self.text_backend.generate(request), timeout=timeout + 0.1
                )
            except asyncio.TimeoutError as error:
                raise AIRuntimeError(AI_RUNTIME_TIMEOUT) from error
        finally:
            await self.text_capacity.release()

    async def transcribe(
        self,
        task_type: TaskType,
        audio: bytes,
        filename: str,
        *,
        language: str | None = None,
        timestamps: bool = False,
        request_id: str | None = None,
    ) -> SpeechInferenceResult:
        if task_type not in SPEECH_TASKS:
            raise AIRuntimeError(AI_RUNTIME_CONFIG_INVALID)
        timeout = SPEECH_TIMEOUTS[task_type]
        request = SpeechInferenceRequest(
            _safe_request_id(request_id),
            task_type,
            audio,
            filename,
            language,
            timestamps,
            timeout,
        )
        await self.speech_capacity.acquire()
        try:
            try:
                return await asyncio.wait_for(
                    self.speech_backend.transcribe(request), timeout=timeout + 0.1
                )
            except asyncio.TimeoutError as error:
                raise AIRuntimeError(AI_RUNTIME_TIMEOUT) from error
        finally:
            await self.speech_capacity.release()

    async def health(self) -> dict[str, Any]:
        checked_at = time.time()
        text, speech = await asyncio.gather(
            self.text_backend.health(), self.speech_backend.health()
        )
        states = {text.status, speech.status}
        if states == {EngineStatus.READY}:
            status = RuntimeStatus.HEALTHY
        elif EngineStatus.READY in states:
            status = RuntimeStatus.DEGRADED
        elif EngineStatus.LOADING in states:
            status = RuntimeStatus.STARTING
        else:
            status = RuntimeStatus.UNAVAILABLE
        return {
            "status": status.value,
            "text_engine": _engine_health_view(text),
            "speech_engine": _engine_health_view(speech),
            "checked_at": checked_at,
            "fallback_policy": self.fallback_policy,
            "capacity": {
                "text": asdict(self.text_capacity.snapshot()),
                "speech": asdict(self.speech_capacity.snapshot()),
            },
        }


def _engine_health_view(health: EngineHealth) -> dict[str, Any]:
    return {
        "status": health.status.value,
        "adapter": health.adapter,
        "latency_ms": health.latency_ms,
        "runtime_version": health.runtime_version,
        "model": health.model,
        "last_error_code": health.last_error_code,
    }


@dataclass(frozen=True)
class HardwareInfo:
    cpu: str
    architecture: str
    ram_gb: float | None
    gpu_vendor: str | None
    gpu_model: str | None
    vram_gb: float | None
    driver_version: str | None


def _ram_gb() -> float | None:
    if os.name == "nt":
        try:
            import ctypes

            class MemoryStatus(ctypes.Structure):
                _fields_ = [
                    ("length", ctypes.c_ulong),
                    ("memory_load", ctypes.c_ulong),
                    ("total_physical", ctypes.c_ulonglong),
                    ("available_physical", ctypes.c_ulonglong),
                    ("total_page_file", ctypes.c_ulonglong),
                    ("available_page_file", ctypes.c_ulonglong),
                    ("total_virtual", ctypes.c_ulonglong),
                    ("available_virtual", ctypes.c_ulonglong),
                    ("available_extended_virtual", ctypes.c_ulonglong),
                ]

            status = MemoryStatus()
            status.length = ctypes.sizeof(MemoryStatus)
            if ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(status)):
                return round(status.total_physical / (1024**3), 1)
        except (AttributeError, OSError):
            return None
    meminfo = Path("/proc/meminfo")
    if meminfo.exists():
        for line in meminfo.read_text(encoding="utf-8").splitlines():
            if line.startswith("MemTotal:"):
                return round(int(line.split()[1]) / (1024**2), 1)
    return None


def detect_hardware() -> HardwareInfo:
    cpu = platform.processor().strip() or platform.machine()
    gpu_vendor = gpu_model = driver = None
    vram_gb = None
    try:
        completed = subprocess.run(
            [
                "nvidia-smi",
                "--query-gpu=name,memory.total,driver_version",
                "--format=csv,noheader,nounits",
            ],
            capture_output=True,
            text=True,
            timeout=2,
            check=False,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        if completed.returncode == 0 and completed.stdout.strip():
            name, memory, driver = [part.strip() for part in completed.stdout.splitlines()[0].split(",", 2)]
            gpu_vendor = "NVIDIA"
            gpu_model = name
            vram_gb = round(float(memory) / 1024, 1)
    except (OSError, subprocess.SubprocessError, ValueError):
        pass
    return HardwareInfo(
        cpu=cpu,
        architecture=platform.machine(),
        ram_gb=_ram_gb(),
        gpu_vendor=gpu_vendor,
        gpu_model=gpu_model,
        vram_gb=vram_gb,
        driver_version=driver,
    )


# Ce qu'il faut de memoire video pour chaque profil.
#
# `ADVANCED_MINIMUM` est la borne sous laquelle le profil est refuse ; la
# recommandation attend `ADVANCED_RECOMMENDED` pour le proposer d'elle-meme.
# Les deux valeurs different volontairement : entre les deux, le profil tient
# mais sans marge, et c'est a l'exploitant de decider s'il la prend.
ADVANCED_MINIMUM_VRAM_GB = 16.0
ADVANCED_RECOMMENDED_VRAM_GB = 24.0
STANDARD_RECOMMENDED_VRAM_GB = 10.0


def recommend_profile(hardware: HardwareInfo) -> ModelProfile:
    if hardware.vram_gb is None:
        return ModelProfile.EFFICIENT
    if hardware.vram_gb >= ADVANCED_RECOMMENDED_VRAM_GB:
        return ModelProfile.ADVANCED
    if hardware.vram_gb >= STANDARD_RECOMMENDED_VRAM_GB:
        return ModelProfile.STANDARD
    return ModelProfile.EFFICIENT


def custom_options_from_environment() -> TextInferenceOptions | None:
    """Les parametres du profil Custom, tels que le deploiement les declare.

    `None` signifie que le deploiement n'a rien declare — et donc que `custom`
    n'est pas un choix disponible. Les deux valeurs sont exigees ensemble : une
    moitie de profil serait completee par un defaut Nova, ce qui reviendrait a
    inventer la moitie de la configuration d'une DSI.

    Ni URL, ni modele, ni secret : ces valeurs-la restent ailleurs, et cette
    surface n'a pas a devenir le chemin par lequel on les changerait.
    """

    raw_tokens = os.getenv("NOVA_AI_CUSTOM_MAX_OUTPUT_TOKENS", "").strip()
    raw_temperature = os.getenv("NOVA_AI_CUSTOM_TEMPERATURE", "").strip()
    if not raw_tokens or not raw_temperature:
        return None
    try:
        tokens = int(raw_tokens)
        temperature = float(raw_temperature)
    except ValueError as error:
        raise AIRuntimeError(AI_RUNTIME_CONFIG_INVALID) from error
    if not 1 <= tokens <= MAX_OUTPUT_TOKENS_CEILING:
        raise AIRuntimeError(AI_RUNTIME_CONFIG_INVALID)
    if not 0.0 <= temperature <= TEMPERATURE_CEILING:
        raise AIRuntimeError(AI_RUNTIME_CONFIG_INVALID)
    try:
        reasoning = ReasoningMode(
            os.getenv("NOVA_AI_CUSTOM_REASONING", "auto").strip().lower()
        )
    except ValueError as error:
        raise AIRuntimeError(AI_RUNTIME_CONFIG_INVALID) from error
    return TextInferenceOptions(tokens, temperature, reasoning)


def router_from_environment() -> OrganizationAIRouter:
    """Build the dedicated router, retaining explicit legacy env compatibility.

    Legacy URLs remain accepted. Model names no longer have commercial defaults:
    an operator must configure them, otherwise inference fails with
    ``AI_MODEL_NOT_READY`` instead of selecting a model silently.
    """

    text_url = os.getenv("NOVA_AI_TEXT_BASE_URL", os.getenv("NOVA_LLM_URL", ""))
    text_model = os.getenv("NOVA_AI_TEXT_MODEL", os.getenv("NOVA_LLM_MODEL", ""))
    speech_url = os.getenv(
        "NOVA_AI_SPEECH_BASE_URL", os.getenv("NOVA_WHISPER_URL", "")
    )
    speech_model = os.getenv(
        "NOVA_AI_SPEECH_MODEL", os.getenv("NOVA_WHISPER_MODEL", "")
    )
    # Reference ou valeur : `env://NOM`, `file:///run/secrets/nom`, ou la
    # valeur elle-meme. Resolue a chaque appel, pour qu'une rotation du
    # fichier monte prenne effet sans redemarrer le service.
    import secrets_provider

    token = secrets_provider.resolve_value(os.getenv("NOVA_AI_RUNTIME_TOKEN", "")) or ""
    try:
        profile = ModelProfile(os.getenv("NOVA_AI_PROFILE", "standard").lower())
    except ValueError as error:
        raise AIRuntimeError(AI_RUNTIME_CONFIG_INVALID) from error
    custom_options = custom_options_from_environment()
    if profile == ModelProfile.CUSTOM and custom_options is None:
        # Echouer au demarrage, bruyamment. Retomber sur `standard` en silence
        # ferait tourner le deploiement avec des reglages que personne n'a
        # choisis, sous un nom qui affirme le contraire.
        raise AIRuntimeError(AI_RUNTIME_CONFIG_INVALID)
    return OrganizationAIRouter(
        OpenAICompatibleTextBackend(
            text_url,
            text_model,
            token=token,
            reasoning_supported=os.getenv("NOVA_AI_REASONING_ADAPTER", "false").lower()
            == "true",
        ),
        WhisperCompatibleSpeechBackend(speech_url, speech_model, token=token),
        profile=profile,
        reasoning_allowed=os.getenv("NOVA_AI_REASONING_ALLOWED", "false").lower()
        == "true",
        max_concurrent_text=int(os.getenv("NOVA_AI_MAX_CONCURRENT_TEXT", "2")),
        max_concurrent_speech=int(os.getenv("NOVA_AI_MAX_CONCURRENT_SPEECH", "1")),
        max_queue=int(os.getenv("NOVA_AI_MAX_QUEUE", "4")),
        queue_wait_seconds=float(
            os.getenv("NOVA_AI_QUEUE_WAIT_SECONDS", str(DEFAULT_QUEUE_WAIT_SECONDS))
        ),
        custom_options=custom_options,
    )
