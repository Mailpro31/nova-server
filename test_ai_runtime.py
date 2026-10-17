"""Phase 32 — private organization AI runtime contracts and routing."""

import asyncio
import json
import os
import re
import unittest
from pathlib import Path
from unittest.mock import patch

import httpx

# `test_main` doit etre importe en premier : il redirige NOVA_DB vers un
# repertoire temporaire avant que `main` ne cree son schema au moment de
# l'import. Sans cet ordre, importer `main` ecrirait dans la vraie base.
from test_main import AdminTestCase, grant_admin_session  # noqa: F401

import ai_runtime
import main
from ai_runtime import (
    AI_MODEL_NOT_READY,
    AI_PROMPT_TOO_LONG,
    AI_RUNTIME_FAILED,
    AI_RUNTIME_BUSY,
    AI_RUNTIME_CONFIG_INVALID,
    AI_RUNTIME_TIMEOUT,
    AI_RUNTIME_UNAVAILABLE,
    AIRuntimeError,
    EngineStatus,
    FakeSpeechInferenceBackend,
    FakeTextInferenceBackend,
    ModelProfile,
    OpenAICompatibleTextBackend,
    OrganizationAIRouter,
    ReasoningMode,
    RuntimeStatus,
    SpeechInferenceRequest,
    TaskType,
    TextInferenceOptions,
    TextInferenceRequest,
    WhisperCompatibleSpeechBackend,
    validate_private_runtime_url,
)

# Le schema s'applique **explicitement**. Importer `main` ne migre plus rien :
# c'est precisement l'effet de bord qui a migre une base de production en
# Phase 30 puis en Phase 37. Un appel visible dans le test remplace un import
# qui decidait tout seul.
main.init_db()



class RuntimeDestinationTests(unittest.TestCase):
    def test_loopback_private_ip_and_service_names_are_allowed(self):
        for value in (
            "http://127.0.0.1:8001/v1",
            "http://10.10.0.8:8001/v1",
            "http://llm:8000/v1",
            "http://host.docker.internal:8000/v1",
        ):
            with self.subTest(value=value):
                self.assertEqual(validate_private_runtime_url(value), value)

    def test_public_metadata_and_credentialed_urls_fail_closed(self):
        for value in (
            "https://8.8.8.8/v1",
            "http://169.254.169.254/latest",
            "http://user:secret@127.0.0.1:8000/v1",
            "file:///tmp/runtime",
            "http://127.0.0.1:8000/v1?target=elsewhere",
        ):
            with self.subTest(value=value):
                with self.assertRaises(AIRuntimeError) as raised:
                    validate_private_runtime_url(value)
                self.assertEqual(raised.exception.code, AI_RUNTIME_CONFIG_INVALID)


class ExternalApiTests(unittest.TestCase):
    """Une API externe (OpenRouter, Mistral, OpenAI), choisie par l'exploitant."""

    def test_a_public_https_api_needs_the_explicit_setting(self):
        with patch.dict(os.environ, {"NOVA_AI_ALLOW_PUBLIC_ENDPOINT": ""}):
            with self.assertRaises(AIRuntimeError):
                validate_private_runtime_url("https://8.8.8.8/api/v1")
        with patch.dict(os.environ, {"NOVA_AI_ALLOW_PUBLIC_ENDPOINT": "1"}):
            self.assertEqual(validate_private_runtime_url("https://8.8.8.8/api/v1"),
                             "https://8.8.8.8/api/v1")
            # Le réseau local reste accepté.
            self.assertEqual(validate_private_runtime_url("http://llm:8000/v1"),
                             "http://llm:8000/v1")
            for value in ("http://8.8.8.8/v1",            # en clair vers internet
                          "https://169.254.169.254/latest",  # métadonnées du cloud
                          "https://user:secret@8.8.8.8/v1",
                          "https://8.8.8.8/v1?target=elsewhere"):
                with self.subTest(value=value):
                    with self.assertRaises(AIRuntimeError):
                        validate_private_runtime_url(value)

    def test_each_engine_can_have_its_own_key(self):
        env = {"NOVA_AI_TEXT_BASE_URL": "http://llm:8000/v1", "NOVA_AI_TEXT_MODEL": "m",
               "NOVA_AI_SPEECH_BASE_URL": "http://speech:8000", "NOVA_AI_SPEECH_MODEL": "s",
               "NOVA_AI_RUNTIME_TOKEN": "commune", "NOVA_AI_TEXT_TOKEN": "texte",
               "NOVA_AI_SPEECH_TOKEN": ""}
        with patch.dict(os.environ, env):
            router = ai_runtime.router_from_environment()
        self.assertEqual(router.text_backend.token, "texte")
        self.assertEqual(router.speech_backend.token, "commune")


class AdapterTests(unittest.IsolatedAsyncioTestCase):
    async def test_openai_compatible_adapter_translates_the_stable_contract(self):
        seen = {}

        def handler(request: httpx.Request):
            seen["path"] = request.url.path
            seen["authorization"] = request.headers.get("authorization")
            seen["body"] = json.loads(request.content)
            return httpx.Response(
                200,
                json={
                    "choices": [
                        {"message": {"content": "result"}, "finish_reason": "stop"}
                    ],
                    "usage": {"prompt_tokens": 2, "completion_tokens": 1, "total_tokens": 3},
                },
            )

        backend = OpenAICompatibleTextBackend(
            "http://127.0.0.1:8001/v1",
            "runtime-owned-model",
            token="internal-secret",
            reasoning_supported=True,
            transport=httpx.MockTransport(handler),
        )
        result = await backend.generate(
            TextInferenceRequest(
                "request-1",
                TaskType.AI_SKILL,
                "member input",
                "server instruction",
                TextInferenceOptions(512, 0.2, ReasoningMode.ON),
                10,
            )
        )
        self.assertEqual(result.text, "result")
        self.assertEqual(result.usage, {"prompt_tokens": 2, "completion_tokens": 1, "total_tokens": 3})
        self.assertEqual(seen["path"], "/v1/chat/completions")
        self.assertEqual(seen["authorization"], "Bearer internal-secret")
        self.assertEqual(seen["body"]["model"], "runtime-owned-model")
        self.assertFalse(seen["body"]["stream"])
        self.assertEqual(seen["body"]["reasoning_effort"], "high")
        self.assertNotIn("internal-secret", repr(result))

    async def test_text_health_checks_loaded_model_without_a_prompt(self):
        requests = []

        def handler(request: httpx.Request):
            requests.append((request.method, request.url.path, request.content))
            return httpx.Response(200, json={"data": [{"id": "configured"}]})

        backend = OpenAICompatibleTextBackend(
            "http://127.0.0.1:8001/v1",
            "configured",
            transport=httpx.MockTransport(handler),
        )
        health = await backend.health()
        self.assertEqual(health.status, EngineStatus.READY)
        self.assertEqual(requests, [("GET", "/v1/models", b"")])

    async def test_missing_model_fails_before_network(self):
        backend = OpenAICompatibleTextBackend("http://127.0.0.1:8001/v1", "")
        with self.assertRaises(AIRuntimeError) as raised:
            await backend.generate(
                TextInferenceRequest(
                    "r",
                    TaskType.REFORMULATION,
                    "input",
                    "instruction",
                    TextInferenceOptions(10, 0, ReasoningMode.OFF),
                    1,
                )
            )
        self.assertEqual(raised.exception.code, AI_MODEL_NOT_READY)

    async def test_whisper_compatible_adapter_uses_runtime_configured_model(self):
        seen = {}

        def handler(request: httpx.Request):
            seen["path"] = request.url.path
            seen["body"] = request.content
            return httpx.Response(
                200, json={"text": "bonjour", "language": "fr", "duration": 1.25}
            )

        backend = WhisperCompatibleSpeechBackend(
            "http://127.0.0.1:8002",
            "runtime-speech-model",
            transport=httpx.MockTransport(handler),
        )
        result = await backend.transcribe(
            SpeechInferenceRequest(
                "request-2",
                TaskType.TRANSCRIPTION_FILE,
                b"RIFFaudio",
                "sample.wav",
                "fr",
                False,
                10,
            )
        )
        self.assertEqual(result.text, "bonjour")
        self.assertEqual(result.duration_seconds, 1.25)
        self.assertEqual(seen["path"], "/v1/audio/transcriptions")
        self.assertIn(b"runtime-speech-model", seen["body"])

    async def test_whisper_compatible_adapter_sends_the_vocabulary_hint(self):
        bodies = []

        def handler(request: httpx.Request):
            bodies.append(request.content)
            return httpx.Response(200, json={"text": "bonjour"})

        backend = WhisperCompatibleSpeechBackend(
            "http://127.0.0.1:8002",
            "runtime-speech-model",
            transport=httpx.MockTransport(handler),
        )
        for prompt in ("IPSA, Aéro 3.", None):
            await backend.transcribe(
                SpeechInferenceRequest(
                    "request-3", TaskType.TRANSCRIPTION_REALTIME, b"RIFFaudio",
                    "sample.wav", "fr", False, 10, prompt=prompt,
                )
            )
        self.assertIn('name="prompt"'.encode(), bodies[0])
        self.assertIn("Aéro 3".encode(), bodies[0])
        self.assertNotIn('name="prompt"'.encode(), bodies[1])


