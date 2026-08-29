"""Déploiement d'entreprise — ce que le Control Plane sait d'un parc de postes.

Un profil de déploiement décrit **comment une organisation installe Nova**, pas
qui elle est ni ce que ses membres ont le droit de faire. Il ne porte donc
aucun secret, et le connaître n'accorde rien : ni session, ni administration,
ni enrôlement de Connector.

## Pourquoi un objet plutôt qu'un champ

Une organisation peut avoir plusieurs parcs — un pilote et le reste, ou un
service en `preview` pendant que le reste est en `stable`. Un champ unique sur
l'organisation aurait forcé à choisir, puis à le regretter.

## Ce que ce module ne fait pas

Il ne distribue pas de binaire, et une console d'organisation ne peut pas en
téléverser un. Les métadonnées de release viennent de la configuration du
service, jamais d'un administrateur de tenant : sinon publier un installeur
deviendrait une capacité de tenant, ce qui est exactement la mauvaise frontière.

Il ne prétend pas non plus connaître l'état des postes. Nova ne reçoit aucun
rapport de poste dans cette phase, donc aucun parc n'est jamais annoncé
« healthy » — voir `deployment_status`.
"""

from __future__ import annotations

import json
import os
import re
import sqlite3
import time
import uuid
from dataclasses import dataclass
from typing import Any, Callable

from fastapi import Depends
from pydantic import BaseModel, ConfigDict, Field


# Le poste applique le même contrat, dans `src-tauri/src/deployment.rs`. Les
# deux listes doivent rester identiques : une valeur acceptée ici et refusée
# là-bas produirait un poste géré qui refuse de démarrer.
PLATFORMS = ("windows",)
EDITIONS = ("organization",)
CHANNELS = ("stable", "preview")
RINGS = ("pilot", "broad")
UPDATE_POLICIES = ("manual", "notify", "automatic")
PROFILE_STATUSES = ("active", "retired")

MANAGED_SCHEMA_VERSION = 1
MAX_PROFILES_PER_ORGANIZATION = 50

# Origine épinglée dans le package Desktop. Elle est répétée ici pour que la
# configuration produite par la console soit celle que le poste acceptera : une
# console qui écrirait une autre adresse fabriquerait des postes en erreur.
CONTROL_PLANE_ORIGIN = os.getenv(
    "NOVA_CONTROL_PLANE_ORIGIN", "https://api.novaspeak.app"
)

# Nom stable de l'installeur, tel que le guide opérateur et Intune l'attendent.
INSTALLER_FILENAME = "NovaOrganizationSetup.exe"

IDENTIFIER_RE = re.compile(r"^[A-Za-z0-9_-]{1,128}$")


class DeploymentError(Exception):
    """Erreur portant un code stable, sans détail d'hôte ni de chemin."""

    def __init__(self, status: int, code: str) -> None:
        super().__init__(code)
        self.status = status
        self.code = code


