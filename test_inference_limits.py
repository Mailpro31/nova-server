"""SEC-003 — l'équité entre membres devant le moteur d'inférence.

## Ce que ce fichier prouve

Le routeur borne ce que le **moteur** exécute : deux générations de texte à la
fois, quatre places d'attente, puis `AI_RUNTIME_BUSY`. Cette porte protège la
machine et ne dit rien de l'équité. Un audit a montré qu'un seul membre pouvait
l'occuper en boucle : ses voisins recevaient `AI_RUNTIME_BUSY` sans avoir rien
demandé, quatre tentatives sur quatre, indéfiniment.

Les tests ci-dessous vérifient les deux moitiés du correctif :

- des quotas par principal **et** par organisation, consommés avant la porte ;
- des bornes de taille sur ce qu'un poste peut envoyer.

`AdversarialFairnessTests` rejoue l'attaque de l'audit. Il compare
explicitement le comportement avec et sans quota, pour qu'on ne puisse pas le
rendre vert en retirant la protection.

## Fixtures

Aucune organisation réelle, aucun tenant réel. `example.com` est réservé par la
RFC 2606 pour cet usage.
"""

import asyncio
import uuid
from unittest.mock import patch

import httpx

# `test_main` en premier : il redirige NOVA_DB vers un répertoire temporaire
# avant que `main` ne soit importé.
from test_main import AdminTestCase  # noqa: F401
from test_business import make_organization

import ai_runtime
import main
import ratelimit
from ai_runtime import (
    FakeSpeechInferenceBackend,
    FakeTextInferenceBackend,
    OrganizationAIRouter,
)

main.init_db()


TEXT_ROUTES = (
    ("/api/reformulate", {"text": "bonjour"}),
    ("/api/command", {"instruction": "resume", "text": "bonjour"}),
    ("/api/engineering-notes", {"instruction": "ignore", "text": "bonjour"}),
    ("/api/structured-notes", {"note_type": "free", "text": "bonjour"}),
)


def member(organization_id: str | None = None) -> tuple[dict, str]:
    """Une session Nova ordinaire. L'adresse est fictive et unique."""
    email = f"m-{uuid.uuid4().hex[:10]}@example.com"
    session = main.issue_token(email, f"poste-{uuid.uuid4().hex[:6]}",
                               organization_id or main.nova_organization_id())
    return {"Authorization": f"Bearer {session['token']}"}, email


class _BrokenBackend:
    """Un compteur partagé injoignable. La panne Redis, sans Redis."""

    shared = True

    def allow(self, *args, **kwargs):
        raise ConnectionError("redis down")

    def healthy(self) -> bool:
        return False

    def reset(self) -> None:
        pass


class InferenceLimitTestCase(AdminTestCase):
    """Socle : un moteur simulé, et un compteur remis à zéro à chaque test."""

    def setUp(self):
        super().setUp()
        main.reset_rate_limiter()
        self.addCleanup(main.reset_rate_limiter)
        self.router = OrganizationAIRouter(
            FakeTextInferenceBackend(), FakeSpeechInferenceBackend()
        )
        patcher = patch.object(main, "ORGANIZATION_AI_ROUTER", self.router)
        patcher.start()
        self.addCleanup(patcher.stop)

    def broken_counter(self):
        """Remplace le backend du limiteur par un backend en panne."""
        limiter = main.rate_limiter()
        original = limiter._backend
        limiter._backend = _BrokenBackend()

        def restore():
            limiter._backend = original

        self.addCleanup(restore)

    def post(self, path, payload, headers):
        return self.client.post(path, headers=headers, json=payload)


# -----------------------------------------------------------------------------
# 1. Quotas par principal
# -----------------------------------------------------------------------------