class CustomProfileTests(unittest.TestCase):
    """`custom` n'est pas un preset Nova : il vient du deploiement.

    Un profil qui existerait dans l'enumeration sans rien changer serait pire
    qu'absent — un administrateur croirait avoir regle quelque chose.
    """

    def router(self, custom=None):
        return OrganizationAIRouter(
            FakeTextInferenceBackend(),
            FakeSpeechInferenceBackend(),
            custom_options=custom,
        )

    def test_custom_is_unavailable_until_the_deployment_declares_it(self):
        self.assertFalse(self.router().custom_available)
        self.assertNotIn(ModelProfile.CUSTOM, ai_runtime.PROFILE_OPTIONS)

    def test_custom_cannot_be_applied_without_its_parameters(self):
        router = self.router()
        with self.assertRaises(AIRuntimeError) as raised:
            router.apply_local_config(
                profile=ModelProfile.CUSTOM,
                reasoning_allowed=False,
                max_concurrent_text=2,
                max_concurrent_speech=1,
                max_queue=4,
                revision=1,
            )
        self.assertEqual(raised.exception.code, AI_RUNTIME_CONFIG_INVALID)

    def test_declared_custom_parameters_are_the_ones_used(self):
        declared = TextInferenceOptions(7000, 0.9, ReasoningMode.ON)
        router = self.router(custom=declared)
        router.apply_local_config(
            profile=ModelProfile.CUSTOM,
            reasoning_allowed=True,
            max_concurrent_text=2,
            max_concurrent_speech=1,
            max_queue=4,
            revision=1,
        )
        self.assertTrue(router.custom_available)
        self.assertEqual(router.profile_options(), declared)
        # Et jusque dans la requete reellement emise, pas seulement en theorie.
        asyncio.run(
            router.generate(TaskType.AI_SKILL, "texte", "consigne")
        )
        emitted = router.text_backend.requests[0].options
        self.assertEqual(emitted.max_output_tokens, 7000)
        self.assertEqual(emitted.temperature, 0.9)

    def test_a_task_that_restores_dictated_text_does_not_sample(self):
        """Les taches de fidelite passent a temperature nulle.

        Mesure sur le modele du lab, deux textes porteurs de dates, d'heures,
        de nombres et de noms propres, vingt tirages par configuration : a
        temperature 0,3 le modele place un fait qui n'existe pas dans onze
        sorties sur quatre-vingts ; a temperature nulle, aucune sur cent vingt.

        Une omission se voit -- l'utilisateur relit son propre texte. Une
        invention se lit avec la meme assurance que le reste.
        """
        router = self.router(custom=TextInferenceOptions(4096, 0.9, ReasoningMode.ON))
        router.apply_local_config(
            profile=ModelProfile.CUSTOM,
            reasoning_allowed=True,
            max_concurrent_text=2,
            max_concurrent_speech=1,
            max_queue=4,
            revision=1,
        )
        for task in ai_runtime.FIDELITY_TASKS:
            with self.subTest(task=task):
                router.text_backend.requests.clear()
                asyncio.run(router.generate(task, "texte", "consigne"))
                self.assertEqual(router.text_backend.requests[0].options.temperature, 0.0)

        # Et une tache qui produit vraiment quelque chose garde le reglage du
        # deploiement : la regle vise la restitution, pas la generation.
        router.text_backend.requests.clear()
        asyncio.run(router.generate(TaskType.AI_SKILL, "texte", "consigne"))
        self.assertEqual(router.text_backend.requests[0].options.temperature, 0.9)

    def test_a_custom_profile_differs_from_standard(self):
        # La verification qui manquait : `custom` etait un clone de `standard`.
        standard = ai_runtime.PROFILE_OPTIONS[ModelProfile.STANDARD]
        declared = TextInferenceOptions(7000, 0.9, ReasoningMode.ON)
        self.assertNotEqual(declared, standard)
        self.assertEqual(self.router(custom=declared).profile_options(), standard)

    def test_custom_still_obeys_the_runtime_ceilings(self):
        # Un deploiement ne s'exonere pas des bornes : elles protegent le
        # runtime partage, pas le confort du profil.
        router = self.router(
            custom=TextInferenceOptions(999_999, 9.5, ReasoningMode.ON)
        )
        router.apply_local_config(
            profile=ModelProfile.CUSTOM,
            reasoning_allowed=True,
            max_concurrent_text=2,
            max_concurrent_speech=1,
            max_queue=4,
            revision=1,
        )
        asyncio.run(router.generate(TaskType.AI_SKILL, "texte", "consigne"))
        emitted = router.text_backend.requests[0].options
        self.assertEqual(emitted.max_output_tokens, ai_runtime.MAX_OUTPUT_TOKENS_CEILING)
        self.assertEqual(emitted.temperature, ai_runtime.TEMPERATURE_CEILING)


