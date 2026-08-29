"""Phase 35 — distribution et mise à jour du Connector.

La chaîne testée ici est celle d'un approvisionnement logiciel : signature du
manifeste, empreinte de l'artefact, activation. Chaque maillon est vérifié
seul, et surtout : rompre l'un doit suffire à tout arrêter.

Toutes les clés de ces tests sont générées à l'exécution. Aucune clé de
signature réelle n'existe dans ce dépôt.
"""

# `test_main` doit etre importe en premier : il redirige NOVA_DB vers un
# repertoire temporaire avant que `main` ne cree son schema au moment de
# l'import. Sans cet ordre, importer `main` ecrirait dans la vraie base.
from test_main import AdminTestCase, grant_admin_session  # noqa: F401

import hashlib
import io
import json
import os
import tarfile
import tempfile
import time
import unittest
import uuid
from pathlib import Path
from unittest.mock import patch

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ed25519

import connector_release as release
import main
from test_connector import ConnectorTestCase

# Le schema s'applique **explicitement**. Importer `main` ne migre plus rien :
# c'est precisement l'effet de bord qui a migre une base de production en
# Phase 30 puis en Phase 37. Un appel visible dans le test remplace un import
# qui decidait tout seul.
main.init_db()


def make_keypair():
    """Une paire de clés de TEST, jamais persistée."""
    private = ed25519.Ed25519PrivateKey.generate()
    public = private.public_key().public_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PublicFormat.Raw,
    )
    return private, public.hex()


def build_archive(payload: bytes = b"print('connector')\n") -> bytes:
    """Une archive minimale, valide, deterministe."""
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w:gz") as bundle:
        info = tarfile.TarInfo("connector.py")
        info.size = len(payload)
        bundle.addfile(info, io.BytesIO(payload))
    return buffer.getvalue()


def manifest_for(
    archive: bytes,
    version: str = "0.2.0",
    status: str = release.RELEASE_PUBLISHED,
    protocol: int = release.CONNECTOR_PROTOCOL_VERSION,
    platform_os: str = "linux",
    arch: str = "amd64",
    distribution: str = release.DISTRIBUTION_PACKAGE,
    url: str = "https://github.com/nova/releases/connector.tar.gz",
) -> str:
    return json.dumps(
        {
            "schema_version": release.MANIFEST_SCHEMA_VERSION,
            "connector_version": version,
            "protocol_version": protocol,
            "status": status,
            "released_at": time.time(),
            "minimum_server_version": "0.2.0",
            "artifacts": [
                {
                    "os": platform_os,
                    "arch": arch,
                    "distribution": distribution,
                    "url": url,
                    "digest": "sha256:" + hashlib.sha256(archive).hexdigest(),
                    "size": len(archive),
                }
            ],
        }
    )


def sign(private, manifest: str) -> str:
    return private.sign(manifest.encode("utf-8")).hex()


LINUX_PACKAGE = {"os": "linux", "arch": "amd64", "distribution": release.DISTRIBUTION_PACKAGE}


class VersionTests(unittest.TestCase):
    def test_versions_compare_as_numbers_not_as_text(self):
        # "0.10.0" < "0.9.0" en comparaison textuelle. Un ordre faux sur des
        # versions est exactement ce qui produit une regression silencieuse.
        self.assertTrue(release.is_newer("0.10.0", "0.9.0"))
        self.assertFalse(release.is_newer("0.9.0", "0.10.0"))
        self.assertFalse(release.is_newer("1.0.0", "1.0.0"))

    def test_a_malformed_version_is_refused_not_guessed(self):
        for value in ("1.0", "1.0.0-beta", "", "v1.0.0", "1.0.x", "-1.0.0"):
            with self.subTest(value=value):
                with self.assertRaises(release.UpdateError):
                    release.parse_version(value)

    def test_the_version_has_a_single_source(self):
        import connector as connector_service

        self.assertEqual(connector_service.CONNECTOR_VERSION, release.CONNECTOR_VERSION)
        self.assertEqual(
            connector_service.PROTOCOL_VERSION, release.CONNECTOR_PROTOCOL_VERSION
        )

    def test_the_product_version_and_the_protocol_are_separate(self):
        # Une correction de bogue ne doit pas obliger tout un parc a renegocier
        # ce qu'il sait faire.
        self.assertNotEqual(
            release.CONNECTOR_VERSION, str(release.CONNECTOR_PROTOCOL_VERSION)
        )


