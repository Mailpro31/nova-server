"""Déploiement d'entreprise — ce que le Control Plane promet et ce qu'il refuse.

Les tests portent sur les frontières, pas sur les getters : isolation de tenant,
absence de secret, autorité de rôle, honnêteté de l'état annoncé, et refus des
valeurs qu'un poste ne saurait pas appliquer.
"""

import json
import os
import tempfile
import unittest
import uuid
from pathlib import Path


DEPLOYMENT_TEST_DIRECTORY = tempfile.TemporaryDirectory()
os.environ.setdefault(
    "NOVA_DB", os.path.join(DEPLOYMENT_TEST_DIRECTORY.name, "nova-deployment-test.db")
)
os.environ.setdefault("NOVA_DOMAINS", "example.com")
os.environ.setdefault("NOVA_ORGANIZATION_NAME", "Nova Deployment Test")


def test_compose_requires_domains_and_product_files_drop_the_inherited_domain():
    root = Path(__file__).resolve().parent
    compose = (root / "docker-compose.yml").read_text(encoding="utf-8")
    assert '${NOVA_DOMAINS:?NOVA_DOMAINS must be set explicitly}' in compose

    inherited_domain = "ipsa" + ".fr"
    offenders = []
    for path in root.rglob("*"):
        if not path.is_file() or ".git" in path.parts or path.name.startswith("test_"):
            continue
        try:
            if inherited_domain in path.read_text(encoding="utf-8"):
                offenders.append(str(path.relative_to(root)))
        except (UnicodeDecodeError, OSError):
            continue
    assert offenders == []


def test_the_production_compose_is_a_deliverable_not_a_build_file():
    """Ce fichier est remis a une organisation : il ne construit rien.

    Le compose de developpement construit depuis les sources et suit `:latest`.
    Le remettre tel quel donnerait a chaque organisation une image differente,
    construite chez elle, et une mise a jour silencieuse de Whisper ou de vLLM
    le jour ou leur etiquette bouge.
    """
    root = Path(__file__).resolve().parent
    compose = (root / "docker-compose.production.yml").read_text(encoding="utf-8")

    assert "build:" not in compose, "un livrable ne construit pas depuis les sources"
    assert ":latest" not in compose, "une etiquette flottante n'est pas une version"
    assert "@sha256:" in compose, (
        "l'image doit etre epinglee par empreinte : une etiquette peut etre "
        "redirigee vers une autre image, une empreinte non")

    # Ni base, ni compteur partage embarques. La checklist de pilote les exige
    # provisionnes, sauvegardes et chiffres au repos ; un volume de conteneur
    # n'offre aucun des trois.
    for embarque in ("image: postgres", "image: redis", "image: vllm"):
        assert embarque not in compose, f"{embarque} n'a pas sa place dans un livrable"

    # Les variables sans defaut acceptable s'arretent, elles ne se devinent pas.
    for obligatoire in ("NOVA_REDIS_URL", "NOVA_DOMAINS", "NOVA_ADMIN_ORIGINS",
                        "NOVA_SERVICE_ENDPOINT"):
        assert f"${{{obligatoire}:?" in compose, (
            f"{obligatoire} doit arreter le demarrage plutot que prendre un defaut")

    # La sonde de vivacite ne lit pas la base : un orchestrateur redemarrerait
    # un processus vivant parce que sa base est momentanement injoignable.
    assert "/api/livez" in compose
    assert "/api/health" not in compose


def test_the_production_compose_carries_the_password_placeholder_the_server_expects():
    """Le marqueur du compose et celui du serveur sont le meme texte.

    Le mot de passe n'entre dans aucun fichier : le compose ecrit un marqueur,
    le serveur le remplace par le contenu du fichier de secret, percent-encode.
    Si les deux textes divergent, l'URL part avec le marqueur en guise de mot de
    passe -- et l'erreur ressemble a un identifiant refuse par PostgreSQL.

    Le compose double le `$` pour que Compose ne l'interprete pas ; c'est bien
    un seul `$` qui atteint le conteneur.
    """
    import main

    root = Path(__file__).resolve().parent
    compose = (root / "docker-compose.production.yml").read_text(encoding="utf-8")

    assert "$" + main.DATABASE_PASSWORD_PLACEHOLDER in compose, (
        "le marqueur du compose doit etre celui que main.py remplace")
    assert "NOVA_DATABASE_PASSWORD_FILE" in compose, (
        "sans le fichier designe, le marqueur reste et la connexion echoue")