class CustomProfileEnvironmentTests(unittest.TestCase):
    """La declaration passe par le deploiement, et seulement par lui."""

    def build(self, **environment):
        keys = {
            "NOVA_AI_CUSTOM_MAX_OUTPUT_TOKENS": "",
            "NOVA_AI_CUSTOM_TEMPERATURE": "",
            "NOVA_AI_CUSTOM_REASONING": "",
        }
        with patch.dict(os.environ, {**keys, **environment}, clear=False):
            for key, value in {**keys, **environment}.items():
                if value == "":
                    os.environ.pop(key, None)
            return ai_runtime.custom_options_from_environment()

    def test_nothing_declared_means_no_custom_profile(self):
        self.assertIsNone(self.build())

    def test_half_a_declaration_is_refused_rather_than_completed(self):
        # Completer l'autre moitie avec un defaut Nova reviendrait a inventer
        # la moitie de la configuration d'une DSI.
        self.assertIsNone(self.build(NOVA_AI_CUSTOM_MAX_OUTPUT_TOKENS="4096"))
        self.assertIsNone(self.build(NOVA_AI_CUSTOM_TEMPERATURE="0.5"))

    def test_a_complete_declaration_is_read(self):
        options = self.build(
            NOVA_AI_CUSTOM_MAX_OUTPUT_TOKENS="4096",
            NOVA_AI_CUSTOM_TEMPERATURE="0.5",
            NOVA_AI_CUSTOM_REASONING="on",
        )
        self.assertEqual(
            options, TextInferenceOptions(4096, 0.5, ReasoningMode.ON)
        )

    def test_values_out_of_range_fail_closed(self):
        for environment in (
            {"NOVA_AI_CUSTOM_MAX_OUTPUT_TOKENS": "0",
             "NOVA_AI_CUSTOM_TEMPERATURE": "0.5"},
            {"NOVA_AI_CUSTOM_MAX_OUTPUT_TOKENS": "99999",
             "NOVA_AI_CUSTOM_TEMPERATURE": "0.5"},
            {"NOVA_AI_CUSTOM_MAX_OUTPUT_TOKENS": "4096",
             "NOVA_AI_CUSTOM_TEMPERATURE": "9"},
            {"NOVA_AI_CUSTOM_MAX_OUTPUT_TOKENS": "beaucoup",
             "NOVA_AI_CUSTOM_TEMPERATURE": "0.5"},
            {"NOVA_AI_CUSTOM_MAX_OUTPUT_TOKENS": "4096",
             "NOVA_AI_CUSTOM_TEMPERATURE": "0.5",
             "NOVA_AI_CUSTOM_REASONING": "beaucoup"},
        ):
            with self.subTest(environment=environment):
                with self.assertRaises(AIRuntimeError):
                    self.build(**environment)

    def test_the_surface_reads_exactly_three_behaviour_settings(self):
        """La liste est courte, et c'est voulu.

        On inspecte ce que la fonction **lit**, pas ce qu'elle raconte : une
        assertion sur la prose passerait au vert le jour ou quelqu'un ajoute
        une variable sans toucher au commentaire. Y laisser entrer une URL
        ferait de la configuration Custom un chemin vers une destination
        arbitraire — un SSRF avec une case a cocher.
        """
        source = Path("ai_runtime.py").read_text(encoding="utf-8")
        declaration = source[
            source.index("def custom_options_from_environment"):
            source.index("def router_from_environment")
        ]
        read = set(re.findall(r'os\.getenv\(\s*"([A-Z0-9_]+)"', declaration))
        self.assertEqual(
            read,
            {
                "NOVA_AI_CUSTOM_MAX_OUTPUT_TOKENS",
                "NOVA_AI_CUSTOM_TEMPERATURE",
                "NOVA_AI_CUSTOM_REASONING",
            },
        )


class RouterTests(unittest.IsolatedAsyncioTestCase):
    def router(self, **kwargs):
        return OrganizationAIRouter(
            kwargs.pop("text", FakeTextInferenceBackend()),
            kwargs.pop("speech", FakeSpeechInferenceBackend()),
            **kwargs,
        )

    async def test_every_stable_task_routes_to_its_engine(self):
        text = FakeTextInferenceBackend()
        speech = FakeSpeechInferenceBackend()
        router = self.router(text=text, speech=speech)
        for task in ai_runtime.TEXT_TASKS:
            result = await router.generate(task, "input", "instruction")
            self.assertEqual(result.text, f"fake:{task.value}")
        for task in ai_runtime.SPEECH_TASKS:
            result = await router.transcribe(task, b"audio", "a.wav")
            self.assertEqual(result.text, f"fake:{task.value}")
        self.assertEqual({request.task_type for request in text.requests}, ai_runtime.TEXT_TASKS)
        self.assertEqual({request.task_type for request in speech.requests}, ai_runtime.SPEECH_TASKS)

    async def test_a_failing_external_engine_falls_back_to_the_local_one(self):
        external = FakeTextInferenceBackend(status=EngineStatus.UNAVAILABLE)
        local = FakeTextInferenceBackend()
        router = self.router(text=external, fallback_text_backend=local)
        self.assertEqual(router.fallback_policy, "local")
        result = await router.generate(TaskType.REFORMULATION, "x", "y")
        self.assertEqual(result.text, "fake:reformulation")
        self.assertEqual((len(external.requests), len(local.requests)), (1, 1))
        # L'API marquée en panne n'est plus appelée pendant le délai.
        await router.generate(TaskType.REFORMULATION, "x", "y")
        self.assertEqual((len(external.requests), len(local.requests)), (1, 2))
        # Sans secours, la panne remonte comme avant.
        with self.assertRaises(AIRuntimeError) as raised:
            await self.router(text=FakeTextInferenceBackend(status=EngineStatus.UNAVAILABLE)
                              ).generate(TaskType.REFORMULATION, "x", "y")
        self.assertEqual(raised.exception.code, AI_RUNTIME_UNAVAILABLE)

    async def test_a_slow_external_engine_falls_back_to_the_local_one(self):
        local = FakeTextInferenceBackend()
        external = FakeTextInferenceBackend(delay=0.2)
        router = self.router(text=external, fallback_text_backend=local)
        # L'API n'a qu'un tiers du délai (0,1 s ici) : 0,2 s, c'est trop.
        with patch.dict(ai_runtime.TEXT_TIMEOUTS, {TaskType.REFORMULATION: 0.3}):
            result = await router.generate(TaskType.REFORMULATION, "x", "y")
            self.assertEqual(result.text, "fake:reformulation")
            self.assertEqual(len(local.requests), 1)
            # Trop lente, elle est mise de côté : la suivante va au secours.
            await router.generate(TaskType.REFORMULATION, "x", "y")
        self.assertEqual((len(external.requests), len(local.requests)), (1, 2))
        # Sans secours, l'API garde tout son délai.
        alone = self.router(text=FakeTextInferenceBackend(delay=0.2))
        with patch.dict(ai_runtime.TEXT_TIMEOUTS, {TaskType.REFORMULATION: 0.3}):
            result = await alone.generate(TaskType.REFORMULATION, "x", "y")
        self.assertEqual(result.text, "fake:reformulation")

    async def test_unknown_or_cross_engine_tasks_fail_closed(self):
        router = self.router()
        with self.assertRaises(AIRuntimeError) as raised:
            await router.generate(TaskType.TRANSCRIPTION_FILE, "x", "y")
        self.assertEqual(raised.exception.code, AI_RUNTIME_CONFIG_INVALID)
        with self.assertRaises(AIRuntimeError):
            await router.transcribe(TaskType.AI_SKILL, b"x", "a.wav")

    async def test_reasoning_is_capability_and_task_derived(self):
        text = FakeTextInferenceBackend()
        router = self.router(
            text=text, profile=ModelProfile.ADVANCED, reasoning_allowed=True
        )
        await router.generate(TaskType.REFORMULATION, "x", "y")
        await router.generate(TaskType.AI_SKILL, "x", "y")
        self.assertEqual(text.requests[0].options.reasoning, ReasoningMode.OFF)
        self.assertEqual(text.requests[1].options.reasoning, ReasoningMode.ON)

    async def test_a_waiting_request_is_served_within_the_budget(self):
        """Une place qui se libere avant la fin du budget doit servir, pas refuser.

        Le cas de l'administration : deux personnes dictent en meme temps sur un
        profil a une seule place. La seconde attendait une seconde puis recevait
        AI_RUNTIME_BUSY, alors que son budget de cinq secondes laissait tout le
        temps de la servir. Le poste retombait sur son moteur local, et la
        personne voyait la qualite changer sans rien avoir fait.
        """
        router = self.router(
            text=FakeTextInferenceBackend(delay=1.6),
            max_concurrent_text=1,
            max_queue=4,
            queue_wait_seconds=4.0,
        )
        running = asyncio.create_task(
            router.generate(TaskType.REFORMULATION, "premier", "instruction")
        )
        await asyncio.sleep(0.01)
        # Sert, apres attente. Aucune exception.
        await router.generate(TaskType.REFORMULATION, "second", "instruction")
        await running

    async def test_the_queue_still_refuses_once_it_is_full(self):
        """La patience ne remplace pas la borne : une file pleine refuse tout de suite.

        Sans ce test, allonger l'attente transformerait le refus franc en un
        entassement silencieux, ce qui est exactement ce que la porte existe pour
        empecher.
        """
        router = self.router(
            text=FakeTextInferenceBackend(delay=0.4),
            max_concurrent_text=1,
            max_queue=0,
            queue_wait_seconds=4.0,
        )
        running = asyncio.create_task(
            router.generate(TaskType.AI_SKILL, "premier", "instruction")
        )
        await asyncio.sleep(0.01)
        with self.assertRaises(AIRuntimeError) as raised:
            await router.generate(TaskType.AI_SKILL, "second", "instruction")
        self.assertEqual(raised.exception.code, AI_RUNTIME_BUSY)
        await running

    async def test_the_patience_is_configurable_and_bounded(self):
        """Le deploiement fixe l'attente ; elle ne peut ni disparaitre ni deriver."""
        self.assertEqual(self.router().text_capacity.queue_wait_seconds,
                         ai_runtime.DEFAULT_QUEUE_WAIT_SECONDS)
        gate = ai_runtime._CapacityGate(1, 1, queue_wait_seconds=0.0)
        self.assertGreaterEqual(gate.queue_wait_seconds, 0.05)
        gate = ai_runtime._CapacityGate(1, 1, queue_wait_seconds=10_000.0)
        self.assertLessEqual(gate.queue_wait_seconds,
                             ai_runtime.MAX_QUEUE_WAIT_SECONDS)

    async def test_busy_is_explicit_when_capacity_and_queue_are_full(self):
        router = self.router(
            text=FakeTextInferenceBackend(delay=0.1),
            max_concurrent_text=1,
            max_queue=0,
        )
        running = asyncio.create_task(
            router.generate(TaskType.AI_SKILL, "first", "instruction")
        )
        await asyncio.sleep(0.01)
        with self.assertRaises(AIRuntimeError) as raised:
            await router.generate(TaskType.AI_SKILL, "second", "instruction")
        self.assertEqual(raised.exception.code, AI_RUNTIME_BUSY)
        await running

    async def test_timeout_is_bounded_and_stable(self):
        # Le routeur garde une marge courte au-dessus du delai de la tache pour
        # laisser l'adaptateur expirer proprement le premier. Le backend factice
        # ignore ce delai : il ne reste que le garde-fou du routeur, et il doit
        # se declencher.
        router = self.router(text=FakeTextInferenceBackend(delay=1.0))
        with patch.dict(ai_runtime.TEXT_TIMEOUTS, {TaskType.AI_SKILL: 0.01}):
            with self.assertRaises(AIRuntimeError) as raised:
                await router.generate(TaskType.AI_SKILL, "input", "instruction")
        self.assertEqual(raised.exception.code, AI_RUNTIME_TIMEOUT)

    async def test_health_is_real_and_degrades_per_engine(self):
        router = self.router(
            text=FakeTextInferenceBackend(status=EngineStatus.READY),
            speech=FakeSpeechInferenceBackend(status=EngineStatus.UNAVAILABLE),
        )
        health = await router.health()
        self.assertEqual(health["status"], RuntimeStatus.DEGRADED.value)
        self.assertEqual(health["text_engine"]["status"], "ready")
        self.assertEqual(health["speech_engine"]["status"], "unavailable")
        self.assertEqual(health["fallback_policy"], "disabled")

    async def test_starting_is_not_synthesized_as_healthy(self):
        router = self.router(
            text=FakeTextInferenceBackend(status=EngineStatus.LOADING),
            speech=FakeSpeechInferenceBackend(status=EngineStatus.LOADING),
        )
        self.assertEqual((await router.health())["status"], "starting")


