"""Le Control Plane complet, contre un vrai PostgreSQL.

## Pourquoi un sous-processus

`main` lit sa configuration a l'import. Un test qui a deja importe `main` sur
SQLite ne peut plus le faire basculer sur PostgreSQL sans mentir sur ce qu'il
mesure. Chaque scenario demarre donc un processus neuf, avec
`NOVA_DATABASE_URL` pointant sur PostgreSQL : c'est le seul moyen de prouver que
le service — et pas seulement l'adaptateur — fonctionne sous l'autre moteur.

Ignore quand `NOVA_TEST_POSTGRES_URL` n'est pas defini. Un test qui se
contenterait de SQLite ne prouverait rien de ce que cette suite existe pour
verifier.
"""

import json
import os
import subprocess
import sys
import textwrap
import unittest
import uuid


POSTGRES_URL = os.getenv("NOVA_TEST_POSTGRES_URL", "").strip()
HERE = os.path.dirname(os.path.abspath(__file__))


def run_against_postgres(body: str, *, schema: str | None = None) -> dict:
    """Execute `body` dans un processus neuf branche sur PostgreSQL.

    Chaque scenario obtient son propre schema PostgreSQL : deux scenarios qui
    partageraient des tables se pollueraient, et un echec deviendrait
    impossible a attribuer.
    """
    schema = schema or ("s" + uuid.uuid4().hex[:12])
    url = f"{POSTGRES_URL}?options=-csearch_path%3D{schema}"

    program = textwrap.dedent(
        """
        import json, os, sys
        import psycopg

        base = os.environ["NOVA_TEST_POSTGRES_URL"]
        schema = os.environ["NOVA_TEST_SCHEMA"]
        with psycopg.connect(base, autocommit=True) as setup:
            setup.execute(f'CREATE SCHEMA IF NOT EXISTS "{schema}"')

        os.environ["NOVA_DATABASE_URL"] = os.environ["NOVA_TEST_TARGET_URL"]
        os.environ.setdefault("NOVA_DOMAINS", "example.com")
        os.environ.setdefault("NOVA_ORGANIZATION_NAME", "Nova Postgres Test")
        os.environ.setdefault("NOVA_ORGANIZATION_ID", "pgtest")

        import database, migrations
        import main

        result = {}
        __BODY__

        print("@@RESULT@@" + json.dumps(result, default=str))
        """
    ).replace("__BODY__", textwrap.dedent(body).strip())

    script = os.path.join(HERE, f"_pg_case_{schema}.py")
    with open(script, "w", encoding="utf-8") as handle:
        handle.write(program)
    environment = dict(os.environ)
    environment["NOVA_TEST_POSTGRES_URL"] = POSTGRES_URL
    environment["NOVA_TEST_TARGET_URL"] = url
    environment["NOVA_TEST_SCHEMA"] = schema
    environment.pop("NOVA_DB", None)
    try:
        completed = subprocess.run(
            [sys.executable, script],
            cwd=HERE,
            env=environment,
            capture_output=True,
            text=True,
            timeout=300,
        )
    finally:
        if os.path.exists(script):
            os.remove(script)
    if completed.returncode != 0 or "@@RESULT@@" not in completed.stdout:
        raise AssertionError(
            f"postgres scenario failed (rc={completed.returncode})\n"
            f"stdout:\n{completed.stdout[-4000:]}\n\nstderr:\n{completed.stderr[-4000:]}"
        )
    return json.loads(completed.stdout.split("@@RESULT@@", 1)[1].strip().splitlines()[0])


@unittest.skipUnless(POSTGRES_URL, "NOVA_TEST_POSTGRES_URL is not set")
class PostgresBootstrapTests(unittest.TestCase):
    def test_an_empty_postgres_schema_migrates_and_the_service_boots(self):
        result = run_against_postgres(
            """
            result["state_before"] = migrations.status(main.database_handle())["state"]
            main.init_db()
            result["state_after"] = migrations.status(main.database_handle())["state"]
            result["dialect"] = main.database_handle().dialect
            result["organization_id"] = main.nova_organization_id()
            with main.db() as conn:
                result["tables"] = len(conn.columns("users")) > 0
                result["core"] = all(
                    conn.table_exists(t) for t in migrations.CORE_TABLES
                )
            """
        )
        self.assertEqual(result["state_before"], "empty")
        self.assertEqual(result["state_after"], "current")
        self.assertEqual(result["dialect"], "postgresql")
        self.assertTrue(result["core"])
        self.assertTrue(result["organization_id"])

    def test_the_service_refuses_to_start_on_a_schema_it_has_not_migrated(self):
        result = run_against_postgres(
            """
            try:
                migrations.check_startup(main.database_handle())
                result["refused"] = None
            except migrations.MigrationError as error:
                result["refused"] = error.code
            """
        )
        self.assertEqual(result["refused"], "DATABASE_MIGRATION_REQUIRED")

    def test_a_schema_from_the_future_is_refused_not_downgraded(self):
        result = run_against_postgres(
            """
            main.init_db()
            handle = main.database_handle()
            with handle.connect() as conn:
                conn.execute(
                    "INSERT INTO schema_migrations(version, name, applied_at) "
                    "VALUES (?,?,?)",
                    (9999, "from_the_future", 0.0),
                )
            result["status"] = migrations.status(handle)["state"]
            try:
                migrations.check_startup(handle)
                result["refused"] = None
            except migrations.MigrationError as error:
                result["refused"] = error.code
            """
        )
        self.assertEqual(result["status"], "ahead")
        self.assertEqual(result["refused"], "DATABASE_SCHEMA_TOO_NEW")


