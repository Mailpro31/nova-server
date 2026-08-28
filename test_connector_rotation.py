"""Phase 34 — rotation du credential Connector et canal local durci.

Deux frontières de confiance, testées séparément parce qu'elles doivent le
rester : une compromission de l'une ne doit pas ouvrir l'autre.
"""

# `test_main` doit etre importe en premier : il redirige NOVA_DB vers un
# repertoire temporaire avant que `main` ne cree son schema au moment de
# l'import. Sans cet ordre, importer `main` ecrirait dans la vraie base.
from test_main import AdminTestCase, grant_admin_session  # noqa: F401

import json
import os
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient

import connector as connector_service
import local_channel
import main
from test_connector import ConnectorTestCase


class RotationTestCase(ConnectorTestCase):
    def rotate_body(self):
        return {"protocol_version": 1, "connector_version": "0.1.0"}

    def enrolled_rotating(self):
        """Un Connector qui declare savoir faire tourner son credential."""
        body = self.enrolled()
        self.sync(
            body["connector_credential"],
            capabilities=["ai_runtime_config_v1", "credential_rotation_v1"],
        )
        return body

    def request_rotation(self, connector_id, token=None):
        headers = token or grant_admin_session("it@example.com", "it_admin")
        return self.client.post(
            f"/api/control/connectors/{connector_id}/rotate", headers=headers
        )

    def rotate(self, credential):
        return self.client.post(
            "/api/connector/credential/rotate",
            headers=self.auth(credential),
            json=self.rotate_body(),
        )

    def confirm(self, credential):
        return self.client.post(
            "/api/connector/credential/confirm",
            headers=self.auth(credential),
            json=self.rotate_body(),
        )


class RotationRequestTests(RotationTestCase):
    def test_an_admin_requests_rotation_and_receives_no_secret(self):
        """Le point du dessin.

        Faire transiter un credential durable par un navigateur
        d'administration en ferait la piece la plus exposee de la chaine. La
        console pose un drapeau ; le Connector fait la rotation lui-meme.
        """
        body = self.enrolled_rotating()
        response = self.request_rotation(body["connector_id"])
        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["credential"]["rotation_state"], "requested")
        blob = json.dumps(payload)
        self.assertNotIn("credential_hash", blob)
        self.assertNotIn(body["connector_credential"], blob)

    def test_the_connector_sees_the_request_on_its_next_sync(self):
        body = self.enrolled_rotating()
        self.request_rotation(body["connector_id"])
        answer = self.sync(
            body["connector_credential"],
            capabilities=["ai_runtime_config_v1", "credential_rotation_v1"],
        )
        self.assertTrue(answer.json()["rotation_requested"])

    def test_a_connector_without_the_capability_is_never_asked(self):
        """Emettre un ordre qu'un Connector ne sait pas lire le laisserait
        « en rotation » pour toujours."""
        body = self.enrolled()  # capacites Phase 33 uniquement
        response = self.request_rotation(body["connector_id"])
        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.json()["detail"], "CONNECTOR_ROTATION_UNSUPPORTED")

    def test_a_read_only_admin_cannot_request_rotation(self):
        body = self.enrolled_rotating()
        headers = grant_admin_session("lecture@example.com", "read_only")
        self.assertEqual(
            self.request_rotation(body["connector_id"], headers).status_code, 403
        )

    def test_a_revoked_connector_cannot_be_rotated(self):
        """Un credential compromis se revoque ; il ne se fait pas tourner.

        Transformer un cas compromis en rotation silencieuse laisserait
        l'attaquant conduire la bascule.
        """
        body = self.enrolled_rotating()
        headers = grant_admin_session("it@example.com", "it_admin")
        self.client.post(
            f"/api/control/connectors/{body['connector_id']}/revoke", headers=headers
        )
        response = self.request_rotation(body["connector_id"])
        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.json()["detail"], "CONNECTOR_REVOKED")

    def test_a_second_request_while_one_is_running_is_refused(self):
        body = self.enrolled_rotating()
        self.assertEqual(self.request_rotation(body["connector_id"]).status_code, 200)
        second = self.request_rotation(body["connector_id"])
        self.assertEqual(second.status_code, 409)
        self.assertEqual(second.json()["detail"], "CONNECTOR_ROTATION_IN_PROGRESS")