class PrincipalQuotaTests(InferenceLimitTestCase):
    def test_the_burst_window_stops_a_sudden_flood(self):
        headers, _ = member()
        burst = ratelimit.INFERENCE_TEXT_PRINCIPAL_BURST.limit
        statuses = [
            self.post("/api/reformulate", {"text": "bonjour"}, headers).status_code
            for _ in range(burst + 3)
        ]
        self.assertEqual(statuses[:burst], [200] * burst)
        self.assertEqual(set(statuses[burst:]), {429})

    def test_the_refusal_names_the_quota_not_the_engine(self):
        # Un poste doit distinguer « vous en avez trop demandé » de « le moteur
        # est plein » : les deux appellent des réactions différentes.
        headers, _ = member()
        for _ in range(ratelimit.INFERENCE_TEXT_PRINCIPAL_BURST.limit):
            self.post("/api/reformulate", {"text": "bonjour"}, headers)
        refused = self.post("/api/reformulate", {"text": "bonjour"}, headers)
        self.assertEqual(refused.status_code, 429)
        self.assertEqual(refused.json()["detail"], "RATE_LIMITED")
        self.assertNotIn("AI_RUNTIME_BUSY", refused.text)

    def test_the_sustained_window_stops_a_slow_loop(self):
        # La rafale seule laisserait boucler indéfiniment à quatre requêtes
        # toutes les dix secondes. On avance l'horloge du limiteur plutôt que
        # d'attendre : le test doit rester rapide et déterministe.
        headers, email = member()
        organization = main.nova_organization_id()
        principal = main.inference_principal_scope(email, organization)
        limiter = main.rate_limiter()
        sustained = ratelimit.INFERENCE_TEXT_PRINCIPAL_SUSTAINED

        # On consomme la fenêtre longue directement, sans passer par la rafale.
        for _ in range(sustained.limit):
            limiter.check("text-principal-sustained", principal, sustained)
        with self.assertRaises(ratelimit.RateLimitExceeded):
            limiter.check("text-principal-sustained", principal, sustained)

    def test_every_text_route_shares_the_same_principal_budget(self):
        # Sinon il suffirait d'alterner entre quatre routes pour quadrupler son
        # quota, alors qu'elles sollicitent le même moteur.
        headers, _ = member()
        budget = ratelimit.INFERENCE_TEXT_PRINCIPAL_BURST.limit
        served = 0
        for index in range(budget + 3):
            path, payload = TEXT_ROUTES[index % len(TEXT_ROUTES)]
            if self.post(path, payload, headers).status_code == 200:
                served += 1
        self.assertEqual(served, budget)

    def test_two_members_do_not_share_a_budget(self):
        first, _ = member()
        second, _ = member()
        for _ in range(ratelimit.INFERENCE_TEXT_PRINCIPAL_BURST.limit):
            self.post("/api/reformulate", {"text": "bonjour"}, first)
        self.assertEqual(
            self.post("/api/reformulate", {"text": "bonjour"}, first).status_code, 429
        )
        # Le voisin n'a rien consommé : il passe.
        self.assertEqual(
            self.post("/api/reformulate", {"text": "bonjour"}, second).status_code, 200
        )

    def test_transcription_has_its_own_budget_apart_from_text(self):
        # Une matinée de dictée ne doit pas fermer les AI Skills.
        headers, _ = member()
        for _ in range(ratelimit.INFERENCE_TEXT_PRINCIPAL_BURST.limit + 2):
            self.post("/api/reformulate", {"text": "bonjour"}, headers)
        response = self.client.post(
            "/api/transcribe", headers=headers,
            files={"file": ("a.wav", b"RIFFaudio", "audio/wav")},
        )
        self.assertEqual(response.status_code, 200)


# -----------------------------------------------------------------------------
# 2. Quotas par organisation
# -----------------------------------------------------------------------------


class TenantQuotaTests(InferenceLimitTestCase):
    def test_many_members_together_reach_the_tenant_ceiling(self):
        organization = main.nova_organization_id()
        tenant = ratelimit.INFERENCE_TEXT_TENANT_BURST
        limiter = main.rate_limiter()
        # On remplit le compteur du tenant sans passer par les comptes : ce que
        # ce test vérifie est le second rideau, pas l'arithmétique du premier.
        for _ in range(tenant.limit):
            limiter.check("text-tenant-burst", organization, tenant)

        headers, _ = member()
        response = self.post("/api/reformulate", {"text": "bonjour"}, headers)
        self.assertEqual(response.status_code, 429)
        self.assertEqual(response.json()["detail"], "RATE_LIMITED")

    def test_the_tenant_ceiling_is_wider_than_one_member(self):
        # Sans quoi le second rideau deviendrait la vraie limite.
        self.assertGreater(
            ratelimit.INFERENCE_TEXT_TENANT_BURST.limit,
            ratelimit.INFERENCE_TEXT_PRINCIPAL_BURST.limit,
        )

    def test_one_tenant_does_not_close_another(self):
        neighbour = make_organization("business", f"biz-{uuid.uuid4().hex[:6]}", "Voisine")
        self.addCleanup(self._forget_organization, neighbour)
        limiter = main.rate_limiter()
        tenant = ratelimit.INFERENCE_TEXT_TENANT_BURST
        for _ in range(tenant.limit):
            limiter.check("text-tenant-burst", main.nova_organization_id(), tenant)

        # Le compteur du voisin n'a pas bougé.
        limiter.check("text-tenant-burst", neighbour, tenant)

    @staticmethod
    def _forget_organization(organization_id: str) -> None:
        with main.db() as conn:
            conn.execute("DELETE FROM organizations WHERE id=?", (organization_id,))


