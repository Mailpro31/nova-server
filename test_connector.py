"""Phase 33 — Private Connector : enrolement, synchronisation, isolation.

Ces tests vivent au niveau des garanties : qui peut s'enroler, ce qu'un
Connector peut voir, ce qu'il ne peut pas atteindre, et ce que le plan de
controle refuse de croire sur parole.
"""

# `test_main` doit etre importe en premier : il redirige NOVA_DB vers un
# repertoire temporaire avant que `main` ne cree son schema au moment de
# l'import. Sans cet ordre, importer `main` ecrirait dans la vraie base.
from test_main import AdminTestCase, grant_admin_session  # noqa: F401

import json
import os
import tempfile
import time
import unittest
import uuid
from pathlib import Path
from unittest.mock import patch

import connector as connector_service
import main


def clear_connectors():
    with main.db() as conn:
        conn.execute("DELETE FROM connectors")
        conn.execute("DELETE FROM connector_enrollments")
        conn.execute("DELETE FROM organization_ai_runtime_config")


class ConnectorTestCase(AdminTestCase):
    def setUp(self):
        super().setUp()
        clear_connectors()
        main._CONNECTOR_CALLS.clear()

    def auth(self, token):
        return {"Authorization": f"Bearer {token}"}

    def enrollment(self, deployment_id="deploiement-1", role="it_admin"):
        headers = grant_admin_session("it@example.com", role)
        response = self.client.post(
            "/api/control/connectors/enrollments",
            headers=headers,
            json={"display_name": "Connector de test", "deployment_id": deployment_id},
        )
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()

    def enroll(self, token, capabilities=None, protocol_version=1):
        return self.client.post(
            "/api/connector/enroll",
            json={
                "enrollment_token": token,
                "protocol_version": protocol_version,
                "connector_version": "0.1.0",
                "capabilities": capabilities
                if capabilities is not None
                else ["ai_runtime_config_v1", "health_reporting_v1"],
            },
        )

    def enrolled(self, **kwargs):
        return self.enroll(self.enrollment(**kwargs)["enrollment_token"]).json()

    def sync(self, credential, **overrides):
        body = {
            "protocol_version": 1,
            "connector_version": "0.1.0",
            "server_version": "0.2.0",
            "capabilities": ["ai_runtime_config_v1", "health_reporting_v1"],
            "applied_revision": 0,
            "apply_status": "pending",
            **overrides,
        }
        return self.client.post(
            "/api/connector/sync", headers=self.auth(credential), json=body
        )

    def desire(self, profile="standard", expected_revision=0):
        headers = grant_admin_session("it@example.com", "it_admin")
        with patch.object(main, "NOVA_SERVER_MODE", main.SERVER_MODE_CONTROL_PLANE):
            response = self.client.put(
                "/api/control/organization/ai-runtime",
                headers=headers,
                json={
                    "expected_revision": expected_revision,
                    "profile": profile,
                    "reasoning_allowed": False,
                    "concurrency_mode": "auto",
                },
            )
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()