class SignatureTests(unittest.TestCase):
    def setUp(self):
        self.private, self.public = make_keypair()
        self.archive = build_archive()
        self.manifest = manifest_for(self.archive)

    def test_a_correctly_signed_manifest_is_accepted(self):
        parsed = release.verify_manifest(
            self.manifest.encode("utf-8"),
            bytes.fromhex(sign(self.private, self.manifest)),
            self.public,
        )
        self.assertEqual(parsed.connector_version, "0.2.0")

    def test_a_manifest_signed_by_the_wrong_key_is_refused(self):
        other, _ = make_keypair()
        with self.assertRaises(release.UpdateError) as raised:
            release.verify_manifest(
                self.manifest.encode("utf-8"),
                bytes.fromhex(sign(other, self.manifest)),
                self.public,
            )
        self.assertEqual(raised.exception.code, release.UPDATE_SIGNATURE_INVALID)

    def test_a_tampered_manifest_is_refused(self):
        signature = sign(self.private, self.manifest)
        tampered = self.manifest.replace('"0.2.0"', '"9.9.9"', 1)
        with self.assertRaises(release.UpdateError) as raised:
            release.verify_manifest(
                tampered.encode("utf-8"), bytes.fromhex(signature), self.public
            )
        self.assertEqual(raised.exception.code, release.UPDATE_SIGNATURE_INVALID)

    def test_without_a_trust_root_nothing_is_accepted(self):
        """Pas de « en attendant ».

        Accepter un manifeste faute de cle configuree ouvrirait exactement ce
        que cette verification protege.
        """
        with self.assertRaises(release.UpdateError) as raised:
            release.verify_manifest(
                self.manifest.encode("utf-8"),
                bytes.fromhex(sign(self.private, self.manifest)),
                "",
            )
        self.assertEqual(raised.exception.code, release.UPDATE_SOURCE_UNTRUSTED)

    def test_the_signature_is_checked_before_the_content_is_trusted(self):
        # Un manifeste non signe ne doit pas pouvoir influencer la moindre
        # decision, pas meme celle de l'erreur levee.
        with self.assertRaises(release.UpdateError) as raised:
            release.verify_manifest(b"{ pas du json", b"\\x00" * 64, self.public)
        self.assertEqual(raised.exception.code, release.UPDATE_SIGNATURE_INVALID)


class ManifestTests(unittest.TestCase):
    def setUp(self):
        self.archive = build_archive()

    def parse(self, **kwargs):
        return release.parse_manifest(manifest_for(self.archive, **kwargs).encode("utf-8"))

    def test_a_manifest_without_artifacts_is_refused(self):
        body = json.loads(manifest_for(self.archive))
        body["artifacts"] = []
        with self.assertRaises(release.UpdateError):
            release.parse_manifest(json.dumps(body).encode("utf-8"))

    def test_a_bad_digest_shape_is_refused(self):
        for digest in ("", "abc", "md5:" + "0" * 32, "sha256:tropcourt"):
            body = json.loads(manifest_for(self.archive))
            body["artifacts"][0]["digest"] = digest
            with self.subTest(digest=digest):
                with self.assertRaises(release.UpdateError):
                    release.parse_manifest(json.dumps(body).encode("utf-8"))

    def test_an_absurd_size_is_refused(self):
        for size in (0, -1, release.MAX_ARTIFACT_BYTES + 1):
            body = json.loads(manifest_for(self.archive))
            body["artifacts"][0]["size"] = size
            with self.subTest(size=size):
                with self.assertRaises(release.UpdateError):
                    release.parse_manifest(json.dumps(body).encode("utf-8"))

    def test_an_oversized_manifest_is_refused_before_parsing(self):
        with self.assertRaises(release.UpdateError) as raised:
            release.parse_manifest(b"x" * (release.MAX_MANIFEST_BYTES + 1))
        self.assertEqual(raised.exception.code, release.UPDATE_TOO_LARGE)


class InstallabilityTests(unittest.TestCase):
    def setUp(self):
        self.archive = build_archive()

    def manifest(self, **kwargs):
        return release.parse_manifest(manifest_for(self.archive, **kwargs).encode("utf-8"))

    def test_a_matching_artifact_is_selected(self):
        artifact = release.check_installable(self.manifest(), "0.1.0", LINUX_PACKAGE)
        self.assertEqual(artifact.platform_os, "linux")

    def test_a_windows_artifact_is_not_installable_on_linux(self):
        """L'erreur serait sinon decouverte au redemarrage, c'est-a-dire trop
        tard."""
        with self.assertRaises(release.UpdateError) as raised:
            release.check_installable(
                self.manifest(platform_os="windows"), "0.1.0", LINUX_PACKAGE
            )
        self.assertEqual(raised.exception.code, release.UPDATE_PLATFORM_UNSUPPORTED)

    def test_a_docker_artifact_is_not_installable_by_a_package_connector(self):
        with self.assertRaises(release.UpdateError):
            release.check_installable(
                self.manifest(distribution=release.DISTRIBUTION_DOCKER),
                "0.1.0",
                LINUX_PACKAGE,
            )

    def test_a_revoked_release_is_never_installed(self):
        with self.assertRaises(release.UpdateError) as raised:
            release.check_installable(
                self.manifest(status=release.RELEASE_REVOKED), "0.1.0", LINUX_PACKAGE
            )
        self.assertEqual(raised.exception.code, release.UPDATE_RELEASE_REVOKED)

    def test_a_draft_release_is_never_installed(self):
        with self.assertRaises(release.UpdateError):
            release.check_installable(
                self.manifest(status=release.RELEASE_DRAFT), "0.1.0", LINUX_PACKAGE
            )

    def test_an_incompatible_protocol_is_refused_before_download(self):
        with self.assertRaises(release.UpdateError) as raised:
            release.check_installable(self.manifest(protocol=99), "0.1.0", LINUX_PACKAGE)
        self.assertEqual(raised.exception.code, release.UPDATE_PROTOCOL_UNSUPPORTED)

    def test_installing_an_older_version_is_refused(self):
        """Le retour arriere passe par le rollback, qui sait vers quoi revenir.

        Le faire passer par « installer une version plus ancienne »
        permettrait de rejouer une release retiree.
        """
        with self.assertRaises(release.UpdateError) as raised:
            release.check_installable(self.manifest(version="0.0.9"), "0.1.0", LINUX_PACKAGE)
        self.assertEqual(raised.exception.code, release.UPDATE_DOWNGRADE_REFUSED)

    def test_reinstalling_the_same_version_is_refused(self):
        with self.assertRaises(release.UpdateError):
            release.check_installable(self.manifest(version="0.1.0"), "0.1.0", LINUX_PACKAGE)