def test_a_fresh_stack_applies_its_schema_without_being_asked():
    """`docker compose up` doit donner un serveur qui repond.

    Le schema s'applique explicitement, jamais au demarrage du service : c'est
    l'effet de bord qui a migre deux bases de production, et `serve.py` ne le
    fait donc pas. Mais personne d'autre ne le faisait ici. La commande du
    README donnait un serveur qui repondait 500 sur toutes ses routes --

        database.OperationalError: no such table: server_state

    -- sans qu'aucun conteneur n'echoue ni qu'aucun journal ne dise quoi faire.
    """
    root = Path(__file__).resolve().parent
    compose = (root / "docker-compose.yml").read_text(encoding="utf-8")

    assert "python\", \"-m\", \"migrations\", \"migrate" in compose.replace("'", '"'), (
        "aucun service n'applique le schema")
    assert "service_completed_successfully" in compose, (
        "l'API doit attendre la fin de la migration, pas son demarrage")
    # Et le service, lui, ne migre toujours pas tout seul.
    serveur = (root / "serve.py").read_text(encoding="utf-8")
    assert "init_db" not in serveur and "migrations" not in serveur, (
        "un service qui migre au demarrage est ce qui a migre deux bases de "
        "production")


def test_no_real_organization_is_shipped_as_a_default():
    """Aucun defaut ne nomme une organisation reelle.

    Le nom, le nom court et le campus sont annonces au poste par `/api/config`
    et affiches tels quels. Un defaut qui nomme une organisation reelle
    s'installe donc chez tous ceux qui ne l'ont pas remplace, et rien ne le
    signale -- le serveur demarre parfaitement.

    Le cas s'est produit sur une installation `orgatest1`, qui affichait
    « Paris » : le correctif precedent avait retire les defauts du code, mais
    ils survivaient dans le compose et dans le fichier d'exemple.
    """
    root = Path(__file__).resolve().parent
    herites = ("ipsa", "IPSA", "Paris")
    for nom in ("docker-compose.yml", ".env.example", "docker-compose.production.yml"):
        texte = (root / nom).read_text(encoding="utf-8")
        for interdit in herites:
            assert interdit not in texte, f"{nom} livre encore « {interdit} »"

    # Et le compose ne doit pas rendre un reglage ineffacable : `:-` traite une
    # valeur vide comme absente, donc un defaut non vide ne peut pas etre
    # efface en vidant la variable.
    compose = (root / "docker-compose.yml").read_text(encoding="utf-8")
    for variable in ("NOVA_ORGANIZATION_SHORT_NAME", "NOVA_ORGANIZATION_CAMPUS"):
        assert f'"${{{variable}:-}}"' in compose, (
            f"{variable} doit pouvoir etre efface en vidant la variable")


def test_an_empty_domain_list_says_so_at_startup():
    """Le defaut herite a disparu ; le silence qu'il laisse ne doit pas rester.

    La production refuse de demarrer sans `NOVA_DOMAINS`, et le compose Linux
    refuse de resoudre sa configuration. Reste le lancement direct en
    developpement : la liste vide n'admet personne, et sans ce message chaque
    premiere connexion echouerait en MEMBERSHIP_NOT_FOUND sans explication.

    Un sous-processus, parce que `ALLOWED_DOMAINS` est calcule a l'import : le
    lire autrement reviendrait a tester autre chose que ce que voit l'operateur.
    """
    import subprocess
    import sys

    root = Path(__file__).resolve().parent
    environment = {
        "PATH": os.environ.get("PATH", ""),
        "HOME": os.environ.get("HOME", ""),
        "NOVA_DB": str(Path(DEPLOYMENT_TEST_DIRECTORY.name) / "domains-warning.db"),
    }
    # Sous Windows, Winsock refuse de s'initialiser sans `SystemRoot` : le
    # sous-processus echouait a `import asyncio` en WinError 10106, bien avant
    # d'atteindre `main`. L'environnement reste volontairement minimal -- il
    # s'agit de verifier ce que voit un operateur qui n'a rien declare -- mais
    # une variable que l'OS exige pour ouvrir une socket n'est pas une
    # declaration de l'operateur.
    for inherited in ("SystemRoot", "SYSTEMROOT", "TEMP", "TMP"):
        value = os.environ.get(inherited)
        if value:
            environment[inherited] = value

    silent = subprocess.run(
        [sys.executable, "-c", "import main"], cwd=root, env=environment,
        capture_output=True, text=True, timeout=120,
    )
    assert silent.returncode == 0, silent.stderr
    assert "NOVA_DOMAINS absent" in silent.stdout

    declared = subprocess.run(
        [sys.executable, "-c", "import main"], cwd=root,
        env={**environment, "NOVA_DOMAINS": "exemple.fr"},
        capture_output=True, text=True, timeout=120,
    )
    assert declared.returncode == 0, declared.stderr
    assert "NOVA_DOMAINS absent" not in declared.stdout