class EnrollmentTests(ConnectorTestCase):
    def test_an_enrollment_code_is_shown_once_and_never_stored(self):
        created = self.enrollment()
        self.assertIn("enrollment_token", created)
        with main.db() as conn:
            row = conn.execute("SELECT * FROM connector_enrollments").fetchone()
        # La base ne porte que l'empreinte : une copie ne permet d'enroler
        # personne, et Nova ne peut pas reafficher le code.
        self.assertNotIn(created["enrollment_token"], json.dumps(dict(row)))
        self.assertEqual(row["token_hash"], main.hash_token(created["enrollment_token"]))

    def test_enrolling_binds_the_connector_to_the_admin_organization(self):
        created = self.enrollment()
        body = self.enroll(created["enrollment_token"]).json()
        self.assertEqual(body["organization_id"], main.nova_organization_id())
        self.assertEqual(body["deployment_id"], "deploiement-1")
        with main.db() as conn:
            row = conn.execute("SELECT * FROM connectors").fetchone()
        self.assertEqual(row["organization_id"], main.nova_organization_id())
        # Le credential durable n'est pas conserve non plus.
        self.assertEqual(
            row["credential_hash"], main.hash_token(body["connector_credential"])
        )

    def test_an_enrollment_code_cannot_be_used_twice(self):
        created = self.enrollment()
        self.assertEqual(self.enroll(created["enrollment_token"]).status_code, 200)
        second = self.enroll(created["enrollment_token"])
        self.assertEqual(second.status_code, 401)
        self.assertEqual(second.json()["detail"], "CONNECTOR_ENROLLMENT_INVALID")

    def test_an_expired_enrollment_code_is_refused(self):
        created = self.enrollment()
        with main.db() as conn:
            conn.execute(
                "UPDATE connector_enrollments SET expires_at=?",
                (time.time() - 1,),
            )
        self.assertEqual(self.enroll(created["enrollment_token"]).status_code, 401)

    def test_an_unknown_code_is_indistinguishable_from_a_consumed_one(self):
        """Trois refus, une seule reponse.

        Distinguer « inconnu », « deja utilise » et « expire » dirait a qui
        sonde lequel de ses essais a presque abouti.
        """
        created = self.enrollment()
        self.enroll(created["enrollment_token"])
        consumed = self.enroll(created["enrollment_token"])
        unknown = self.enroll("code-qui-nexiste-pas-du-tout-0123456789")
        self.assertEqual(consumed.status_code, unknown.status_code)
        self.assertEqual(consumed.json()["detail"], unknown.json()["detail"])

    def test_a_member_cannot_create_an_enrollment(self):
        for role, expected in (("read_only", 403), ("organization_admin", 403)):
            with self.subTest(role=role):
                headers = grant_admin_session(f"{role}@example.com", role)
                response = self.client.post(
                    "/api/control/connectors/enrollments",
                    headers=headers,
                    json={"display_name": "x", "deployment_id": "y"},
                )
                self.assertEqual(response.status_code, expected)

    def test_an_unsupported_protocol_is_refused_cleanly(self):
        created = self.enrollment()
        response = self.enroll(created["enrollment_token"], protocol_version=99)
        self.assertEqual(response.status_code, 426)
        self.assertEqual(response.json()["detail"], "CONNECTOR_PROTOCOL_UNSUPPORTED")


class CredentialTests(ConnectorTestCase):
    def test_an_invalid_credential_is_refused(self):
        self.assertEqual(self.sync("pas-un-credential").status_code, 401)

    def test_an_admin_session_token_is_not_a_connector_credential(self):
        """Les principaux ne se substituent pas les uns aux autres."""
        headers = grant_admin_session("it@example.com", "it_admin")
        admin_token = headers["Authorization"].split(" ", 1)[1]
        self.assertEqual(self.sync(admin_token).status_code, 401)

    def test_a_revoked_connector_can_no_longer_synchronize(self):
        body = self.enrolled()
        headers = grant_admin_session("it@example.com", "it_admin")
        revoked = self.client.post(
            f"/api/control/connectors/{body['connector_id']}/revoke", headers=headers
        )
        self.assertEqual(revoked.status_code, 200)
        self.assertEqual(revoked.json()["status"], "revoked")
        response = self.sync(body["connector_credential"])
        self.assertEqual(response.status_code, 401)
        self.assertEqual(response.json()["detail"], "CONNECTOR_CREDENTIAL_INVALID")

    def test_a_revoked_connector_is_indistinguishable_from_an_unknown_one(self):
        body = self.enrolled()
        headers = grant_admin_session("it@example.com", "it_admin")
        self.client.post(
            f"/api/control/connectors/{body['connector_id']}/revoke", headers=headers
        )
        revoked = self.sync(body["connector_credential"])
        unknown = self.sync("credential-inexistant-0123456789")
        self.assertEqual(revoked.json()["detail"], unknown.json()["detail"])

    def test_re_enrollment_creates_a_new_identity(self):
        first = self.enrolled()
        headers = grant_admin_session("it@example.com", "it_admin")
        self.client.post(
            f"/api/control/connectors/{first['connector_id']}/revoke", headers=headers
        )
        second = self.enrolled()
        self.assertNotEqual(first["connector_id"], second["connector_id"])
        self.assertNotEqual(
            first["connector_credential"], second["connector_credential"]
        )
        # L'ancien credential ne redevient jamais valide.
        self.assertEqual(self.sync(first["connector_credential"]).status_code, 401)
        self.assertEqual(self.sync(second["connector_credential"]).status_code, 200)