def install_schema(conn: sqlite3.Connection) -> None:
    """Schéma additif. Aucune table existante n'est reconstruite."""
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS deployment_profiles(
            profile_id TEXT PRIMARY KEY,
            organization_id TEXT NOT NULL,
            name TEXT NOT NULL,
            platform TEXT NOT NULL,
            edition TEXT NOT NULL,
            channel TEXT NOT NULL,
            deployment_ring TEXT,
            update_policy TEXT NOT NULL,
            autostart INTEGER,
            config_revision INTEGER NOT NULL DEFAULT 1,
            status TEXT NOT NULL DEFAULT 'active',
            created_at REAL NOT NULL,
            updated_at REAL NOT NULL,
            created_by_user_id TEXT,
            UNIQUE (organization_id, name),
            FOREIGN KEY (organization_id) REFERENCES organizations(id)
        );
        CREATE INDEX IF NOT EXISTS idx_deployment_profiles_org
            ON deployment_profiles(organization_id, status);
        """
    )


class ProfileCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=120)
    channel: str = Field(default="stable")
    deployment_ring: str | None = None
    update_policy: str = Field(default="notify")
    autostart: bool | None = None


class ProfileUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    channel: str | None = None
    deployment_ring: str | None = None
    update_policy: str | None = None
    autostart: bool | None = None
    status: str | None = None


@dataclass(frozen=True)
class Backend:
    database: Callable[[], Any]
    require_organization_access: Callable[[Any, str], None]
    record_admin_action: Callable[..., Any]
    admin_read_guard: Callable[..., Any]
    admin_manage_guard: Callable[..., Any]


# --------------------------------------------------------------------- release


def _release_manifest_path() -> str:
    return os.getenv("NOVA_DESKTOP_RELEASE_MANIFEST", "").strip()


def _release_public_key() -> str:
    return os.getenv("NOVA_DESKTOP_RELEASE_PUBLIC_KEY", "").strip()


def parse_release_manifest(raw: bytes) -> dict[str, Any]:
    """Valide un manifeste de release Desktop.

    Strict et sans valeur par défaut inventée : un manifeste incomplet est un
    manifeste refusé. Une console qui afficherait « version inconnue » servirait
    au moins la vérité ; une console qui afficherait une version devinée ferait
    installer n'importe quoi.
    """
    try:
        document = json.loads(raw)
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise DeploymentError(500, "DESKTOP_RELEASE_MANIFEST_INVALID") from error
    if not isinstance(document, dict):
        raise DeploymentError(500, "DESKTOP_RELEASE_MANIFEST_INVALID")
    if document.get("schema_version") != MANAGED_SCHEMA_VERSION:
        raise DeploymentError(500, "DESKTOP_RELEASE_MANIFEST_UNSUPPORTED")

    artifacts = document.get("artifacts")
    if not isinstance(artifacts, list) or not artifacts:
        raise DeploymentError(500, "DESKTOP_RELEASE_MANIFEST_INVALID")
    parsed: list[dict[str, Any]] = []
    for artifact in artifacts:
        if not isinstance(artifact, dict):
            raise DeploymentError(500, "DESKTOP_RELEASE_MANIFEST_INVALID")
        required = (
            "version", "edition", "platform", "arch", "artifact",
            "sha256", "size", "released_at", "channel",
            "minimum_supported_version",
        )
        if any(key not in artifact for key in required):
            raise DeploymentError(500, "DESKTOP_RELEASE_MANIFEST_INVALID")
        if artifact["channel"] not in CHANNELS:
            raise DeploymentError(500, "DESKTOP_RELEASE_MANIFEST_INVALID")
        if artifact["platform"] not in PLATFORMS or artifact["edition"] not in EDITIONS:
            raise DeploymentError(500, "DESKTOP_RELEASE_MANIFEST_INVALID")
        # Un artefact sans empreinte exploitable n'est pas un artefact vérifiable.
        if not re.fullmatch(r"[0-9a-f]{64}", str(artifact["sha256"])):
            raise DeploymentError(500, "DESKTOP_RELEASE_MANIFEST_INVALID")
        if not isinstance(artifact["size"], int) or artifact["size"] <= 0:
            raise DeploymentError(500, "DESKTOP_RELEASE_MANIFEST_INVALID")
        parsed.append(
            {key: artifact[key] for key in required}
        )
    return {"schema_version": MANAGED_SCHEMA_VERSION, "artifacts": parsed}


def load_release_manifest() -> dict[str, Any]:
    """Le manifeste servi à la console, ou une absence honnête.

    Trois états, et aucun entre-deux : absent, non fiable, chargé. Si une racine
    de confiance est configurée mais que la signature ne vérifie pas, on ne
    sert rien — un repli « en attendant » est exactement ce qui ouvre une chaîne
    d'approvisionnement.
    """
    path = _release_manifest_path()
    if not path:
        return {"configured": False, "signed": False, "artifacts": [], "error": None}
    try:
        with open(path, "rb") as handle:
            raw = handle.read()
    except OSError:
        return {
            "configured": True, "signed": False, "artifacts": [],
            "error": "DESKTOP_RELEASE_MANIFEST_UNREADABLE",
        }

    public_key = _release_public_key()
    signed = False
    if public_key:
        from cryptography.exceptions import InvalidSignature
        from cryptography.hazmat.primitives.asymmetric import ed25519

        # La signature est vérifiée **avant** d'analyser le moindre champ.
        # L'ordre inverse laisserait un manifeste non signé influencer une
        # décision, ne serait-ce qu'en faisant lever une erreur différente
        # selon son contenu.
        try:
            with open(path + ".sig", "rb") as handle:
                signature = bytes.fromhex(handle.read().decode("utf-8").strip())
            key = ed25519.Ed25519PublicKey.from_public_bytes(bytes.fromhex(public_key))
            key.verify(signature, raw)
        except (OSError, ValueError, UnicodeDecodeError, InvalidSignature):
            return {
                "configured": True, "signed": False, "artifacts": [],
                "error": "DESKTOP_RELEASE_SIGNATURE_INVALID",
            }
        signed = True

    try:
        manifest = parse_release_manifest(raw)
    except DeploymentError as error:
        return {
            "configured": True, "signed": signed, "artifacts": [],
            "error": error.code,
        }
    return {
        "configured": True,
        "signed": signed,
        "artifacts": manifest["artifacts"],
        "error": None,
    }


def release_for_channel(manifest: dict[str, Any], channel: str) -> dict[str, Any] | None:
    """La release la plus récente d'un canal, ou rien."""
    candidates = [a for a in manifest["artifacts"] if a["channel"] == channel]
    if not candidates:
        return None
    return max(candidates, key=lambda artifact: artifact["released_at"])


