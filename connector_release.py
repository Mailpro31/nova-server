"""Distribution et mise à jour du Private Connector.

## Ce que ce module refuse de faire

Le plan de contrôle n'envoie jamais de commande. Pas de shell, pas de chemin
d'exécutable, pas d'arguments : seulement une **intention typée** — « passe à
telle version ». Le Connector sait comment se mettre à jour ; Nova n'a pas à le
lui dicter, et un canal qui pourrait le dicter serait un shell distant sous un
autre nom.

## La chaîne de confiance

TLS dit d'où vient un octet, pas qui l'a produit. Un artefact n'est donc jamais
activé sur la seule foi d'un `https://` : le manifeste est **signé**, la
signature est vérifiée contre une clé publique que le déploiement connaît
d'avance, et l'artefact est vérifié par empreinte contre ce manifeste signé.
Trois maillons, dans cet ordre :

    signature du manifeste → empreinte de l'artefact → activation

Rompre le premier suffit à tout arrêter.

## Deux modes de distribution, et pourquoi ils diffèrent

Un conteneur ne peut pas remplacer sa propre image. Lui donner le socket Docker
pour qu'il essaie reviendrait à lui donner la machine — bien plus grave que le
problème que cela résoudrait.

- `package` : le Connector tourne sous un superviseur qui le relance. Il peut
  réellement se mettre à jour : staging, vérification, bascule atomique d'un
  lien, sortie, relance, contrôle de santé, retour arrière si besoin.
- `docker` : l'image **est** l'artefact, et son remplacement appartient à
  l'orchestrateur. Le Connector rapporte sa version et vérifie sa
  compatibilité ; il ne déclare pas savoir se mettre à jour, et la console ne
  lui propose donc rien.

Prétendre le contraire dans le second cas produirait un bouton qui ne marche
pas — la pire des deux options.
"""

from __future__ import annotations

import hashlib
import json
import os
import platform
import shutil
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlparse

# ── Version canonique ────────────────────────────────────────────────────
#
# Une seule source de vérité. `connector.py`, le manifeste et Nova Admin la
# lisent ici ; la recopier ailleurs la ferait diverger au premier oubli.

CONNECTOR_VERSION = "0.1.0"

# Le protocole ne suit pas la version produit. Une correction de bogue ne doit
# pas obliger tout un parc à renégocier ce qu'il sait faire.
CONNECTOR_PROTOCOL_VERSION = 1

MANIFEST_SCHEMA_VERSION = 1

DISTRIBUTION_PACKAGE = "package"
DISTRIBUTION_DOCKER = "docker"

# Deux mecanismes, deux contrats. Les confondre produirait dans la console un
# bouton « Mettre a jour » qui echouerait a chaque fois sur un conteneur.
CAPABILITY_UPDATE = "connector_update_v1"
CAPABILITY_SELF_UPDATE = "connector_self_update_v1"
CAPABILITY_ORCHESTRATOR_UPDATE = "connector_orchestrator_update_v1"

# Codes de sortie lus par le superviseur. Ce sont les seuls ordres qu'un
# Connector lui adresse : il n'y a pas de canal par lequel lui demander autre
# chose que « relance-moi ».
EXIT_STOP = 0
EXIT_RESTART_FOR_UPDATE = 10
EXIT_RESTART_AFTER_ROLLBACK = 11

UPDATE_BLOCKED_BY_ROTATION = "CONNECTOR_UPDATE_BLOCKED_BY_ROTATION"
UPDATE_EXTERNAL_REQUIRED = "external_update_required"

# Au-dela, le nouveau processus est considere comme incapable de devenir sain.
# Attendre indefiniment laisserait un deploiement casse sans retour arriere.
HEALTH_TIMEOUT_SECONDS = float(os.getenv("NOVA_CONNECTOR_HEALTH_TIMEOUT", "90"))
# Une version qui a echoue ne se retente pas en boucle : le plan de controle
# reproposerait la meme cible a chaque battement.
UPDATE_RETRY_COOLDOWN_SECONDS = float(
    os.getenv("NOVA_CONNECTOR_UPDATE_COOLDOWN", "3600")
)

