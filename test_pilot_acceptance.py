"""Le parcours pilote complet, sur une base PostgreSQL neuve.

## Ce que ce fichier prouve

Qu'une organisation peut être amenée de « base vide » à « membre qui dicte »
**sans une seule instruction SQL écrite à la main**. C'est la question à
laquelle un pilote doit répondre avant tout le reste : si l'amorçage demande à
quelqu'un d'ouvrir la base, le produit n'est pas déployable, quelle que soit la
qualité du reste.

Chaque étape passe donc par le chemin qu'un opérateur emprunterait vraiment :
la commande de migration, l'API HTTP, la commande d'opérateur `admin_cli`. Une
fixture qui insérerait directement les lignes ferait passer le test et cacherait
exactement ce qu'il doit révéler.

## Base neuve, jamais partagée

Chaque scénario obtient sa propre base PostgreSQL, créée puis détruite. Un
scénario qui hériterait de données d'un autre ne prouverait pas l'amorçage.
"""

import json
import os
import subprocess
import sys
import textwrap
import unittest
import uuid

import migrations


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


def run_pilot(body: str, url: str, *, slug: str = "pilot", extra: dict | None = None) -> dict:
    """Execute un scenario dans un processus neuf, branche sur `url`."""
    program = textwrap.dedent(
        """
        import contextlib, io, json, os, sys
        sys.path.insert(0, sys.argv[1])

        os.environ["NOVA_DATABASE_URL"] = sys.argv[2]
        os.environ["NOVA_ORGANIZATION_ID"] = sys.argv[3]
        os.environ.setdefault("NOVA_ORGANIZATION_NAME", "Pilot Organization")
        os.environ.setdefault("NOVA_DOMAINS", "pilot.example")
        # Mode developpement : sans SMTP, c'est le seul moyen d'obtenir le code
        # a usage unique — exactement comme un developpeur le lit dans sa
        # console. Aucune lecture directe de la base n'est faite pour cela.
        os.environ.setdefault("NOVA_ENV", "development")

        from fastapi.testclient import TestClient
        import admin_cli
        import database, migrations
        import main

        client = TestClient(main.app)
        result = {}


        def sign_in(email, machine="pilot-machine"):
            \"\"\"Le parcours reel : demander un code, le lire, le presenter.\"\"\"
            captured = io.StringIO()
            with contextlib.redirect_stdout(captured):
                started = client.post("/api/auth/request", json={"email": email})
            assert started.status_code == 200, started.text
            printed = captured.getvalue()
            code = printed.rsplit(":", 1)[-1].strip()
            verified = client.post(
                "/api/auth/verify",
                json={"email": email, "code": code, "machine": machine},
            )
            assert verified.status_code == 200, verified.text
            return verified.json()["token"]


        def admin_session(email):
            with main.db() as conn:
                row = main.user_by_email(conn, main.nova_organization_id(), email)
            return {"Authorization": "Bearer "
                    + main.create_admin_session(row, "pilot")["token"]}

        __BODY__

        print("@@RESULT@@" + json.dumps(result, default=str))
        """
    ).replace("__BODY__", textwrap.dedent(body).strip())

    script = os.path.join(HERE, f"_pilot_{uuid.uuid4().hex[:10]}.py")
    with open(script, "w", encoding="utf-8") as handle:
        handle.write(program)
    environment = dict(os.environ)
    for key in list(environment):
        if key.startswith("NOVA_") and key not in ("NOVA_TEST_POSTGRES_URL",):
            del environment[key]
    if REDIS_URL:
        environment["NOVA_REDIS_URL"] = REDIS_URL
    environment.update(extra or {})
    try:
        completed = subprocess.run(
            [sys.executable, script, HERE, url, slug],
            cwd=HERE,
            env=environment,
            capture_output=True,
            text=True,
            timeout=600,
        )
    finally:
        if os.path.exists(script):
            os.remove(script)
    if completed.returncode != 0 or "@@RESULT@@" not in completed.stdout:
        raise AssertionError(
            f"scenario pilote echoue (rc={completed.returncode})\n"
            f"stdout:\n{completed.stdout[-5000:]}\n\nstderr:\n{completed.stderr[-5000:]}"
        )
    return json.loads(completed.stdout.split("@@RESULT@@", 1)[1].strip().splitlines()[0])