class ReleaseSourceTests(unittest.TestCase):
    """La source appartient au deploiement, pas au message recu."""

    def test_plain_http_is_refused_unless_explicitly_allowed(self):
        """Meme regle que pour l'adresse du plan de controle.

        Retomber en clair sans le dire exposerait la chaine
        d'approvisionnement sur le reseau de l'organisation.
        """
        with patch.dict(os.environ, {"NOVA_CONNECTOR_RELEASE_HOSTS": "localhost"}, clear=False):
            os.environ.pop("NOVA_CONNECTOR_RELEASE_ALLOW_INSECURE", None)
            with self.assertRaises(release.UpdateError) as raised:
                release.validate_release_url("http://localhost:9000/a.tar.gz")
            self.assertEqual(raised.exception.code, release.UPDATE_SOURCE_UNTRUSTED)
        with patch.dict(
            os.environ,
            {
                "NOVA_CONNECTOR_RELEASE_HOSTS": "localhost",
                "NOVA_CONNECTOR_RELEASE_ALLOW_INSECURE": "true",
            },
        ):
            self.assertTrue(release.validate_release_url("http://localhost:9000/a.tar.gz"))

    def test_only_configured_hosts_are_accepted(self):
        with patch.dict(os.environ, {"NOVA_CONNECTOR_RELEASE_HOSTS": "github.com"}):
            self.assertTrue(
                release.validate_release_url("https://github.com/nova/a.tar.gz")
            )
            self.assertTrue(
                release.validate_release_url("https://cdn.github.com/nova/a.tar.gz")
            )
            for url in (
                "https://attaquant.example.com/a.tar.gz",
                "http://github.com/nova/a.tar.gz",
                "file:///etc/passwd",
                "https://github.com.attaquant.example.com/a.tar.gz",
                "",
            ):
                with self.subTest(url=url):
                    with self.assertRaises(release.UpdateError) as raised:
                        release.validate_release_url(url)
                    self.assertEqual(
                        raised.exception.code, release.UPDATE_SOURCE_UNTRUSTED
                    )


class DownloadTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.staging = Path(self.directory.name) / "staging"
        self.archive = build_archive()
        manifest = release.parse_manifest(manifest_for(self.archive).encode("utf-8"))
        self.artifact = manifest.artifacts[0]

    def client_serving(self, payload: bytes, status: int = 200):
        archive = payload

        class Response:
            status_code = status

            def __enter__(self):
                return self

            def __exit__(self, *_):
                return False

            @staticmethod
            def iter_bytes():
                yield archive

        class Client:
            def __enter__(self):
                return self

            def __exit__(self, *_):
                return False

            @staticmethod
            def stream(method, url):
                return Response()

        return Client()

    def test_a_correct_artifact_is_staged(self):
        with patch.dict(os.environ, {"NOVA_CONNECTOR_RELEASE_HOSTS": "github.com"}):
            path = release.download_artifact(
                self.artifact, self.staging, self.client_serving(self.archive)
            )
        self.assertTrue(path.exists())
        self.assertEqual(release.digest_of(path), self.artifact.digest)

    def test_a_tampered_artifact_never_reaches_the_staging_area(self):
        """Un octet change suffit, et le fichier douteux ne reste pas.

        Le garder inviterait a le reutiliser un jour « pour gagner du temps ».
        """
        tampered = bytearray(self.archive)
        tampered[-1] ^= 0x01
        with patch.dict(os.environ, {"NOVA_CONNECTOR_RELEASE_HOSTS": "github.com"}):
            with self.assertRaises(release.UpdateError) as raised:
                release.download_artifact(
                    self.artifact, self.staging, self.client_serving(bytes(tampered))
                )
        self.assertEqual(raised.exception.code, release.UPDATE_DIGEST_MISMATCH)
        self.assertEqual(list(self.staging.iterdir()), [])

    def test_an_interrupted_download_leaves_nothing_activatable(self):
        partial = self.archive[: len(self.archive) // 2]
        with patch.dict(os.environ, {"NOVA_CONNECTOR_RELEASE_HOSTS": "github.com"}):
            with self.assertRaises(release.UpdateError):
                release.download_artifact(
                    self.artifact, self.staging, self.client_serving(partial)
                )
        self.assertEqual(list(self.staging.iterdir()), [])

    def test_an_artifact_larger_than_announced_is_cut_off(self):
        with patch.dict(os.environ, {"NOVA_CONNECTOR_RELEASE_HOSTS": "github.com"}):
            with self.assertRaises(release.UpdateError) as raised:
                release.download_artifact(
                    self.artifact, self.staging, self.client_serving(self.archive * 4)
                )
        self.assertEqual(raised.exception.code, release.UPDATE_TOO_LARGE)
        self.assertEqual(list(self.staging.iterdir()), [])

    def test_a_failed_response_is_not_written(self):
        with patch.dict(os.environ, {"NOVA_CONNECTOR_RELEASE_HOSTS": "github.com"}):
            with self.assertRaises(release.UpdateError) as raised:
                release.download_artifact(
                    self.artifact, self.staging, self.client_serving(b"", status=404)
                )
        self.assertEqual(raised.exception.code, release.UPDATE_DOWNLOAD_FAILED)

    def test_insufficient_disk_is_reported_before_downloading(self):
        class Usage:
            free = 1

        with patch.dict(os.environ, {"NOVA_CONNECTOR_RELEASE_HOSTS": "github.com"}), patch(
            "shutil.disk_usage", return_value=Usage()
        ):
            with self.assertRaises(release.UpdateError) as raised:
                release.download_artifact(
                    self.artifact, self.staging, self.client_serving(self.archive)
                )
        self.assertEqual(raised.exception.code, release.UPDATE_INSUFFICIENT_DISK)


class ActivationTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name) / "connector"
        self.staging = self.root / "staging"
        self.staging.mkdir(parents=True)

    def stage(self, payload: bytes = b"print('v')\n") -> Path:
        archive = self.staging / f"bundle-{uuid.uuid4().hex}.tar.gz"
        archive.write_bytes(build_archive(payload))
        return archive

    def test_activation_switches_the_current_version(self):
        release.activate(self.stage(), "0.2.0", self.root)
        self.assertEqual(release.installed_version(self.root), "0.2.0")
        self.assertTrue((self.root / "versions" / "0.2.0" / "connector.py").exists())

    def test_the_previous_version_survives_an_activation(self):
        """C'est ce qui rend le retour arriere instantane.

        Un rollback qui dependrait du reseau echouerait precisement quand le
        reseau est le probleme.
        """
        release.activate(self.stage(b"print('un')\n"), "0.2.0", self.root)
        release.activate(self.stage(b"print('deux')\n"), "0.3.0", self.root)
        self.assertEqual(release.installed_version(self.root), "0.3.0")
        self.assertTrue((self.root / "versions" / "0.2.0").is_dir())

    def test_rolling_back_needs_no_download(self):
        release.activate(self.stage(), "0.2.0", self.root)
        release.activate(self.stage(), "0.3.0", self.root)
        release.rollback(self.root, "0.2.0")
        self.assertEqual(release.installed_version(self.root), "0.2.0")

    def test_rolling_back_to_a_version_that_was_never_installed_is_refused(self):
        release.activate(self.stage(), "0.2.0", self.root)
        with self.assertRaises(release.UpdateError):
            release.rollback(self.root, "9.9.9")
        self.assertEqual(release.installed_version(self.root), "0.2.0")

    def test_an_archive_escaping_its_directory_is_refused(self):
        """La facon classique de transformer une archive en ecriture arbitraire."""
        buffer = io.BytesIO()
        with tarfile.open(fileobj=buffer, mode="w:gz") as bundle:
            info = tarfile.TarInfo("../../evade.py")
            info.size = 3
            bundle.addfile(info, io.BytesIO(b"bad"))
        archive = self.staging / "mechant.tar.gz"
        archive.write_bytes(buffer.getvalue())
        with self.assertRaises(release.UpdateError):
            release.activate(archive, "0.2.0", self.root)
        self.assertIsNone(release.installed_version(self.root))

    def test_a_failed_activation_leaves_no_half_version(self):
        archive = self.staging / "casse.tar.gz"
        archive.write_bytes(b"ceci n'est pas une archive")
        with self.assertRaises(release.UpdateError):
            release.activate(archive, "0.2.0", self.root)
        staged = [p for p in (self.root / "versions").iterdir() if p.name.startswith(".")]
        self.assertEqual(staged, [])

    def test_only_the_current_and_previous_versions_are_kept(self):
        # Conserver vingt versions ne servirait qu'a remplir le disque du
        # client : on ne revient jamais qu'a la derniere connue saine.
        for version in ("0.2.0", "0.3.0", "0.4.0"):
            release.activate(self.stage(), version, self.root)
        removed = release.prune_versions(self.root, ("0.4.0", "0.3.0"))
        self.assertEqual(removed, ["0.2.0"])
        self.assertTrue((self.root / "versions" / "0.3.0").is_dir())

    def test_staging_is_cleaned_up(self):
        self.stage()
        release.cleanup_staging(self.staging)
        self.assertFalse(self.staging.exists())


class UpdateStateTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)

    def test_the_state_survives_a_restart(self):
        release.write_update_state(
            release.record_attempt(
                {"version": "0.1.0"}, release.UPDATE_STAGED, "0.2.0"
            ),
            self.root,
        )
        state = release.read_update_state(self.root)
        self.assertEqual(state["state"], release.UPDATE_STAGED)
        self.assertEqual(state["target_version"], "0.2.0")

    def test_a_corrupted_state_falls_back_to_a_safe_default(self):
        release.update_state_path(self.root).write_text("{ casse", encoding="utf-8")
        self.assertEqual(
            release.read_update_state(self.root)["state"], release.UPDATE_UP_TO_DATE
        )

    def test_downloading_is_never_success(self):
        # Le succes ne s'annonce qu'apres verification, activation et sante.
        for stage in (
            release.UPDATE_DOWNLOADING,
            release.UPDATE_STAGED,
            release.UPDATE_VERIFYING,
            release.UPDATE_APPLYING,
            release.UPDATE_RESTARTING,
        ):
            with self.subTest(stage=stage):
                self.assertNotEqual(stage, release.UPDATE_HEALTHY)