class SyncTests(ConnectorTestCase):
    def test_synchronizing_reports_presence_and_returns_the_desired_state(self):
        body = self.enrolled()
        self.desire(profile="efficient")
        answer = self.sync(body["connector_credential"])
        self.assertEqual(answer.status_code, 200)
        payload = answer.json()
        self.assertEqual(payload["desired"]["ai_runtime"]["profile"], "efficient")
        self.assertEqual(payload["desired"]["ai_runtime"]["revision"], 1)
        self.assertEqual(
            payload["supported_operations"],
            ["apply_ai_runtime_config", "report_health"],
        )

    def test_the_control_plane_pushes_only_declared_capabilities(self):
        """Pas de magie par numero de version.

        Un Connector qui ne declare pas savoir appliquer une configuration de
        runtime n'en recoit pas. Deduire la capacite d'une version marcherait
        jusqu'a la premiere version intermediaire.
        """
        body = self.enrolled()
        self.desire()
        answer = self.sync(
            body["connector_credential"], capabilities=["health_reporting_v1"]
        )
        self.assertEqual(answer.json()["desired"], {})

    def test_an_unknown_capability_is_dropped_not_trusted(self):
        body = self.enrolled()
        answer = self.sync(
            body["connector_credential"],
            capabilities=["ai_runtime_config_v1", "remote_shell_v1"],
        )
        self.assertEqual(answer.status_code, 200)
        with main.db() as conn:
            row = conn.execute("SELECT capabilities FROM connectors").fetchone()
        self.assertEqual(json.loads(row["capabilities"]), ["ai_runtime_config_v1"])

    def test_delivery_is_recorded_but_is_not_application(self):
        """Recevoir n'est pas appliquer.

        C'est la distinction que la Phase 32 ne pouvait pas faire, et la raison
        pour laquelle un « Applied » affiche trop tot enverrait chercher une
        panne dans le modele.
        """
        body = self.enrolled()
        self.desire()
        self.sync(body["connector_credential"])
        headers = grant_admin_session("it@example.com", "it_admin")
        view = self.client.get(
            "/api/control/organization/ai-runtime", headers=headers
        ).json()
        self.assertEqual(view["delivered"]["revision"], 1)
        self.assertEqual(view["reported"]["applied_revision"], 0)
        self.assertEqual(view["reported"]["apply_status"], "delivered")

    def test_an_applied_ack_advances_the_chain(self):
        body = self.enrolled()
        self.desire()
        self.sync(body["connector_credential"])
        self.sync(
            body["connector_credential"], applied_revision=1, apply_status="applied"
        )
        headers = grant_admin_session("it@example.com", "it_admin")
        view = self.client.get(
            "/api/control/organization/ai-runtime", headers=headers
        ).json()
        self.assertEqual(view["reported"]["applied_revision"], 1)
        self.assertEqual(view["reported"]["apply_status"], "applied")
        self.assertIsNotNone(view["reported"]["applied_at"])

    def test_a_failed_ack_never_marks_the_revision_applied(self):
        body = self.enrolled()
        self.desire()
        self.sync(body["connector_credential"])
        self.sync(
            body["connector_credential"],
            applied_revision=0,
            apply_status="failed",
            last_error_code="AI_RUNTIME_CONFIG_REJECTED",
        )
        headers = grant_admin_session("it@example.com", "it_admin")
        view = self.client.get(
            "/api/control/organization/ai-runtime", headers=headers
        ).json()
        self.assertEqual(view["reported"]["applied_revision"], 0)
        self.assertEqual(view["reported"]["apply_status"], "failed")
        self.assertEqual(
            view["reported"]["last_apply_error"], "AI_RUNTIME_CONFIG_REJECTED"
        )

    def test_an_applied_revision_never_goes_backwards(self):
        """Protection contre un Connector redemarre avec un etat perime."""
        body = self.enrolled()
        self.desire()
        self.sync(
            body["connector_credential"], applied_revision=1, apply_status="applied"
        )
        self.sync(
            body["connector_credential"], applied_revision=0, apply_status="applied"
        )
        with main.db() as conn:
            row = conn.execute("SELECT applied_revision FROM connectors").fetchone()
        self.assertEqual(row["applied_revision"], 1)

    def test_an_invalid_apply_status_is_refused(self):
        body = self.enrolled()
        response = self.sync(body["connector_credential"], apply_status="tout_va_bien")
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json()["detail"], "CONNECTOR_APPLY_STATUS_INVALID")

    def test_the_connector_clock_is_not_authoritative(self):
        body = self.enrolled()
        answer = self.sync(body["connector_credential"], local_time=1.0)
        # Le serveur date sa propre reponse ; l'heure du Connector reste un
        # renseignement de diagnostic.
        self.assertGreater(answer.json()["server_time"], 1_600_000_000)
        with main.db() as conn:
            row = conn.execute("SELECT last_seen_at FROM connectors").fetchone()
        self.assertGreater(row["last_seen_at"], 1_600_000_000)

    def test_health_carries_no_user_content(self):
        body = self.enrolled()
        self.sync(
            body["connector_credential"],
            health={
                "runtime_status": "healthy",
                "text_engine": "ready",
                "speech_engine": "ready",
                "queue_length": 2,
                "gpu_model": "GPU de test",
                "vram_gb": 8.0,
            },
        )
        headers = grant_admin_session("it@example.com", "it_admin")
        listing = self.client.get("/api/control/connectors", headers=headers).json()
        state = listing["connectors"][0]["runtime_state"]
        self.assertEqual(state["runtime_status"], "healthy")
        self.assertEqual(
            set(state),
            {
                "runtime_status",
                "text_engine",
                "speech_engine",
                "queue_length",
                "gpu_model",
                "vram_gb",
                "last_error_code",
            },
        )