RELEASE_DRAFT = "draft"
RELEASE_PUBLISHED = "published"
RELEASE_REVOKED = "revoked"

# États de mise à jour. Ils sont nombreux parce que les confondre ferait
# afficher un succès là où il n'y a qu'un téléchargement terminé.
UPDATE_UP_TO_DATE = "up_to_date"
UPDATE_AVAILABLE = "update_available"
UPDATE_DOWNLOADING = "downloading"
UPDATE_STAGED = "staged"
UPDATE_VERIFYING = "verifying"
UPDATE_APPLYING = "applying"
UPDATE_RESTARTING = "restarting"
UPDATE_HEALTHY = "healthy"
UPDATE_ROLLBACK_IN_PROGRESS = "rollback_in_progress"
UPDATE_ROLLED_BACK = "rolled_back"
UPDATE_FAILED = "failed"
UPDATE_UNSUPPORTED = "unsupported"

UPDATE_STATES = frozenset(
    {
        UPDATE_UP_TO_DATE,
        UPDATE_AVAILABLE,
        UPDATE_DOWNLOADING,
        UPDATE_STAGED,
        UPDATE_VERIFYING,
        UPDATE_APPLYING,
        UPDATE_RESTARTING,
        UPDATE_HEALTHY,
        UPDATE_ROLLBACK_IN_PROGRESS,
        UPDATE_ROLLED_BACK,
        UPDATE_FAILED,
        UPDATE_UNSUPPORTED,
    }
)

UPDATE_POLICY_MANUAL = "manual"
UPDATE_POLICY_NOTIFY = "notify"
UPDATE_POLICY_AUTOMATIC = "automatic"
UPDATE_POLICIES = frozenset(
    {UPDATE_POLICY_MANUAL, UPDATE_POLICY_NOTIFY, UPDATE_POLICY_AUTOMATIC}
)

# Codes sûrs. Ni chemin de fichier, ni URL signée, ni détail d'hôte.
UPDATE_SIGNATURE_INVALID = "CONNECTOR_UPDATE_SIGNATURE_INVALID"
UPDATE_DIGEST_MISMATCH = "CONNECTOR_UPDATE_DIGEST_MISMATCH"
UPDATE_MANIFEST_INVALID = "CONNECTOR_UPDATE_MANIFEST_INVALID"
UPDATE_PLATFORM_UNSUPPORTED = "CONNECTOR_UPDATE_PLATFORM_UNSUPPORTED"
UPDATE_PROTOCOL_UNSUPPORTED = "CONNECTOR_UPDATE_PROTOCOL_UNSUPPORTED"
UPDATE_RELEASE_REVOKED = "CONNECTOR_UPDATE_RELEASE_REVOKED"
UPDATE_DOWNGRADE_REFUSED = "CONNECTOR_UPDATE_DOWNGRADE_REFUSED"
UPDATE_INSUFFICIENT_DISK = "CONNECTOR_UPDATE_INSUFFICIENT_DISK"
UPDATE_SOURCE_UNTRUSTED = "CONNECTOR_UPDATE_SOURCE_UNTRUSTED"
UPDATE_DOWNLOAD_FAILED = "CONNECTOR_UPDATE_DOWNLOAD_FAILED"
UPDATE_TOO_LARGE = "CONNECTOR_UPDATE_TOO_LARGE"
UPDATE_HEALTH_FAILED = "CONNECTOR_UPDATE_HEALTH_FAILED"
UPDATE_NOT_SUPPORTED_HERE = "CONNECTOR_UPDATE_NOT_SUPPORTED_HERE"

MAX_ARTIFACT_BYTES = 128 * 1024 * 1024
MAX_MANIFEST_BYTES = 256 * 1024
DOWNLOAD_TIMEOUT_SECONDS = 300.0
# De la place pour l'artefact, sa décompression, et la version conservée pour
# le retour arrière. Manquer d'espace au milieu d'une bascule est la façon la
# plus banale de casser un déploiement.
DISK_HEADROOM_FACTOR = 3