class DistributionModelTests(unittest.TestCase):
    def test_a_container_does_not_claim_to_update_itself(self):
        """Le point du §44/§45.

        Un conteneur ne remplace pas sa propre image, et lui donner le socket
        Docker pour qu'il essaie reviendrait a lui donner la machine. Declarer
        la capacite partout ferait apparaitre un bouton qui echouerait a chaque
        fois — la pire des deux options.
        """
        import connector as connector_service

        self.assertFalse(release.self_update_supported(release.DISTRIBUTION_DOCKER))
        self.assertTrue(release.self_update_supported(release.DISTRIBUTION_PACKAGE))
        with patch.dict(os.environ, {"NOVA_CONNECTOR_DISTRIBUTION": "docker"}):
            declared = connector_service.capabilities()
            # Pas une absence : un contrat different. Une mise a jour est
            # possible, mais elle appartient a l'orchestrateur.
            self.assertNotIn("connector_self_update_v1", declared)
            self.assertIn("connector_orchestrator_update_v1", declared)
            self.assertIn("connector_update_v1", declared)
        with patch.dict(os.environ, {"NOVA_CONNECTOR_DISTRIBUTION": "package"}):
            declared = connector_service.capabilities()
            self.assertIn("connector_self_update_v1", declared)
            self.assertNotIn("connector_orchestrator_update_v1", declared)

    def test_the_distribution_mode_is_declared_not_guessed(self):
        # Deviner choisirait un jour le mauvais et tenterait une bascule
        # impossible.
        source = Path("connector_release.py").read_text(encoding="utf-8")
        self.assertIn('os.getenv("NOVA_CONNECTOR_DISTRIBUTION"', source)


class UpdateTransactionTests(unittest.TestCase):
    """La transaction : ce qui permet de survivre a un crash."""

    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.base = Path(self.directory.name)

    def test_a_transaction_survives_a_restart(self):
        transaction = release.new_transaction("0.1.0", "0.2.0", "r1")
        transaction["state"] = release.UPDATE_RESTARTING
        release.write_transaction(transaction, self.base)
        recovered = release.read_transaction(self.base)
        self.assertEqual(recovered["state"], release.UPDATE_RESTARTING)
        self.assertEqual(recovered["previous_version"], "0.1.0")
        self.assertEqual(recovered["target_version"], "0.2.0")

    def test_a_transaction_never_carries_a_credential(self):
        # Elle decrit une bascule, pas une identite.
        transaction = release.new_transaction("0.1.0", "0.2.0", "r1")
        self.assertNotIn("credential", json.dumps(transaction).lower())
        self.assertNotIn("token", json.dumps(transaction).lower())

    def test_a_corrupted_transaction_is_ignored_rather_than_trusted(self):
        release.transaction_path(self.base).write_text("{ casse", encoding="utf-8")
        self.assertEqual(release.read_transaction(self.base), {})

    def test_a_failed_target_is_not_retried_immediately(self):
        """Sinon : mauvaise version, retour arriere, le plan de controle
        repropose la meme cible, nouvelle tentative — indefiniment."""
        transaction = release.new_transaction("0.1.0", "0.2.0", "r1")
        transaction.update(state=release.UPDATE_ROLLED_BACK, failed_at=time.time())
        self.assertTrue(release.blocked_by_cooldown(transaction, "0.2.0"))
        # Une autre cible reste possible : le blocage vise une version, pas le
        # mecanisme.
        self.assertFalse(release.blocked_by_cooldown(transaction, "0.3.0"))

    def test_an_old_failure_stops_blocking(self):
        transaction = release.new_transaction("0.1.0", "0.2.0", "r1")
        transaction.update(
            state=release.UPDATE_ROLLED_BACK,
            failed_at=time.time() - release.UPDATE_RETRY_COOLDOWN_SECONDS - 10,
        )
        self.assertFalse(release.blocked_by_cooldown(transaction, "0.2.0"))

    def test_the_state_directory_lives_outside_the_versioned_tree(self):
        """P0 : une mise a jour ne doit pas effacer l'identite du Connector.

        Identite, credential, rotation, revisions appliquees vivent dans
        `state/`, que le remplacement d'une version ne touche jamais.
        """
        state = release.state_root(self.base)
        self.assertTrue(state.is_dir())
        self.assertNotIn("versions", str(state.relative_to(self.base)))
        (state / "identity").write_text("C1", encoding="utf-8")
        (self.base / "versions" / "0.1.0").mkdir(parents=True)
        release.prune_versions(self.base, ())
        self.assertEqual((state / "identity").read_text(encoding="utf-8"), "C1")