@unittest.skipUnless(POSTGRES_URL, "NOVA_TEST_POSTGRES_URL is not set")
class FreshTenantBootstrapTests(unittest.TestCase):
    """De « base vide » à « organisation administrée », sans SQL manuel."""

    def setUp(self):
        self.name = "pilot_" + uuid.uuid4().hex[:10]
        self.addCleanup(drop_database, self.name)
        self.url = create_database(self.name)

    def test_a_fresh_tenant_reaches_a_working_organization(self):
        result = run_pilot(
            """
            handle = main.database_handle()

            # 1. Base vide. Le service refuse de demarrer avant migration.
            result["state_before"] = migrations.status(handle)["state"]
            try:
                migrations.check_startup(handle)
                result["startup_before"] = None
            except migrations.MigrationError as error:
                result["startup_before"] = error.code

            # 2. Migration explicite — la commande d'exploitation.
            result["migrated"] = migrations.migrate(handle)["applied"]
            result["state_after"] = migrations.status(handle)["state"]
            main.init_db()

            # 3. L'organisation existe, sans qu'aucune ligne n'ait ete ecrite
            #    a la main.
            organization_id = main.nova_organization_id()
            result["organization_id_stable"] = (
                organization_id == main.nova_organization_id()
            )

            # 4. Le premier compte nait de la connexion, pas d'un INSERT.
            sign_in("first.admin@pilot.example")
            result["account_created"] = True

            # 5. Le premier administrateur est nomme par la commande
            #    d'operateur. Aucun endpoint ne le permet, et c'est voulu.
            result["grant"] = admin_cli.run(
                ["grant", "first.admin@pilot.example", "organization_admin"]
            )

            # 6. La console repond.
            admin = admin_session("first.admin@pilot.example")
            result["organization_readable"] = client.get(
                "/api/control/organization", headers=admin
            ).status_code
            result["members_readable"] = client.get(
                f"/api/control/organizations/{organization_id}/members", headers=admin
            ).status_code
            """,
            self.url,
        )
        self.assertEqual(result["state_before"], "empty")
        self.assertEqual(result["startup_before"], "DATABASE_MIGRATION_REQUIRED")
        # Derive du disque : figer la liste faisait echouer ce test a chaque
        # migration ajoutee, pour une raison sans rapport avec ce qu'il verifie —
        # qu'un tenant neuf atteint le schema courant sans SQL manuel.
        self.assertEqual(
            result["migrated"],
            [migration.version for migration in migrations.discover()],
        )
        self.assertEqual(result["state_after"], "current")
        self.assertTrue(result["organization_id_stable"])
        self.assertTrue(result["account_created"])
        self.assertEqual(result["grant"], 0, "admin_cli grant doit reussir")
        self.assertEqual(result["organization_readable"], 200)
        self.assertEqual(result["members_readable"], 200)

    def test_no_http_endpoint_can_grant_administration(self):
        """La porte derobee qui n'existe pas.

        Un endpoint « faites de moi un administrateur » resterait une porte
        derobee quelles que soient ses conditions. Le seul chemin est la
        commande locale.
        """
        result = run_pilot(
            """
            main.init_db()
            organization_id = main.nova_organization_id()
            token = sign_in("plain.member@pilot.example")
            member = {"Authorization": "Bearer " + token}

            # Un membre ne peut ni lire ni ecrire l'administration.
            result["control"] = client.get(
                "/api/control/organization", headers=member
            ).status_code
            result["administrators"] = client.get(
                f"/api/control/organizations/{organization_id}/administrators",
                headers=member,
            ).status_code
            result["self_promotion"] = client.post(
                f"/api/control/organizations/{organization_id}/administrators/"
                "someone/role",
                headers=member, json={"security_role": "organization_admin"},
            ).status_code

            # Et aucune route ne porte un nom qui ressemblerait a cela.
            result["routes"] = [
                r.path for r in main.app.routes
                if hasattr(r, "path") and (
                    "bootstrap" in r.path or "promote" in r.path or "grant" in r.path
                )
            ]
            """,
            self.url,
        )
        for name, status in (
            ("control", result["control"]),
            ("administrators", result["administrators"]),
            ("self_promotion", result["self_promotion"]),
        ):
            self.assertIn(status, (401, 403, 404, 405), f"{name} -> {status}")
        self.assertEqual(result["routes"], [])