class PayloadLimitTests(ConnectorTestCase):
    def test_unknown_fields_are_refused_not_ignored(self):
        """Parsing strict sur un protocole versionne.

        Ignorer un champ inconnu ferait croire a un futur Connector que le plan
        de controle a compris une instruction qu'il n'a jamais lue.
        """
        body = self.enrolled()
        response = self.client.post(
            "/api/connector/sync",
            headers=self.auth(body["connector_credential"]),
            json={
                "protocol_version": 1,
                "connector_version": "0.1.0",
                "capabilities": [],
                "apply_status": "pending",
                "execute_shell": "rm -rf /",
            },
        )
        self.assertEqual(response.status_code, 422)

    def test_too_many_capabilities_are_refused(self):
        body = self.enrolled()
        response = self.sync(
            body["connector_credential"],
            capabilities=[f"capacite_{index}" for index in range(64)],
        )
        self.assertEqual(response.status_code, 422)

    def test_an_oversized_body_is_refused_before_parsing(self):
        body = self.enrolled()
        response = self.client.post(
            "/api/connector/sync",
            headers={
                **self.auth(body["connector_credential"]),
                "Content-Length": str(main.CONNECTOR_MAX_BODY_BYTES + 1),
            },
            content=b"x" * (main.CONNECTOR_MAX_BODY_BYTES + 1),
        )
        self.assertEqual(response.status_code, 413)

    def test_a_chatty_connector_is_throttled_alone(self):
        first = self.enrolled()
        second = self.enrolled(deployment_id="deploiement-2")
        for _ in range(121):
            self.sync(first["connector_credential"])
        self.assertEqual(self.sync(first["connector_credential"]).status_code, 429)
        # Le voisin n'est pas puni pour le bruit du premier.
        self.assertEqual(self.sync(second["connector_credential"]).status_code, 200)