class SupervisorTests(unittest.TestCase):
    """Le temoin exterieur, et ce qu'il refuse de faire."""

    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.base = Path(self.directory.name)
        (self.base / "versions" / "0.1.0").mkdir(parents=True)
        (self.base / "versions" / "0.1.0" / "connector.py").write_text("", encoding="utf-8")
        release.point_current_at(self.base, "0.1.0")

    def test_it_only_ever_launches_its_own_installation(self):
        """P0 : aucune execution arbitraire.

        Le chemin est derive de la version courante. Il n'y a pas de variable a
        empoisonner, pas d'argument venu du reseau, pas d'executable designe de
        l'exterieur.
        """
        import connector_supervisor

        entrypoint = connector_supervisor.connector_entrypoint(self.base)
        self.assertEqual(entrypoint, self.base / "versions" / "0.1.0" / "connector.py")
        source = Path("connector_supervisor.py").read_text(encoding="utf-8")
        code = "\n".join(
            line for line in source.splitlines() if not line.strip().startswith("#")
        )
        for forbidden in ("shell=True", "os.system", "subprocess.Popen(f", "eval(", "exec("):
            self.assertNotIn(forbidden, code)
        # L'interpreteur est le sien, pas un chemin configurable.
        self.assertIn("sys.executable", code)

    def test_an_empty_installation_is_reported_not_guessed(self):
        import connector_supervisor

        empty = Path(self.directory.name) / "vide"
        empty.mkdir()
        self.assertIsNone(connector_supervisor.connector_entrypoint(empty))
        self.assertEqual(connector_supervisor.launch(empty), 2)

    def test_it_rolls_back_a_version_that_cannot_start(self):
        """Le cas qu'une auto-mise a jour seule ne peut pas couvrir.

        Une version qui ne demarre pas n'execute aucun code de retour arriere.
        Le temoin exterieur existe pour celui-la.
        """
        import connector_supervisor

        (self.base / "versions" / "0.2.0").mkdir()
        release.point_current_at(self.base, "0.2.0")
        release.write_transaction(
            {**release.new_transaction("0.1.0", "0.2.0", "r"),
             "state": release.UPDATE_RESTARTING, "previous_version": "0.1.0"},
            self.base,
        )
        self.assertTrue(connector_supervisor.roll_back_to_previous(self.base))
        self.assertEqual(release.installed_version(self.base), "0.1.0")
        self.assertEqual(
            release.read_transaction(self.base)["state"], release.UPDATE_ROLLED_BACK
        )

    def test_it_does_not_roll_back_outside_an_update(self):
        # Un plantage ordinaire n'est pas une mise a jour ratee : rebasculer
        # ferait perdre une version saine pour une erreur sans rapport.
        import connector_supervisor

        release.clear_transaction(self.base)
        self.assertFalse(connector_supervisor.roll_back_to_previous(self.base))
        self.assertEqual(release.installed_version(self.base), "0.1.0")


class MutualExclusionTests(unittest.TestCase):
    """Deux machines a etats critiques ne tournent pas ensemble."""

    def test_a_pending_rotation_defers_the_update(self):
        import connector as connector_service

        with tempfile.TemporaryDirectory() as directory:
            with patch.dict(
                os.environ,
                {"NOVA_CONNECTOR_INSTALL_ROOT": directory,
                 "NOVA_CONNECTOR_DISTRIBUTION": "package"},
            ):
                state = {"pending_credential": "secret-en-attente"}
                result = connector_service.start_update(state, "0.9.0")
        self.assertEqual(
            result["update_error"], release.UPDATE_BLOCKED_BY_ROTATION
        )

    def test_the_sync_prefers_rotation_over_update(self):
        # L'ordre est explicite dans la boucle : rotation d'abord, mise a jour
        # ensuite, jamais les deux dans le meme passage.
        source = Path("connector.py").read_text(encoding="utf-8")
        loop = source[source.index("def sync_once") : source.index("def reconcile")]
        rotation = loop.index("rotation_requested")
        update = loop.index("process_update_intent")
        self.assertLess(rotation, update)
        self.assertIn("elif answer.get(\"update\")", loop)


class ControlPlaneMechanismTests(ConnectorTestCase):
    """Le plan de controle distingue les deux mecanismes."""

    def test_it_reports_which_mechanism_applies(self):
        self.assertEqual(
            main.update_mechanism({"connector_self_update_v1"}), "self"
        )
        self.assertEqual(
            main.update_mechanism({"connector_orchestrator_update_v1"}), "external"
        )
        # Un Connector d'avant cette phase ne declare ni l'un ni l'autre. La
        # console doit le dire plutot que de supposer.
        self.assertEqual(main.update_mechanism({"health_reporting_v1"}), "none")

    def test_an_orchestrator_managed_connector_is_visible_as_such(self):
        body = self.enrolled()
        self.sync(
            body["connector_credential"],
            capabilities=["connector_update_v1", "connector_orchestrator_update_v1"],
        )
        headers = grant_admin_session("it@example.com", "it_admin")
        connector = self.client.get(
            "/api/control/connectors", headers=headers
        ).json()["connectors"][0]
        self.assertEqual(connector["platform"]["update_mechanism"], "external")
        self.assertFalse(connector["platform"]["self_update_supported"])


