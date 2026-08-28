"""Canal de contrôle local entre le Private Connector et nova-server.

## Le défaut que ce module supprime

La Phase 33 faisait parler le Connector au plan de données par le **listener
public**, avec un **secret statique partagé** posé dans l'environnement et dans
le fichier compose. Ce secret n'avait ni rotation, ni révocation, ni durée de
vie ; il était le même à chaque redémarrage et donnait accès à la
reconfiguration de tout le runtime.

Deux choses changent ici, et elles sont indépendantes :

1. **La surface interne quitte le listener public.** Elle est servie par une
   application ASGI distincte, sur son propre point d'écoute. Une session
   Desktop ou une session d'administration arrivant sur l'API publique ne peut
   pas l'atteindre, quels que soient ses droits.
2. **Le secret statique disparaît du chemin nominal.** En production, le canal
   est un socket Unix dont les permissions de fichier *sont* l'authentification.
   En développement, un jeton **éphémère régénéré à chaque démarrage** remplace
   le secret figé.

## Ce que ce canal ne protège pas

Rien contre un root local. Si l'administrateur de la machine est compromis, le
Connector et le plan de données le sont aussi — un socket ne change pas cela.
Ce qui est visé : l'exposition réseau, l'accès accidentel par un autre
processus, un secret réutilisable d'un déploiement à l'autre, et le rayon
d'impact d'une fuite.

## Ce que ce canal permet

Quatre opérations nommées. Pas de shell, pas d'URL arbitraire, pas de SQL, pas
d'accès au système de fichiers, pas de proxy interne.
"""

from __future__ import annotations

import os
import secrets
import socket
import sys
from pathlib import Path

from fastapi import Depends, FastAPI, Header
from pydantic import BaseModel, ConfigDict, Field

import main

LOCAL_TRANSPORT_UNIX = "unix"
LOCAL_TRANSPORT_LOOPBACK = "loopback"

# Explicite, jamais deviné. Une détection automatique choisirait un jour le
# transport le plus faible sans que personne ne l'ait décidé.
LOCAL_TRANSPORT = os.getenv("NOVA_CONNECTOR_LOCAL_TRANSPORT", LOCAL_TRANSPORT_UNIX)
LOCAL_SOCKET_PATH = os.getenv("NOVA_CONNECTOR_LOCAL_SOCKET", "/run/nova/control.sock")
LOCAL_LOOPBACK_HOST = "127.0.0.1"
LOCAL_LOOPBACK_PORT = int(os.getenv("NOVA_CONNECTOR_LOCAL_PORT", "8129"))
LOCAL_AUTH_FILE = os.getenv("NOVA_CONNECTOR_LOCAL_AUTH_FILE", "/run/nova/local-auth")

SOCKET_MODE = 0o660
# Le repertoire porte la garantie, pas seulement le socket. uvicorn cree le
# socket puis le passe a 0o666 — world-writable — avant que quiconque puisse le
# resserrer. Un repertoire non traversable par le reste du monde rend cette
# fenetre inatteignable : sans permission de traversee, le mode du socket lui
# meme n'est plus une porte.
SOCKET_DIRECTORY_MODE = 0o770
AUTH_FILE_MODE = 0o600


def unix_sockets_available() -> bool:
    """AF_UNIX existe-t-il réellement ici ?

    Windows ne le fournit pas à cette version de Python, et la boucle
    d'événements asyncio ne le servirait pas. Mieux vaut le constater et le dire
    que produire une abstraction qui ne tourne nulle part.
    """
    return hasattr(socket, "AF_UNIX") and sys.platform != "win32"