class TenantIsolationTests(ConnectorTestCase):
    """P0 : un Connector ne voit que son organisation."""

    def other_organization(self) -> str:
        organization_id = str(uuid.uuid4())
        with main.db() as conn:
            conn.execute(
                "INSERT INTO organizations(id, slug, display_name, org_type, "
                "status, created_at) VALUES (?,?,?,?,?,?)",
                (organization_id, "autre", "Autre organisation", "education",
                 "active", time.time()),
            )
        return organization_id

    def connector_for(self, organization_id: str) -> dict:
        """Un Connector d'un autre tenant, cree directement en base.

        Passer par l'API demanderait un administrateur de cet autre tenant ;
        ce que l'on veut verifier, c'est ce que l'authentification autorise une
        fois le Connector existant.
        """
        credential = "credential-autre-tenant-0123456789"
        with main.db() as conn:
            conn.execute(
                "INSERT INTO connectors(connector_id, organization_id, deployment_id, "
                "display_name, credential_hash, protocol_version, capabilities, "
                "created_at) VALUES (?,?,?,?,?,?,?,?)",
                (uuid.uuid4().hex, organization_id, "deploiement-b", "B",
                 main.hash_token(credential), 1,
                 json.dumps(["ai_runtime_config_v1"]), time.time()),
            )
        return {"connector_credential": credential}

    def test_a_connector_never_receives_another_tenant_desired_state(self):
        self.desire(profile="advanced")  # organisation de ce serveur
        other = self.connector_for(self.other_organization())
        answer = self.sync(other["connector_credential"])
        self.assertEqual(answer.status_code, 200)
        self.assertEqual(answer.json()["desired"], {})

    def test_a_connector_cannot_acknowledge_for_another_tenant(self):
        self.desire()
        other = self.connector_for(self.other_organization())
        self.sync(
            other["connector_credential"], applied_revision=1, apply_status="applied"
        )
        headers = grant_admin_session("it@example.com", "it_admin")
        view = self.client.get(
            "/api/control/organization/ai-runtime", headers=headers
        ).json()
        # L'organisation de ce serveur n'a rien applique.
        self.assertEqual(view["reported"]["applied_revision"], 0)

    def test_an_admin_sees_only_the_connectors_of_their_organization(self):
        self.enrolled()
        self.connector_for(self.other_organization())
        headers = grant_admin_session("it@example.com", "it_admin")
        listing = self.client.get("/api/control/connectors", headers=headers).json()
        self.assertEqual(len(listing["connectors"]), 1)
        self.assertEqual(listing["connectors"][0]["deployment_id"], "deploiement-1")

    def test_an_admin_cannot_revoke_another_tenant_connector(self):
        organization_id = self.other_organization()
        self.connector_for(organization_id)
        with main.db() as conn:
            row = conn.execute(
                "SELECT connector_id FROM connectors WHERE organization_id=?",
                (organization_id,),
            ).fetchone()
        headers = grant_admin_session("it@example.com", "it_admin")
        response = self.client.post(
            f"/api/control/connectors/{row['connector_id']}/revoke", headers=headers
        )
        self.assertEqual(response.status_code, 404)


class ConnectorStatusTests(ConnectorTestCase):
    def status_after(self, seconds_ago: float) -> str:
        body = self.enrolled()
        self.sync(body["connector_credential"])
        with main.db() as conn:
            conn.execute(
                "UPDATE connectors SET last_seen_at=?", (time.time() - seconds_ago,)
            )
            row = conn.execute("SELECT * FROM connectors").fetchone()
        return main.connector_status(row)

    def test_presence_is_deduced_from_the_last_contact(self):
        self.assertEqual(self.status_after(1), "online")
        self.assertEqual(
            self.status_after(main.CONNECTOR_DEGRADED_AFTER_SECONDS + 5), "degraded"
        )
        self.assertEqual(
            self.status_after(main.CONNECTOR_OFFLINE_AFTER_SECONDS + 5), "offline"
        )

    def test_a_connector_that_never_called_is_offline_not_missing(self):
        self.enrolled()
        headers = grant_admin_session("it@example.com", "it_admin")
        listing = self.client.get("/api/control/connectors", headers=headers).json()
        self.assertEqual(listing["connectors"][0]["status"], "offline")

    def test_revocation_survives_silence(self):
        # Un Connector revoque ne redevient pas « hors ligne » : la distinction
        # dit a l'administrateur si sa decision tient toujours.
        body = self.enrolled()
        headers = grant_admin_session("it@example.com", "it_admin")
        self.client.post(
            f"/api/control/connectors/{body['connector_id']}/revoke", headers=headers
        )
        listing = self.client.get("/api/control/connectors", headers=headers).json()
        self.assertEqual(listing["connectors"][0]["status"], "revoked")

    def test_remote_apply_is_not_claimed_without_a_reachable_connector(self):
        self.enrolled()  # enrole, jamais vu
        headers = grant_admin_session("it@example.com", "it_admin")
        with patch.object(main, "NOVA_SERVER_MODE", main.SERVER_MODE_CONTROL_PLANE):
            view = self.client.get(
                "/api/control/organization/ai-runtime", headers=headers
            ).json()
        self.assertFalse(view["remote_apply"]["supported"])

    def test_remote_apply_becomes_possible_once_a_capable_connector_reports(self):
        body = self.enrolled()
        self.sync(body["connector_credential"])
        headers = grant_admin_session("it@example.com", "it_admin")
        with patch.object(main, "NOVA_SERVER_MODE", main.SERVER_MODE_CONTROL_PLANE):
            view = self.client.get(
                "/api/control/organization/ai-runtime", headers=headers
            ).json()
        self.assertTrue(view["remote_apply"]["supported"])
        self.assertEqual(view["remote_apply"]["capable_connectors"], 1)


