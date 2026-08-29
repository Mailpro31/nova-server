"""Exercices d'exploitation, contre une vraie infrastructure.

Trois choses qu'aucun test unitaire ne peut établir :

- une sauvegarde restaurée reconstitue bien la base — et pas seulement « ne
  plante pas » ;
- deux processus distincts servant la **même route HTTP** partagent bien un
  quota ;
- une panne de Redis produit le comportement déclaré, route par route.

Ignorés quand l'infrastructure n'est pas fournie. Un test qui se contenterait
d'une simulation ne prouverait rien de ce que ce fichier existe pour vérifier.
"""

import json
import os
import subprocess
import sys
import tempfile
import textwrap
import time
import unittest
import uuid
from pathlib import Path

import backup
import database
import migrations
import ratelimit


POSTGRES_URL = os.getenv("NOVA_TEST_POSTGRES_URL", "").strip()
REDIS_URL = os.getenv("NOVA_TEST_REDIS_URL", "").strip()
HERE = os.path.dirname(os.path.abspath(__file__))


def tool_view(url: str) -> str:
    """L'URL telle que l'outil PostgreSQL la voit.

    Ici l'outil s'execute dans le conteneur : `127.0.0.1:55434` y designerait
    le conteneur lui-meme, pas le serveur. `NOVA_TEST_POSTGRES_TOOL_URL` porte
    l'adresse interne, et seul le nom de base est repris de `url`.
    """
    internal = os.getenv("NOVA_TEST_POSTGRES_TOOL_URL", "").strip()
    if not internal:
        return url
    return internal.rstrip("/") + "/" + url.rpartition("/")[2]


def fresh_database(name: str) -> str:
    """Une base PostgreSQL neuve, detruite en fin de test."""
    import psycopg

    with psycopg.connect(POSTGRES_URL, autocommit=True) as admin:
        admin.execute(f'DROP DATABASE IF EXISTS "{name}"')
        admin.execute(f'CREATE DATABASE "{name}"')
    base, _, _ = POSTGRES_URL.rpartition("/")
    return f"{base}/{name}"


def drop_database(name: str) -> None:
    import psycopg

    try:
        with psycopg.connect(POSTGRES_URL, autocommit=True) as admin:
            admin.execute(f'DROP DATABASE IF EXISTS "{name}"')
    except Exception:
        pass


def populate(url: str) -> dict:
    """Des donnees representatives, entierement fictives."""
    db = database.Database(url)
    invariants: dict = {}
    try:
        migrations.migrate(db)
        with db.connect() as conn:
            for index in range(2):
                organization = f"org-drill-{index}"
                conn.execute(
                    "INSERT INTO organizations(id, slug, display_name, created_at) "
                    "VALUES (?,?,?,?)",
                    (organization, f"drill-{index}", f"Drill {index}", 1000.0 + index),
                )
                for member in range(3):
                    user_id = f"user-{index}-{member}"
                    email = f"member{member}@drill{index}.example"
                    conn.execute(
                        "INSERT INTO users(user_id, organization_id, email, "
                        "normalized_email, role, security_role, status, disabled, "
                        "created_at, updated_at) VALUES (?,?,?,?,?,?,?,?,?,?)",
                        (user_id, organization, email, email, "student",
                         "organization_admin" if member == 0 else "member",
                         "active", 0, 1000.0, 1000.0),
                    )
                    conn.execute(
                        "INSERT INTO federated_identities(user_id, provider, "
                        "external_subject, organization_id, email, verification, "
                        "external_issuer, created_at, last_seen) "
                        "VALUES (?,?,?,?,?,?,?,?,?)",
                        (user_id, "microsoft_entra", f"subject-{index}-{member}",
                         organization, email, "verified", "", 1000.0, 1000.0),
                    )
                conn.execute(
                    "INSERT INTO deployment_profiles(profile_id, organization_id, name, "
                    "platform, edition, channel, update_policy, config_revision, status, "
                    "created_at, updated_at) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                    (f"dpl-drill-{index}", organization, "parc", "windows",
                     "organization", "stable", "notify", 1, "active", 1000.0, 1000.0),
                )
        with db.connect() as conn:
            for table in ("organizations", "users", "federated_identities",
                          "deployment_profiles"):
                invariants[table] = conn.execute(
                    f"SELECT COUNT(*) FROM {table}"
                ).fetchone()[0]
            invariants["schema_version"] = migrations.status(db)["current_version"]
    finally:
        db.close()
    return invariants


