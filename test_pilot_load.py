"""Charge d'un pilote, pas d'un benchmark d'entreprise.

L'ordre de grandeur visé est celui d'un premier pilote : quelques dizaines de
membres, une poignée de postes qui se synchronisent. Ce qu'on cherche à voir
n'est pas un débit, mais trois choses qui cassent silencieusement sous
concurrence : une erreur serveur inattendue, un pool épuisé, une limite de débit
qui compte faux.
"""

import json
import os
import subprocess
import sys
import textwrap
import unittest
import uuid


POSTGRES_URL = os.getenv("NOVA_TEST_POSTGRES_URL", "").strip()
REDIS_URL = os.getenv("NOVA_TEST_REDIS_URL", "").strip()
HERE = os.path.dirname(os.path.abspath(__file__))


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
class PilotLoadTests(unittest.TestCase):
    def setUp(self):
        self.name = "load_" + uuid.uuid4().hex[:10]
        self.addCleanup(drop_database, self.name)
        self.url = create_database(self.name)

    def _run(self, body: str) -> dict:
        program = textwrap.dedent(
            """
            import json, os, sys, threading, time
            sys.path.insert(0, sys.argv[1])
            os.environ["NOVA_DATABASE_URL"] = sys.argv[2]
            os.environ["NOVA_ORGANIZATION_ID"] = "load-pilot"
            os.environ["NOVA_ORGANIZATION_NAME"] = "Load Pilot"
            os.environ["NOVA_DOMAINS"] = "pilot.example"
            os.environ["NOVA_ENV"] = "development"
            os.environ["NOVA_DB_POOL_MAX"] = "10"

            from fastapi.testclient import TestClient
            import main, observability

            main.init_db()
            client = TestClient(main.app)
            result = {}
            __BODY__
            print("@@RESULT@@" + json.dumps(result, default=str))
            """
        ).replace("__BODY__", textwrap.dedent(body).strip())

        script = os.path.join(HERE, f"_load_{uuid.uuid4().hex[:10]}.py")
        with open(script, "w", encoding="utf-8") as handle:
            handle.write(program)
        environment = dict(os.environ)
        for key in list(environment):
            if key.startswith("NOVA_") and key != "NOVA_TEST_POSTGRES_URL":
                del environment[key]
        if REDIS_URL:
            environment["NOVA_REDIS_URL"] = REDIS_URL
        try:
            completed = subprocess.run(
                [sys.executable, script, HERE, self.url],
                cwd=HERE, env=environment, capture_output=True, text=True, timeout=900,
            )
        finally:
            if os.path.exists(script):
                os.remove(script)
        if completed.returncode != 0 or "@@RESULT@@" not in completed.stdout:
            raise AssertionError(
                f"charge echouee (rc={completed.returncode})\n"
                f"{completed.stdout[-4000:]}\n{completed.stderr[-4000:]}"
            )
        return json.loads(completed.stdout.split("@@RESULT@@", 1)[1].strip().splitlines()[0])

    def test_concurrent_metadata_reads_stay_healthy(self):
        """Cinquante lectures concurrentes : aucun 5xx, pool stable."""
        result = self._run(
            """
            organization_id = main.nova_organization_id()

            # Vingt membres, comme un petit pilote.
            with main.db() as conn:
                for index in range(20):
                    main.create_user(
                        conn, organization_id, f"member{index}@pilot.example",
                        security_role="organization_admin" if index == 0 else "member",
                    )
                admin_row = main.user_by_email(
                    conn, organization_id, "member0@pilot.example"
                )
            admin = {"Authorization": "Bearer "
                     + main.create_admin_session(admin_row, "load")["token"]}
            base = f"/api/control/organizations/{organization_id}"

            codes = []
            lock = threading.Lock()
            barrier = threading.Barrier(50)
            paths = [base + "/members", base + "/administrators", base + "/audit",
                     "/api/control/organization", "/api/readyz"]

            def hit(index):
                barrier.wait()
                response = client.get(paths[index % len(paths)], headers=admin)
                with lock:
                    codes.append(response.status_code)

            started = time.monotonic()
            threads = [threading.Thread(target=hit, args=(i,)) for i in range(50)]
            for thread in threads:
                thread.start()
            for thread in threads:
                thread.join()
            result["elapsed"] = round(time.monotonic() - started, 2)
            result["codes"] = sorted(set(codes))
            result["server_errors"] = sum(1 for c in codes if c >= 500)
            result["total"] = len(codes)
            result["pool"] = main.database_handle().pool_stats
            """
        )
        self.assertEqual(result["total"], 50)
        self.assertEqual(result["server_errors"], 0, f"codes: {result['codes']}")
        # Le pool doit être rendu : une connexion fuitée ne revient jamais.
        self.assertEqual(result["pool"]["leased"], 0, result["pool"])
        self.assertLessEqual(result["pool"]["idle"], result["pool"]["max"])

    def test_concurrent_connector_sync_is_bounded_but_never_broken(self):
        """La synchronisation concurrente reste servie, et ne casse rien."""
        result = self._run(
            """
            organization_id = main.nova_organization_id()
            codes = []
            lock = threading.Lock()
            barrier = threading.Barrier(20)

            def sync(index):
                barrier.wait()
                # Sans credential valide : ce qui est mesuré ici est la
                # robustesse du chemin, pas une authentification reussie.
                response = client.post(
                    "/api/connector/sync",
                    headers={"Authorization": f"Bearer absent-{index}"},
                    json={},
                )
                with lock:
                    codes.append(response.status_code)

            threads = [threading.Thread(target=sync, args=(i,)) for i in range(20)]
            for thread in threads:
                thread.start()
            for thread in threads:
                thread.join()
            result["codes"] = sorted(set(codes))
            result["server_errors"] = sum(1 for c in codes if c >= 500)
            result["pool"] = main.database_handle().pool_stats
            """
        )
        self.assertEqual(
            result["server_errors"], 0,
            f"un credential invalide doit produire un refus, pas une erreur: {result['codes']}",
        )
        self.assertEqual(result["pool"]["leased"], 0, result["pool"])

    def test_the_rate_limit_bites_without_breaking_the_service(self):
        """La limite se déclenche, et le service continue de répondre."""
        result = self._run(
            """
            codes = []
            lock = threading.Lock()
            barrier = threading.Barrier(30)

            def attempt():
                barrier.wait()
                response = client.post(
                    "/api/auth/request", json={"email": "load@pilot.example"}
                )
                with lock:
                    codes.append(response.status_code)

            threads = [threading.Thread(target=attempt) for _ in range(30)]
            for thread in threads:
                thread.start()
            for thread in threads:
                thread.join()
            result["accepted"] = sum(1 for c in codes if c == 200)
            result["limited"] = sum(1 for c in codes if c == 429)
            result["server_errors"] = sum(1 for c in codes if c >= 500)
            # Le service repond toujours apres la rafale.
            result["still_alive"] = client.get("/api/livez").status_code
            result["still_ready"] = client.get("/api/readyz").status_code
            """
        )
        self.assertEqual(result["server_errors"], 0)
        self.assertGreater(result["limited"], 0, "la limite doit mordre")
        self.assertGreater(result["accepted"], 0, "le quota doit d'abord servir")
        self.assertEqual(result["still_alive"], 200)
        self.assertIn(result["still_ready"], (200, 503))


if __name__ == "__main__":
    unittest.main()