class AuditTests(ConnectorTestCase):
    def entries(self) -> list[dict]:
        with main.db() as conn:
            rows = conn.execute(
                "SELECT * FROM admin_audit_log ORDER BY created_at"
            ).fetchall()
        return [dict(row) for row in rows]

    def test_enrollment_and_revocation_are_audited_without_secrets(self):
        created = self.enrollment()
        body = self.enroll(created["enrollment_token"]).json()
        headers = grant_admin_session("it@example.com", "it_admin")
        self.client.post(
            f"/api/control/connectors/{body['connector_id']}/revoke", headers=headers
        )
        blob = json.dumps(self.entries())
        actions = {entry["action"] for entry in self.entries()}
        self.assertIn("connector.enrollment.create", actions)
        self.assertIn("connector.enrolled", actions)
        self.assertIn("connector.revoke", actions)
        self.assertNotIn(created["enrollment_token"], blob)
        self.assertNotIn(body["connector_credential"], blob)

    def test_the_connector_is_named_as_the_actor_not_a_person(self):
        created = self.enrollment()
        self.enroll(created["enrollment_token"])
        enrolled = [e for e in self.entries() if e["action"] == "connector.enrolled"][0]
        self.assertEqual(enrolled["actor_role"], "connector")
        self.assertIsNone(enrolled["actor_user_id"])

    def test_apply_outcomes_are_audited(self):
        body = self.enrolled()
        self.desire()
        self.sync(body["connector_credential"])
        self.sync(
            body["connector_credential"], applied_revision=1, apply_status="applied"
        )
        actions = {entry["action"] for entry in self.entries()}
        self.assertIn("connector.apply.applied", actions)