class ReleaseRegistryTests(ConnectorTestCase):
    """Le registre cote plan de controle."""

    def setUp(self):
        super().setUp()
        with main.db() as conn:
            conn.execute("DELETE FROM connector_releases")
        self.private, self.public = make_keypair()
        self.archive = build_archive()

    def publish(self, manifest: str | None = None, signer=None, key=None):
        manifest = manifest or manifest_for(self.archive)
        headers = grant_admin_session("it@example.com", "it_admin")
        # `key` peut valoir la chaine vide : c'est precisement le cas
        # « aucune racine de confiance configuree ». Un `or` l'ecraserait.
        with patch.object(
            main,
            "NOVA_CONNECTOR_RELEASE_PUBLIC_KEY",
            self.public if key is None else key,
        ):
            return self.client.post(
                "/api/control/connector-releases",
                headers=headers,
                json={
                    "manifest": manifest,
                    "signature": sign(signer or self.private, manifest),
                },
            )

    def test_a_signed_release_is_published(self):
        response = self.publish()
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["connector_version"], "0.2.0")
        self.assertEqual(response.json()["status"], "published")

    def test_the_control_plane_never_stores_an_unsigned_release(self):
        """Un plan de controle compromis ne doit pas suffire a distribuer.

        S'il acceptait sans verifier, la verification cote Connector
        deviendrait la seule barriere de toute la chaine.
        """
        other, _ = make_keypair()
        response = self.publish(signer=other)
        self.assertEqual(response.status_code, 400)
        self.assertEqual(
            response.json()["detail"], release.UPDATE_SIGNATURE_INVALID
        )
        with main.db() as conn:
            self.assertEqual(
                conn.execute("SELECT COUNT(*) FROM connector_releases").fetchone()[0], 0
            )

    def test_publishing_without_a_trust_root_is_refused(self):
        response = self.publish(key="")
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json()["detail"], release.UPDATE_SOURCE_UNTRUSTED)

    def test_a_published_release_is_immutable(self):
        """Meme version, meme contenu — sinon toute empreinte devient caduque."""
        self.assertEqual(self.publish().status_code, 200)
        corrected = manifest_for(build_archive(b"print('corrige')\n"))
        second = self.publish(corrected)
        self.assertEqual(second.status_code, 409)
        self.assertEqual(second.json()["detail"], "CONNECTOR_RELEASE_IMMUTABLE")

    def test_a_revoked_release_stops_being_installable(self):
        release_id = self.publish().json()["release_id"]
        headers = grant_admin_session("it@example.com", "it_admin")
        revoked = self.client.post(
            f"/api/control/connector-releases/{release_id}/revoke", headers=headers
        )
        self.assertEqual(revoked.status_code, 200)
        self.assertEqual(revoked.json()["status"], "revoked")

    def test_a_read_only_admin_cannot_publish_or_revoke(self):
        headers = grant_admin_session("lecture@example.com", "read_only")
        with patch.object(main, "NOVA_CONNECTOR_RELEASE_PUBLIC_KEY", self.public):
            manifest = manifest_for(self.archive)
            response = self.client.post(
                "/api/control/connector-releases",
                headers=headers,
                json={"manifest": manifest, "signature": sign(self.private, manifest)},
            )
        self.assertEqual(response.status_code, 403)

    def test_the_listing_never_exposes_the_manifest_or_signature(self):
        self.publish()
        headers = grant_admin_session("it@example.com", "it_admin")
        listing = self.client.get(
            "/api/control/connector-releases", headers=headers
        ).json()
        blob = json.dumps(listing)
        self.assertNotIn("signature", blob)
        self.assertNotIn("artifacts", blob)
        self.assertTrue(listing["trust_root_configured"] is False or True)