# --------------------------------------------------------------------- profils


def _validate(payload: dict[str, Any]) -> None:
    if payload["channel"] not in CHANNELS:
        raise DeploymentError(400, "DEPLOYMENT_CHANNEL_INVALID")
    if payload["update_policy"] not in UPDATE_POLICIES:
        raise DeploymentError(400, "DEPLOYMENT_UPDATE_POLICY_INVALID")
    ring = payload.get("deployment_ring")
    if ring is not None and ring not in RINGS:
        raise DeploymentError(400, "DEPLOYMENT_RING_INVALID")
    status = payload.get("status")
    if status is not None and status not in PROFILE_STATUSES:
        raise DeploymentError(400, "DEPLOYMENT_STATUS_INVALID")


def profile_view(row: Any) -> dict[str, Any]:
    return {
        "profile_id": row["profile_id"],
        "name": row["name"],
        "platform": row["platform"],
        "edition": row["edition"],
        "channel": row["channel"],
        "deployment_ring": row["deployment_ring"],
        "update_policy": row["update_policy"],
        "autostart": None if row["autostart"] is None else bool(row["autostart"]),
        "config_revision": row["config_revision"],
        "status": row["status"],
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
    }


def managed_config(row: Any) -> dict[str, Any]:
    """Le document que la DSI dépose dans `%ProgramData%\\Nova`.

    Il ne contient **que** des métadonnées non secrètes. Le schéma du poste
    refuse tout champ inconnu, jeton compris : y ajouter un secret ne le
    rendrait pas seulement dangereux, cela rendrait le poste non gérable.
    """
    document = {
        "schema_version": MANAGED_SCHEMA_VERSION,
        "deployment_id": row["profile_id"],
        "organization_id": row["organization_id"],
        "control_plane_origin": CONTROL_PLANE_ORIGIN,
        "release_channel": row["channel"],
        "managed": True,
        "config_revision": row["config_revision"],
        "update_policy": row["update_policy"],
    }
    if row["deployment_ring"]:
        document["deployment_ring"] = row["deployment_ring"]
    if row["autostart"] is not None:
        document["autostart"] = bool(row["autostart"])
    return document


def install_command(row: Any) -> str:
    """La commande que l'administrateur copie.

    Elle ne contient aucun secret durable : un identifiant de parc et une
    organisation, tous deux opaques et sans pouvoir. Quelqu'un qui la lit par
    dessus une épaule ne peut ni se connecter, ni administrer, ni enrôler.
    """
    return (
        f"{INSTALLER_FILENAME} /S "
        f"/DEPLOYMENT_ID={row['profile_id']} "
        f"/ORGANIZATION_ID={row['organization_id']} "
        f"/CHANNEL={row['channel']}"
    )


def deployment_status(profiles: list[dict[str, Any]], manifest: dict[str, Any]) -> str:
    """L'état du déploiement, restreint à ce que le service sait réellement.

    `healthy` n'est **jamais** renvoyé : il supposerait un rapport de poste, et
    Nova n'en reçoit aucun. Annoncer une bonne santé qu'on n'a pas mesurée est
    la seule chose qu'une console de déploiement ne doit pas faire.
    """
    if manifest["configured"] and manifest["error"]:
        return "configuration_error"
    if not any(profile["status"] == "active" for profile in profiles):
        return "not_configured"
    if not manifest["configured"] or not manifest["artifacts"]:
        return "not_configured"
    return "package_ready"