class RotationFlowTests(RotationTestCase):
    def test_a_full_rotation_replaces_the_credential_without_re_enrollment(self):
        body = self.enrolled_rotating()
        old = body["connector_credential"]

        issued = self.rotate(old)
        self.assertEqual(issued.status_code, 200)
        new = issued.json()["connector_credential"]
        self.assertNotEqual(new, old)
        self.assertEqual(issued.json()["generation"], 2)

        confirmed = self.confirm(new)
        self.assertEqual(confirmed.status_code, 200)
        self.assertFalse(confirmed.json()["already_confirmed"])

        # Le nouveau vaut, l'ancien ne vaut plus. Aucun re-enrolement.
        self.assertEqual(self.sync(new).status_code, 200)
        self.assertEqual(self.sync(old).status_code, 401)

    def test_the_old_credential_still_works_until_the_new_one_is_proven(self):
        """La fenetre de chevauchement existe pour une raison.

        Reveler le nouveau et tuer l'ancien dans le meme geste transformerait
        la moindre ecriture disque ratee en re-enrolement manuel.
        """
        body = self.enrolled_rotating()
        old = body["connector_credential"]
        new = self.rotate(old).json()["connector_credential"]
        # Les deux valent, tant que la preuve n'a pas eu lieu.
        self.assertEqual(self.sync(old).status_code, 200)
        self.assertEqual(self.sync(new).status_code, 200)

    def test_the_confirmation_must_use_the_new_credential(self):
        # Le serveur ne croit pas sur parole qu'un secret a ete recu : il
        # constate que le Connector sait s'en servir.
        body = self.enrolled_rotating()
        old = body["connector_credential"]
        self.rotate(old)
        response = self.confirm(old)
        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.json()["detail"], "CONNECTOR_ROTATION_FAILED")

    def test_confirming_twice_is_idempotent(self):
        """La livraison est au-moins-une-fois : un ACK perdu ne doit pas couter
        une identite."""
        body = self.enrolled_rotating()
        new = self.rotate(body["connector_credential"]).json()["connector_credential"]
        self.assertFalse(self.confirm(new).json()["already_confirmed"])
        replay = self.confirm(new)
        self.assertEqual(replay.status_code, 200)
        self.assertTrue(replay.json()["already_confirmed"])
        self.assertEqual(replay.json()["generation"], 2)
        self.assertEqual(self.sync(new).status_code, 200)

    def test_a_second_rotation_while_pending_is_refused(self):
        body = self.enrolled_rotating()
        old = body["connector_credential"]
        self.assertEqual(self.rotate(old).status_code, 200)
        second = self.rotate(old)
        self.assertEqual(second.status_code, 409)
        self.assertEqual(second.json()["detail"], "CONNECTOR_ROTATION_IN_PROGRESS")

    def test_the_pending_credential_cannot_ask_for_a_third_one(self):
        body = self.enrolled_rotating()
        new = self.rotate(body["connector_credential"]).json()["connector_credential"]
        self.assertEqual(self.rotate(new).status_code, 409)

    def test_an_expired_rotation_leaves_the_current_credential_valid(self):
        """Echouer dans le sens sur.

        Invalider l'ancien parce que le nouveau n'est pas arrive enfermerait
        dehors un Connector qui n'a rien fait de mal.
        """
        body = self.enrolled_rotating()
        old = body["connector_credential"]
        new = self.rotate(old).json()["connector_credential"]
        with main.db() as conn:
            conn.execute(
                "UPDATE connectors SET pending_created_at=?",
                (time.time() - main.CONNECTOR_ROTATION_TTL_SECONDS - 5,),
            )
        self.assertEqual(self.sync(new).status_code, 401)
        self.assertEqual(self.sync(old).status_code, 200)
        with main.db() as conn:
            row = conn.execute("SELECT * FROM connectors").fetchone()
        self.assertIsNone(row["pending_credential_hash"])
        self.assertEqual(row["rotation_state"], "failed")

    def test_rotation_is_rate_limited(self):
        body = self.enrolled_rotating()
        new = self.rotate(body["connector_credential"]).json()["connector_credential"]
        self.confirm(new)
        # Un Connector compromis ne doit pas pouvoir fabriquer des milliers de
        # secrets.
        self.assertEqual(self.rotate(new).status_code, 429)

    def test_no_credential_is_ever_stored_in_clear(self):
        body = self.enrolled_rotating()
        old = body["connector_credential"]
        new = self.rotate(old).json()["connector_credential"]
        with main.db() as conn:
            row = dict(conn.execute("SELECT * FROM connectors").fetchone())
        blob = json.dumps(row)
        self.assertNotIn(old, blob)
        self.assertNotIn(new, blob)
        self.assertEqual(row["pending_credential_hash"], main.hash_token(new))

    def test_the_pending_hash_disappears_once_the_rotation_completes(self):
        # Conserver un historique d'empreintes n'aurait servi qu'a agrandir la
        # surface d'une fuite de base.
        body = self.enrolled_rotating()
        new = self.rotate(body["connector_credential"]).json()["connector_credential"]
        self.confirm(new)
        with main.db() as conn:
            row = conn.execute("SELECT * FROM connectors").fetchone()
        self.assertIsNone(row["pending_credential_hash"])
        self.assertIsNone(row["pending_created_at"])
        self.assertEqual(row["rotation_state"], "stable")
        self.assertEqual(row["credential_generation"], 2)
        self.assertIsNotNone(row["last_rotated_at"])

    def test_rotation_is_audited_without_secrets(self):
        body = self.enrolled_rotating()
        self.request_rotation(body["connector_id"])
        new = self.rotate(body["connector_credential"]).json()["connector_credential"]
        self.confirm(new)
        with main.db() as conn:
            rows = [dict(r) for r in conn.execute("SELECT * FROM admin_audit_log")]
        actions = {row["action"] for row in rows}
        self.assertIn("connector.rotation.requested", actions)
        self.assertIn("connector.rotation.started", actions)
        self.assertIn("connector.rotation.completed", actions)
        blob = json.dumps(rows)
        self.assertNotIn(new, blob)
        self.assertNotIn(body["connector_credential"], blob)
        self.assertNotIn(main.hash_token(new), blob)