@unittest.skipUnless(POSTGRES_URL, "NOVA_TEST_POSTGRES_URL is not set")
class BackupRestoreDrillTests(unittest.TestCase):
    """L'exercice complet : sauvegarder, casser, restaurer ailleurs, verifier."""

    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.source_name = "novadrill_src_" + uuid.uuid4().hex[:8]
        self.target_name = "novadrill_dst_" + uuid.uuid4().hex[:8]
        self.addCleanup(drop_database, self.source_name)
        self.addCleanup(drop_database, self.target_name)
        self.source = fresh_database(self.source_name)

    def test_a_backup_restored_into_a_fresh_database_reconstitutes_everything(self):
        invariants = populate(self.source)
        archive = Path(self.directory.name) / "control-plane.dump"

        result = backup.dump(self.source, archive, tool_url=tool_view(self.source))
        self.assertTrue(archive.is_file())
        self.assertGreater(result.manifest["size"], 0)
        self.assertEqual(result.manifest["schema_version"], invariants["schema_version"])
        self.assertIn("PostgreSQL", result.manifest["server_version"])

        # L'archive se verifie sans base : c'est la question qu'on se pose a
        # trois heures du matin.
        self.assertEqual(backup.verify(archive)["sha256"], result.manifest["sha256"])

        # La source est detruite, comme dans un vrai sinistre.
        source_db = database.Database(self.source)
        with source_db.connect() as conn:
            conn.executescript("DROP TABLE deployment_profiles; DROP TABLE users CASCADE;")
        source_db.close()

        # Restauration dans une base NEUVE, jamais par-dessus la source.
        target = fresh_database(self.target_name)
        backup.restore(archive, target, tool_url=tool_view(target))

        restored = database.Database(target)
        try:
            self.assertEqual(migrations.status(restored)["state"], "current")
            with restored.connect() as conn:
                for table, expected in invariants.items():
                    if table == "schema_version":
                        continue
                    actual = conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
                    self.assertEqual(actual, expected, table)

                # Les liens d'identite survivent : un compte restaure sans son
                # identite federee serait un compte que personne ne peut plus
                # utiliser.
                orphans = conn.execute(
                    "SELECT COUNT(*) FROM federated_identities f "
                    "LEFT JOIN users u ON u.user_id=f.user_id "
                    "AND u.organization_id=f.organization_id WHERE u.user_id IS NULL"
                ).fetchone()[0]
                self.assertEqual(orphans, 0)

                # Le cloisonnement par organisation tient.
                leaked = conn.execute(
                    "SELECT COUNT(*) FROM users u JOIN deployment_profiles d "
                    "ON d.organization_id=u.organization_id "
                    "WHERE u.organization_id NOT IN (?,?)",
                    ("org-drill-0", "org-drill-1"),
                ).fetchone()[0]
                self.assertEqual(leaked, 0)

                # La contrainte d'unicite est bien revenue avec le schema.
                with self.assertRaises(database.IntegrityError):
                    conn.execute(
                        "INSERT INTO organizations(id, slug, display_name, created_at) "
                        "VALUES (?,?,?,?)",
                        ("org-drill-0", "dup", "Duplicate", 1.0),
                    )
        finally:
            restored.close()

    def test_restoring_over_a_populated_database_is_refused_by_default(self):
        populate(self.source)
        archive = Path(self.directory.name) / "guard.dump"
        backup.dump(self.source, archive, tool_url=tool_view(self.source))
        with self.assertRaises(backup.BackupError) as raised:
            backup.restore(archive, self.source, tool_url=tool_view(self.source))
        self.assertEqual(raised.exception.code, "RESTORE_TARGET_NOT_EMPTY")

    def test_a_corrupted_archive_is_refused_before_any_restore(self):
        populate(self.source)
        archive = Path(self.directory.name) / "corrupt.dump"
        backup.dump(self.source, archive, tool_url=tool_view(self.source))
        archive.write_bytes(archive.read_bytes() + b"tampered")
        with self.assertRaises(backup.BackupError) as raised:
            backup.verify(archive)
        self.assertEqual(raised.exception.code, "BACKUP_DIGEST_MISMATCH")

    def test_overwriting_an_existing_backup_is_refused(self):
        populate(self.source)
        archive = Path(self.directory.name) / "once.dump"
        backup.dump(self.source, archive, tool_url=tool_view(self.source))
        with self.assertRaises(backup.BackupError) as raised:
            backup.dump(self.source, archive, tool_url=tool_view(self.source))
        self.assertEqual(raised.exception.code, "BACKUP_DESTINATION_EXISTS")

    def test_no_credential_appears_in_the_manifest(self):
        populate(self.source)
        archive = Path(self.directory.name) / "clean.dump"
        result = backup.dump(self.source, archive, tool_url=tool_view(self.source))
        serialized = json.dumps(result.manifest)
        for forbidden in ("password", "@", "postgresql://", "phase38b"):
            self.assertNotIn(forbidden, serialized, forbidden)

    def test_a_sqlite_control_plane_is_refused_by_this_tool(self):
        with self.assertRaises(backup.BackupError) as raised:
            backup.dump("sqlite:///nova.db", Path(self.directory.name) / "x.dump")
        self.assertEqual(raised.exception.code, "BACKUP_POSTGRESQL_ONLY")