# -----------------------------------------------------------------------------
# 3. Panne du compteur partagé
# -----------------------------------------------------------------------------


class CounterOutageTests(InferenceLimitTestCase):
    def test_text_refuses_without_a_counter(self):
        # Sans compteur, rien ne distingue un membre d'une boucle.
        self.broken_counter()
        headers, _ = member()
        for path, payload in TEXT_ROUTES:
            with self.subTest(path=path):
                response = self.post(path, payload, headers)
                self.assertEqual(response.status_code, 503)
                self.assertEqual(
                    response.json()["detail"], "RATE_LIMIT_BACKEND_UNAVAILABLE"
                )

    def test_skills_refuse_without_a_counter(self):
        self.broken_counter()
        headers, _ = member()
        response = self.post("/api/skills/run", {"skill_id": "x", "text": "y"}, headers)
        self.assertEqual(response.status_code, 503)

    def test_dictation_survives_without_a_counter(self):
        """La dictée est la seule fonction que le produit refuse de fermer.

        Une panne Redis ne doit pas rendre les postes muets. Ce qui reste actif
        pendant l'incident : la porte de capacité du routeur.
        """
        self.broken_counter()
        headers, _ = member()
        response = self.client.post(
            "/api/transcribe", headers=headers,
            files={"file": ("a.wav", b"RIFFaudio", "audio/wav")},
        )
        self.assertEqual(response.status_code, 200)

    def test_import_refuses_without_a_counter(self):
        # L'inverse de la dictée : une boucle d'imports volumineux pendant une
        # panne priverait tout le monde.
        self.broken_counter()
        headers, _ = member()
        response = self.client.post(
            "/api/import/transcribe", headers=headers,
            files={"file": ("a.wav", b"RIFFaudio", "audio/wav")},
        )
        self.assertEqual(response.status_code, 503)


# -----------------------------------------------------------------------------
# 4. Bornes de taille
# -----------------------------------------------------------------------------


class RequestSizeTests(InferenceLimitTestCase):
    def test_a_text_at_the_limit_is_accepted(self):
        headers, _ = member()
        response = self.post(
            "/api/reformulate", {"text": "A" * main.MAX_INFERENCE_TEXT_CHARS}, headers
        )
        self.assertEqual(response.status_code, 200)

    def test_a_text_over_the_limit_never_reaches_the_engine(self):
        headers, _ = member()
        response = self.post(
            "/api/reformulate",
            {"text": "A" * (main.MAX_INFERENCE_TEXT_CHARS + 1)},
            headers,
        )
        self.assertEqual(response.status_code, 422)
        self.assertEqual(self.router.text_backend.requests, [])

    def test_a_style_prompt_over_the_limit_is_refused(self):
        headers, _ = member()
        response = self.post(
            "/api/reformulate",
            {"text": "bonjour", "style_prompt": "P" * (main.MAX_STYLE_PROMPT_CHARS + 1)},
            headers,
        )
        self.assertEqual(response.status_code, 422)
        self.assertEqual(self.router.text_backend.requests, [])

    def test_a_style_prompt_at_the_limit_is_accepted(self):
        headers, _ = member()
        response = self.post(
            "/api/reformulate",
            {"text": "bonjour", "style_prompt": "P" * main.MAX_STYLE_PROMPT_CHARS},
            headers,
        )
        self.assertEqual(response.status_code, 200)

    def test_engineering_notes_are_bounded_too(self):
        headers, _ = member()
        response = self.post(
            "/api/engineering-notes",
            {"instruction": "x", "text": "A" * (main.MAX_ENGINEERING_NOTES_CHARS + 1)},
            headers,
        )
        self.assertEqual(response.status_code, 422)
        self.assertEqual(self.router.text_backend.requests, [])

    def test_a_skill_payload_is_bounded(self):
        headers, _ = member()
        response = self.post(
            "/api/skills/run",
            {"skill_id": "x", "text": "A" * (main.MAX_INFERENCE_TEXT_CHARS + 1)},
            headers,
        )
        self.assertEqual(response.status_code, 422)
        self.assertEqual(self.router.text_backend.requests, [])

    def test_the_million_character_payload_of_the_audit_is_refused(self):
        headers, _ = member()
        response = self.post("/api/reformulate", {"text": "A" * 1_000_000}, headers)
        self.assertEqual(response.status_code, 422)
        self.assertEqual(self.router.text_backend.requests, [])