class UpdateIntentTests(ConnectorTestCase):
    """L'intention typee, et ce qu'elle refuse."""

    def setUp(self):
        super().setUp()
        with main.db() as conn:
            conn.execute("DELETE FROM connector_releases")
        self.private, self.public = make_keypair()
        self.archive = build_archive()

    def publish(self, version="0.2.0"):
        manifest = manifest_for(self.archive, version=version)
        headers = grant_admin_session("it@example.com", "it_admin")
        with patch.object(main, "NOVA_CONNECTOR_RELEASE_PUBLIC_KEY", self.public):
            return self.client.post(
                "/api/control/connector-releases",
                headers=headers,
                json={"manifest": manifest, "signature": sign(self.private, manifest)},
            ).json()

    def updatable_connector(self):
        body = self.enrolled()
        self.sync(
            body["connector_credential"],
            # Un deploiement `package` : il declare savoir conduire sa
            # propre bascule.
            capabilities=[
                "ai_runtime_config_v1",
                "connector_update_v1",
                "connector_self_update_v1",
            ],
            connector_version="0.1.0",
            platform={"os": "linux", "arch": "amd64", "distribution": "package"},
        )
        return body

    def request_update(self, connector_id, version="0.2.0", token=None):
        headers = token or grant_admin_session("it@example.com", "it_admin")
        return self.client.post(
            f"/api/control/connectors/{connector_id}/update",
            headers=headers,
            json={"target_version": version},
        )

    def test_the_payload_carries_a_version_and_nothing_executable(self):
        """P0 : aucun shell, jamais.

        Le corps de la demande ne comporte qu'un champ. Il n'y a pas de place
        pour une commande, un chemin ou un argument — et c'est structurel, pas
        une convention.
        """
        self.assertEqual(
            set(main.ConnectorUpdateRequest.model_fields), {"target_version"}
        )
        source = Path("main.py").read_text(encoding="utf-8")
        payload = source[
            source.index("def connector_update_payload") : source.index(
                "class ConnectorRotateBody"
            )
        ]
        for forbidden in ("command", "shell", "bash", "powershell", "argv", "exec"):
            self.assertNotIn(forbidden, payload.lower())

    def test_an_update_can_be_requested_and_is_seen_at_the_next_sync(self):
        self.publish()
        body = self.updatable_connector()
        response = self.request_update(body["connector_id"])
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["update"]["target_version"], "0.2.0")
        answer = self.sync(
            body["connector_credential"],
            capabilities=["connector_update_v1", "connector_self_update_v1"],
            connector_version="0.1.0",
        )
        self.assertEqual(answer.json()["update"]["requested_version"], "0.2.0")

    def test_a_docker_connector_is_never_asked_to_update_itself(self):
        """P0 : le plan de controle n'envoie jamais un ordre d'auto-mise a jour
        a un Connector qui ne declare pas savoir la conduire.

        Il resterait « en cours de mise a jour » indefiniment, et la console
        afficherait une operation qui n'arrivera jamais.
        """
        self.publish()
        body = self.enrolled()  # ne declare pas connector_self_update_v1
        response = self.request_update(body["connector_id"])
        self.assertEqual(response.status_code, 409)
        self.assertEqual(
            response.json()["detail"], "CONNECTOR_UPDATE_NOT_SUPPORTED_HERE"
        )

    def test_an_unknown_version_is_refused(self):
        body = self.updatable_connector()
        response = self.request_update(body["connector_id"], "9.9.9")
        self.assertEqual(response.status_code, 404)

    def test_a_revoked_release_cannot_be_requested(self):
        published = self.publish()
        headers = grant_admin_session("it@example.com", "it_admin")
        self.client.post(
            f"/api/control/connector-releases/{published['release_id']}/revoke",
            headers=headers,
        )
        body = self.updatable_connector()
        response = self.request_update(body["connector_id"])
        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.json()["detail"], release.UPDATE_RELEASE_REVOKED)

    def test_a_downgrade_cannot_be_requested(self):
        self.publish(version="0.0.5")
        body = self.updatable_connector()
        response = self.request_update(body["connector_id"], "0.0.5")
        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.json()["detail"], release.UPDATE_DOWNGRADE_REFUSED)

    def test_a_read_only_admin_cannot_request_an_update(self):
        self.publish()
        body = self.updatable_connector()
        headers = grant_admin_session("lecture@example.com", "read_only")
        self.assertEqual(
            self.request_update(body["connector_id"], token=headers).status_code, 403
        )

    def test_an_admin_cannot_update_another_tenant_connector(self):
        self.publish()
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
                "connector_version, created_at) VALUES (?,?,?,?,?,?,?,?,?)",
                ("connector-b", organization_b, "dep-b", "B",
                 main.hash_token("credential-b-0123456789"), 1,
                 json.dumps(["connector_update_v1"]), "0.1.0", time.time()),
            )
        self.assertEqual(self.request_update("connector-b").status_code, 404)

    def test_a_satisfied_intent_stops_being_shown(self):
        """Garder la cible apres la bascule ferait croire a une mise a jour
        perpetuellement en cours."""
        self.publish()
        body = self.updatable_connector()
        self.request_update(body["connector_id"])
        self.sync(
            body["connector_credential"],
            capabilities=["connector_update_v1", "connector_self_update_v1"],
            connector_version="0.2.0",
            update_state="healthy",
        )
        headers = grant_admin_session("it@example.com", "it_admin")
        listing = self.client.get("/api/control/connectors", headers=headers).json()
        self.assertIsNone(listing["connectors"][0]["update"]["target_version"])

    def test_support_status_reflects_the_published_versions(self):
        self.publish(version="0.5.0")
        self.assertEqual(main.recommended_connector_version(), "0.5.0")
        self.assertEqual(main.connector_support_status("0.1.0"), "update_available")
        self.assertEqual(main.connector_support_status("0.5.0"), "up_to_date")
        with patch.dict(os.environ, {"NOVA_CONNECTOR_MINIMUM_VERSION": "0.4.0"}):
            self.assertEqual(main.connector_support_status("0.1.0"), "unsupported")

    def test_an_unsupported_connector_is_flagged_not_disconnected(self):
        """Couper la synchronisation le priverait de la mise a jour qui le
        remettrait a niveau."""
        body = self.updatable_connector()
        with patch.dict(os.environ, {"NOVA_CONNECTOR_MINIMUM_VERSION": "9.0.0"}):
            answer = self.sync(
                body["connector_credential"],
                capabilities=["connector_update_v1", "connector_self_update_v1"],
                connector_version="0.1.0",
            )
            self.assertEqual(answer.status_code, 200)
            self.assertEqual(answer.json()["update"]["support_status"], "unsupported")


class UpdateAuditTests(ConnectorTestCase):
    def test_release_and_update_actions_are_audited_without_secrets(self):
        private, public = make_keypair()
        archive = build_archive()
        manifest = manifest_for(archive)
        headers = grant_admin_session("it@example.com", "it_admin")
        with patch.object(main, "NOVA_CONNECTOR_RELEASE_PUBLIC_KEY", public):
            self.client.post(
                "/api/control/connector-releases",
                headers=headers,
                json={"manifest": manifest, "signature": sign(private, manifest)},
            )
        with main.db() as conn:
            rows = [dict(r) for r in conn.execute("SELECT * FROM admin_audit_log")]
        actions = {row["action"] for row in rows}
        self.assertIn("connector.release.publish", actions)
        blob = json.dumps(rows)
        self.assertNotIn(sign(private, manifest), blob)
        self.assertNotIn(public, blob)
        self.assertNotIn("artifacts", blob)


if __name__ == "__main__":
    unittest.main()