class DeploymentApplyTests(ConnectorTestCase):
    """Le point par lequel le Connector configure le nova-server local."""

    TOKEN = "jeton-de-deploiement-local-0123456789"

    def apply(self, token=None, **overrides):
        payload = {
            "revision": 4,
            "profile": "efficient",
            "reasoning_allowed": False,
            "max_concurrent_text": 2,
            "max_concurrent_speech": 1,
            "max_queue": 4,
            **overrides,
        }
        return self.client.post(
            "/api/deployment/ai-runtime/apply",
            headers=self.auth(token if token is not None else self.TOKEN),
            json=payload,
        )

    def test_the_route_does_not_exist_without_a_deployment_secret(self):
        with patch.object(main, "NOVA_DEPLOYMENT_APPLY_TOKEN", ""):
            response = self.apply()
        self.assertEqual(response.status_code, 404)
        self.assertEqual(response.json()["detail"], "DEPLOYMENT_APPLY_DISABLED")

    def test_a_wrong_secret_is_refused(self):
        with patch.object(main, "NOVA_DEPLOYMENT_APPLY_TOKEN", self.TOKEN):
            response = self.apply(token="pas-le-bon-jeton")
        self.assertEqual(response.status_code, 401)

    def test_a_valid_configuration_is_applied_and_confirmed(self):
        from ai_runtime import (
            FakeSpeechInferenceBackend,
            FakeTextInferenceBackend,
            OrganizationAIRouter,
        )

        router = OrganizationAIRouter(
            FakeTextInferenceBackend(), FakeSpeechInferenceBackend()
        )
        with patch.object(main, "NOVA_DEPLOYMENT_APPLY_TOKEN", self.TOKEN), patch.object(
            main, "ORGANIZATION_AI_ROUTER", router
        ):
            response = self.apply()
        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(body["status"], "applied")
        self.assertEqual(body["revision"], 4)
        self.assertEqual(router.applied_revision, 4)

    def test_an_impossible_configuration_is_reported_not_forced(self):
        from ai_runtime import (
            FakeSpeechInferenceBackend,
            FakeTextInferenceBackend,
            OrganizationAIRouter,
        )

        router = OrganizationAIRouter(
            FakeTextInferenceBackend(), FakeSpeechInferenceBackend()
        )
        with patch.object(main, "NOVA_DEPLOYMENT_APPLY_TOKEN", self.TOKEN), patch.object(
            main, "ORGANIZATION_AI_ROUTER", router
        ):
            response = self.apply(profile="custom")
        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(body["status"], "failed")
        self.assertEqual(body["error_code"], "AI_RUNTIME_PROFILE_UNAVAILABLE")
        # L'ancienne configuration reste active : un refus ne laisse pas le
        # parc sans reglage.
        self.assertEqual(router.profile, main.ModelProfile.STANDARD)

    def test_the_route_carries_no_runtime_url_or_model(self):
        # L'infrastructure appartient au deploiement. Le plan de controle envoie
        # un comportement, jamais une destination.
        fields = set(main.DeploymentApplyBody.model_fields)
        self.assertEqual(
            fields,
            {"revision", "profile", "reasoning_allowed", "max_concurrent_text",
             "max_concurrent_speech", "max_queue"},
        )