class InferenceEndpointTests(AdminTestCase):
    def setUp(self):
        super().setUp()
        with main.db() as conn:
            conn.execute("DELETE FROM usage")
            conn.execute("DELETE FROM organization_policies")

    def auth(self, token):
        return {"Authorization": f"Bearer {token}"}

    def fake_router(self, **kwargs):
        return OrganizationAIRouter(
            kwargs.pop("text", FakeTextInferenceBackend()),
            kwargs.pop("speech", FakeSpeechInferenceBackend()),
            **kwargs,
        )

    def deny(self, key: str):
        with main.db() as conn:
            conn.execute(
                "INSERT INTO organization_policies(organization_id,schema_version,revision,settings,updated_at) "
                "VALUES (?,?,?,?,?)",
                (
                    main.nova_organization_id(),
                    main.POLICY_SCHEMA_VERSION,
                    1,
                    json.dumps({key: False}),
                    1,
                ),
            )

    def test_reformulation_routes_once_and_counts_once(self):
        token = self.sign_in("member@example.com")
        router = self.fake_router()
        with patch.object(main, "ORGANIZATION_AI_ROUTER", router):
            response = self.client.post(
                "/api/reformulate",
                headers=self.auth(token),
                json={"text": "draft", "style_prompt": "improve"},
            )
        self.assertEqual(response.status_code, 200)
        # Sans Style, les contrôles du Style par défaut s'appliquent : le faux
        # moteur répond « fake:reformulation », qui ne reprend rien de la
        # dictée, et l'élève reçoit donc « draft ». Le moteur est appelé une
        # fois et compté une fois.
        self.assertEqual(response.json()["text"], "draft")
        self.assertEqual(len(router.text_backend.requests), 1)
        with main.db() as conn:
            row = conn.execute(
                "SELECT reformulations FROM usage WHERE email=?", ("member@example.com",)
            ).fetchone()
        self.assertEqual(row["reformulations"], 1)

    def test_policy_refuses_command_before_inference(self):
        self.deny("voice_commands_enabled")
        token = self.sign_in("member@example.com")
        router = self.fake_router()
        with patch.object(main, "ORGANIZATION_AI_ROUTER", router):
            response = self.client.post(
                "/api/command",
                headers=self.auth(token),
                json={"instruction": "improve", "text": "draft"},
            )
        self.assertEqual(response.status_code, 403)
        self.assertEqual(router.text_backend.requests, [])

    def test_classroom_stays_closed_however_capable_the_runtime_is(self):
        """Le runtime est une infrastructure, jamais une autorisation.

        Moteur sain, profil le plus capable, policy permissive : le mode
        pedagogique ferme quand meme les commandes et les notes d'ingenierie.
        Un runtime qui pourrait rouvrir une capacite fermee par le produit
        ferait de la configuration de deploiement un contournement de policy.
        """
        token = self.sign_in("member@example.com")
        router = self.fake_router(
            profile=ModelProfile.ADVANCED, reasoning_allowed=True
        )
        with patch.object(main, "EDUCATION_MODE", "classroom"):
            with patch.object(main, "ORGANIZATION_AI_ROUTER", router):
                for path, payload in (
                    ("/api/command", {"instruction": "improve", "text": "draft"}),
                    ("/api/engineering-notes", {"text": "draft"}),
                ):
                    with self.subTest(path=path):
                        response = self.client.post(
                            path, headers=self.auth(token), json=payload
                        )
                        self.assertEqual(response.status_code, 403)
        self.assertEqual(router.text_backend.requests, [])

    def test_file_policy_never_blocks_microphone(self):
        self.deny("file_import_enabled")
        token = self.sign_in("member@example.com")
        router = self.fake_router()
        files = {"file": ("a.wav", b"RIFFaudio", "audio/wav")}
        with patch.object(main, "ORGANIZATION_AI_ROUTER", router):
            microphone = self.client.post(
                "/api/transcribe", headers=self.auth(token), files=files
            )
            imported = self.client.post(
                "/api/import/transcribe", headers=self.auth(token), files=files
            )
        self.assertEqual(microphone.status_code, 200)
        self.assertEqual(imported.status_code, 403)
        self.assertEqual(
            [request.task_type for request in router.speech_backend.requests],
            [TaskType.TRANSCRIPTION_REALTIME],
        )

    def test_the_desktop_can_neither_pick_a_model_nor_name_a_runtime(self):
        """L'infrastructure appartient au deploiement, jamais au poste.

        Un client qui pourrait designer un modele ou une destination ferait de
        chaque poste un choix d'architecture, et de la frontiere privee une
        suggestion. Les champs surnumeraires sont ignores, pas honores.
        """
        token = self.sign_in("member@example.com")
        router = self.fake_router()
        with patch.object(main, "ORGANIZATION_AI_ROUTER", router):
            response = self.client.post(
                "/api/reformulate",
                headers=self.auth(token),
                json={
                    "text": "draft",
                    "style_prompt": "improve",
                    "model": "some-other-model",
                    "runtime_url": "http://attacker.example.com/v1",
                    "cloud_fallback": True,
                },
            )
        self.assertEqual(response.status_code, 200)
        # Une seule requete, routee par le serveur, avec le type de tache que
        # le serveur a choisi : rien du corps surnumeraire n'a ete honore.
        self.assertEqual(len(router.text_backend.requests), 1)
        self.assertEqual(
            router.text_backend.requests[0].task_type, TaskType.REFORMULATION
        )

    def test_runtime_error_payload_is_safe_and_stable(self):
        token = self.sign_in("member@example.com")
        router = self.fake_router(
            text=FakeTextInferenceBackend(status=EngineStatus.UNAVAILABLE)
        )
        with patch.object(main, "ORGANIZATION_AI_ROUTER", router):
            response = self.client.post(
                "/api/command",
                headers=self.auth(token),
                json={"instruction": "private instruction", "text": "private text"},
            )
        self.assertEqual(response.status_code, 503)
        self.assertEqual(
            response.json()["detail"],
            {"code": AI_RUNTIME_UNAVAILABLE, "task_type": "voice_command"},
        )
        self.assertNotIn("private", response.text)

    def test_health_does_not_count_usage_or_send_content(self):
        router = self.fake_router()
        with patch.object(main, "ORGANIZATION_AI_ROUTER", router):
            response = self.client.get("/api/health")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(router.text_backend.requests, [])
        self.assertEqual(router.speech_backend.requests, [])
        with main.db() as conn:
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM usage").fetchone()[0], 0)


