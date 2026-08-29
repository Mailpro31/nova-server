"""Le Connector, réellement enrôlé contre un vrai serveur.

Ce fichier ne simule ni le transport, ni le processus. Un serveur HTTP écoute
sur un port, un Connector est lancé comme processus séparé, et les deux se
parlent par le réseau — parce que les propriétés à vérifier ici vivent
précisément dans cet aller-retour :

- un code d'enrôlement ne sert qu'une fois ;
- le Control Plane ne garde que l'empreinte du credential ;
- un Connector arrêté puis relancé se reconnecte **sans nouvel enrôlement** ;
- une rotation ne révoque l'ancien credential qu'une fois le nouveau prouvé.

Instance temporaire, base temporaire, credentials éphémères. Le Connector
préexistant de la machine n'est jamais touché.
"""

import json
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import textwrap
import time
import unittest
import uuid
from pathlib import Path

import httpx


POSTGRES_URL = os.getenv("NOVA_TEST_POSTGRES_URL", "").strip()
REDIS_URL = os.getenv("NOVA_TEST_REDIS_URL", "").strip()
HERE = os.path.dirname(os.path.abspath(__file__))


def free_port() -> int:
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return probe.getsockname()[1]


def create_database(name: str) -> str:
    import psycopg

    with psycopg.connect(POSTGRES_URL, autocommit=True) as admin:
        admin.execute(f'DROP DATABASE IF EXISTS "{name}"')
        admin.execute(f'CREATE DATABASE "{name}"')
    return POSTGRES_URL.rpartition("/")[0] + "/" + name


def drop_database(name: str) -> None:
    import psycopg

    try:
        with psycopg.connect(POSTGRES_URL, autocommit=True) as admin:
            admin.execute(f'DROP DATABASE IF EXISTS "{name}"')
    except Exception:
        pass