def write_restricted(path: Path, content: str, mode: int) -> None:
    """Écrit un fichier sensible, restreint **avant** d'être rempli.

    Créer puis restreindre laisserait une fenêtre, courte mais réelle, pendant
    laquelle le contenu serait lisible par tout le système. L'écriture est
    atomique : un fichier temporaire dans le même répertoire, puis un
    remplacement — un lecteur voit l'ancien contenu ou le nouveau, jamais un
    fichier à moitié écrit.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.parent / f".{path.name}.{os.getpid()}.tmp"
    descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, mode)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        if temporary.exists():
            temporary.unlink(missing_ok=True)
    try:
        os.chmod(path, mode)
    except OSError:
        # Windows ne porte pas les permissions POSIX : `chmod` n'y bascule que
        # le bit lecture seule. La documentation de déploiement le dit plutôt
        # que de laisser croire à une protection qui n'existe pas.
        pass


def prepare_socket_directory(path: str) -> Path:
    """Sécurise le répertoire du socket **avant** que le socket existe.

    C'est l'ordre qui compte. uvicorn crée le socket avec le masque du
    processus, puis le passe explicitement à `0o666`. Entre cette création et
    tout resserrement ultérieur, le socket est ouvert en écriture à tout le
    système — une fenêtre courte, mais réelle, et exploitable si le répertoire
    est traversable.

    Restreindre le répertoire ferme la fenêtre au lieu de courir après elle :
    un processus qui ne peut pas traverser `/run/nova` ne peut atteindre aucun
    socket qui s'y trouve, quel que soit le mode de celui-ci.

    Le socket périmé d'un arrêt brutal est retiré ici : `bind()` échouerait
    sinon, et le serveur refuserait de démarrer pour un fichier mort.
    """
    socket_path = Path(path)
    directory = socket_path.parent
    directory.mkdir(parents=True, exist_ok=True)
    try:
        os.chmod(directory, SOCKET_DIRECTORY_MODE)
    except OSError:
        # Un montage en lecture seule, ou une plateforme sans permissions
        # POSIX. On le dit plutôt que de laisser croire que c'est fait.
        pass
    if socket_path.exists() and socket_path.is_socket():
        socket_path.unlink(missing_ok=True)
    return directory


# Éphémère, régénéré à chaque démarrage. Un jeton qui survit au redémarrage
# redeviendrait le secret statique que cette phase supprime.
_LOOPBACK_TOKEN = secrets.token_urlsafe(32)


def loopback_token() -> str:
    return _LOOPBACK_TOKEN


def publish_loopback_token() -> Path:
    """Dépose le jeton éphémère là où le Connector local le lira."""
    path = Path(LOCAL_AUTH_FILE)
    write_restricted(path, _LOOPBACK_TOKEN, AUTH_FILE_MODE)
    return path


class LocalConnectorPrincipal:
    """Le Connector, vu depuis le plan de données.

    Un quatrième principal, distinct de l'utilisateur, de l'administrateur et
    du `ConnectorPrincipal` du plan de contrôle. La distinction n'est pas
    décorative : une compromission d'une frontière ne doit pas ouvrir l'autre,
    et le credential Nova Cloud du Connector n'a rien à faire ici.
    """

    def __init__(self, transport: str):
        self.transport = transport


def local_principal(
    authorization: str | None = Header(default=None),
) -> LocalConnectorPrincipal:
    if LOCAL_TRANSPORT == LOCAL_TRANSPORT_UNIX:
        # Les permissions du socket **sont** l'authentification. Y ajouter un
        # jeton partagé ne protégerait de rien de plus — celui qui peut ouvrir
        # le socket peut aussi lire le fichier qui porterait le jeton — et
        # réintroduirait exactement le secret que cette phase retire.
        return LocalConnectorPrincipal(LOCAL_TRANSPORT_UNIX)
    scheme, _, presented = (authorization or "").partition(" ")
    if scheme.lower() != "bearer" or not secrets.compare_digest(
        main.hash_token(presented.strip()), main.hash_token(_LOOPBACK_TOKEN)
    ):
        raise main.auth_error(401, "LOCAL_CONNECTOR_AUTH_FAILED")
    return LocalConnectorPrincipal(LOCAL_TRANSPORT_LOOPBACK)


internal_app = FastAPI(
    title="Nova local control channel",
    version=main.app.version,
    # Aucune documentation servie : cette surface n'est pas destinée à être
    # explorée depuis un navigateur.
    docs_url=None,
    redoc_url=None,
    openapi_url=None,
)


class LocalApplyBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    revision: int = Field(ge=1)
    profile: str = Field(max_length=32)
    reasoning_allowed: bool = False
    max_concurrent_text: int = Field(ge=1, le=64)
    max_concurrent_speech: int = Field(ge=1, le=32)
    max_queue: int = Field(ge=0, le=256)


@internal_app.get("/internal/connector/capabilities")
def local_capabilities(
    principal: LocalConnectorPrincipal = Depends(local_principal),
):
    """Ce que ce plan de données sait faire, et par quel transport.

    La liste est courte et fermée. C'est elle qui dit qu'il n'y a pas
    d'exécution générique derrière ce canal.
    """
    return {
        "transport": principal.transport,
        "server_version": main.app.version,
        "operations": [
            "get_local_capabilities",
            "get_local_runtime_state",
            "apply_ai_runtime_config",
            "report_local_health",
        ],
    }


@internal_app.get("/internal/connector/runtime")
async def local_runtime(
    principal: LocalConnectorPrincipal = Depends(local_principal),
):
    return await main.local_runtime_state()


@internal_app.get("/internal/connector/health")
async def local_health(
    principal: LocalConnectorPrincipal = Depends(local_principal),
):
    state = await main.local_runtime_state()
    return {
        "runtime_status": state["runtime_status"],
        "text_engine": state["text_engine"],
        "speech_engine": state["speech_engine"],
        "queue_length": state["queue_length"],
    }


@internal_app.post("/internal/connector/runtime/apply")
async def local_apply(
    body: LocalApplyBody,
    principal: LocalConnectorPrincipal = Depends(local_principal),
):
    return await main.apply_runtime_configuration(
        body.revision,
        body.profile,
        body.reasoning_allowed,
        body.max_concurrent_text,
        body.max_concurrent_speech,
        body.max_queue,
    )


def transport_plan() -> dict:
    """Ce que ce processus servira, et pourquoi — décidé avant d'écouter.

    Le mode `unix` sur une plateforme sans AF_UNIX échoue ici, bruyamment,
    plutôt que de retomber en silence sur un transport plus faible. Un repli
    discret vers la boucle locale serait exactement la dégradation que cette
    phase cherche à rendre impossible.
    """
    if LOCAL_TRANSPORT == LOCAL_TRANSPORT_UNIX:
        if not unix_sockets_available():
            return {
                "transport": LOCAL_TRANSPORT_UNIX,
                "available": False,
                "error_code": "LOCAL_TRANSPORT_UNAVAILABLE",
                "reason": "AF_UNIX absent sur cette plateforme",
            }
        return {
            "transport": LOCAL_TRANSPORT_UNIX,
            "available": True,
            "uds": LOCAL_SOCKET_PATH,
            "socket_mode": SOCKET_MODE,
            "directory_mode": SOCKET_DIRECTORY_MODE,
        }
    if LOCAL_TRANSPORT == LOCAL_TRANSPORT_LOOPBACK:
        return {
            "transport": LOCAL_TRANSPORT_LOOPBACK,
            "available": True,
            # Jamais 0.0.0.0 : ce canal n'a aucune raison de quitter la machine.
            "host": LOCAL_LOOPBACK_HOST,
            "port": LOCAL_LOOPBACK_PORT,
            "auth_file": LOCAL_AUTH_FILE,
        }
    return {
        "transport": LOCAL_TRANSPORT,
        "available": False,
        "error_code": "LOCAL_TRANSPORT_UNAVAILABLE",
        "reason": "transport inconnu",
    }