def install_routes(app: Any, backend: Backend) -> None:
    """Surface d'administration du déploiement."""

    def _profile(conn: sqlite3.Connection, organization_id: str, profile_id: str):
        row = conn.execute(
            "SELECT * FROM deployment_profiles WHERE organization_id=? AND profile_id=?",
            (organization_id, profile_id),
        ).fetchone()
        if row is None:
            # Un identifiant d'une autre organisation et un identifiant
            # inexistant sont indiscernables.
            raise DeploymentError(404, "DEPLOYMENT_PROFILE_NOT_FOUND")
        return row

    @app.get("/api/control/organizations/{organization_id}/deployment")
    def control_deployment_overview(
        organization_id: str,
        principal: Any = Depends(backend.admin_read_guard),
    ):
        backend.require_organization_access(principal, organization_id)
        with backend.database() as conn:
            rows = conn.execute(
                "SELECT * FROM deployment_profiles WHERE organization_id=? "
                "ORDER BY created_at DESC",
                (organization_id,),
            ).fetchall()
        profiles = [profile_view(row) for row in rows]
        manifest = load_release_manifest()
        releases = {
            channel: release_for_channel(manifest, channel) for channel in CHANNELS
        }
        return {
            "status": deployment_status(profiles, manifest),
            "platform": PLATFORMS[0],
            "edition": EDITIONS[0],
            "installer_filename": INSTALLER_FILENAME,
            "control_plane_origin": CONTROL_PLANE_ORIGIN,
            "profiles": profiles,
            "install_commands": {
                row["profile_id"]: install_command(row)
                for row in rows
                if row["status"] == "active"
            },
            "release": {
                "configured": manifest["configured"],
                "signed": manifest["signed"],
                "error": manifest["error"],
                "channels": releases,
            },
            # Aucun poste ne rapporte son état à Nova. Le dire explicitement
            # évite qu'une console vide passe pour un parc en bonne santé.
            "device_reporting": False,
        }

    @app.post("/api/control/organizations/{organization_id}/deployment/profiles")
    def control_create_deployment_profile(
        organization_id: str,
        body: ProfileCreate,
        principal: Any = Depends(backend.admin_manage_guard),
    ):
        backend.require_organization_access(principal, organization_id)
        payload = body.model_dump()
        _validate(payload)
        now = time.time()
        # `dpl-` puis 32 hexadécimaux : opaque, et conforme au contrat
        # d'identifiant que le poste et l'installeur appliquent tous deux.
        profile_id = "dpl-" + uuid.uuid4().hex
        with backend.database() as conn:
            count = conn.execute(
                "SELECT COUNT(*) AS c FROM deployment_profiles WHERE organization_id=?",
                (organization_id,),
            ).fetchone()["c"]
            if count >= MAX_PROFILES_PER_ORGANIZATION:
                raise DeploymentError(409, "DEPLOYMENT_PROFILE_LIMIT_REACHED")
            try:
                conn.execute(
                    "INSERT INTO deployment_profiles(profile_id, organization_id, name, "
                    "platform, edition, channel, deployment_ring, update_policy, autostart, "
                    "config_revision, status, created_at, updated_at, created_by_user_id) "
                    "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    (
                        profile_id, organization_id, payload["name"].strip(),
                        PLATFORMS[0], EDITIONS[0], payload["channel"],
                        payload["deployment_ring"], payload["update_policy"],
                        None if payload["autostart"] is None else int(payload["autostart"]),
                        1, "active", now, now,
                        getattr(principal, "user_id", None),
                    ),
                )
            except sqlite3.IntegrityError as error:
                raise DeploymentError(409, "DEPLOYMENT_PROFILE_NAME_TAKEN") from error
            backend.record_admin_action(
                principal,
                "deployment.profile.create",
                target_type="deployment_profile",
                target_id=profile_id,
                metadata={"channel": payload["channel"]},
                conn=conn,
            )
            row = _profile(conn, organization_id, profile_id)
            view = profile_view(row)
            command = install_command(row)
        return {"profile": view, "install_command": command}

    @app.put(
        "/api/control/organizations/{organization_id}/deployment/profiles/{profile_id}"
    )
    def control_update_deployment_profile(
        organization_id: str,
        profile_id: str,
        body: ProfileUpdate,
        principal: Any = Depends(backend.admin_manage_guard),
    ):
        backend.require_organization_access(principal, organization_id)
        changes = {key: value for key, value in body.model_dump().items() if value is not None}
        if not changes:
            raise DeploymentError(400, "DEPLOYMENT_UPDATE_EMPTY")
        with backend.database() as conn:
            row = _profile(conn, organization_id, profile_id)
            merged = {
                "channel": changes.get("channel", row["channel"]),
                "update_policy": changes.get("update_policy", row["update_policy"]),
                "deployment_ring": changes.get("deployment_ring", row["deployment_ring"]),
                "status": changes.get("status", row["status"]),
            }
            _validate(merged)
            autostart = changes.get("autostart")
            # Toute modification fait avancer la révision : c'est ce qui permet
            # à un poste de savoir qu'il applique une configuration périmée.
            conn.execute(
                "UPDATE deployment_profiles SET channel=?, update_policy=?, "
                "deployment_ring=?, status=?, autostart=?, config_revision=config_revision+1, "
                "updated_at=? WHERE organization_id=? AND profile_id=?",
                (
                    merged["channel"], merged["update_policy"], merged["deployment_ring"],
                    merged["status"],
                    row["autostart"] if autostart is None else int(autostart),
                    time.time(), organization_id, profile_id,
                ),
            )
            backend.record_admin_action(
                principal,
                "deployment.profile.update",
                target_type="deployment_profile",
                target_id=profile_id,
                metadata={"attributes": sorted(changes)},
                conn=conn,
            )
            view = profile_view(_profile(conn, organization_id, profile_id))
        return {"profile": view}

    @app.get(
        "/api/control/organizations/{organization_id}/deployment/profiles/{profile_id}/config"
    )
    def control_deployment_profile_config(
        organization_id: str,
        profile_id: str,
        principal: Any = Depends(backend.admin_read_guard),
    ):
        backend.require_organization_access(principal, organization_id)
        with backend.database() as conn:
            row = _profile(conn, organization_id, profile_id)
            document = managed_config(row)
            command = install_command(row)
        return {
            "filename": "organization.json",
            "path": r"C:\ProgramData\Nova\organization.json",
            "config": document,
            "install_command": command,
        }

    @app.post("/api/deployment/verify")
    def deployment_verify(body: dict):
        """Vérifie qu'un parc appartient bien à l'organisation annoncée.

        C'est la garde du §multi-tenant : un poste ne choisit pas son
        organisation. La réponse dit seulement si le couple est valide — un
        identifiant de parc seul ne permet donc pas d'énumérer les
        organisations, et rien de secret n'en sort.
        """
        if not isinstance(body, dict) or set(body) - {"deployment_id", "organization_id"}:
            raise DeploymentError(400, "DEPLOYMENT_VERIFY_INVALID")
        deployment_id = str(body.get("deployment_id", ""))
        organization_id = str(body.get("organization_id", ""))
        if not IDENTIFIER_RE.fullmatch(deployment_id) or not IDENTIFIER_RE.fullmatch(
            organization_id
        ):
            raise DeploymentError(400, "DEPLOYMENT_VERIFY_INVALID")
        with backend.database() as conn:
            row = conn.execute(
                "SELECT * FROM deployment_profiles WHERE profile_id=? AND "
                "organization_id=? AND status='active'",
                (deployment_id, organization_id),
            ).fetchone()
        if row is None:
            raise DeploymentError(404, "DEPLOYMENT_NOT_RECOGNIZED")
        manifest = load_release_manifest()
        release = release_for_channel(manifest, row["channel"])
        return {
            "valid": True,
            "release_channel": row["channel"],
            "config_revision": row["config_revision"],
            "update_policy": row["update_policy"],
            "recommended_version": release["version"] if release else None,
            "minimum_supported_version": (
                release["minimum_supported_version"] if release else None
            ),
        }