@unittest.skipUnless(POSTGRES_URL, "NOVA_TEST_POSTGRES_URL is not set")
class ConnectorEndToEndTests(unittest.TestCase):
    """Un vrai serveur, un vrai Connector, deux processus."""

    def setUp(self):
        self.name = "conn_" + uuid.uuid4().hex[:10]
        self.addCleanup(drop_database, self.name)
        self.url = create_database(self.name)
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.port = free_port()
        self.base = f"http://127.0.0.1:{self.port}"
        self.state = os.path.join(self.directory.name, "connector-state.json")

        self._bootstrap()
        self._start_server()

    # ------------------------------------------------------------ amorçage

    def _environment(self) -> dict:
        environment = dict(os.environ)
        for key in list(environment):
            if key.startswith("NOVA_") and not key.startswith("NOVA_TEST_"):
                del environment[key]
        environment.update(
            {
                "NOVA_DATABASE_URL": self.url,
                "NOVA_ORGANIZATION_ID": "connector-pilot",
                "NOVA_ORGANIZATION_NAME": "Connector Pilot",
                "NOVA_DOMAINS": "pilot.example",
                "NOVA_ENV": "development",
                "PYTHONUNBUFFERED": "1",
            }
        )
        if REDIS_URL:
            environment["NOVA_REDIS_URL"] = REDIS_URL
        return environment

    def _python(self, program: str, *arguments: str) -> subprocess.CompletedProcess:
        script = os.path.join(self.directory.name, f"step_{uuid.uuid4().hex[:8]}.py")
        with open(script, "w", encoding="utf-8") as handle:
            handle.write(program)
        return subprocess.run(
            [sys.executable, script, *arguments],
            cwd=HERE, env=self._environment(), capture_output=True, text=True,
            timeout=600,
        )

    def _bootstrap(self):
        """Migre, cree l'organisation et un `it_admin`, puis rend son jeton.

        L'adresse est unique par test : le compteur de limitation est partage
        entre les scenarios, et reutiliser la meme adresse ferait echouer
        l'amorcage du septieme test pour une raison sans rapport avec ce qu'il
        verifie.
        """
        email = f"it-{uuid.uuid4().hex[:8]}@pilot.example"
        program = textwrap.dedent(
            f"""
            import contextlib, io, json, sys
            sys.path.insert(0, r"{HERE}")
            from fastapi.testclient import TestClient
            import admin_cli, main

            main.init_db()
            client = TestClient(main.app)
            email = "{email}"

            captured = io.StringIO()
            with contextlib.redirect_stdout(captured):
                started = client.post("/api/auth/request", json={{"email": email}})
            assert started.status_code == 200, f"request={{started.status_code}} {{started.text}}"
            printed = captured.getvalue()
            assert "[DEV]" in printed, f"aucun code imprime: {{printed!r}}"
            code = printed.rsplit(":", 1)[-1].strip()

            verified = client.post(
                "/api/auth/verify",
                json={{"email": email, "code": code, "machine": "connector"}},
            )
            assert verified.status_code == 200, f"verify={{verified.status_code}} {{verified.text}}"

            granted = admin_cli.run(["grant", email, "it_admin"])
            assert granted == 0, f"grant={{granted}}"

            with main.db() as conn:
                row = main.user_by_email(conn, main.nova_organization_id(), email)
            assert row is not None, "le compte n'a pas ete cree"
            session = main.create_admin_session(row, "connector")
            print("@@TOKEN@@" + json.dumps({{
                "token": session["token"],
                "organization_id": main.nova_organization_id(),
            }}))
            """
        )
        completed = self._python(program)
        self.assertEqual(
            completed.returncode, 0,
            completed.stdout[-2000:] + "\n" + completed.stderr[-3000:],
        )
        payload = json.loads(
            completed.stdout.split("@@TOKEN@@", 1)[1].strip().splitlines()[0]
        )
        self.admin = {"Authorization": "Bearer " + payload["token"]}
        self.organization_id = payload["organization_id"]

    def _start_server(self):
        self.server = subprocess.Popen(
            [sys.executable, "-m", "uvicorn", "main:app",
             "--host", "127.0.0.1", "--port", str(self.port), "--log-level", "warning"],
            cwd=HERE, env=self._environment(),
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
        )
        self.addCleanup(self._stop_server)
        for _ in range(120):
            if self.server.poll() is not None:
                raise AssertionError(
                    "le serveur s'est arrete: " + (self.server.stdout.read() or "")[-3000:]
                )
            try:
                if httpx.get(self.base + "/api/livez", timeout=2).status_code == 200:
                    return
            except Exception:
                time.sleep(0.5)
        raise AssertionError("le serveur n'a jamais repondu")

    def _stop_server(self):
        if self.server.poll() is None:
            self.server.terminate()
            try:
                self.server.wait(timeout=30)
            except subprocess.TimeoutExpired:
                self.server.kill()

    # ------------------------------------------------------------ helpers

    def _enrollment_token(self, name: str = "pilote") -> str:
        response = httpx.post(
            self.base + "/api/control/connectors/enrollments",
            headers=self.admin,
            json={"display_name": name, "deployment_id": "pilot-deployment"},
            timeout=30,
        )
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()["enrollment_token"]

    def _connector(self, *arguments: str) -> subprocess.CompletedProcess:
        environment = self._environment()
        environment["NOVA_CONNECTOR_STATE"] = self.state
        # Boucle locale en clair : c'est un environnement de test, et le
        # refuser rendrait la recette impossible sans rien prouver.
        environment["NOVA_CONNECTOR_ALLOW_INSECURE"] = "true"
        return subprocess.run(
            [sys.executable, "connector.py", *arguments],
            cwd=HERE, env=environment, capture_output=True, text=True, timeout=300,
        )

    def _sync_body(self) -> dict:
        """Le corps minimal qu'un Connector envoie reellement.

        Repris de `connector.py` : le contrat est strict (`extra="forbid"`), et
        un corps vide serait refuse avant meme l'authentification — ce qui
        mesurerait la validation, pas le credential.
        """
        import connector as connector_module

        return {
            "protocol_version": connector_module.PROTOCOL_VERSION,
            "connector_version": connector_module.CONNECTOR_VERSION,
            "applied_revision": 0,
            "apply_status": "pending",
        }

    def _rotation_body(self) -> dict:
        """Le corps attendu par `rotate` et `confirm` : version de protocole
        et version du Connector, rien d'autre (`extra="forbid"`)."""
        import connector as connector_module

        return {
            "protocol_version": connector_module.PROTOCOL_VERSION,
            "connector_version": connector_module.CONNECTOR_VERSION,
        }

    def _local_state(self) -> dict:
        return json.loads(Path(self.state).read_text(encoding="utf-8"))

    def _stored_credentials(self) -> list[dict]:
        response = httpx.get(
            self.base + "/api/control/connectors", headers=self.admin, timeout=30
        )
        self.assertEqual(response.status_code, 200, response.text)
        return response.json().get("connectors", [])

    # ------------------------------------------------------------- tests

    def test_enrollment_then_replay_is_refused(self):
        token = self._enrollment_token()
        enrolled = self._connector(
            "enroll", "--control-plane", self.base, "--token", token
        )
        self.assertEqual(enrolled.returncode, 0, enrolled.stdout + enrolled.stderr)
        self.assertIn("enrole", enrolled.stdout)

        state = self._local_state()
        self.assertTrue(state.get("connector_id"))
        self.assertTrue(state.get("connector_credential"))

        # Le Control Plane ne garde que l'empreinte : le credential en clair
        # n'existe que sur le Connector.
        listed = self._stored_credentials()
        self.assertTrue(listed)
        serialized = json.dumps(listed)
        self.assertNotIn(state["connector_credential"], serialized)

        # Le meme code, une seconde fois : refuse.
        shutil.copyfile(self.state, self.state + ".keep")
        replay = self._connector(
            "enroll", "--control-plane", self.base, "--token", token
        )
        self.assertNotEqual(replay.returncode, 0, "un code consomme doit etre refuse")

    def test_a_restarted_connector_reconnects_with_the_same_credential(self):
        token = self._enrollment_token()
        self.assertEqual(
            self._connector("enroll", "--control-plane", self.base, "--token", token).returncode,
            0,
        )
        before = self._local_state()

        # Une synchronisation authentifiee avec le credential enrole.
        first = httpx.post(
            self.base + "/api/connector/sync",
            headers={"Authorization": "Bearer " + before["connector_credential"]},
            json=self._sync_body(), timeout=60,
        )
        self.assertEqual(first.status_code, 200, first.text)

        # « Redemarrage » : le processus repart de son etat local, sans jamais
        # redemander de code d'enrolement.
        status = self._connector("status")
        self.assertEqual(status.returncode, 0, status.stderr)
        reported = json.loads(status.stdout)
        self.assertTrue(reported["enrolled"])
        self.assertEqual(reported["connector_id"], before["connector_id"])

        after = httpx.post(
            self.base + "/api/connector/sync",
            headers={"Authorization": "Bearer " + before["connector_credential"]},
            json=self._sync_body(), timeout=60,
        )
        self.assertEqual(after.status_code, 200, after.text)
        self.assertEqual(self._local_state()["connector_id"], before["connector_id"])

    def test_credential_rotation_never_leaves_a_gap(self):
        """C1 reste valide jusqu'à ce que C2 soit prouvé, puis C1 tombe."""
        token = self._enrollment_token()
        self.assertEqual(
            self._connector("enroll", "--control-plane", self.base, "--token", token).returncode,
            0,
        )
        first = self._local_state()["connector_credential"]

        def sync(credential: str) -> int:
            return httpx.post(
                self.base + "/api/connector/sync",
                headers={"Authorization": "Bearer " + credential},
                json=self._sync_body(), timeout=60,
            ).status_code

        self.assertEqual(sync(first), 200)

        # Rotation demandee par le Connector, avec C1.
        rotated = httpx.post(
            self.base + "/api/connector/credential/rotate",
            headers={"Authorization": "Bearer " + first},
            json=self._rotation_body(), timeout=60,
        )
        self.assertEqual(rotated.status_code, 200, rotated.text)
        second = rotated.json()["connector_credential"]
        self.assertNotEqual(first, second)

        # Entre la demande et la preuve, C1 fonctionne toujours : sans cela,
        # une rotation interrompue laisserait le Connector muet.
        self.assertEqual(sync(first), 200)

        # Preuve de C2 : c'est elle qui bascule.
        confirmed = httpx.post(
            self.base + "/api/connector/credential/confirm",
            headers={"Authorization": "Bearer " + second},
            json=self._rotation_body(), timeout=60,
        )
        self.assertEqual(confirmed.status_code, 200, confirmed.text)

        self.assertEqual(sync(second), 200, "le nouveau credential doit servir")
        self.assertIn(sync(first), (401, 403), "l'ancien doit etre revoque")

    def test_an_interrupted_rotation_leaves_a_working_credential(self):
        """Une rotation abandonnée en cours de route ne coupe rien.

        C'est le scénario du redémarrage au mauvais moment : la demande est
        partie, la preuve n'est jamais venue. Le Connector doit rester
        authentifié avec ce qu'il a.
        """
        token = self._enrollment_token()
        self.assertEqual(
            self._connector("enroll", "--control-plane", self.base, "--token", token).returncode,
            0,
        )
        first = self._local_state()["connector_credential"]

        rotated = httpx.post(
            self.base + "/api/connector/credential/rotate",
            headers={"Authorization": "Bearer " + first},
            json=self._rotation_body(), timeout=60,
        )
        self.assertEqual(rotated.status_code, 200)
        # On abandonne ici : aucune confirmation n'est envoyee.

        still_working = httpx.post(
            self.base + "/api/connector/sync",
            headers={"Authorization": "Bearer " + first},
            json=self._sync_body(), timeout=60,
        )
        self.assertEqual(
            still_working.status_code, 200,
            "une rotation interrompue ne doit jamais laisser le Connector muet",
        )

    def test_an_unknown_credential_is_refused(self):
        refused = httpx.post(
            self.base + "/api/connector/sync",
            headers={"Authorization": "Bearer credential-qui-n-existe-pas"},
            json=self._rotation_body(), timeout=60,
        )
        self.assertIn(refused.status_code, (401, 403))

    def test_the_console_never_returns_a_credential(self):
        token = self._enrollment_token()
        self.assertEqual(
            self._connector("enroll", "--control-plane", self.base, "--token", token).returncode,
            0,
        )
        credential = self._local_state()["connector_credential"]
        listing = httpx.get(
            self.base + "/api/control/connectors", headers=self.admin, timeout=30
        ).text
        self.assertNotIn(credential, listing)
        for forbidden in ("connector_credential", "credential_hash"):
            self.assertNotIn(forbidden, listing, forbidden)

    def test_the_local_state_never_leaves_the_connector(self):
        token = self._enrollment_token()
        self._connector("enroll", "--control-plane", self.base, "--token", token)
        # `status` est la commande de diagnostic : elle ne doit rien reveler.
        status = self._connector("status")
        self.assertEqual(status.returncode, 0)
        self.assertNotIn(self._local_state()["connector_credential"], status.stdout)


if __name__ == "__main__":
    unittest.main()