class StyleResolutionTests(AdminTestCase):
    """Trois origines de Style, une autorite par origine.

    Integre et personnel : la consigne appartient a l'utilisateur, le serveur
    l'applique telle quelle. Organisation : la consigne appartient au package
    publie, le serveur la retrouve lui-meme. Le poste envoie toujours les deux
    — un identifiant et une consigne — et c'est l'origine de l'identifiant qui
    decide laquelle fait foi.
    """

    def setUp(self):
        super().setUp()
        with main.db() as conn:
            conn.execute("DELETE FROM organization_package_versions")
            conn.execute("DELETE FROM organization_packages")
            conn.execute("DELETE FROM organization_policies")
            conn.execute("DELETE FROM usage")

    def auth(self, token):
        return {"Authorization": f"Bearer {token}"}

    def fake_router(self):
        return OrganizationAIRouter(
            FakeTextInferenceBackend(), FakeSpeechInferenceBackend()
        )

    def publish_style(self, instruction: str, activate: bool = True) -> str:
        token = self.admin_session()
        base = (
            f"/api/control/organizations/{main.nova_organization_id()}/packages"
        )
        package = self.client.post(
            base,
            headers=self.auth(token),
            json={"package_type": "style", "name": "Style Organisation"},
        ).json()
        pid = package["package_id"]
        version = self.client.post(
            f"{base}/{pid}/versions",
            headers=self.auth(token),
            json={
                "content": {
                    "name": "Style Organisation",
                    "instruction": instruction,
                }
            },
        ).json()["versions"][0]["version_id"]
        self.client.post(
            f"{base}/{pid}/versions/{version}/publish",
            headers=self.auth(token),
            json={"activate": activate},
        )
        return main.package_identifier("style", pid)

    def reformulate(self, router, **body):
        token = self.sign_in("membre@example.com")
        with patch.object(main, "ORGANIZATION_AI_ROUTER", router):
            return self.client.post(
                "/api/reformulate",
                headers=self.auth(token),
                json={"text": "brouillon", **body},
            )

    def test_a_builtin_style_is_applied_as_the_client_sent_it(self):
        router = self.fake_router()
        response = self.reformulate(
            router,
            style_id="default_improve_transcriptions",
            style_prompt="Ameliore la transcription.",
        )
        self.assertEqual(response.status_code, 200)
        request = router.text_backend.requests[0]
        # Telle qu'envoyee, suivie du contrat commun a toute reformulation. Le
        # vocabulaire de l'organisation peut s'intercaler ; l'ordre, lui, tient.
        self.assertTrue(request.instruction.startswith("Ameliore la transcription."))
        self.assertTrue(request.instruction.endswith(main.REWRITE_CONTRACT))
        self.assertEqual(request.task_type, TaskType.STYLE_TRANSFORM)

    def test_a_personal_style_is_applied_as_the_client_sent_it(self):
        # Un Style personnel est ecrit par l'utilisateur : lui en refuser la
        # consigne reviendrait a lui refuser son propre reglage.
        router = self.fake_router()
        response = self.reformulate(
            router, style_id="style-perso-1742", style_prompt="Ecris en vers."
        )
        self.assertEqual(response.status_code, 200)
        instruction = router.text_backend.requests[0].instruction
        self.assertTrue(instruction.startswith("Ecris en vers."))
        self.assertTrue(instruction.endswith(main.REWRITE_CONTRACT))

    def test_no_style_at_all_stays_plain_reformulation(self):
        router = self.fake_router()
        response = self.reformulate(router)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            router.text_backend.requests[0].task_type, TaskType.REFORMULATION
        )

    def test_an_organization_style_is_resolved_by_the_server(self):
        """Le coeur du contrat : la consigne vient du package, pas du poste."""
        style_id = self.publish_style("Reponds en une phrase.")
        router = self.fake_router()
        response = self.reformulate(
            router,
            style_id=style_id,
            style_prompt="Consigne perimee restee dans le cache du poste.",
        )
        self.assertEqual(response.status_code, 200)
        request = router.text_backend.requests[0]
        self.assertIn("Reponds en une phrase.", request.instruction)
        self.assertNotIn("perimee", request.instruction)
        self.assertEqual(request.task_type, TaskType.STYLE_TRANSFORM)

    def test_an_unpublished_organization_style_stops_being_executable(self):
        """La revocation est la raison d'etre de l'identifiant.

        Un poste dont le catalogue est en retard garde la consigne en memoire.
        S'il pouvait l'envoyer comme un prompt ordinaire, depublier un Style
        n'aurait aucun effet tant qu'aucun poste ne s'est rafraichi.
        """
        style_id = self.publish_style("Consigne retiree.", activate=False)
        router = self.fake_router()
        response = self.reformulate(
            router, style_id=style_id, style_prompt="Consigne retiree."
        )
        self.assertEqual(response.status_code, 404)
        self.assertEqual(response.json()["detail"], "STYLE_NOT_AVAILABLE")
        self.assertEqual(router.text_backend.requests, [])

    def test_an_unknown_organization_style_is_refused_the_same_way(self):
        # Indistinguable d'un Style non publie : un membre n'a pas a deduire ce
        # que son organisation prepare.
        router = self.fake_router()
        response = self.reformulate(
            router, style_id="org_style_inexistant", style_prompt="peu importe"
        )
        self.assertEqual(response.status_code, 404)
        self.assertEqual(response.json()["detail"], "STYLE_NOT_AVAILABLE")
        self.assertEqual(router.text_backend.requests, [])

    def test_a_closed_capability_closes_the_organization_style(self):
        """Le mode pedagogique ferme les Styles ; l'identifiant le respecte.

        Le poste, lui, pourrait avoir une consigne en cache. C'est exactement
        la situation ou faire autorite cote poste contournerait une decision
        de l'etablissement.
        """
        style_id = self.publish_style("Reponds en une phrase.")
        router = self.fake_router()
        with patch.object(main, "EDUCATION_MODE", "assessment"):
            response = self.reformulate(
                router, style_id=style_id, style_prompt="Reponds en une phrase."
            )
        self.assertEqual(response.status_code, 404)
        self.assertEqual(router.text_backend.requests, [])

    def test_an_organization_style_needs_no_personal_entitlement(self):
        """Acquis de la Phase 31, verrouille ici aussi.

        L'organisation paie deja son deploiement. Facturer a ses membres un
        palier personnel pour le contenu qu'elle leur distribue se defendrait
        mal — et le serveur ne connait aucun palier.
        """
        style_id = self.publish_style("Reponds en une phrase.")
        router = self.fake_router()
        response = self.reformulate(router, style_id=style_id, style_prompt="x")
        self.assertEqual(response.status_code, 200)
        self.assertNotIn("entitlement", response.text.lower())
        self.assertNotIn("ultra", response.text.lower())

    def test_the_organization_prefix_is_derived_from_the_identifier_rule(self):
        # Un litteral retape ici et la divergerait un jour en silence.
        self.assertTrue(
            main.package_identifier("style", "abc").startswith(
                main.ORGANIZATION_STYLE_PREFIX
            )
        )
        self.assertFalse(
            main.package_identifier("ai_skill", "abc").startswith(
                main.ORGANIZATION_STYLE_PREFIX
            )
        )