@unittest.skipUnless(REDIS_URL, "NOVA_TEST_REDIS_URL is not set")
class MultiWorkerHttpTests(unittest.TestCase):
    """Deux processus servant la meme route partagent-ils vraiment un quota ?

    C'est la question que la Phase 38A avait laissee a moitie repondue : le
    partage etait prouve sur `ratelimit.py`, pas sur une route reelle.
    """

    def _worker(self, script_body: str, count: int) -> list[dict]:
        program = textwrap.dedent(
            """
            import json, os, sys, tempfile
            sys.path.insert(0, sys.argv[1])
            os.environ["NOVA_DB"] = sys.argv[2]
            os.environ["NOVA_REDIS_URL"] = sys.argv[3]
            os.environ.setdefault("NOVA_DOMAINS", "example.com")
            os.environ.setdefault("NOVA_ORGANIZATION_NAME", "Multi Worker")

            from fastapi.testclient import TestClient
            import main

            main.init_db()
            client = TestClient(main.app)
            __BODY__
            print("@@RESULT@@" + json.dumps(result))
            """
        ).replace("__BODY__", textwrap.dedent(script_body).strip())

        with tempfile.TemporaryDirectory() as directory:
            script = os.path.join(directory, "worker.py")
            with open(script, "w", encoding="utf-8") as handle:
                handle.write(program)
            shared_db = os.path.join(directory, "shared.db")
            # Chaque worker amorce la meme base SQLite : ce qui est teste ici
            # est le partage du compteur, pas celui de la base.
            processes = [
                subprocess.Popen(
                    [sys.executable, script, HERE, shared_db, REDIS_URL],
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    cwd=HERE,
                    text=True,
                )
                for _ in range(count)
            ]
            results = []
            for process in processes:
                out, err = process.communicate(timeout=300)
                self.assertEqual(process.returncode, 0, err[-3000:])
                results.append(json.loads(out.split("@@RESULT@@", 1)[1].strip().splitlines()[0]))
        return results

    def setUp(self):
        limiter = ratelimit.RateLimiter(redis_url=REDIS_URL)
        limiter.reset()
        self.address = f"shared-{uuid.uuid4().hex[:8]}@example.com"

    def test_two_workers_share_one_quota_on_a_real_http_route(self):
        quota = ratelimit.AUTH_START.limit
        per_worker = quota  # chacun tente le quota entier
        body = f"""
            result = {{"accepted": 0, "refused": 0, "shared": main.rate_limiter().shared}}
            for _ in range({per_worker}):
                response = client.post(
                    "/api/auth/request", json={{"email": "{self.address}"}}
                )
                if response.status_code == 429:
                    result["refused"] += 1
                else:
                    result["accepted"] += 1
            """
        results = self._worker(body, 2)

        for result in results:
            self.assertTrue(result["shared"], "chaque worker doit utiliser Redis")
        accepted = sum(result["accepted"] for result in results)
        refused = sum(result["refused"] for result in results)

        # Deux workers, chacun tentant le quota entier : le total accepte doit
        # rester le quota, pas son double. C'est exactement ce que le compteur
        # en memoire de processus ne savait pas faire.
        self.assertEqual(accepted, quota)
        self.assertEqual(refused, quota)