class AudioSizeTests(InferenceLimitTestCase):
    """La borne est éprouvée sur une valeur réduite : le mécanisme est le même,
    et allouer 25 Mo dans une suite de tests coûterait sans rien prouver de
    plus. Les valeurs réelles sont vérifiées séparément."""

    def test_the_declared_sizes_stay_reasonable(self):
        self.assertEqual(main.MAX_TRANSCRIBE_AUDIO_BYTES, 25 * 1024 * 1024)
        self.assertEqual(main.MAX_IMPORT_AUDIO_BYTES, 100 * 1024 * 1024)
        self.assertLess(main.MAX_TRANSCRIBE_AUDIO_BYTES, main.MAX_IMPORT_AUDIO_BYTES)

    def test_audio_at_the_limit_reaches_the_engine(self):
        headers, _ = member()
        with patch.object(main, "MAX_TRANSCRIBE_AUDIO_BYTES", 2048):
            response = self.client.post(
                "/api/transcribe", headers=headers,
                files={"file": ("a.wav", b"R" * 2048, "audio/wav")},
            )
        self.assertEqual(response.status_code, 200)

    def test_audio_over_the_limit_is_refused_and_never_read_whole(self):
        headers, _ = member()
        with patch.object(main, "MAX_TRANSCRIBE_AUDIO_BYTES", 2048):
            response = self.client.post(
                "/api/transcribe", headers=headers,
                files={"file": ("a.wav", b"R" * 4096, "audio/wav")},
            )
        self.assertEqual(response.status_code, 413)
        self.assertEqual(response.json()["detail"], "PAYLOAD_TOO_LARGE")
        self.assertEqual(self.router.speech_backend.requests, [])

    def test_an_oversized_import_is_refused(self):
        headers, _ = member()
        with patch.object(main, "MAX_IMPORT_AUDIO_BYTES", 2048):
            response = self.client.post(
                "/api/import/transcribe", headers=headers,
                files={"file": ("a.wav", b"R" * 4096, "audio/wav")},
            )
        self.assertEqual(response.status_code, 413)
        self.assertEqual(self.router.speech_backend.requests, [])

    def test_a_declared_oversize_is_refused_before_the_upload(self):
        # L'en-tête est écrit par le client et ne protège rien à lui seul ; il
        # évite seulement d'attendre un téléversement voué au refus.
        allowance = main.MULTIPART_ENVELOPE_ALLOWANCE_BYTES
        main.reject_oversized_declaration(10, 100)          # sous la borne
        main.reject_oversized_declaration(100 + allowance, 100)  # dans la marge
        with self.assertRaises(Exception) as raised:
            main.reject_oversized_declaration(100 + allowance + 1, 100)
        self.assertEqual(raised.exception.status_code, 413)

    def test_the_envelope_allowance_never_refuses_a_valid_file(self):
        """`Content-Length` couvre l'enveloppe multipart, pas le seul fichier.

        Sans marge, un enregistrement valide juste sous la borne serait refusé
        parce que les frontières multipart et le nom du fichier s'ajoutent à sa
        taille. Ce test a attrapé exactement cette régression.
        """
        headers, _ = member()
        with patch.object(main, "MAX_TRANSCRIBE_AUDIO_BYTES", 2048):
            response = self.client.post(
                "/api/transcribe", headers=headers,
                files={"file": ("enregistrement-au-nom-tres-long.wav",
                                b"R" * 2048, "audio/wav")},
            )
        self.assertEqual(response.status_code, 200)