class UpdateError(RuntimeError):
    """Erreur sûre à exposer : un code, jamais un chemin ni une URL."""

    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


# ── Version ──────────────────────────────────────────────────────────────


def parse_version(value: str) -> tuple[int, int, int]:
    """SemVer réduit à ce qui sert ici : comparer trois entiers.

    Volontairement strict. Accepter des formes approximatives ferait comparer
    des versions qui ne se comparent pas, et un ordre faux sur des versions est
    exactement ce qui produit une régression silencieuse.
    """
    parts = (value or "").strip().split(".")
    if len(parts) != 3:
        raise UpdateError(UPDATE_MANIFEST_INVALID)
    try:
        major, minor, patch = (int(part) for part in parts)
    except ValueError as error:
        raise UpdateError(UPDATE_MANIFEST_INVALID) from error
    if min(major, minor, patch) < 0:
        raise UpdateError(UPDATE_MANIFEST_INVALID)
    return major, minor, patch


def is_newer(candidate: str, current: str) -> bool:
    return parse_version(candidate) > parse_version(current)


def current_platform() -> dict:
    """Ce sur quoi ce Connector tourne réellement.

    Le mode de distribution n'est pas deviné : il est déclaré. Deviner
    choisirait un jour le mauvais et tenterait une bascule impossible.
    """
    machine = platform.machine().lower()
    architecture = {"x86_64": "amd64", "amd64": "amd64", "aarch64": "arm64",
                    "arm64": "arm64"}.get(machine, machine)
    return {
        "os": platform.system().lower(),
        "arch": architecture,
        "distribution": os.getenv("NOVA_CONNECTOR_DISTRIBUTION", DISTRIBUTION_DOCKER),
    }


def update_capabilities(distribution: str | None = None) -> list[str]:
    """Le contrat de mise a jour de ce deploiement.

    `package` sait conduire sa propre bascule. `docker` ne le peut pas — un
    conteneur ne remplace pas sa propre image — et declare donc un contrat
    different, pas une absence : le plan de controle sait qu'une mise a jour
    est possible, mais qu'elle appartient a l'orchestrateur.
    """
    mode = distribution or current_platform()["distribution"]
    if mode == DISTRIBUTION_PACKAGE:
        return [CAPABILITY_UPDATE, CAPABILITY_SELF_UPDATE]
    return [CAPABILITY_UPDATE, CAPABILITY_ORCHESTRATOR_UPDATE]


def self_update_supported(distribution: str | None = None) -> bool:
    """Ce déploiement peut-il réellement se mettre à jour lui-même ?

    Un conteneur ne remplace pas sa propre image, et lui donner le socket
    Docker pour qu'il essaie reviendrait à lui donner la machine. En mode
    `docker`, la réponse est non — et la console ne proposera donc rien plutôt
    que d'offrir un bouton qui ne marche pas.
    """
    mode = distribution or current_platform()["distribution"]
    return mode == DISTRIBUTION_PACKAGE


# ── Manifeste ────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class Artifact:
    platform_os: str
    arch: str
    distribution: str
    url: str
    digest: str
    size: int


@dataclass(frozen=True)
class ReleaseManifest:
    schema_version: int
    connector_version: str
    protocol_version: int
    status: str
    released_at: float
    minimum_server_version: str
    artifacts: tuple[Artifact, ...]