from fastapi.testclient import TestClient  # noqa: E402

import deployment  # noqa: E402
import main  # noqa: E402

# Le schema s'applique explicitement : importer `main` ne migre plus rien, et ce
# fichier etait le seul de la suite a ne pas faire l'appel. Il passait quand
# meme, parce qu'un autre module de test importe `main` avant lui et partage la
# base que le premier `NOVA_DB` a designee -- donc les tables existaient deja,
# creees par quelqu'un d'autre. Lance seul, le meme fichier echouait dix-huit
# fois sur « no such table: deployment_profiles ». Un test dont le resultat
# depend de ses voisins ne dit rien de ce qu'il pretend verifier.
main.init_db()


def manifest_document(version: str = "1.1.0", channel: str = "stable") -> dict:
    return {
        "schema_version": 1,
        "artifacts": [
            {
                "version": version,
                "edition": "organization",
                "platform": "windows",
                "arch": "x86_64",
                "artifact": f"Nova-Organization-{version}-x64-Setup.exe",
                "sha256": "a" * 64,
                "size": 23559903,
                "released_at": 1756000000.0,
                "channel": channel,
                "minimum_supported_version": "1.0.0",
            }
        ],
    }


class DeploymentProfileTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.client = TestClient(main.app)

    def setUp(self):
        with main.db() as conn:
            for table in ("deployment_profiles", "admin_audit_log", "admin_sessions", "users"):
                conn.execute(f"DELETE FROM {table}")
        self.organization_id = main.nova_organization_id()
        self.it_headers = self._admin(self.organization_id, "it_admin")
        self.reader_headers = self._admin(self.organization_id, "read_only")
        self.org_admin_headers = self._admin(self.organization_id, "organization_admin")
        # Aucun manifeste par défaut : l'état de départ est « non configuré »,
        # et c'est ce que la console doit dire.
        os.environ.pop("NOVA_DESKTOP_RELEASE_MANIFEST", None)
        os.environ.pop("NOVA_DESKTOP_RELEASE_PUBLIC_KEY", None)

    def _admin(self, organization_id: str, role: str) -> dict:
        with main.db() as conn:
            user_id = main.create_user(
                conn,
                organization_id,
                f"admin-{uuid.uuid4().hex[:8]}@example.com",
                security_role=role,
            )
            account = main.user_by_id(conn, user_id)
        session = main.create_admin_session(account, "test")
        return {"Authorization": f"Bearer {session['token']}"}

    def _profile(self, headers=None, **overrides) -> dict:
        body = {"name": f"parc-{uuid.uuid4().hex[:6]}"}
        body.update(overrides)
        response = self.client.post(
            f"/api/control/organizations/{self.organization_id}/deployment/profiles",
            headers=headers or self.it_headers,
            json=body,
        )
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()

    # ------------------------------------------------------------- autorité

    def test_deployment_is_managed_by_it_admin_and_read_by_read_only(self):
        # `it_admin` gère la machine ; c'est la frontière posée en Phase 22 et
        # elle vaut ici sans exception.
        self._profile(headers=self.it_headers)

        # `organization_admin` gère les personnes, pas le parc.
        refused = self.client.post(
            f"/api/control/organizations/{self.organization_id}/deployment/profiles",
            headers=self.org_admin_headers,
            json={"name": "interdit"},
        )
        self.assertEqual(refused.status_code, 403, refused.text)

        # `read_only` observe sans agir.
        overview = self.client.get(
            f"/api/control/organizations/{self.organization_id}/deployment",
            headers=self.reader_headers,
        )
        self.assertEqual(overview.status_code, 200, overview.text)
        refused = self.client.post(
            f"/api/control/organizations/{self.organization_id}/deployment/profiles",
            headers=self.reader_headers,
            json={"name": "interdit"},
        )
        self.assertEqual(refused.status_code, 403, refused.text)

    def test_an_anonymous_caller_gets_nothing(self):
        base = f"/api/control/organizations/{self.organization_id}/deployment"
        self.assertIn(self.client.get(base).status_code, {401, 403})
        self.assertIn(
            self.client.post(f"{base}/profiles", json={"name": "x"}).status_code,
            {401, 403},
        )

    # -------------------------------------------------------- valeurs sûres

    def test_only_values_the_workstation_can_apply_are_accepted(self):
        for payload, expected in (
            ({"name": "x", "channel": "nightly"}, "DEPLOYMENT_CHANNEL_INVALID"),
            ({"name": "x", "update_policy": "forced"}, "DEPLOYMENT_UPDATE_POLICY_INVALID"),
            ({"name": "x", "deployment_ring": "everyone"}, "DEPLOYMENT_RING_INVALID"),
        ):
            response = self.client.post(
                f"/api/control/organizations/{self.organization_id}/deployment/profiles",
                headers=self.it_headers,
                json=payload,
            )
            self.assertEqual(response.status_code, 400, response.text)
            self.assertEqual(response.json()["error"], expected)

    def test_unknown_and_dangerous_fields_are_refused(self):
        for extra in (
            {"server_url": "https://attacker.example"},
            {"control_plane_origin": "https://attacker.example"},
            {"organization_id": "another-org"},
            {"token": "x"},
            {"command": "calc.exe"},
            {"script": "iex"},
        ):
            response = self.client.post(
                f"/api/control/organizations/{self.organization_id}/deployment/profiles",
                headers=self.it_headers,
                json={"name": f"p-{uuid.uuid4().hex[:6]}", **extra},
            )
            self.assertEqual(response.status_code, 422, f"{extra}: {response.text}")

    # -------------------------------------------------- configuration machine

    def test_the_managed_config_is_complete_and_carries_no_secret(self):
        created = self._profile(channel="preview", update_policy="manual")
        profile_id = created["profile"]["profile_id"]
        response = self.client.get(
            f"/api/control/organizations/{self.organization_id}/deployment"
            f"/profiles/{profile_id}/config",
            headers=self.it_headers,
        )
        self.assertEqual(response.status_code, 200, response.text)
        document = response.json()["config"]

        self.assertEqual(document["schema_version"], 1)
        self.assertEqual(document["deployment_id"], profile_id)
        self.assertEqual(document["organization_id"], self.organization_id)
        self.assertEqual(document["control_plane_origin"], deployment.CONTROL_PLANE_ORIGIN)
        self.assertEqual(document["release_channel"], "preview")
        self.assertEqual(document["update_policy"], "manual")
        self.assertTrue(document["managed"])

        # Rien de ce qui suit n'a de raison d'exister dans un fichier machine
        # lisible par tout utilisateur du poste.
        serialized = json.dumps(document).lower()
        for forbidden in (
            "token", "secret", "password", "bearer", "credential",
            "client_secret", "api_key", "private",
        ):
            self.assertNotIn(forbidden, serialized, forbidden)

    def test_the_install_command_carries_no_durable_secret(self):
        created = self._profile()
        command = created["install_command"]
        self.assertIn("/S ", command)
        self.assertIn(created["profile"]["profile_id"], command)
        self.assertIn(self.organization_id, command)
        lowered = command.lower()
        for forbidden in ("token", "secret", "password", "/server_url", "http://", "https://"):
            self.assertNotIn(forbidden, lowered, forbidden)

    # ------------------------------------------------------ isolation tenant

    def test_a_profile_of_another_organization_is_invisible(self):
        created = self._profile()
        profile_id = created["profile"]["profile_id"]
        other = "org-" + uuid.uuid4().hex[:8]
        with main.db() as conn:
            conn.execute(
                "INSERT OR IGNORE INTO organizations(id, slug, display_name, created_at) "
                "VALUES (?,?,?,?)",
                (other, other, "Other", 0),
            )

        # Même un administrateur légitime de cette organisation ne peut pas
        # atteindre le parc d'une autre : la réponse est la même que pour un
        # identifiant inexistant.
        for target in (profile_id, "dpl-" + uuid.uuid4().hex):
            response = self.client.get(
                f"/api/control/organizations/{other}/deployment/profiles/{target}/config",
                headers=self.it_headers,
            )
            self.assertIn(response.status_code, {403, 404}, response.text)

    def test_verify_refuses_a_deployment_paired_with_another_organization(self):
        created = self._profile()
        profile_id = created["profile"]["profile_id"]

        ok = self.client.post(
            "/api/deployment/verify",
            json={"deployment_id": profile_id, "organization_id": self.organization_id},
        )
        self.assertEqual(ok.status_code, 200, ok.text)
        self.assertTrue(ok.json()["valid"])

        # Le poste ne choisit pas son organisation. Un couple invalide est
        # refusé, et la réponse n'apprend pas à quelle organisation le parc
        # appartient réellement.
        wrong = self.client.post(
            "/api/deployment/verify",
            json={"deployment_id": profile_id, "organization_id": "another-org"},
        )
        self.assertEqual(wrong.status_code, 404, wrong.text)
        self.assertEqual(wrong.json()["error"], "DEPLOYMENT_NOT_RECOGNIZED")
        self.assertNotIn(self.organization_id, wrong.text)

    def test_verify_refuses_malformed_or_extra_fields(self):
        for body in (
            {"deployment_id": "dpl-1"},
            {"deployment_id": "../etc", "organization_id": "o"},
            {"deployment_id": "d", "organization_id": "o", "role": "admin"},
            {"deployment_id": "d" * 200, "organization_id": "o"},
        ):
            response = self.client.post("/api/deployment/verify", json=body)
            self.assertEqual(response.status_code, 400, f"{body}: {response.text}")

    def test_a_retired_profile_stops_being_recognized(self):
        created = self._profile()
        profile_id = created["profile"]["profile_id"]
        response = self.client.put(
            f"/api/control/organizations/{self.organization_id}/deployment/profiles/{profile_id}",
            headers=self.it_headers,
            json={"status": "retired"},
        )
        self.assertEqual(response.status_code, 200, response.text)
        verify = self.client.post(
            "/api/deployment/verify",
            json={"deployment_id": profile_id, "organization_id": self.organization_id},
        )
        self.assertEqual(verify.status_code, 404, verify.text)

    # ------------------------------------------------------------- révision

    def test_every_change_advances_the_configuration_revision(self):
        created = self._profile()
        profile_id = created["profile"]["profile_id"]
        self.assertEqual(created["profile"]["config_revision"], 1)
        updated = self.client.put(
            f"/api/control/organizations/{self.organization_id}/deployment/profiles/{profile_id}",
            headers=self.it_headers,
            json={"channel": "preview"},
        )
        self.assertEqual(updated.status_code, 200, updated.text)
        self.assertEqual(updated.json()["profile"]["config_revision"], 2)
        self.assertEqual(updated.json()["profile"]["channel"], "preview")

    def test_an_empty_update_is_refused_rather_than_bumping_the_revision(self):
        profile_id = self._profile()["profile"]["profile_id"]
        response = self.client.put(
            f"/api/control/organizations/{self.organization_id}/deployment/profiles/{profile_id}",
            headers=self.it_headers,
            json={},
        )
        self.assertEqual(response.status_code, 400, response.text)
        self.assertEqual(response.json()["error"], "DEPLOYMENT_UPDATE_EMPTY")

    # ------------------------------------------------------------- releases

    def test_without_a_manifest_the_console_says_not_configured(self):
        self._profile()
        overview = self.client.get(
            f"/api/control/organizations/{self.organization_id}/deployment",
            headers=self.it_headers,
        ).json()
        self.assertEqual(overview["status"], "not_configured")
        self.assertFalse(overview["release"]["configured"])
        self.assertFalse(overview["release"]["signed"])
        self.assertFalse(overview["device_reporting"])

    def test_a_valid_manifest_makes_the_package_ready(self):
        path = os.path.join(DEPLOYMENT_TEST_DIRECTORY.name, "release.json")
        with open(path, "w", encoding="utf-8") as handle:
            json.dump(manifest_document(), handle)
        os.environ["NOVA_DESKTOP_RELEASE_MANIFEST"] = path
        self._profile()
        overview = self.client.get(
            f"/api/control/organizations/{self.organization_id}/deployment",
            headers=self.it_headers,
        ).json()
        self.assertEqual(overview["status"], "package_ready")
        stable = overview["release"]["channels"]["stable"]
        self.assertEqual(stable["version"], "1.1.0")
        self.assertEqual(stable["minimum_supported_version"], "1.0.0")
        # Sans racine de confiance configurée, on ne prétend pas que c'est signé.
        self.assertFalse(overview["release"]["signed"])

    def test_a_broken_manifest_becomes_a_configuration_error_not_a_guess(self):
        path = os.path.join(DEPLOYMENT_TEST_DIRECTORY.name, "broken.json")
        document = manifest_document()
        document["artifacts"][0]["sha256"] = "not-a-digest"
        with open(path, "w", encoding="utf-8") as handle:
            json.dump(document, handle)
        os.environ["NOVA_DESKTOP_RELEASE_MANIFEST"] = path
        self._profile()
        overview = self.client.get(
            f"/api/control/organizations/{self.organization_id}/deployment",
            headers=self.it_headers,
        ).json()
        self.assertEqual(overview["status"], "configuration_error")
        self.assertEqual(overview["release"]["channels"]["stable"], None)

    def test_a_configured_trust_root_with_a_bad_signature_serves_nothing(self):
        path = os.path.join(DEPLOYMENT_TEST_DIRECTORY.name, "signed.json")
        with open(path, "w", encoding="utf-8") as handle:
            json.dump(manifest_document(), handle)
        with open(path + ".sig", "w", encoding="utf-8") as handle:
            handle.write("00" * 64)
        os.environ["NOVA_DESKTOP_RELEASE_MANIFEST"] = path
        os.environ["NOVA_DESKTOP_RELEASE_PUBLIC_KEY"] = "ab" * 32
        loaded = deployment.load_release_manifest()
        self.assertEqual(loaded["error"], "DESKTOP_RELEASE_SIGNATURE_INVALID")
        self.assertEqual(loaded["artifacts"], [])
        self.assertFalse(loaded["signed"])

    def test_the_console_never_claims_a_health_it_has_not_measured(self):
        # `healthy` exigerait un rapport de poste, et Nova n'en reçoit aucun.
        path = os.path.join(DEPLOYMENT_TEST_DIRECTORY.name, "healthy.json")
        with open(path, "w", encoding="utf-8") as handle:
            json.dump(manifest_document(), handle)
        os.environ["NOVA_DESKTOP_RELEASE_MANIFEST"] = path
        self._profile()
        overview = self.client.get(
            f"/api/control/organizations/{self.organization_id}/deployment",
            headers=self.it_headers,
        ).json()
        self.assertNotEqual(overview["status"], "healthy")
        self.assertFalse(overview["device_reporting"])

    # ---------------------------------------------------------------- audit

    def test_creating_a_profile_is_recorded_without_leaking_anything(self):
        profile_id = self._profile()["profile"]["profile_id"]
        with main.db() as conn:
            row = conn.execute(
                "SELECT * FROM admin_audit_log WHERE action='deployment.profile.create' "
                "AND target_id=?",
                (profile_id,),
            ).fetchone()
        self.assertIsNotNone(row)
        self.assertEqual(row["organization_id"], self.organization_id)
        self.assertNotIn("token", (row["metadata"] or "").lower())