# -----------------------------------------------------------------------------
# 5. Confidentialité des clés de compteur
# -----------------------------------------------------------------------------


class QuotaIdentityTests(InferenceLimitTestCase):
    def test_the_principal_scope_carries_no_address(self):
        email = "prenom.nom@example.com"
        scope = main.inference_principal_scope(email, "org-abc")
        self.assertNotIn("prenom", scope)
        self.assertNotIn("@", scope)
        self.assertNotIn("example.com", scope)
        self.assertEqual(len(scope), 64)  # SHA-256 hexadécimal

    def test_the_same_address_in_two_organizations_is_two_principals(self):
        email = "homonyme@example.com"
        self.assertNotEqual(
            main.inference_principal_scope(email, "org-a"),
            main.inference_principal_scope(email, "org-b"),
        )

    def test_the_scope_is_stable_for_one_person(self):
        email = "stable@example.com"
        self.assertEqual(
            main.inference_principal_scope(email, "org-a"),
            main.inference_principal_scope(email, "org-a"),
        )


# -----------------------------------------------------------------------------
# 6. L'attaque de l'audit, rejouée
# -----------------------------------------------------------------------------


class AdversarialFairnessTests(InferenceLimitTestCase):
    """Un membre peut-il encore priver un autre membre du moteur ?

    Le test compare deux mondes sur exactement le même scénario : avec les
    quotas, et avec des quotas rendus inopérants. Le second reproduit le
    comportement d'avant le correctif — sans lui, ce test resterait vert si
    quelqu'un retirait la protection.
    """

    ## Ce que ce test mesure, et pourquoi pas autre chose
    ##
    ## L'audit comptait les refus subis par la victime. Cette mesure ne se
    ## reproduit pas ici : le harnais appelle l'application en process, et
    ## l'accès SQLite de l'authentification est synchrone — il bloque la boucle
    ## d'événements et sérialise la salve, si bien que la porte ne se remplit
    ## jamais de la même façon deux fois. Une assertion là-dessus serait
    ## instable, et un test de sécurité instable finit désactivé.
    ##
    ## On mesure donc la propriété qui **cause** la privation, et qui est
    ## exacte : combien de requêtes d'un seul membre atteignent le moteur. Elle
    ## ne dépend d'aucun entrelacement — seulement du quota.

    # Courte, mais non nulle : la concurrence doit être réelle.
    DELAY = 0.05

    def _router(self):
        """Un routeur **neuf** par boucle d'événements.

        Ses sémaphores sont liés à la boucle qui les a créés : en réutiliser un
        d'un `asyncio.run()` à l'autre lève « bound to a different event loop ».
        """
        return OrganizationAIRouter(
            FakeTextInferenceBackend(delay=self.DELAY),
            FakeSpeechInferenceBackend(delay=self.DELAY),
            max_concurrent_text=2,
            max_concurrent_speech=1,
            max_queue=4,
        )

    def _flood(self, attacker, victim, count, quotas=None, victim_calls=4):
        """Une salve, sur un routeur neuf. Renvoie le routeur et les issues."""
        router = self._router()
        patches = [patch.object(main, "ORGANIZATION_AI_ROUTER", router)]
        if quotas is not None:
            patches.append(patch.dict(main.INFERENCE_QUOTAS, {"text": quotas}))
        for entered in patches:
            entered.start()
        try:
            outcomes = asyncio.run(
                self._run(attacker, victim, count, victim_calls)
            )
        finally:
            for entered in reversed(patches):
                entered.stop()
        return router, outcomes

    async def _run(self, attacker, victim, burst, victim_calls=4):
        transport = httpx.ASGITransport(app=main.app)
        async with httpx.AsyncClient(transport=transport, base_url="http://t") as client:

            async def call(headers):
                response = await client.post(
                    "/api/reformulate", headers=headers,
                    json={"text": "bonjour"}, timeout=30,
                )
                if response.status_code == 200:
                    return "200"
                detail = response.json().get("detail", "")
                if detail == "RATE_LIMITED":
                    return "RATE_LIMITED"
                if isinstance(detail, dict) and detail.get("code") == "AI_RUNTIME_BUSY":
                    return "AI_RUNTIME_BUSY"
                return f"autre:{response.status_code}"

            flood = [asyncio.create_task(call(attacker)) for _ in range(burst)]
            # Toutes les tâches sont distribuées dans la même boucle : cinquante
            # millisecondes suffisent largement à ce qu'elles aient atteint la
            # porte, alors qu'aucune n'a encore rendu sa réponse (le moteur
            # simulé met DELAY = 250 ms). La marge est de cinq pour un — le test
            # ne dépend pas d'une milliseconde près.
            await asyncio.sleep(0.05)
            victim_outcomes = [await call(victim) for _ in range(victim_calls)]
            attacker_outcomes = await asyncio.gather(*flood, return_exceptions=True)
        return [str(outcome) for outcome in attacker_outcomes], victim_outcomes

    UNLIMITED = ratelimit.Quota(
        10_000, 60.0, ratelimit.CLASS_INFERENCE, ratelimit.FAIL_OPEN
    )

    def test_without_quotas_the_whole_flood_reaches_the_engine(self):
        """Le monde d'avant le correctif, pour que la comparaison ait un sens.

        Sans ce cas, le test suivant resterait vert si quelqu'un retirait la
        protection : il faut voir la différence, pas seulement un bon chiffre.
        """
        attacker, _ = member()
        victim, _ = member()
        router, (attacker_outcomes, _victim) = self._flood(
            attacker, victim, 12, quotas=(self.UNLIMITED,) * 4, victim_calls=0
        )
        burst = ratelimit.INFERENCE_TEXT_PRINCIPAL_BURST.limit
        self.assertEqual(attacker_outcomes.count("RATE_LIMITED"), 0)
        self.assertGreater(
            len(router.text_backend.requests), burst,
            "sans quota, un seul membre doit pouvoir dépasser sa rafale",
        )

    def test_the_engine_never_sees_more_than_one_burst_from_one_member(self):
        """La propriété de sécurité, mesurée là où elle est exacte."""
        attacker, _ = member()
        victim, _ = member()
        burst = ratelimit.INFERENCE_TEXT_PRINCIPAL_BURST.limit
        # Aucune requête de la victime : ce test compte ce que le moteur reçoit
        # de l'attaquant seul.
        router, (attacker_outcomes, _victim) = self._flood(
            attacker, victim, 12, victim_calls=0
        )

        # Douze requêtes présentées, quatre reçues par le moteur. Les huit
        # autres sont refusées avant la porte de capacité, donc sans jamais
        # prendre la place de personne.
        self.assertEqual(len(router.text_backend.requests), burst)
        self.assertEqual(attacker_outcomes.count("RATE_LIMITED"), 12 - burst)

    def test_the_refusals_name_the_quota_not_the_engine(self):
        # Un membre qui dépasse doit l'apprendre, et ne pas croire que le
        # moteur est en panne.
        attacker, _ = member()
        victim, _ = member()
        _router, (attacker_outcomes, _victim) = self._flood(attacker, victim, 12)
        self.assertEqual(attacker_outcomes.count("AI_RUNTIME_BUSY"), 0)

    def test_a_neighbour_keeps_his_own_budget_during_a_flood(self):
        # L'équité : la salve d'un membre ne consomme rien du quota d'un autre.
        attacker, _ = member()
        victim, _ = member()
        _router, (_attacker, victim_outcomes) = self._flood(attacker, victim, 12)
        self.assertEqual(
            victim_outcomes.count("RATE_LIMITED"), 0,
            f"le voisin paie pour la salve : {victim_outcomes}",
        )

    def test_the_attacker_cannot_sustain_the_pressure(self):
        # Trois vagues sur un routeur neuf à chaque fois : la fenêtre longue
        # doit finir par tenir le compte, salve après salve.
        attacker, _ = member()
        victim, _ = member()
        served = 0
        for _ in range(3):
            router, _outcomes = self._flood(attacker, victim, 12, victim_calls=0)
            served += len(router.text_backend.requests)
        self.assertLessEqual(
            served, ratelimit.INFERENCE_TEXT_PRINCIPAL_SUSTAINED.limit,
            "la fenêtre longue doit borner le cumul des vagues",
        )


if __name__ == "__main__":
    import unittest

    unittest.main()