class RotationIsolationTests(RotationTestCase):
    """P0 : la rotation ne franchit pas la frontiere de tenant."""

    def test_an_admin_cannot_rotate_another_tenant_connector(self):
        import uuid

        organization_b = str(uuid.uuid4())
        with main.db() as conn:
            conn.execute(
                "INSERT INTO organizations(id, slug, display_name, org_type, status, "
                "created_at) VALUES (?,?,?,?,?,?)",
                (organization_b, "b", "B", "education", "active", time.time()),
            )
            conn.execute(
                "INSERT INTO connectors(connector_id, organization_id, deployment_id, "
                "display_name, credential_hash, protocol_version, capabilities, "
                "created_at) VALUES (?,?,?,?,?,?,?,?)",
                ("connector-b", organization_b, "dep-b", "B",
                 main.hash_token("credential-b-0123456789"), 1,
                 json.dumps(["credential_rotation_v1"]), time.time()),
            )
        self.assertEqual(self.request_rotation("connector-b").status_code, 404)

    def test_a_connector_rotates_only_its_own_credential(self):
        # L'identite vient du principal authentifie ; le corps ne porte aucun
        # identifiant de Connector, et ne peut donc pas en designer un autre.
        self.assertEqual(
            set(main.ConnectorRotateBody.model_fields),
            {"protocol_version", "connector_version"},
        )