def parse_manifest(raw: bytes) -> ReleaseManifest:
    if len(raw) > MAX_MANIFEST_BYTES:
        raise UpdateError(UPDATE_TOO_LARGE)
    try:
        body = json.loads(raw.decode("utf-8"))
    except (ValueError, UnicodeDecodeError) as error:
        raise UpdateError(UPDATE_MANIFEST_INVALID) from error
    if not isinstance(body, dict) or body.get("schema_version") != MANIFEST_SCHEMA_VERSION:
        raise UpdateError(UPDATE_MANIFEST_INVALID)
    status = body.get("status")
    if status not in {RELEASE_DRAFT, RELEASE_PUBLISHED, RELEASE_REVOKED}:
        raise UpdateError(UPDATE_MANIFEST_INVALID)
    parse_version(str(body.get("connector_version", "")))

    artifacts = []
    for entry in body.get("artifacts") or []:
        if not isinstance(entry, dict):
            raise UpdateError(UPDATE_MANIFEST_INVALID)
        digest = str(entry.get("digest", ""))
        if not digest.startswith("sha256:") or len(digest) != 71:
            raise UpdateError(UPDATE_MANIFEST_INVALID)
        size = entry.get("size")
        if not isinstance(size, int) or not 0 < size <= MAX_ARTIFACT_BYTES:
            raise UpdateError(UPDATE_MANIFEST_INVALID)
        artifacts.append(
            Artifact(
                platform_os=str(entry.get("os", "")),
                arch=str(entry.get("arch", "")),
                distribution=str(entry.get("distribution", "")),
                url=str(entry.get("url", "")),
                digest=digest,
                size=size,
            )
        )
    if not artifacts:
        raise UpdateError(UPDATE_MANIFEST_INVALID)
    return ReleaseManifest(
        schema_version=MANIFEST_SCHEMA_VERSION,
        connector_version=str(body["connector_version"]),
        protocol_version=int(body.get("protocol_version", 0)),
        status=status,
        released_at=float(body.get("released_at", 0)),
        minimum_server_version=str(body.get("minimum_server_version", "0.0.0")),
        artifacts=tuple(artifacts),
    )


def verify_manifest(raw: bytes, signature: bytes, public_key_hex: str) -> ReleaseManifest:
    """Vérifie la signature **avant** de faire confiance au moindre champ.

    L'ordre compte : analyser puis vérifier laisserait un manifeste non signé
    influencer une décision — ne serait-ce qu'en faisant lever une erreur
    différente selon son contenu.
    """
    from cryptography.exceptions import InvalidSignature
    from cryptography.hazmat.primitives.asymmetric import ed25519

    if not public_key_hex:
        # Pas de racine de confiance configurée : on ne bascule pas « en
        # attendant ». C'est exactement la situation où un repli permissif
        # ouvrirait la chaîne d'approvisionnement.
        raise UpdateError(UPDATE_SOURCE_UNTRUSTED)
    try:
        key = ed25519.Ed25519PublicKey.from_public_bytes(bytes.fromhex(public_key_hex))
        key.verify(signature, raw)
    except (InvalidSignature, ValueError) as error:
        raise UpdateError(UPDATE_SIGNATURE_INVALID) from error
    return parse_manifest(raw)


def select_artifact(manifest: ReleaseManifest, target: dict) -> Artifact:
    """L'artefact de cette plateforme, ou rien.

    Un Connector Linux ne doit pas pouvoir installer un artefact Windows :
    l'erreur serait découverte au redémarrage, c'est-à-dire trop tard.
    """
    for artifact in manifest.artifacts:
        if (
            artifact.platform_os == target["os"]
            and artifact.arch == target["arch"]
            and artifact.distribution == target["distribution"]
        ):
            return artifact
    raise UpdateError(UPDATE_PLATFORM_UNSUPPORTED)


def check_installable(
    manifest: ReleaseManifest, current_version: str, target: dict
) -> Artifact:
    """Toutes les raisons de refuser, avant d'avoir téléchargé quoi que ce soit."""
    if manifest.status == RELEASE_REVOKED:
        raise UpdateError(UPDATE_RELEASE_REVOKED)
    if manifest.status != RELEASE_PUBLISHED:
        raise UpdateError(UPDATE_MANIFEST_INVALID)
    if manifest.protocol_version != CONNECTOR_PROTOCOL_VERSION:
        raise UpdateError(UPDATE_PROTOCOL_UNSUPPORTED)
    if not is_newer(manifest.connector_version, current_version):
        # Le retour arrière passe par le chemin de rollback, qui sait vers quoi
        # revenir. Le faire passer par « installer une version plus ancienne »
        # permettrait de rejouer une release retirée.
        raise UpdateError(UPDATE_DOWNGRADE_REFUSED)
    return select_artifact(manifest, target)


# ── Source de distribution ───────────────────────────────────────────────