@unittest.skipUnless(POSTGRES_URL, "NOVA_TEST_POSTGRES_URL is not set")
class PilotJourneyTests(unittest.TestCase):
    """Le parcours complet d'un pilote, dans l'ordre où il se déroule."""

    def setUp(self):
        self.name = "journey_" + uuid.uuid4().hex[:10]
        self.addCleanup(drop_database, self.name)
        self.url = create_database(self.name)

    def test_the_whole_pilot_journey(self):
        result = run_pilot(
            """
            main.init_db()
            organization_id = main.nova_organization_id()

            # --- personas, tous crees par le parcours reel
            sign_in("it.admin@pilot.example")
            sign_in("org.admin@pilot.example")
            sign_in("reader@pilot.example")
            sign_in("member@pilot.example", machine="member-machine")
            admin_cli.run(["grant", "it.admin@pilot.example", "it_admin"])
            admin_cli.run(["grant", "org.admin@pilot.example", "organization_admin"])
            admin_cli.run(["grant", "reader@pilot.example", "read_only"])

            it = admin_session("it.admin@pilot.example")
            org = admin_session("org.admin@pilot.example")
            reader = admin_session("reader@pilot.example")
            base = f"/api/control/organizations/{organization_id}"

            # --- deploiement : profil, config machine, commande d'installation
            created = client.post(
                base + "/deployment/profiles", headers=it,
                json={"name": "parc-pilote", "channel": "stable",
                      "update_policy": "notify"},
            )
            result["profile_status"] = created.status_code
            profile = created.json()["profile"]
            result["deployment_id"] = profile["profile_id"]
            result["install_command"] = created.json()["install_command"]

            config = client.get(
                base + f"/deployment/profiles/{profile['profile_id']}/config",
                headers=it,
            )
            result["config_status"] = config.status_code
            result["managed_config"] = config.json()["config"]
            result["managed_path"] = config.json()["path"]

            # Le couple (parc, organisation) est verifiable, et un couple
            # croise ne l'est pas.
            result["verify_ok"] = client.post(
                "/api/deployment/verify",
                json={"deployment_id": profile["profile_id"],
                      "organization_id": organization_id},
            ).status_code
            result["verify_cross"] = client.post(
                "/api/deployment/verify",
                json={"deployment_id": profile["profile_id"],
                      "organization_id": "another-organization"},
            ).status_code

            # --- policies
            policy = client.get("/api/control/organization/policy", headers=org)
            result["policy_status"] = policy.status_code
            body = policy.json()
            result["policy_keys"] = sorted(body.get("settings", {}))[:6]
            result["policy_revision"] = body.get("revision")
            result["policy_configured"] = body.get("configured")

            # --- roles : le lecteur lit, n'ecrit pas
            #
            # Il lit l'organisation, pas la liste des membres : celle-ci releve
            # de `identity_manage`, que `read_only` n'a pas. Voir la matrice
            # des capacites de la Phase 22.
            result["reader_reads"] = client.get(
                "/api/control/organization", headers=reader
            ).status_code
            result["reader_members"] = client.get(
                base + "/members", headers=reader
            ).status_code
            result["reader_writes"] = client.post(
                base + "/deployment/profiles", headers=reader, json={"name": "interdit"}
            ).status_code
            # `organization_admin` gere les personnes, pas la machine.
            result["org_admin_deployment"] = client.post(
                base + "/deployment/profiles", headers=org, json={"name": "interdit"}
            ).status_code
            # `it_admin` gere la machine, pas les personnes.
            result["it_admin_members"] = client.get(base + "/members", headers=it).status_code

            # --- membre : session valide puis suspension
            member_token = sign_in("member@pilot.example", machine="member-machine")
            member = {"Authorization": "Bearer " + member_token}
            result["member_before"] = client.get("/api/me", headers=member).status_code

            with main.db() as conn:
                member_row = main.user_by_email(
                    conn, organization_id, "member@pilot.example"
                )
            suspended = client.post(
                base + f"/members/{member_row['user_id']}/status",
                headers=org, json={"status": "disabled"},
            )
            result["suspend_status"] = suspended.status_code
            result["member_after"] = client.get("/api/me", headers=member).status_code

            # --- diagnostics de la console
            result["connectors"] = client.get(
                "/api/control/connectors", headers=it
            ).status_code
            result["ai_runtime"] = client.get(
                "/api/control/organization/ai-runtime", headers=it
            ).status_code
            result["deployment_overview"] = client.get(
                base + "/deployment", headers=it
            ).status_code
            result["audit"] = client.get(base + "/audit", headers=org).status_code
            """,
            self.url,
        )

        self.assertEqual(result["profile_status"], 200)
        self.assertEqual(result["config_status"], 200)
        self.assertEqual(result["verify_ok"], 200)
        self.assertEqual(result["verify_cross"], 404, "un couple croise doit etre refuse")

        # La configuration machine ne porte aucun secret.
        config = json.dumps(result["managed_config"]).lower()
        for forbidden in ("token", "secret", "password", "credential", "client_secret"):
            self.assertNotIn(forbidden, config, forbidden)
        self.assertEqual(result["managed_config"]["organization_id"] and True, True)
        self.assertTrue(result["managed_path"].endswith("organization.json"))

        # La commande d'installation ne porte pas de secret non plus.
        command = result["install_command"].lower()
        for forbidden in ("token", "secret", "password", "server_url", "http"):
            self.assertNotIn(forbidden, command, forbidden)

        self.assertEqual(result["policy_status"], 200)
        self.assertTrue(result["policy_keys"])

        # Les trois rôles, chacun dans son périmètre.
        self.assertEqual(result["reader_reads"], 200)
        self.assertEqual(result["reader_writes"], 403)
        # La liste des membres releve de la gestion d'identites, pas de la
        # lecture : `read_only` ne la voit pas.
        self.assertEqual(result["reader_members"], 403)
        self.assertEqual(result["org_admin_deployment"], 403)
        self.assertIn(result["it_admin_members"], (401, 403))

        # La suspension coupe la session immédiatement.
        self.assertEqual(result["member_before"], 200)
        self.assertEqual(result["suspend_status"], 200)
        self.assertIn(result["member_after"], (401, 403))

        for name in ("connectors", "ai_runtime", "deployment_overview", "audit"):
            self.assertEqual(result[name], 200, name)