class LocalChannelTests(unittest.TestCase):
    """Le canal entre le Connector et nova-server, sur la meme machine."""

    def setUp(self):
        self.client = TestClient(local_channel.internal_app)

    def auth(self, token):
        return {"Authorization": f"Bearer {token}"}

    def test_the_internal_routes_are_absent_from_the_public_api(self):
        """La propriete structurelle de la Phase 34.

        Une session Desktop ou d'administration arrivant sur l'API publique ne
        peut pas atteindre cette surface, quels que soient ses droits — non pas
        parce qu'un garde la refuse, mais parce qu'elle n'y est pas.
        """
        public = {getattr(route, "path", "") for route in main.app.routes}
        internal = {
            getattr(route, "path", "") for route in local_channel.internal_app.routes
        }
        operations = {path for path in internal if path.startswith("/internal/")}
        self.assertTrue(operations)
        self.assertEqual(operations & public, set())

    def test_a_desktop_or_admin_token_does_not_open_the_local_channel(self):
        with patch.object(
            local_channel, "LOCAL_TRANSPORT", local_channel.LOCAL_TRANSPORT_LOOPBACK
        ):
            for token in ("jeton-de-session-desktop", "jeton-admin-quelconque"):
                with self.subTest(token=token):
                    response = self.client.get(
                        "/internal/connector/runtime", headers=self.auth(token)
                    )
                    self.assertEqual(response.status_code, 401)
                    self.assertEqual(
                        response.json()["detail"], "LOCAL_CONNECTOR_AUTH_FAILED"
                    )

    def test_the_ephemeral_token_opens_it(self):
        with patch.object(
            local_channel, "LOCAL_TRANSPORT", local_channel.LOCAL_TRANSPORT_LOOPBACK
        ):
            response = self.client.get(
                "/internal/connector/capabilities",
                headers=self.auth(local_channel.loopback_token()),
            )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["transport"], "loopback")

    def test_the_socket_permissions_are_the_authentication_over_unix(self):
        # Ajouter un jeton par-dessus le socket ne protegerait de rien de plus
        # et reintroduirait le secret partage que cette phase retire.
        with patch.object(
            local_channel, "LOCAL_TRANSPORT", local_channel.LOCAL_TRANSPORT_UNIX
        ):
            response = self.client.get("/internal/connector/capabilities")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["transport"], "unix")

    def test_only_typed_operations_exist(self):
        with patch.object(
            local_channel, "LOCAL_TRANSPORT", local_channel.LOCAL_TRANSPORT_UNIX
        ):
            operations = self.client.get(
                "/internal/connector/capabilities"
            ).json()["operations"]
        self.assertEqual(
            operations,
            [
                "get_local_capabilities",
                "get_local_runtime_state",
                "apply_ai_runtime_config",
                "report_local_health",
            ],
        )
        for forbidden in ("exec", "shell", "sql", "fetch", "proxy", "file"):
            for operation in operations:
                self.assertNotIn(forbidden, operation)

    def test_a_malformed_apply_is_refused(self):
        with patch.object(
            local_channel, "LOCAL_TRANSPORT", local_channel.LOCAL_TRANSPORT_UNIX
        ):
            response = self.client.post(
                "/internal/connector/runtime/apply",
                json={"revision": 1, "profile": "standard", "execute": "rm -rf /"},
            )
        self.assertEqual(response.status_code, 422)

    def test_applying_through_the_local_channel_reconfigures_the_runtime(self):
        from ai_runtime import (
            FakeSpeechInferenceBackend,
            FakeTextInferenceBackend,
            OrganizationAIRouter,
        )

        router = OrganizationAIRouter(
            FakeTextInferenceBackend(), FakeSpeechInferenceBackend()
        )
        with patch.object(
            local_channel, "LOCAL_TRANSPORT", local_channel.LOCAL_TRANSPORT_UNIX
        ), patch.object(main, "ORGANIZATION_AI_ROUTER", router):
            response = self.client.post(
                "/internal/connector/runtime/apply",
                json={
                    "revision": 9,
                    "profile": "efficient",
                    "reasoning_allowed": False,
                    "max_concurrent_text": 2,
                    "max_concurrent_speech": 1,
                    "max_queue": 4,
                },
            )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["status"], "applied")
        self.assertEqual(router.applied_revision, 9)