class AIRuntimeAdminTests(AdminTestCase):
    def setUp(self):
        super().setUp()
        with main.db() as conn:
            conn.execute("DELETE FROM organization_ai_runtime_config")

    def test_read_only_admin_sees_safe_runtime_state(self):
        headers = grant_admin_session("reader@example.com", "read_only")
        router = OrganizationAIRouter(
            FakeTextInferenceBackend(), FakeSpeechInferenceBackend()
        )
        with patch.object(main, "ORGANIZATION_AI_ROUTER", router):
            response = self.client.get(
                "/api/control/organization/ai-runtime", headers=headers
            )
        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(body["processing_location"], "organization_private_infrastructure")
        self.assertEqual(body["desired"]["cloud_fallback"], "disabled")
        forbidden = ("prompt", "transcript", "audio", "selected_text", "instruction")
        serialized = json.dumps(body).lower()
        for key in forbidden:
            self.assertNotIn(key, serialized)

    def test_it_admin_applies_dedicated_config_with_revision(self):
        headers = grant_admin_session("it@example.com", "it_admin")
        router = OrganizationAIRouter(
            FakeTextInferenceBackend(), FakeSpeechInferenceBackend()
        )
        with patch.object(main, "ORGANIZATION_AI_ROUTER", router), patch.object(
            main, "NOVA_SERVER_MODE", main.SERVER_MODE_DEDICATED
        ):
            response = self.client.put(
                "/api/control/organization/ai-runtime",
                headers=headers,
                json={
                    "expected_revision": 0,
                    "profile": "standard",
                    "reasoning_allowed": True,
                    "concurrency_mode": "manual",
                    "max_concurrent_text": 3,
                    "max_concurrent_speech": 2,
                    "max_queue": 5,
                },
            )
        self.assertEqual(response.status_code, 200)
        reported = response.json()["reported"]
        self.assertEqual(reported["applied_revision"], 1)
        self.assertEqual(reported["apply_status"], main.APPLY_STATUS_APPLIED)
        self.assertEqual(router.applied_revision, 1)
        self.assertEqual(router.text_capacity.limit, 3)

    def test_revision_conflict_never_overwrites(self):
        headers = grant_admin_session("it@example.com", "it_admin")
        router = OrganizationAIRouter(
            FakeTextInferenceBackend(), FakeSpeechInferenceBackend()
        )
        payload = {
            "expected_revision": 0,
            "profile": "efficient",
            "reasoning_allowed": False,
            "concurrency_mode": "auto",
        }
        with patch.object(main, "ORGANIZATION_AI_ROUTER", router):
            self.assertEqual(
                self.client.put(
                    "/api/control/organization/ai-runtime", headers=headers, json=payload
                ).status_code,
                200,
            )
            conflict = self.client.put(
                "/api/control/organization/ai-runtime", headers=headers, json=payload
            )
        self.assertEqual(conflict.status_code, 409)
        # Meme contrat que les autres conflits d'ecriture du plan de controle :
        # `auth_error` ne transporte qu'un code, jamais de detail technique.
        self.assertEqual(conflict.json()["detail"], "AI_RUNTIME_REVISION_CONFLICT")

    def test_control_plane_does_not_claim_remote_apply(self):
        headers = grant_admin_session("it@example.com", "it_admin")
        router = OrganizationAIRouter(
            FakeTextInferenceBackend(), FakeSpeechInferenceBackend()
        )
        with patch.object(main, "ORGANIZATION_AI_ROUTER", router), patch.object(
            main, "NOVA_SERVER_MODE", main.SERVER_MODE_CONTROL_PLANE
        ):
            response = self.client.put(
                "/api/control/organization/ai-runtime",
                headers=headers,
                json={
                    "expected_revision": 0,
                    "profile": "standard",
                    "reasoning_allowed": False,
                    "concurrency_mode": "auto",
                },
            )
        body = response.json()
        # La revision est enregistree et attend. Sans Connector joignable, elle
        # n'est ni livree ni appliquee — et la console ne pretend pas l'inverse.
        self.assertEqual(body["reported"]["apply_status"], main.APPLY_STATUS_PENDING)
        self.assertEqual(body["reported"]["applied_revision"], 0)
        self.assertEqual(body["delivered"]["revision"], 0)
        self.assertTrue(body["remote_apply"]["requires_private_connector"])
        self.assertFalse(body["remote_apply"]["supported"])
        self.assertEqual(body["remote_apply"]["capable_connectors"], 0)

    def test_organization_admin_cannot_reconfigure_deployment(self):
        headers = grant_admin_session("org@example.com", "organization_admin")
        response = self.client.put(
            "/api/control/organization/ai-runtime",
            headers=headers,
            json={
                "expected_revision": 0,
                "profile": "standard",
                "reasoning_allowed": False,
                "concurrency_mode": "auto",
            },
        )
        self.assertEqual(response.status_code, 403)

    def test_the_console_is_told_which_profiles_this_deployment_offers(self):
        """Un choix propose puis refuse a l'enregistrement n'est pas un choix."""
        headers = grant_admin_session("it@example.com", "it_admin")
        router = OrganizationAIRouter(
            FakeTextInferenceBackend(), FakeSpeechInferenceBackend()
        )
        with patch.object(main, "ORGANIZATION_AI_ROUTER", router):
            view = self.client.get(
                "/api/control/organization/ai-runtime", headers=headers
            ).json()
        self.assertEqual(
            view["available_profiles"], ["efficient", "standard", "advanced"]
        )
        self.assertEqual(view["custom_profile"], {"available": False})

    def test_custom_is_refused_when_the_deployment_never_declared_it(self):
        headers = grant_admin_session("it@example.com", "it_admin")
        router = OrganizationAIRouter(
            FakeTextInferenceBackend(), FakeSpeechInferenceBackend()
        )
        with patch.object(main, "ORGANIZATION_AI_ROUTER", router):
            response = self.client.put(
                "/api/control/organization/ai-runtime",
                headers=headers,
                json={"expected_revision": 0, "profile": "custom",
                      "reasoning_allowed": False, "concurrency_mode": "auto"},
            )
        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.json()["detail"], "AI_RUNTIME_PROFILE_UNAVAILABLE")
        # Rien n'a ete enregistre : le refus precede l'ecriture.
        with main.db() as conn:
            self.assertIsNone(
                conn.execute(
                    "SELECT 1 FROM organization_ai_runtime_config"
                ).fetchone()
            )

    def test_a_declared_custom_profile_becomes_selectable_and_applies(self):
        headers = grant_admin_session("it@example.com", "it_admin")
        router = OrganizationAIRouter(
            FakeTextInferenceBackend(),
            FakeSpeechInferenceBackend(),
            custom_options=TextInferenceOptions(4096, 0.5, ReasoningMode.ON),
        )
        with patch.object(main, "ORGANIZATION_AI_ROUTER", router):
            view = self.client.get(
                "/api/control/organization/ai-runtime", headers=headers
            ).json()
            self.assertIn("custom", view["available_profiles"])
            self.assertEqual(
                view["custom_profile"],
                {"available": True, "max_output_tokens": 4096,
                 "temperature": 0.5, "reasoning": "on"},
            )
            saved = self.client.put(
                "/api/control/organization/ai-runtime",
                headers=headers,
                json={"expected_revision": 0, "profile": "custom",
                      "reasoning_allowed": True, "concurrency_mode": "auto"},
            )
        self.assertEqual(saved.status_code, 200, saved.text)
        body = saved.json()
        self.assertEqual(body["desired"]["profile"], "custom")
        self.assertEqual(
            body["reported"]["applied_revision"], body["desired"]["revision"]
        )
        self.assertEqual(router.profile, ModelProfile.CUSTOM)

    def test_the_custom_parameters_are_read_only(self):
        """Ils viennent du deploiement et n'y retournent pas.

        Les rendre ecrivables ici ouvrirait, depuis un navigateur, la surface
        que la Phase 32 a justement gardee cote exploitation.
        """
        headers = grant_admin_session("it@example.com", "it_admin")
        router = OrganizationAIRouter(
            FakeTextInferenceBackend(),
            FakeSpeechInferenceBackend(),
            custom_options=TextInferenceOptions(4096, 0.5, ReasoningMode.ON),
        )
        with patch.object(main, "ORGANIZATION_AI_ROUTER", router):
            self.client.put(
                "/api/control/organization/ai-runtime",
                headers=headers,
                json={"expected_revision": 0, "profile": "custom",
                      "reasoning_allowed": True, "concurrency_mode": "auto",
                      "max_output_tokens": 16000, "temperature": 1.9,
                      "text_model": "un-autre-modele",
                      "text_base_url": "http://attaquant.example.com/v1"},
            )
        self.assertEqual(router.custom_options.max_output_tokens, 4096)
        self.assertEqual(router.custom_options.temperature, 0.5)

    def test_audit_records_revision_not_runtime_or_content(self):
        headers = grant_admin_session("it@example.com", "it_admin")
        router = OrganizationAIRouter(
            FakeTextInferenceBackend(), FakeSpeechInferenceBackend()
        )
        with patch.object(main, "ORGANIZATION_AI_ROUTER", router):
            response = self.client.put(
                "/api/control/organization/ai-runtime",
                headers=headers,
                json={
                    "expected_revision": 0,
                    "profile": "efficient",
                    "reasoning_allowed": False,
                    "concurrency_mode": "auto",
                },
            )
        self.assertEqual(response.status_code, 200)
        with main.db() as conn:
            row = conn.execute(
                "SELECT metadata FROM admin_audit_log WHERE action=?",
                ("ai_runtime.desired_config.update",),
            ).fetchone()
        metadata = json.loads(row["metadata"])
        self.assertEqual(set(metadata), {"old_revision", "new_revision", "apply_mode"})