class ReleaseManifestParsingTests(unittest.TestCase):
    def test_a_manifest_missing_a_field_is_refused(self):
        for missing in ("version", "sha256", "size", "channel", "minimum_supported_version"):
            document = manifest_document()
            del document["artifacts"][0][missing]
            with self.assertRaises(deployment.DeploymentError, msg=missing):
                deployment.parse_release_manifest(json.dumps(document).encode())

    def test_an_unsupported_schema_is_refused_rather_than_guessed(self):
        document = manifest_document()
        document["schema_version"] = 2
        with self.assertRaises(deployment.DeploymentError):
            deployment.parse_release_manifest(json.dumps(document).encode())

    def test_a_foreign_platform_or_edition_is_refused(self):
        for field, value in (("platform", "linux"), ("edition", "personal"), ("channel", "beta")):
            document = manifest_document()
            document["artifacts"][0][field] = value
            with self.assertRaises(deployment.DeploymentError, msg=field):
                deployment.parse_release_manifest(json.dumps(document).encode())

    def test_the_newest_release_of_a_channel_wins(self):
        document = manifest_document()
        older = dict(document["artifacts"][0])
        older.update({"version": "1.0.9", "released_at": 1700000000.0})
        document["artifacts"].append(older)
        parsed = deployment.parse_release_manifest(json.dumps(document).encode())
        manifest = {"configured": True, "signed": False, "artifacts": parsed["artifacts"], "error": None}
        self.assertEqual(deployment.release_for_channel(manifest, "stable")["version"], "1.1.0")
        self.assertIsNone(deployment.release_for_channel(manifest, "preview"))


if __name__ == "__main__":
    unittest.main()