class ConnectorServiceTests(unittest.TestCase):
    """Le processus Connector lui-meme, sans reseau."""

    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.state = Path(self.directory.name) / "connector-state.json"
        patcher = patch.dict(
            os.environ, {"NOVA_CONNECTOR_STATE": str(self.state)}, clear=False
        )
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_the_state_file_is_written_restricted(self):
        connector_service.write_state({"connector_credential": "secret"})
        self.assertTrue(self.state.exists())
        if os.name != "nt":
            self.assertEqual(self.state.stat().st_mode & 0o777, 0o600)

    def test_an_injected_credential_wins_over_the_state_file(self):
        # Un deploiement qui passe son secret par son orchestrateur n'a pas a
        # le poser sur le disque.
        with patch.dict(os.environ, {"NOVA_CONNECTOR_CREDENTIAL": "injecte"}):
            self.assertEqual(
                connector_service.connector_credential({"connector_credential": "disque"}),
                "injecte",
            )
        self.assertEqual(
            connector_service.connector_credential({"connector_credential": "disque"}),
            "disque",
        )

    def test_a_plain_http_control_plane_is_refused_by_default(self):
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("NOVA_CONNECTOR_ALLOW_INSECURE", None)
            with self.assertRaises(SystemExit):
                connector_service.validate_control_plane_url("http://nova.example.com")
        self.assertEqual(
            connector_service.validate_control_plane_url("https://nova.example.com/"),
            "https://nova.example.com",
        )

    def test_plain_http_is_allowed_only_when_explicitly_asked(self):
        with patch.dict(os.environ, {"NOVA_CONNECTOR_ALLOW_INSECURE": "true"}):
            self.assertEqual(
                connector_service.validate_control_plane_url("http://127.0.0.1:8099"),
                "http://127.0.0.1:8099",
            )

    def test_an_already_applied_revision_is_not_reapplied(self):
        """La livraison est au-moins-une-fois ; l'application est idempotente."""
        state = {"last_applied_revision": 7, "last_apply_status": "applied"}
        with patch.object(connector_service, "apply_locally") as apply_locally:
            result = connector_service.reconcile(state, {"revision": 7})
        apply_locally.assert_not_called()
        self.assertEqual(result, state)

    def test_an_older_revision_is_never_applied(self):
        state = {"last_applied_revision": 7, "last_apply_status": "applied"}
        with patch.object(connector_service, "apply_locally") as apply_locally:
            result = connector_service.reconcile(state, {"revision": 6})
        apply_locally.assert_not_called()
        self.assertEqual(result["last_applied_revision"], 7)

    def test_a_new_revision_is_applied_and_persisted_before_acknowledgement(self):
        state = {"last_applied_revision": 7, "last_apply_status": "applied"}
        desired = {
            "revision": 8,
            "profile": "efficient",
            "reasoning_allowed": False,
            "max_concurrent_text": 2,
            "max_concurrent_speech": 1,
            "max_queue": 4,
        }
        with patch.object(
            connector_service, "apply_locally", return_value=("applied", None, None)
        ):
            result = connector_service.reconcile(state, desired)
        self.assertEqual(result["last_applied_revision"], 8)
        # Ecrit sur disque avant tout acquittement : si le processus meurt ici,
        # le redemarrage acquitte sans reappliquer.
        self.assertEqual(
            json.loads(self.state.read_text(encoding="utf-8"))["last_applied_revision"], 8
        )

    def test_a_failed_apply_does_not_advance_the_applied_revision(self):
        state = {"last_applied_revision": 7, "last_apply_status": "applied"}
        desired = {
            "revision": 8,
            "profile": "advanced",
            "reasoning_allowed": True,
            "max_concurrent_text": 2,
            "max_concurrent_speech": 1,
            "max_queue": 4,
        }
        with patch.object(
            connector_service,
            "apply_locally",
            return_value=("failed", "AI_RUNTIME_HARDWARE_INCOMPATIBLE", None),
        ):
            result = connector_service.reconcile(state, desired)
        self.assertEqual(result["last_applied_revision"], 7)
        self.assertEqual(result["last_apply_status"], "failed")
        self.assertEqual(result["last_error_code"], "AI_RUNTIME_HARDWARE_INCOMPATIBLE")

    def test_the_connector_declares_only_typed_capabilities(self):
        # Aucune capacite d'execution generique : le Connector n'est pas un
        # shell distant, et sa liste de capacites doit le montrer.
        self.assertEqual(
            connector_service.CAPABILITIES,
            ["ai_runtime_config_v1", "health_reporting_v1", "diagnostics_v1"],
        )
        for forbidden in ("exec", "shell", "command", "tunnel", "proxy", "file"):
            for capability in connector_service.CAPABILITIES:
                self.assertNotIn(forbidden, capability)

    def test_the_sync_payload_carries_only_control_data(self):
        """On inspecte ce que le Connector **envoie**, pas ce qu'il raconte.

        Une assertion sur la prose du module passerait au vert le jour ou
        quelqu'un ajoute un champ sans toucher au commentaire. Ce qui compte est
        le corps reellement transmis au plan de controle.
        """
        captured = {}

        class Answer:
            status_code = 200

            @staticmethod
            def json():
                return {"heartbeat_seconds": 45, "desired": {}}

            @staticmethod
            def raise_for_status():
                return None

        def fake_post(url, headers=None, json=None, timeout=None):
            captured["body"] = json
            return Answer()

        state = {
            "control_plane_url": "https://nova.example.com",
            "connector_credential": "secret",
            "last_applied_revision": 3,
            "last_apply_status": "applied",
        }
        with patch.object(connector_service.httpx, "post", fake_post), patch.object(
            connector_service, "local_health", return_value={"runtime_status": "healthy"}
        ):
            connector_service.sync_once(state)

        self.assertEqual(
            set(captured["body"]),
            {
                "protocol_version",
                "connector_version",
                "server_version",
                "capabilities",
                "applied_revision",
                "apply_status",
                "last_error_code",
                "health",
                "local_time",
            },
        )
        blob = json.dumps(captured["body"]).lower()
        for forbidden in ("transcript", "prompt", "audio", "clipboard", "text"):
            self.assertNotIn(forbidden, blob)


if __name__ == "__main__":
    unittest.main()