def trusted_release_hosts() -> set[str]:
    raw = os.getenv("NOVA_CONNECTOR_RELEASE_HOSTS", "github.com,objects.githubusercontent.com")
    return {host.strip().lower() for host in raw.split(",") if host.strip()}


def validate_release_url(url: str) -> str:
    """La source des artefacts appartient au déploiement, pas au message reçu.

    Si le plan de contrôle pouvait désigner n'importe quelle adresse, un
    administrateur — ou quiconque prendrait sa place — ferait télécharger et
    exécuter du code arbitraire par tous les Connectors d'un tenant. L'adresse
    est donc validée contre une liste que le déploiement configure.
    """
    parsed = urlparse((url or "").strip())
    # HTTP n'est tolere qu'en developpement, et seulement si le deploiement l'a
    # explicitement demande — meme regle que pour l'adresse du plan de
    # controle. Retomber en clair sans le dire exposerait la chaine
    # d'approvisionnement sur le reseau de l'organisation.
    insecure_allowed = (
        os.getenv("NOVA_CONNECTOR_RELEASE_ALLOW_INSECURE", "false").lower() == "true"
    )
    allowed_schemes = {"https", "http"} if insecure_allowed else {"https"}
    if parsed.scheme not in allowed_schemes or not parsed.hostname:
        raise UpdateError(UPDATE_SOURCE_UNTRUSTED)
    host = parsed.hostname.lower()
    allowed = trusted_release_hosts()
    if host not in allowed and not any(host.endswith("." + item) for item in allowed):
        raise UpdateError(UPDATE_SOURCE_UNTRUSTED)
    return url


# ── Téléchargement et vérification ───────────────────────────────────────