class ContextWindowBoundTests(unittest.IsolatedAsyncioTestCase):
    """La sortie demandee doit tenir dans ce que le moteur annonce.

    Un moteur borne `prompt + max_tokens` par sa fenetre. Sans cette borne,
    le profil `standard` (2048 jetons) sur une fenetre de 2048 produit une
    requete que le moteur refuse en 400 — quelle que soit la dictee.
    """

    def _engine(self, *, window=2048, prompt_tokens=None, tokenize=True, seen=None):
        def handler(request: httpx.Request):
            path = request.url.path
            if path == "/tokenize":
                if not tokenize:
                    return httpx.Response(404, json={"detail": "Not Found"})
                body = json.loads(request.content)
                count = prompt_tokens
                if count is None:
                    count = sum(len(m["content"]) for m in body["messages"]) // 4
                return httpx.Response(
                    200, json={"count": count, "max_model_len": window}
                )
            if path == "/v1/models":
                return httpx.Response(
                    200,
                    json={"data": [{"id": "engine-model", "max_model_len": window}]},
                )
            if seen is not None:
                seen.append(json.loads(request.content))
            return httpx.Response(
                200,
                json={
                    "choices": [
                        {"message": {"content": "ok"}, "finish_reason": "stop"}
                    ]
                },
            )

        return OpenAICompatibleTextBackend(
            "http://127.0.0.1:8001/v1",
            "engine-model",
            transport=httpx.MockTransport(handler),
        )

    def _request(self, text, max_output_tokens=2048):
        return TextInferenceRequest(
            "r",
            TaskType.STYLE_TRANSFORM,
            text,
            "instruction",
            TextInferenceOptions(max_output_tokens, 0.3, ReasoningMode.OFF),
            30,
        )

    async def test_a_short_prompt_keeps_room_for_the_whole_answer(self):
        seen = []
        backend = self._engine(prompt_tokens=116, seen=seen)
        await backend.generate(self._request("Bonjour."))
        # 2048 demandes ; 2048 - 116 - 8 reellement disponibles.
        self.assertEqual(seen[0]["max_tokens"], 1924)

    async def test_a_long_prompt_shrinks_the_answer_instead_of_failing(self):
        seen = []
        backend = self._engine(prompt_tokens=1800, seen=seen)
        await backend.generate(self._request("mot " * 2000))
        self.assertEqual(seen[0]["max_tokens"], 240)

    async def test_a_request_never_exceeds_what_the_profile_asked_for(self):
        seen = []
        backend = self._engine(prompt_tokens=10, seen=seen)
        await backend.generate(self._request("court", max_output_tokens=256))
        self.assertEqual(seen[0]["max_tokens"], 256)

    async def test_too_little_room_is_an_explicit_error_not_a_bad_request(self):
        seen = []
        backend = self._engine(prompt_tokens=2000, seen=seen)
        with self.assertRaises(AIRuntimeError) as raised:
            await backend.generate(self._request("mot " * 5000))
        self.assertEqual(raised.exception.code, AI_PROMPT_TOO_LONG)
        # Rien n'est parti vers le moteur : la requete aurait ete invalide.
        self.assertEqual(seen, [])

    async def test_exactly_the_minimum_room_is_still_served(self):
        seen = []
        backend = self._engine(prompt_tokens=2048 - 8 - 64, seen=seen)
        await backend.generate(self._request("limite"))
        self.assertEqual(seen[0]["max_tokens"], 64)

    async def test_without_tokenize_the_fallback_never_overstates_capacity(self):
        seen = []
        backend = self._engine(tokenize=False, seen=seen)
        text = "x" * 400
        await backend.generate(self._request(text))
        characters = len(text) + len("instruction")
        estimated = characters // 2 + 8 * 2 + 8
        self.assertEqual(seen[0]["max_tokens"], 2048 - estimated - 8)
        self.assertLess(seen[0]["max_tokens"], 2048)

    async def test_an_unknown_window_changes_nothing(self):
        seen = []

        def handler(request: httpx.Request):
            if request.url.path in {"/tokenize", "/v1/models"}:
                return httpx.Response(404, json={"detail": "Not Found"})
            seen.append(json.loads(request.content))
            return httpx.Response(
                200,
                json={
                    "choices": [
                        {"message": {"content": "ok"}, "finish_reason": "stop"}
                    ]
                },
            )

        backend = OpenAICompatibleTextBackend(
            "http://127.0.0.1:8001/v1",
            "engine-model",
            transport=httpx.MockTransport(handler),
        )
        await backend.generate(self._request("texte"))
        self.assertEqual(seen[0]["max_tokens"], 2048)

    async def test_the_window_is_read_once_and_then_reused(self):
        calls = []

        def handler(request: httpx.Request):
            calls.append(request.url.path)
            if request.url.path == "/tokenize":
                return httpx.Response(404, json={"detail": "Not Found"})
            if request.url.path == "/v1/models":
                return httpx.Response(
                    200,
                    json={"data": [{"id": "engine-model", "max_model_len": 2048}]},
                )
            return httpx.Response(
                200,
                json={
                    "choices": [
                        {"message": {"content": "ok"}, "finish_reason": "stop"}
                    ]
                },
            )

        backend = OpenAICompatibleTextBackend(
            "http://127.0.0.1:8001/v1",
            "engine-model",
            transport=httpx.MockTransport(handler),
        )
        await backend.generate(self._request("un"))
        await backend.generate(self._request("deux"))
        self.assertEqual(calls.count("/v1/models"), 1)