@unittest.skipUnless(POSTGRES_URL, "NOVA_TEST_POSTGRES_URL is not set")
class PostgresHttpServiceTests(unittest.TestCase):
    """De vraies requetes HTTP contre le service, sous PostgreSQL."""

    def test_representative_http_calls_succeed(self):
        result = run_against_postgres(
            """
            from fastapi.testclient import TestClient
            main.init_db()
            client = TestClient(main.app)
            organization_id = main.nova_organization_id()

            with main.db() as conn:
                admin_id = main.create_user(
                    conn, organization_id, "pg-admin@example.com",
                    security_role="organization_admin",
                )
                it_id = main.create_user(
                    conn, organization_id, "pg-it@example.com",
                    security_role="it_admin",
                )
                admin_row = main.user_by_id(conn, admin_id)
                it_row = main.user_by_id(conn, it_id)
            admin = {"Authorization": "Bearer " +
                     main.create_admin_session(admin_row, "test")["token"]}
            it = {"Authorization": "Bearer " +
                  main.create_admin_session(it_row, "test")["token"]}

            base = f"/api/control/organizations/{organization_id}"
            result["organization"] = client.get("/api/control/organization", headers=admin).status_code
            result["members"] = client.get(base + "/members", headers=admin).status_code
            result["administrators"] = client.get(base + "/administrators", headers=admin).status_code
            result["audit"] = client.get(base + "/audit", headers=admin).status_code
            result["policy"] = client.get("/api/control/organization/policy", headers=admin).status_code
            result["packages"] = client.get(base + "/packages", headers=admin).status_code
            result["provisioning"] = client.get(base + "/provisioning", headers=admin).status_code
            result["deployment"] = client.get(base + "/deployment", headers=it).status_code
            result["connectors"] = client.get("/api/control/connectors", headers=it).status_code
            result["ai_runtime"] = client.get("/api/control/organization/ai-runtime", headers=it).status_code
            """
        )
        for route, status in result.items():
            self.assertEqual(status, 200, f"{route} returned {status}")

    def test_scim_provisioning_works_end_to_end_on_postgres(self):
        result = run_against_postgres(
            """
            from fastapi.testclient import TestClient
            main.init_db()
            client = TestClient(main.app)
            organization_id = main.nova_organization_id()
            with main.db() as conn:
                admin_id = main.create_user(
                    conn, organization_id, "pg-scim-admin@example.com",
                    security_role="organization_admin",
                )
                admin_row = main.user_by_id(conn, admin_id)
            admin = {"Authorization": "Bearer " +
                     main.create_admin_session(admin_row, "test")["token"]}

            created = client.post(
                f"/api/control/organizations/{organization_id}/provisioning/credentials",
                headers=admin, json={"name": "pg", "provider_context": "generic"},
            )
            result["credential_status"] = created.status_code
            token = created.json()["token"]
            scim = {"Content-Type": "application/scim+json",
                    "Authorization": "Bearer " + token}

            user = client.post("/scim/v2/Users", headers=scim, json={
                "schemas": ["urn:ietf:params:scim:schemas:core:2.0:User"],
                "userName": "pg.user@example.com",
                "externalId": "pg-ext-1",
                "active": True,
                "name": {"givenName": "Pg", "familyName": "User"},
                "emails": [{"value": "pg.user@example.com", "primary": True}],
            })
            result["create_status"] = user.status_code
            user_id = user.json()["id"]
            result["security_role_is_member"] = None
            with main.db() as conn:
                row = main.user_by_id(conn, user_id)
                result["security_role_is_member"] = row["security_role"] == "member"

            # Doublon d'externalId : la contrainte doit trancher, pas planter.
            again = client.post("/scim/v2/Users", headers=scim, json={
                "schemas": ["urn:ietf:params:scim:schemas:core:2.0:User"],
                "userName": "pg.user@example.com",
                "externalId": "pg-ext-1",
                "emails": [{"value": "pg.user@example.com", "primary": True}],
            })
            result["duplicate_status"] = again.status_code

            result["filter_status"] = client.get(
                '/scim/v2/Users?filter=userName eq "pg.user@example.com"', headers=scim
            ).status_code

            patched = client.patch(f"/scim/v2/Users/{user_id}", headers=scim, json={
                "schemas": ["urn:ietf:params:scim:api:messages:2.0:PatchOp"],
                "Operations": [{"op": "replace", "path": "active", "value": "False"}],
            })
            result["disable_status"] = patched.status_code
            result["disabled"] = patched.json()["active"] is False

            group = client.post("/scim/v2/Groups", headers=scim, json={
                "schemas": ["urn:ietf:params:scim:schemas:core:2.0:Group"],
                "displayName": "PG Group", "externalId": "pg-group-1", "members": [],
            })
            result["group_status"] = group.status_code

            deleted = client.delete(f"/scim/v2/Users/{user_id}", headers=scim)
            result["delete_status"] = deleted.status_code
            """
        )
        self.assertEqual(result["credential_status"], 200)
        self.assertEqual(result["create_status"], 201)
        self.assertTrue(result["security_role_is_member"], "SCIM must never escalate")
        self.assertEqual(result["duplicate_status"], 200, "a retry reconciles, never 500")
        self.assertEqual(result["filter_status"], 200)
        self.assertEqual(result["disable_status"], 200)
        self.assertTrue(result["disabled"])
        self.assertEqual(result["group_status"], 201)
        self.assertEqual(result["delete_status"], 204)

    def test_tenant_isolation_holds_on_postgres(self):
        result = run_against_postgres(
            """
            from fastapi.testclient import TestClient
            main.init_db()
            client = TestClient(main.app)
            organization_id = main.nova_organization_id()
            other = "org-" + __import__("uuid").uuid4().hex[:8]
            with main.db() as conn:
                conn.execute(
                    "INSERT INTO organizations(id, slug, display_name, created_at) "
                    "VALUES (?,?,?,?)",
                    (other, other, "Other", 0.0),
                )
                admin_id = main.create_user(
                    conn, organization_id, "pg-iso@example.com",
                    security_role="organization_admin",
                )
                admin_row = main.user_by_id(conn, admin_id)
            admin = {"Authorization": "Bearer " +
                     main.create_admin_session(admin_row, "test")["token"]}
            result["own"] = client.get(
                f"/api/control/organizations/{organization_id}/members", headers=admin
            ).status_code
            result["foreign"] = client.get(
                f"/api/control/organizations/{other}/members", headers=admin
            ).status_code
            """
        )
        self.assertEqual(result["own"], 200)
        self.assertIn(result["foreign"], (403, 404))

    def test_a_unique_violation_is_a_conflict_not_a_crash(self):
        result = run_against_postgres(
            """
            main.init_db()
            organization_id = main.nova_organization_id()
            with main.db() as conn:
                main.create_user(conn, organization_id, "pg-dup@example.com")
            try:
                with main.db() as conn:
                    main.create_user(conn, organization_id, "pg-dup@example.com")
                result["raised"] = None
            except database.IntegrityError:
                result["raised"] = "IntegrityError"
            except Exception as error:
                result["raised"] = type(error).__name__
            """
        )
        self.assertEqual(result["raised"], "IntegrityError")

    def test_concurrent_identical_scim_creates_produce_one_user(self):
        """La course reelle, sous PostgreSQL.

        C'est ce que `BEGIN IMMEDIATE` protegeait sous SQLite. Sous PostgreSQL
        c'est la contrainte unique qui doit trancher, et le resultat doit etre
        le meme : un utilisateur, pas deux.
        """
        result = run_against_postgres(
            """
            import threading
            from fastapi.testclient import TestClient
            main.init_db()
            client = TestClient(main.app)
            organization_id = main.nova_organization_id()
            with main.db() as conn:
                admin_id = main.create_user(
                    conn, organization_id, "pg-race-admin@example.com",
                    security_role="organization_admin",
                )
                admin_row = main.user_by_id(conn, admin_id)
            admin = {"Authorization": "Bearer " +
                     main.create_admin_session(admin_row, "test")["token"]}
            token = client.post(
                f"/api/control/organizations/{organization_id}/provisioning/credentials",
                headers=admin, json={"name": "race", "provider_context": "generic"},
            ).json()["token"]
            scim = {"Content-Type": "application/scim+json",
                    "Authorization": "Bearer " + token}
            payload = {
                "schemas": ["urn:ietf:params:scim:schemas:core:2.0:User"],
                "userName": "race.user@example.com",
                "externalId": "race-ext",
                "emails": [{"value": "race.user@example.com", "primary": True}],
            }
            codes = []
            lock = threading.Lock()
            barrier = threading.Barrier(4)

            def attempt():
                barrier.wait()
                response = client.post("/scim/v2/Users", headers=scim, json=payload)
                with lock:
                    codes.append(response.status_code)

            threads = [threading.Thread(target=attempt) for _ in range(4)]
            for t in threads:
                t.start()
            for t in threads:
                t.join()
            result["codes"] = sorted(codes)
            with main.db() as conn:
                result["users"] = conn.execute(
                    "SELECT COUNT(*) FROM scim_user_management WHERE external_id=?",
                    ("race-ext",),
                ).fetchone()[0]
            """
        )
        self.assertEqual(result["users"], 1, "the constraint must produce exactly one user")
        self.assertTrue(
            all(code < 500 for code in result["codes"]),
            f"a race must never surface as a server error: {result['codes']}",
        )


if __name__ == "__main__":
    unittest.main()