@unittest.skipUnless(REDIS_URL, "NOVA_TEST_REDIS_URL is not set")
class RedisOutageTests(unittest.TestCase):
    """Panne du compteur partage : chaque route se comporte comme declare."""

    class _Down:
        shared = True

        def allow(self, *_args, **_kwargs):
            raise ConnectionError("redis unreachable")

        def healthy(self):
            return False

        def reset(self):
            pass

    def test_a_fail_closed_route_refuses_and_a_fail_open_route_continues(self):
        limiter = ratelimit.RateLimiter(redis_url=REDIS_URL)
        limiter._backend = self._Down()

        with self.assertRaises(ratelimit.LimiterUnavailable):
            limiter.check("email", "someone", ratelimit.AUTH_START)
        with self.assertRaises(ratelimit.LimiterUnavailable):
            limiter.check("credential", "c1", ratelimit.SCIM_PER_CREDENTIAL)

        # La synchronisation continue : refuser tout couperait la configuration
        # de chaque organisation pour une panne de compteur.
        limiter.check("connector", "c1", ratelimit.CONNECTOR_SYNC)

    def test_the_limiter_recovers_when_redis_comes_back(self):
        limiter = ratelimit.RateLimiter(redis_url=REDIS_URL)
        healthy_backend = limiter._backend
        limiter._backend = self._Down()
        self.assertFalse(limiter.healthy())
        with self.assertRaises(ratelimit.LimiterUnavailable):
            limiter.check("email", "recover", ratelimit.AUTH_START)

        # Redis revient : aucun redemarrage n'est necessaire.
        limiter._backend = healthy_backend
        limiter.reset()
        self.assertTrue(limiter.healthy())
        limiter.check("email", "recover", ratelimit.AUTH_START)


@unittest.skipUnless(POSTGRES_URL, "NOVA_TEST_POSTGRES_URL is not set")
class RedisIsNotSourceOfTruthTests(unittest.TestCase):
    def test_business_state_survives_a_redis_flush(self):
        """Vider Redis ne doit rien coûter d'autre que des compteurs."""
        name = "novaredis_" + uuid.uuid4().hex[:8]
        self.addCleanup(drop_database, name)
        url = fresh_database(name)
        invariants = populate(url)

        if REDIS_URL:
            import redis

            client = redis.Redis.from_url(REDIS_URL)
            client.flushdb()

        db = database.Database(url)
        try:
            with db.connect() as conn:
                for table, expected in invariants.items():
                    if table == "schema_version":
                        continue
                    self.assertEqual(
                        conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0],
                        expected,
                        table,
                    )
        finally:
            db.close()


if __name__ == "__main__":
    unittest.main()