def digest_of(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return "sha256:" + digest.hexdigest()


def ensure_disk_space(directory: Path, needed: int) -> None:
    try:
        free = shutil.disk_usage(directory).free
    except OSError:
        return
    if free < needed * DISK_HEADROOM_FACTOR:
        raise UpdateError(UPDATE_INSUFFICIENT_DISK)


def download_artifact(artifact: Artifact, staging: Path, client=None) -> Path:
    """Télécharge dans le staging, borné, sans jamais activer un fichier partiel.

    Le fichier n'apparaît sous son nom définitif qu'une fois complet : une
    coupure réseau laisse un fichier temporaire, jamais un artefact à moitié
    écrit qu'une reprise prendrait pour valide.
    """
    import httpx

    validate_release_url(artifact.url)
    staging.mkdir(parents=True, exist_ok=True)
    ensure_disk_space(staging, artifact.size)

    final = staging / f"connector-{artifact.digest.split(':')[1][:16]}.tar.gz"
    temporary = final.with_suffix(".part")
    session = client or httpx.Client(
        timeout=DOWNLOAD_TIMEOUT_SECONDS,
        # Une redirection vers un hôte non approuvé contournerait la liste.
        follow_redirects=False,
    )
    written = 0
    try:
        with session:
            with session.stream("GET", artifact.url) as response:
                if response.status_code != 200:
                    raise UpdateError(UPDATE_DOWNLOAD_FAILED)
                with temporary.open("wb") as handle:
                    for chunk in response.iter_bytes():
                        written += len(chunk)
                        if written > artifact.size:
                            raise UpdateError(UPDATE_TOO_LARGE)
                        handle.write(chunk)
    except UpdateError:
        temporary.unlink(missing_ok=True)
        raise
    except Exception as error:  # réseau, TLS, interruption
        temporary.unlink(missing_ok=True)
        raise UpdateError(UPDATE_DOWNLOAD_FAILED) from error

    if written != artifact.size:
        temporary.unlink(missing_ok=True)
        raise UpdateError(UPDATE_DIGEST_MISMATCH)
    if digest_of(temporary) != artifact.digest:
        # L'artefact douteux ne reste pas sur le disque : le garder inviterait
        # à le réutiliser un jour « pour gagner du temps ».
        temporary.unlink(missing_ok=True)
        raise UpdateError(UPDATE_DIGEST_MISMATCH)
    os.replace(temporary, final)
    return final


# ── Installation atomique ────────────────────────────────────────────────


def install_root() -> Path:
    return Path(os.getenv("NOVA_CONNECTOR_INSTALL_ROOT", "/opt/nova/connector"))


def activate(archive: Path, version: str, root: Path | None = None) -> Path:
    """Déballe puis bascule, sans jamais laisser un mélange de versions.

    Chaque version vit dans son propre répertoire ; `current` est un lien qui
    désigne l'une d'elles. La bascule est un remplacement de lien : à tout
    instant, `current` pointe vers une version complète — l'ancienne ou la
    nouvelle, jamais un état intermédiaire. C'est aussi ce qui rend le retour
    arrière instantané.
    """
    import tarfile

    base = root or install_root()
    versions = base / "versions"
    target = versions / version
    versions.mkdir(parents=True, exist_ok=True)
    if target.exists():
        shutil.rmtree(target)
    extracted = Path(tempfile.mkdtemp(dir=versions, prefix=".staging-"))
    try:
        with tarfile.open(archive, "r:gz") as bundle:
            for member in bundle.getmembers():
                # Un chemin absolu ou remontant écrirait hors du répertoire de
                # destination — la façon classique de transformer une archive
                # en écriture arbitraire.
                name = Path(member.name)
                if name.is_absolute() or ".." in name.parts:
                    raise UpdateError(UPDATE_MANIFEST_INVALID)
                if not (member.isfile() or member.isdir()):
                    raise UpdateError(UPDATE_MANIFEST_INVALID)
            # `filter="data"` refuse en plus les liens, les fichiers spéciaux
            # et les chemins absolus — la même famille d'évasions que le
            # contrôle ci-dessus, appliquée par la bibliothèque. Les deux
            # coexistent : le contrôle explicite vaut sur les Python qui
            # n'ont pas encore ce filtre.
            try:
                bundle.extractall(extracted, filter="data")
            except TypeError:
                bundle.extractall(extracted)
        os.replace(extracted, target)
    except UpdateError:
        shutil.rmtree(extracted, ignore_errors=True)
        raise
    except Exception as error:
        shutil.rmtree(extracted, ignore_errors=True)
        raise UpdateError(UPDATE_MANIFEST_INVALID) from error

    point_current_at(base, version)
    return target


def point_current_at(base: Path, version: str) -> None:
    """Fait pointer `current` vers une version, atomiquement si possible."""
    current = base / "current"
    target = base / "versions" / version
    if not target.is_dir():
        raise UpdateError(UPDATE_MANIFEST_INVALID)
    try:
        temporary = base / f".current.{os.getpid()}"
        if temporary.exists() or temporary.is_symlink():
            temporary.unlink()
        temporary.symlink_to(target, target_is_directory=True)
        os.replace(temporary, current)
    except (OSError, NotImplementedError):
        # Windows sans droit de lien symbolique : un fichier de pointage tient
        # le même rôle. Il est écrit atomiquement, et la documentation dit que
        # la parité n'est pas revendiquée.
        pointer = base / "current.txt"
        temporary = base / f".current.{os.getpid()}.txt"
        temporary.write_text(version, encoding="utf-8")
        os.replace(temporary, pointer)


def installed_version(base: Path | None = None) -> str | None:
    base = base or install_root()
    pointer = base / "current.txt"
    if pointer.exists():
        return pointer.read_text(encoding="utf-8").strip() or None
    current = base / "current"
    if current.is_symlink() or current.exists():
        try:
            return Path(os.readlink(current)).name
        except OSError:
            return current.resolve().name
    return None


def rollback(base: Path, version: str) -> None:
    """Revient à une version déjà installée. Rien à retélécharger.

    C'est la raison pour laquelle l'ancienne arborescence est conservée : un
    retour arrière qui dépendrait du réseau échouerait précisément quand le
    réseau est le problème.
    """
    point_current_at(base, version)


def prune_versions(base: Path, keep: tuple[str, ...]) -> list[str]:
    """Ne garde que la version courante et la précédente.

    Conserver vingt versions ne servirait qu'à remplir le disque du client :
    on ne revient jamais qu'à la dernière connue saine.
    """
    versions = base / "versions"
    removed = []
    if not versions.is_dir():
        return removed
    for entry in versions.iterdir():
        if entry.is_dir() and entry.name not in keep:
            shutil.rmtree(entry, ignore_errors=True)
            removed.append(entry.name)
    return removed


def cleanup_staging(staging: Path) -> None:
    if staging.is_dir():
        shutil.rmtree(staging, ignore_errors=True)


# ── Journal d'état local ─────────────────────────────────────────────────


def state_root(base: Path | None = None) -> Path:
    """La ou vit ce qui doit survivre a un remplacement de version.

    Identite, credential, rotation en cours, revisions appliquees : rien de
    tout cela n'a sa place dans un repertoire versionne, sinon une mise a jour
    effacerait l'identite du Connector en meme temps que son code.

        state/      identite, credential, transaction
        versions/   1.0.0/  1.1.0/
        current ->  versions/1.1.0
    """
    root = (base or install_root()) / "state"
    root.mkdir(parents=True, exist_ok=True)
    return root


def update_state_path(base: Path | None = None) -> Path:
    return state_root(base) / "update-state.json"


def transaction_path(base: Path | None = None) -> Path:
    return state_root(base) / "update-transaction.json"


def read_transaction(base: Path | None = None) -> dict:
    path = transaction_path(base)
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (ValueError, OSError):
        return {}


def write_transaction(transaction: dict, base: Path | None = None) -> None:
    """Ecriture atomique : une reprise lit une transaction coherente.

    Aucun credential n'entre ici. Ce fichier decrit une bascule, pas une
    identite.
    """
    path = transaction_path(base)
    temporary = path.parent / f".{path.name}.{os.getpid()}.tmp"
    temporary.write_text(json.dumps(transaction, indent=2), encoding="utf-8")
    os.replace(temporary, path)


def clear_transaction(base: Path | None = None) -> None:
    transaction_path(base).unlink(missing_ok=True)


def new_transaction(from_version: str, target_version: str, release_id: str) -> dict:
    return {
        "update_id": hashlib.sha256(
            f"{from_version}->{target_version}:{release_id}".encode("utf-8")
        ).hexdigest()[:16],
        "from_version": from_version,
        "target_version": target_version,
        "release_id": release_id,
        "state": UPDATE_AVAILABLE,
        "previous_version": from_version,
        "attempt_count": 0,
        "started_at": time.time(),
    }


def blocked_by_cooldown(transaction: dict, target_version: str) -> bool:
    """Cette cible a-t-elle deja echoue trop recemment ?

    Sans cela : mauvaise version -> retour arriere -> le plan de controle
    repropose la meme cible -> nouvelle tentative -> boucle. Le plan de
    controle doit voir l'echec et une nouvelle tentative doit etre un acte
    explicite, pas un reflexe.
    """
    if transaction.get("target_version") != target_version:
        return False
    if transaction.get("state") not in {UPDATE_FAILED, UPDATE_ROLLED_BACK}:
        return False
    failed_at = transaction.get("failed_at") or 0
    return time.time() - failed_at < UPDATE_RETRY_COOLDOWN_SECONDS


def read_update_state(base: Path | None = None) -> dict:
    path = update_state_path(base)
    if not path.exists():
        return {"state": UPDATE_UP_TO_DATE, "version": CONNECTOR_VERSION}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (ValueError, OSError):
        return {"state": UPDATE_UP_TO_DATE, "version": CONNECTOR_VERSION}


def write_update_state(state: dict, base: Path | None = None) -> None:
    """Écriture atomique : une reprise lit un état cohérent, jamais un moignon."""
    path = update_state_path(base)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.parent / f".{path.name}.{os.getpid()}.tmp"
    temporary.write_text(json.dumps(state, indent=2), encoding="utf-8")
    os.replace(temporary, path)


def record_attempt(
    state: dict,
    stage: str,
    target_version: str | None = None,
    error_code: str | None = None,
) -> dict:
    return {
        **state,
        "state": stage,
        "target_version": target_version,
        "last_error_code": error_code,
        "updated_at": time.time(),
    }