class LocalTransportTests(unittest.TestCase):
    def test_an_unknown_transport_fails_closed(self):
        with patch.object(local_channel, "LOCAL_TRANSPORT", "carrier-pigeon"):
            plan = local_channel.transport_plan()
        self.assertFalse(plan["available"])
        self.assertEqual(plan["error_code"], "LOCAL_TRANSPORT_UNAVAILABLE")

    def test_unix_on_a_platform_without_af_unix_fails_closed(self):
        """Pas de repli silencieux vers la boucle locale.

        Un deploiement de production qui glisserait sur le canal de
        developpement sans que personne ne le decide serait exactement la
        degradation que cette phase rend impossible.
        """
        with patch.object(
            local_channel, "LOCAL_TRANSPORT", local_channel.LOCAL_TRANSPORT_UNIX
        ), patch.object(local_channel, "unix_sockets_available", lambda: False):
            plan = local_channel.transport_plan()
        self.assertFalse(plan["available"])
        self.assertEqual(plan["error_code"], "LOCAL_TRANSPORT_UNAVAILABLE")
        self.assertNotIn("host", plan)

    def test_the_loopback_plan_never_binds_beyond_the_machine(self):
        with patch.object(
            local_channel, "LOCAL_TRANSPORT", local_channel.LOCAL_TRANSPORT_LOOPBACK
        ):
            plan = local_channel.transport_plan()
        self.assertTrue(plan["available"])
        self.assertEqual(plan["host"], "127.0.0.1")
        self.assertNotEqual(plan["host"], "0.0.0.0")

    def test_the_socket_directory_is_secured_before_the_socket_exists(self):
        """La fenetre observee sur Linux, fermee par l'ordre des operations.

        uvicorn cree le socket puis le passe explicitement a 0o666 —
        world-writable. Mesure en conteneur : 0o755 -> 0o666. Un chmod apres
        coup ne fait que raccourcir cette fenetre ; un repertoire non
        traversable la rend inatteignable, quel que soit le mode du socket.
        """
        with tempfile.TemporaryDirectory() as base:
            socket_path = Path(base) / "nova" / "control.sock"
            directory = local_channel.prepare_socket_directory(str(socket_path))
            self.assertTrue(directory.is_dir())
            if sys.platform != "win32":
                mode = directory.stat().st_mode & 0o777
                self.assertEqual(mode, local_channel.SOCKET_DIRECTORY_MODE)
                # Ni traversable ni lisible par le reste du monde : c'est cette
                # propriete qui ferme la fenetre.
                self.assertEqual(mode & 0o007, 0)

    def test_preparing_the_directory_clears_a_stale_socket(self):
        # Un socket laisse par un arret brutal ferait echouer `bind()`, et le
        # serveur refuserait de demarrer pour un fichier mort.
        if sys.platform == "win32":
            self.skipTest("AF_UNIX indisponible")
        import socket as socket_module

        with tempfile.TemporaryDirectory() as base:
            socket_path = Path(base) / "nova" / "control.sock"
            socket_path.parent.mkdir(parents=True)
            listener = socket_module.socket(socket_module.AF_UNIX)
            listener.bind(str(socket_path))
            listener.close()
            self.assertTrue(socket_path.exists())
            local_channel.prepare_socket_directory(str(socket_path))
            self.assertFalse(socket_path.exists())

    def test_the_unix_plan_asks_for_restricted_socket_permissions(self):
        with patch.object(
            local_channel, "LOCAL_TRANSPORT", local_channel.LOCAL_TRANSPORT_UNIX
        ), patch.object(local_channel, "unix_sockets_available", lambda: True):
            plan = local_channel.transport_plan()
        self.assertTrue(plan["available"])
        self.assertEqual(plan["socket_mode"], 0o660)
        # Ni lisible ni ecrivable par le reste du monde.
        self.assertEqual(plan["socket_mode"] & 0o007, 0)
        self.assertEqual(plan["directory_mode"], 0o770)
        self.assertEqual(plan["directory_mode"] & 0o007, 0)

    def test_the_local_token_is_ephemeral_not_configured(self):
        """Un jeton qui survit au redemarrage redeviendrait le secret statique
        que cette phase supprime."""
        source = Path("local_channel.py").read_text(encoding="utf-8")
        self.assertIn("secrets.token_urlsafe(32)", source)
        self.assertNotIn("NOVA_CONNECTOR_LOCAL_SECRET", source)
        self.assertNotIn(
            'os.getenv("NOVA_DEPLOYMENT_APPLY_TOKEN"', source
        )


class LocalSecretFileTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.path = Path(self.directory.name) / "local-auth"

    def test_the_file_is_written_atomically_and_restricted(self):
        local_channel.write_restricted(self.path, "secret", 0o600)
        self.assertEqual(self.path.read_text(encoding="utf-8"), "secret")
        # Aucun fichier temporaire ne survit a l'ecriture.
        self.assertEqual(
            [p.name for p in self.path.parent.iterdir()], ["local-auth"]
        )

    @unittest.skipIf(sys.platform == "win32", "permissions POSIX indisponibles")
    def test_the_file_is_not_world_readable_on_posix(self):
        local_channel.write_restricted(self.path, "secret", 0o600)
        self.assertEqual(self.path.stat().st_mode & 0o777, 0o600)

    @unittest.skipUnless(sys.platform == "win32", "comportement Windows")
    def test_windows_permissions_are_not_claimed(self):
        # Le code tente le chmod et n'echoue pas ; il ne pretend pas non plus
        # que le fichier est protege. La documentation porte cette limite.
        local_channel.write_restricted(self.path, "secret", 0o600)
        self.assertTrue(self.path.exists())


class CredentialSeparationTests(RotationTestCase):
    """Les deux frontieres ne partagent aucun secret."""

    def test_the_cloud_credential_is_never_sent_to_the_data_plane(self):
        """Une compromission d'une frontiere ne doit pas ouvrir l'autre."""
        captured = {}

        class Answer:
            status_code = 200

            @staticmethod
            def json():
                return {"status": "applied", "revision": 1}

        class Session:
            def __enter__(self):
                return self

            def __exit__(self, *_):
                return False

            def post(self, path, headers=None, json=None):
                captured["headers"] = headers or {}
                captured["body"] = json
                return Answer()

        with patch.object(
            connector_service, "local_client", lambda: (Session(), {})
        ):
            connector_service.apply_locally(
                {
                    "revision": 1,
                    "profile": "standard",
                    "reasoning_allowed": False,
                    "max_concurrent_text": 2,
                    "max_concurrent_speech": 1,
                    "max_queue": 4,
                }
            )
        blob = json.dumps(captured)
        self.assertNotIn("connector_credential", blob)
        # En transport unix, aucun en-tete d'autorisation n'est meme envoye.
        self.assertEqual(captured["headers"], {})

    def test_the_local_secret_is_never_reported_to_the_control_plane(self):
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
            "connector_credential": "secret-cloud",
            "last_applied_revision": 1,
            "last_apply_status": "applied",
        }
        with patch.object(connector_service.httpx, "post", fake_post), patch.object(
            connector_service,
            "local_health",
            return_value={"runtime_status": "healthy", "last_error_code": None},
        ):
            connector_service.sync_once(state)
        blob = json.dumps(captured["body"])
        self.assertNotIn(local_channel.loopback_token(), blob)
        self.assertNotIn("auth_file", blob)
        self.assertNotIn("socket", blob)


if __name__ == "__main__":
    unittest.main()