@unittest.skipUnless(POSTGRES_URL, "NOVA_TEST_POSTGRES_URL is not set")
class TenantIsolationTests(unittest.TestCase):
    """Deux organisations, aucune fuite."""

    def setUp(self):
        self.first = "tenant_a_" + uuid.uuid4().hex[:8]
        self.second = "tenant_b_" + uuid.uuid4().hex[:8]
        self.addCleanup(drop_database, self.first)
        self.addCleanup(drop_database, self.second)
        self.url_a = create_database(self.first)
        self.url_b = create_database(self.second)

    def test_an_admin_of_one_tenant_reaches_nothing_of_the_other(self):
        # Chaque organisation vit dans son instance : c'est le mode dedie.
        # L'isolation se verifie donc a la frontiere de l'API.
        setup_b = run_pilot(
            """
            main.init_db()
            result["organization_id"] = main.nova_organization_id()
            sign_in("b.admin@pilot.example")
            admin_cli.run(["grant", "b.admin@pilot.example", "organization_admin"])
            """,
            self.url_b,
            slug="tenant-b",
        )
        foreign = setup_b["organization_id"]

        result = run_pilot(
            f"""
            main.init_db()
            organization_id = main.nova_organization_id()
            # Un seul role par compte : `grant` remplace, il n'ajoute pas.
            # `organization_admin` gere les personnes, ce qui suffit ici.
            sign_in("a.admin@pilot.example")
            admin_cli.run(["grant", "a.admin@pilot.example", "organization_admin"])
            admin = admin_session("a.admin@pilot.example")

            foreign = "{foreign}"
            result["same_tenant"] = organization_id == foreign
            result["own_members"] = client.get(
                f"/api/control/organizations/{{organization_id}}/members", headers=admin
            ).status_code
            for name, path in (
                ("members", "/members"),
                ("administrators", "/administrators"),
                ("packages", "/packages"),
                ("provisioning", "/provisioning"),
                ("audit", "/audit"),
            ):
                result["foreign_" + name] = client.get(
                    f"/api/control/organizations/{{foreign}}" + path, headers=admin
                ).status_code
            """,
            self.url_a,
            slug="tenant-a",
        )

        self.assertFalse(result["same_tenant"], "les deux organisations doivent differer")
        self.assertEqual(result["own_members"], 200)
        for key, status in result.items():
            if key.startswith("foreign_"):
                self.assertIn(status, (403, 404), f"{key} -> {status}")


@unittest.skipUnless(POSTGRES_URL, "NOVA_TEST_POSTGRES_URL is not set")
class ManagedModeTests(unittest.TestCase):
    """Un poste géré reste géré, quoi qu'il arrive."""

    def setUp(self):
        self.name = "managed_" + uuid.uuid4().hex[:10]
        self.addCleanup(drop_database, self.name)
        self.url = create_database(self.name)

    def test_the_managed_configuration_carries_only_public_metadata(self):
        result = run_pilot(
            """
            main.init_db()
            organization_id = main.nova_organization_id()
            sign_in("it@pilot.example")
            admin_cli.run(["grant", "it@pilot.example", "it_admin"])
            it = admin_session("it@pilot.example")
            base = f"/api/control/organizations/{organization_id}"
            profile = client.post(
                base + "/deployment/profiles", headers=it, json={"name": "parc"}
            ).json()["profile"]
            result["config"] = client.get(
                base + f"/deployment/profiles/{profile['profile_id']}/config",
                headers=it,
            ).json()["config"]
            """,
            self.url,
        )
        config = result["config"]
        # Le contrat exact que le poste applique — tout champ inconnu y serait
        # refuse, secret compris.
        self.assertEqual(config["schema_version"], 1)
        self.assertTrue(config["managed"])
        self.assertIn(config["release_channel"], ("stable", "preview"))
        self.assertTrue(config["control_plane_origin"].startswith("https://"))
        self.assertEqual(
            set(config) - {
                "schema_version", "deployment_id", "organization_id",
                "control_plane_origin", "release_channel", "managed",
                "config_revision", "update_policy", "deployment_ring", "autostart",
            },
            set(),
            "aucun champ hors contrat",
        )


if __name__ == "__main__":
    unittest.main()