class EngineRefusalLogTests(unittest.IsolatedAsyncioTestCase):
    """Un refus du moteur doit rester lisible sans exposer de contenu."""

    async def test_the_refusal_logs_status_and_type_but_never_the_message(self):
        dictated = "phrase dictee par un membre"

        def handler(request: httpx.Request):
            if request.url.path == "/tokenize":
                return httpx.Response(200, json={"count": 5, "max_model_len": 2048})
            return httpx.Response(
                400,
                json={
                    "error": {
                        "message": "refused: " + dictated,
                        "type": "BadRequestError",
                    }
                },
            )

        backend = OpenAICompatibleTextBackend(
            "http://127.0.0.1:8001/v1",
            "engine-model",
            transport=httpx.MockTransport(handler),
        )
        with self.assertLogs("nova", level="ERROR") as captured:
            with self.assertRaises(AIRuntimeError) as raised:
                await backend.generate(
                    TextInferenceRequest(
                        "r",
                        TaskType.STYLE_TRANSFORM,
                        dictated,
                        "instruction",
                        TextInferenceOptions(256, 0.3, ReasoningMode.OFF),
                        30,
                    )
                )
        self.assertEqual(raised.exception.code, AI_RUNTIME_FAILED)
        record = captured.records[0]
        self.assertEqual(record.getMessage(), "ai.engine_refused")
        self.assertEqual(record.fields["status"], 400)
        self.assertEqual(record.fields["error_type"], "BadRequestError")
        self.assertNotIn(dictated, json.dumps(record.fields))

    async def test_an_unreadable_refusal_still_logs_its_status(self):
        def handler(request: httpx.Request):
            if request.url.path == "/tokenize":
                return httpx.Response(200, json={"count": 5, "max_model_len": 2048})
            return httpx.Response(500, content=b"<html>engine crashed</html>")

        backend = OpenAICompatibleTextBackend(
            "http://127.0.0.1:8001/v1",
            "engine-model",
            transport=httpx.MockTransport(handler),
        )
        with self.assertLogs("nova", level="ERROR") as captured:
            with self.assertRaises(AIRuntimeError):
                await backend.generate(
                    TextInferenceRequest(
                        "r",
                        TaskType.REFORMULATION,
                        "texte",
                        "instruction",
                        TextInferenceOptions(256, 0.3, ReasoningMode.OFF),
                        30,
                    )
                )
        self.assertEqual(captured.records[0].fields["error_type"], "unknown")
        self.assertEqual(captured.records[0].fields["status"], 500)



class AdvancedProfileHardwareGateTests(unittest.TestCase):
    """`advanced` ne doit pas etre accepte sur une machine qui ne le tient pas.

    La detection de memoire video passe par `nvidia-smi`. Sur une machine sans
    carte NVIDIA, ou dans un conteneur qui n'expose pas l'outil, elle rend None
    -- et le garde-fou d'origine ne se declenchait que sur une valeur connue et
    trop basse. Il laissait donc passer `advanced` exactement sur les machines
    les moins capables de le servir.
    """

    def body(self, profile="advanced"):
        import main

        return main.AIRuntimeConfigUpdateRequest(
            expected_revision=1, profile=profile, reasoning_allowed=False
        )

    def validate(self, vram_gb):
        import ai_runtime
        import main

        hardware = ai_runtime.HardwareInfo(
            cpu="x", architecture="AMD64", ram_gb=32.0,
            gpu_vendor=None, gpu_model=None, vram_gb=vram_gb, driver_version=None,
        )
        with patch.object(main, "detect_hardware", return_value=hardware), \
             patch.object(main, "NOVA_SERVER_MODE", main.SERVER_MODE_DEDICATED):
            return main.validate_ai_runtime_update(self.body())

    def test_advanced_is_refused_when_the_video_memory_is_unknown(self):
        import main

        with self.assertRaises(Exception) as raised:
            self.validate(None)
        self.assertEqual(raised.exception.detail, "AI_RUNTIME_HARDWARE_INCOMPATIBLE")
        self.assertEqual(raised.exception.status_code, 409)
        del main

    def test_advanced_is_refused_below_the_minimum(self):
        with self.assertRaises(Exception) as raised:
            self.validate(8.0)
        self.assertEqual(raised.exception.detail, "AI_RUNTIME_HARDWARE_INCOMPATIBLE")

    def test_advanced_is_accepted_at_the_minimum(self):
        import ai_runtime

        resolved = self.validate(ai_runtime.ADVANCED_MINIMUM_VRAM_GB)
        self.assertEqual(resolved["profile"], ai_runtime.ModelProfile.ADVANCED)

    def test_the_recommendation_stays_prudent_without_a_measurement(self):
        """Sans mesure, on recommande le profil le plus modeste, pas le plus gros."""
        import ai_runtime

        unknown = ai_runtime.HardwareInfo(
            cpu="x", architecture="AMD64", ram_gb=None,
            gpu_vendor=None, gpu_model=None, vram_gb=None, driver_version=None,
        )
        self.assertEqual(
            ai_runtime.recommend_profile(unknown), ai_runtime.ModelProfile.EFFICIENT
        )


if __name__ == "__main__":
    unittest.main()
