"""Nova Private Connector.

Le pont entre le plan de controle Nova et le plan de donnees d'une
organisation. Il tourne **dans** l'infrastructure de l'organisation, a cote de
nova-server.

## Le sens de la connexion

C'est le Connector qui appelle Nova, jamais l'inverse. Une organisation n'a
donc aucun port entrant a ouvrir, et Nova n'a jamais besoin de connaitre une
adresse privee. C'est la seule propriete qui rend l'application a distance
acceptable pour une DSI.

## Ce qui circule

Des donnees de controle : revisions, statuts, versions, sante, resume
materiel. Jamais d'audio, de prompt, de transcription, de sortie de modele, ni
le contenu complet d'un Style ou d'un AI Skill. Le Connector n'a acces a aucun
de ces contenus et n'a aucune raison d'en demander.

## Ce que ce processus n'est pas

Pas un tunnel, pas un shell distant, pas un transfert de fichiers. Il ne sait
faire que ce que le protocole nomme : appliquer une configuration de runtime,
rapporter une sante.

## Autonomie

Si Nova est injoignable, ce processus se contente de reessayer. Le plan de
donnees local continue de servir la dictee, la reformulation, les Styles et les
AI Skills avec la derniere configuration appliquee. Perdre le plan de controle
ne doit jamais couper l'usage.

Usage :

    python connector.py enroll --control-plane https://... --token <une-fois>
    python connector.py run
"""

from __future__ import annotations

import argparse
import json
import os
import random
import signal
import sys
import time
from pathlib import Path
from urllib.parse import urlparse

import httpx

import connector_release

# Version canonique : une seule source de verite. La recopier ici la ferait
# diverger du manifeste au premier oubli.
CONNECTOR_VERSION = connector_release.CONNECTOR_VERSION
PROTOCOL_VERSION = connector_release.CONNECTOR_PROTOCOL_VERSION

# Ce que ce Connector sait faire. Le plan de controle ne lui enverra rien
# d'autre : une capacite absente d'ici n'est jamais poussee.
BASE_CAPABILITIES = [
    "ai_runtime_config_v1",
    "health_reporting_v1",
    "diagnostics_v1",
    "credential_rotation_v1",
]


def capabilities() -> list[str]:
    """Les capacites reellement exercables sur ce deploiement.

    Deux contrats, pas une presence et une absence. `package` declare
    `connector_self_update_v1` : il sait conduire sa propre bascule. `docker`
    declare `connector_orchestrator_update_v1` : une mise a jour est possible,
    mais elle appartient a l'orchestrateur. Un conteneur ne remplace pas sa
    propre image, et la console doit le dire plutot que d'offrir un bouton qui
    echouerait a chaque fois.
    """
    return list(BASE_CAPABILITIES) + connector_release.update_capabilities()


# Conserve pour les appelants qui lisent une liste figee ; le calcul ci-dessus
# reste l'autorite.
CAPABILITIES = BASE_CAPABILITIES

DEFAULT_HEARTBEAT_SECONDS = 45
BACKOFF_INITIAL_SECONDS = 5.0
BACKOFF_MAX_SECONDS = 300.0
REQUEST_TIMEOUT_SECONDS = 30.0
APPLY_TIMEOUT_SECONDS = 120.0

APPLY_STATUS_PENDING = "pending"
APPLY_STATUS_APPLIED = "applied"
APPLY_STATUS_FAILED = "failed"


# ── Configuration ────────────────────────────────────────────────────────


def state_path() -> Path:
    return Path(
        os.getenv("NOVA_CONNECTOR_STATE", "connector-state.json")
    ).expanduser()


LOCAL_TRANSPORT_UNIX = "unix"
LOCAL_TRANSPORT_LOOPBACK = "loopback"


def local_transport() -> str:
    return os.getenv("NOVA_CONNECTOR_LOCAL_TRANSPORT", LOCAL_TRANSPORT_UNIX)


def local_socket_path() -> str:
    return os.getenv("NOVA_CONNECTOR_LOCAL_SOCKET", "/run/nova/control.sock")


def local_auth_file() -> Path:
    return Path(os.getenv("NOVA_CONNECTOR_LOCAL_AUTH_FILE", "/run/nova/local-auth"))


def local_loopback_url() -> str:
    port = os.getenv("NOVA_CONNECTOR_LOCAL_PORT", "8129")
    return f"http://127.0.0.1:{port}"


def local_client() -> tuple[httpx.Client, dict[str, str]] | None:
    """Un client vers le plan de donnees local, ou rien.

    En `unix`, l'authentification est le socket lui-meme : pouvoir l'ouvrir,
    c'est etre autorise. Aucun jeton n'est envoye, parce qu'aucun jeton
    n'ajouterait de garantie — qui peut ouvrir le socket pourrait aussi lire le
    fichier qui le porterait.

    En `loopback`, le jeton est ephemere : nova-server le regenere a chaque
    demarrage et le depose avec des permissions restreintes. Ce n'est plus un
    secret partage figes dans un fichier de deploiement.
    """
    transport = local_transport()
    if transport == LOCAL_TRANSPORT_UNIX:
        path = local_socket_path()
        if not Path(path).exists():
            return None
        return (
            httpx.Client(
                transport=httpx.HTTPTransport(uds=path),
                base_url="http://local",
                timeout=APPLY_TIMEOUT_SECONDS,
            ),
            {},
        )
    if transport == LOCAL_TRANSPORT_LOOPBACK:
        auth_file = local_auth_file()
        if not auth_file.exists():
            return None
        token = auth_file.read_text(encoding="utf-8").strip()
        return (
            httpx.Client(base_url=local_loopback_url(), timeout=APPLY_TIMEOUT_SECONDS),
            {"Authorization": f"Bearer {token}"},
        )
    return None


def validate_control_plane_url(value: str) -> str:
    """Le plan de controle est public : HTTPS, sans exception silencieuse.

    Une adresse en clair n'est toleree qu'en developpement, et seulement si le
    deploiement l'a explicitement demande. Retomber en HTTP sans le dire
    exposerait le credential du Connector sur le reseau de l'organisation.
    """
    parsed = urlparse((value or "").strip())
    if parsed.scheme == "https" and parsed.hostname:
        return value.rstrip("/")
    insecure_allowed = os.getenv("NOVA_CONNECTOR_ALLOW_INSECURE", "false").lower() == "true"
    if parsed.scheme == "http" and parsed.hostname and insecure_allowed:
        return value.rstrip("/")
    raise SystemExit(
        "adresse de plan de controle refusee : HTTPS est requis "
        "(NOVA_CONNECTOR_ALLOW_INSECURE=true pour un test local uniquement)"
    )


# ── Etat local ───────────────────────────────────────────────────────────


def read_state() -> dict:
    path = state_path()
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (ValueError, OSError):
        return {}


def write_state(state: dict) -> None:
    """Ecrit l'etat local avec des permissions restrictives.

    Le fichier porte le credential du Connector. Il est cree en 0600 **avant**
    d'ecrire : creer puis restreindre laisserait une fenetre, courte mais
    reelle, ou le secret serait lisible par tout le systeme.
    """
    path = state_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.parent / f".{path.name}.{os.getpid()}.tmp"
    descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            json.dump(state, handle, indent=2)
            handle.flush()
            os.fsync(handle.fileno())
        # Remplacement atomique : un redemarrage lit l'ancien etat ou le
        # nouveau, jamais un fichier a moitie ecrit. C'est ce qui permet de
        # perdre le processus au milieu d'une rotation sans perdre l'identite.
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)
    try:
        os.chmod(path, 0o600)
    except OSError:
        # Windows ne porte pas les permissions POSIX. La documentation de
        # deploiement decrit la protection a appliquer sur ce chemin.
        pass


def connector_credential(state: dict) -> str:
    """Le credential, depuis l'environnement en priorite.

    Un deploiement qui injecte son secret par le mecanisme de son
    orchestrateur — Docker secret, systemd credential — n'a pas a le poser sur
    le disque. Le fichier d'etat reste le repli.
    """
    return os.getenv("NOVA_CONNECTOR_CREDENTIAL") or state.get("connector_credential", "")


def pending_credential(state: dict) -> str:
    """Le credential recu mais pas encore confirme, s'il y en a un."""
    return state.get("pending_credential", "")


def rotate_credential(state: dict) -> dict:
    """Fait tourner le credential de ce Connector, sans re-enrolement.

    L'ordre est celui-ci et pas l'inverse :

        demander -> persister -> prouver -> l'ancien meurt

    Persister avant de prouver est le point critique. Si le processus meurt
    entre les deux, le redemarrage retrouve le secret en attente et finit la
    bascule ; l'inverse perdrait une identite pour une ecriture disque ratee.
    """
    credential = connector_credential(state)
    body = {"protocol_version": PROTOCOL_VERSION, "connector_version": CONNECTOR_VERSION}
    response = httpx.post(
        f"{state['control_plane_url']}/api/connector/credential/rotate",
        headers={"Authorization": f"Bearer {credential}"},
        json=body,
        timeout=REQUEST_TIMEOUT_SECONDS,
    )
    if response.status_code == 409:
        # Une bascule est deja en cours. Ce n'est pas une erreur : la reprise
        # s'en chargera avec le secret en attente.
        print("[connector] rotation deja en cours", flush=True)
        return state
    if response.status_code != 200:
        print(f"[connector] rotation refusee ({response.status_code})", flush=True)
        return state

    state = {**state, "pending_credential": response.json()["connector_credential"]}
    write_state(state)
    return confirm_rotation(state)


def confirm_rotation(state: dict) -> dict:
    """Prouve la possession du nouveau secret, puis promeut l'etat local.

    L'appel est authentifie avec le **nouveau** credential : le serveur ne
    croit pas sur parole, il constate. Tant que cette preuve n'a pas abouti,
    l'ancien secret reste celui qui compte localement.
    """
    pending = pending_credential(state)
    if not pending:
        return state
    body = {"protocol_version": PROTOCOL_VERSION, "connector_version": CONNECTOR_VERSION}
    try:
        response = httpx.post(
            f"{state['control_plane_url']}/api/connector/credential/confirm",
            headers={"Authorization": f"Bearer {pending}"},
            json=body,
            timeout=REQUEST_TIMEOUT_SECONDS,
        )
    except httpx.HTTPError:
        # Le plan de controle est injoignable. Le secret en attente reste sur
        # disque et la reprise reessaiera : rien n'est perdu, rien n'est force.
        return state
    if response.status_code == 200:
        promoted = {**state, "connector_credential": pending}
        promoted.pop("pending_credential", None)
        write_state(promoted)
        print(
            f"[connector] credential renouvele (generation {response.json().get('generation')})",
            flush=True,
        )
        return promoted
    if response.status_code == 401:
        # Le secret en attente a expire ou a ete invalide. L'ancien vaut
        # toujours : on abandonne cette bascule plutot que de s'enfermer dehors.
        abandoned = {**state}
        abandoned.pop("pending_credential", None)
        write_state(abandoned)
        print("[connector] secret en attente expire — rotation abandonnee", flush=True)
        return abandoned
    return state


# ── Plan de donnees local ────────────────────────────────────────────────


def local_health() -> dict:
    """Un resume de sante, et rien de plus.

    Aucune requete, aucune transcription, aucune sortie de modele : le plan de
    controle n'en a pas besoin pour dire si un deploiement va bien.

    Un canal local indisponible se rapporte comme tel. Le taire ferait croire
    que le plan de donnees est en panne alors que seul le tuyau manque.
    """
    empty = {
        "runtime_status": "unavailable",
        "text_engine": "unavailable",
        "speech_engine": "unavailable",
        "queue_length": 0,
        "last_error_code": "LOCAL_TRANSPORT_UNAVAILABLE",
    }
    client = local_client()
    if client is None:
        return empty
    session, headers = client
    try:
        with session:
            response = session.get("/internal/connector/health", headers=headers)
        if response.status_code != 200:
            return {**empty, "last_error_code": "LOCAL_CONNECTOR_AUTH_FAILED"}
        body = response.json()
    except (httpx.HTTPError, ValueError):
        return {**empty, "last_error_code": "LOCAL_DATA_PLANE_UNAVAILABLE"}
    return {
        "runtime_status": body.get("runtime_status", "unavailable"),
        "text_engine": body.get("text_engine", "unavailable"),
        "speech_engine": body.get("speech_engine", "unavailable"),
        "queue_length": int(body.get("queue_length", 0)),
        "last_error_code": None,
    }


def apply_locally(desired: dict) -> tuple[str, str | None, dict | None]:
    """Applique une configuration au nova-server local.

    Renvoie `(statut, code d'erreur, sante)`. Le statut n'est « applique » que
    si le plan de donnees l'a confirme : deviner reviendrait a rapporter au plan
    de controle un succes que personne n'a observe.
    """
    client = local_client()
    if client is None:
        # Ni socket, ni jeton ephemere : il n'y a pas de chemin nominal, et il
        # n'y a surtout pas de secret par defaut sur lequel retomber.
        return APPLY_STATUS_FAILED, "LOCAL_TRANSPORT_UNAVAILABLE", None
    session, headers = client
    payload = {
        "revision": desired["revision"],
        "profile": desired["profile"],
        "reasoning_allowed": desired["reasoning_allowed"],
        "max_concurrent_text": desired["max_concurrent_text"],
        "max_concurrent_speech": desired["max_concurrent_speech"],
        "max_queue": desired["max_queue"],
    }
    try:
        with session:
            response = session.post(
                "/internal/connector/runtime/apply", headers=headers, json=payload
            )
    except httpx.HTTPError:
        return APPLY_STATUS_FAILED, "LOCAL_DATA_PLANE_UNAVAILABLE", None
    if response.status_code == 401:
        return APPLY_STATUS_FAILED, "LOCAL_CONNECTOR_AUTH_FAILED", None
    if response.status_code != 200:
        return APPLY_STATUS_FAILED, "LOCAL_DATA_PLANE_UNAVAILABLE", None
    body = response.json()
    if body.get("status") != APPLY_STATUS_APPLIED:
        return APPLY_STATUS_FAILED, body.get("error_code"), None
    return (
        APPLY_STATUS_APPLIED,
        None,
        {
            "runtime_status": body.get("runtime_status", "unavailable"),
            "text_engine": body.get("text_engine", "unavailable"),
            "speech_engine": body.get("speech_engine", "unavailable"),
        },
    )


# ── Enrolement ───────────────────────────────────────────────────────────


def enroll(control_plane: str, enrollment_token: str) -> dict:
    control_plane = validate_control_plane_url(control_plane)
    response = httpx.post(
        f"{control_plane}/api/connector/enroll",
        json={
            "enrollment_token": enrollment_token,
            "protocol_version": PROTOCOL_VERSION,
            "connector_version": CONNECTOR_VERSION,
            "capabilities": capabilities(),
        },
        timeout=REQUEST_TIMEOUT_SECONDS,
    )
    if response.status_code != 200:
        # Le code d'enrolement n'est jamais reaffiche, pas meme tronque.
        raise SystemExit(
            f"enrolement refuse ({response.status_code}) : "
            f"{response.json().get('detail', 'inconnu')}"
        )
    body = response.json()
    state = {
        "control_plane_url": control_plane,
        "connector_id": body["connector_id"],
        "connector_credential": body["connector_credential"],
        "organization_id": body["organization_id"],
        "deployment_id": body["deployment_id"],
        "last_applied_revision": 0,
        "last_apply_status": APPLY_STATUS_PENDING,
        "last_error_code": None,
    }
    write_state(state)
    return state


# ── Mise a jour ──────────────────────────────────────────────────────────


def install_base() -> Path:
    return connector_release.install_root()


def fetch_manifest(url: str) -> tuple[bytes, bytes]:
    """Recupere le manifeste et sa signature depuis la source de distribution.

    Deux fichiers : le manifeste, et sa signature detachee a cote. Le plan de
    controle n'est pas dans ce chemin — il n'est pas un maillon de la chaine
    d'approvisionnement.
    """
    connector_release.validate_release_url(url)
    manifest = httpx.get(url, timeout=REQUEST_TIMEOUT_SECONDS, follow_redirects=False)
    signature = httpx.get(
        url + ".sig", timeout=REQUEST_TIMEOUT_SECONDS, follow_redirects=False
    )
    if manifest.status_code != 200 or signature.status_code != 200:
        raise connector_release.UpdateError(connector_release.UPDATE_DOWNLOAD_FAILED)
    return manifest.content, bytes.fromhex(signature.text.strip())


def trust_root() -> str:
    return os.getenv("NOVA_CONNECTOR_RELEASE_PUBLIC_KEY", "").strip()


def manifest_url_for(version: str) -> str:
    """L'adresse du manifeste d'une version, derivee d'une base configuree.

    La base appartient au deploiement. Si le plan de controle pouvait envoyer
    une adresse, il ferait telecharger et executer du code arbitraire par tous
    les Connectors d'un tenant.
    """
    base = os.getenv("NOVA_CONNECTOR_RELEASE_BASE", "").rstrip("/")
    if not base:
        raise connector_release.UpdateError(connector_release.UPDATE_SOURCE_UNTRUSTED)
    return f"{base}/connector-{version}.json"


def start_update(state: dict, target_version: str) -> dict:
    """Telecharge, verifie, deballe, bascule — puis demande a etre relance.

    Rien n'est active avant que la signature du manifeste et l'empreinte de
    l'artefact soient toutes deux confirmees. La derniere action de ce
    processus est de sortir avec un code que le superviseur comprend : c'est la
    frontiere processus, et elle est reelle.
    """
    base = install_base()
    transaction = connector_release.read_transaction(base)

    if connector_release.blocked_by_cooldown(transaction, target_version):
        print(f"[connector] {target_version} a echoue recemment — pas de nouvelle tentative", flush=True)
        return state
    if pending_credential(state):
        # Deux machines a etats critiques en meme temps produiraient des
        # combinaisons que personne n'a raisonnees.
        print("[connector] rotation en cours — mise a jour differee", flush=True)
        return {**state, "update_error": connector_release.UPDATE_BLOCKED_BY_ROTATION}

    transaction = connector_release.new_transaction(
        CONNECTOR_VERSION, target_version, target_version
    )
    transaction["attempt_count"] = transaction.get("attempt_count", 0) + 1

    def fail(code: str) -> dict:
        transaction.update(
            state=connector_release.UPDATE_FAILED,
            last_error_code=code,
            failed_at=time.time(),
        )
        connector_release.write_transaction(transaction, base)
        print(f"[connector] mise a jour refusee : {code}", flush=True)
        return {**state, "update_error": code}

    try:
        transaction["state"] = connector_release.UPDATE_VERIFYING
        connector_release.write_transaction(transaction, base)
        raw, signature = fetch_manifest(manifest_url_for(target_version))
        manifest = connector_release.verify_manifest(raw, signature, trust_root())
        artifact = connector_release.check_installable(
            manifest, CONNECTOR_VERSION, connector_release.current_platform()
        )

        transaction["state"] = connector_release.UPDATE_DOWNLOADING
        connector_release.write_transaction(transaction, base)
        staged = connector_release.download_artifact(artifact, base / "staging")

        transaction.update(state=connector_release.UPDATE_STAGED, staged_path=str(staged))
        connector_release.write_transaction(transaction, base)

        transaction["state"] = connector_release.UPDATE_APPLYING
        connector_release.write_transaction(transaction, base)
        connector_release.activate(staged, manifest.connector_version, base)

        # Persister AVANT de sortir : si le processus meurt ici, le suivant
        # retrouve une transaction en `restarting` et sait quoi verifier.
        transaction["state"] = connector_release.UPDATE_RESTARTING
        transaction["activated_at"] = time.time()
        connector_release.write_transaction(transaction, base)
    except connector_release.UpdateError as error:
        return fail(error.code)

    print(
        f"[connector] {CONNECTOR_VERSION} -> {target_version} active, redemarrage",
        flush=True,
    )
    raise SystemExit(connector_release.EXIT_RESTART_FOR_UPDATE)


def verify_after_restart(state: dict) -> dict:
    """Le nouveau processus fait ses preuves, ou rend la main a l'ancien.

    « Le processus a demarre » ne vaut pas « la mise a jour a reussi ». Il faut
    la bonne version, un credential encore valide, un plan de controle joignable
    et un canal local qui repond. Sans quoi on revient a la derniere version
    connue saine — et c'est le nouveau processus qui le decide, parce qu'il est
    le seul a pouvoir constater son propre echec.
    """
    base = install_base()
    transaction = connector_release.read_transaction(base)
    if transaction.get("state") != connector_release.UPDATE_RESTARTING:
        return state

    target = transaction.get("target_version")
    deadline = time.monotonic() + connector_release.HEALTH_TIMEOUT_SECONDS
    failure = None

    if CONNECTOR_VERSION != target:
        # Le superviseur a relance l'ancienne version : la bascule n'a pas pris.
        failure = "version"
    else:
        while time.monotonic() < deadline:
            local = local_health()
            try:
                state, _ = sync_once({**state, "update_state": connector_release.UPDATE_RESTARTING})
                if local.get("last_error_code") is None:
                    transaction.update(
                        state=connector_release.UPDATE_HEALTHY,
                        completed_at=time.time(),
                    )
                    connector_release.write_transaction(transaction, base)
                    connector_release.prune_versions(
                        base, (target, transaction.get("previous_version") or "")
                    )
                    connector_release.cleanup_staging(base / "staging")
                    print(f"[connector] mise a jour confirmee : {target}", flush=True)
                    # Retirer la valeur transitoire : sans cela, elle resterait
                    # collee dans l'etat et le Connector rapporterait
                    # « restarting » indefiniment apres une bascule reussie.
                    # C'est la transaction, desormais `healthy`, qui fait foi.
                    confirmed = dict(state)
                    confirmed.pop("update_state", None)
                    return confirmed
            except (httpx.HTTPError, ValueError, KeyError):
                # Nova temporairement injoignable n'est pas une mise a jour
                # ratee : la sante locale et la reconnexion sont deux signaux
                # distincts. On reessaie jusqu'au delai.
                pass
            time.sleep(2)
        failure = "health"

    previous = transaction.get("previous_version")
    transaction.update(
        state=connector_release.UPDATE_ROLLBACK_IN_PROGRESS,
        last_error_code=connector_release.UPDATE_HEALTH_FAILED,
        failed_at=time.time(),
    )
    connector_release.write_transaction(transaction, base)
    print(f"[connector] mise a jour non confirmee ({failure}) — retour a {previous}", flush=True)
    try:
        connector_release.rollback(base, previous)
    except connector_release.UpdateError:
        transaction["state"] = connector_release.UPDATE_FAILED
        connector_release.write_transaction(transaction, base)
        return state
    transaction["state"] = connector_release.UPDATE_ROLLED_BACK
    connector_release.write_transaction(transaction, base)
    raise SystemExit(connector_release.EXIT_RESTART_AFTER_ROLLBACK)


def report_update_state() -> str:
    """L'etat de bascule tel que ce processus le voit, pour le rapport."""
    transaction = connector_release.read_transaction(install_base())
    if not transaction:
        return connector_release.UPDATE_UP_TO_DATE
    return transaction.get("state", connector_release.UPDATE_UP_TO_DATE)


def process_update_intent(state: dict, update: dict) -> dict:
    """Le point d'insertion : une intention recue, une decision locale.

    En mode `docker`, il n'y a rien a faire ici : le remplacement appartient a
    l'orchestrateur. Le Connector se contente de rapporter que la cible n'est
    pas atteinte.
    """
    target = (update or {}).get("requested_version")
    if not target or target == CONNECTOR_VERSION:
        return state
    if not connector_release.self_update_supported():
        print(f"[connector] {target} demandee — remplacement par l'orchestrateur requis", flush=True)
        return {**state, "external_update_required": target}
    return start_update(state, target)


# ── Boucle de synchronisation ────────────────────────────────────────────


def sync_once(state: dict) -> tuple[dict, float]:
    """Un aller-retour. Renvoie l'etat mis a jour et le delai avant le suivant.

    L'ordre compte : rapporter d'abord ce que l'on sait, recevoir ensuite ce
    qu'il faut faire. Une revision deja appliquee est acquittee sans etre
    reappliquee — la livraison est au-moins-une-fois, l'application doit donc
    etre idempotente.
    """
    credential = connector_credential(state)
    body = {
        "protocol_version": PROTOCOL_VERSION,
        "connector_version": CONNECTOR_VERSION,
        "server_version": state.get("server_version", ""),
        "capabilities": capabilities(),
        # Ce sur quoi ce Connector tourne. Le plan de controle s'en sert pour
        # ne proposer qu'un artefact reellement installable ici.
        "platform": connector_release.current_platform(),
        "update_state": state.get("update_state") or report_update_state(),
        "applied_revision": int(state.get("last_applied_revision", 0)),
        "apply_status": state.get("last_apply_status", APPLY_STATUS_PENDING),
        "last_error_code": state.get("last_error_code"),
        "health": local_health(),
        "local_time": time.time(),
    }
    response = httpx.post(
        f"{state['control_plane_url']}/api/connector/sync",
        headers={"Authorization": f"Bearer {credential}"},
        json=body,
        timeout=REQUEST_TIMEOUT_SECONDS,
    )
    if response.status_code in {401, 403}:
        # Revoque, ou credential invalide. Ce n'est pas une panne : reessayer
        # vite ne servirait qu'a marteler le plan de controle.
        print("[connector] credential refuse — synchronisation suspendue", flush=True)
        return state, BACKOFF_MAX_SECONDS
    if response.status_code == 426:
        print("[connector] protocole non supporte par le plan de controle", flush=True)
        return state, BACKOFF_MAX_SECONDS
    response.raise_for_status()
    answer = response.json()
    heartbeat = float(answer.get("heartbeat_seconds", DEFAULT_HEARTBEAT_SECONDS))

    desired = (answer.get("desired") or {}).get("ai_runtime")
    if desired:
        state = reconcile(state, desired)
    # La rotation d'abord : une bascule de version pendant une transition de
    # credential ferait tourner deux machines a etats critiques ensemble.
    if answer.get("rotation_requested"):
        state = rotate_credential(state)
    elif answer.get("update"):
        state = process_update_intent(state, answer["update"])
    return state, heartbeat


def reconcile(state: dict, desired: dict) -> dict:
    """Rapproche l'etat local de l'etat souhaite, sans jamais reculer."""
    revision = int(desired["revision"])
    applied = int(state.get("last_applied_revision", 0))

    if revision == applied and state.get("last_apply_status") == APPLY_STATUS_APPLIED:
        # Deja pose. Reappliquer a chaque battement provoquerait des
        # reconfigurations inutiles du runtime.
        return state
    if revision < applied:
        # Un retour en arriere ne se fait pas en livrant une revision plus
        # ancienne : le plan de controle en cree une nouvelle qui porte
        # l'ancienne configuration, exactement comme un rollback de package.
        print(
            f"[connector] revision {revision} anterieure a {applied} — ignoree",
            flush=True,
        )
        return state

    status, error_code, health = apply_locally(desired)
    # Persister AVANT d'acquitter. Si le processus meurt entre les deux, le
    # redemarrage retrouve la revision posee et l'acquitte sans la reappliquer.
    state = {
        **state,
        "last_applied_revision": revision if status == APPLY_STATUS_APPLIED else applied,
        "last_apply_status": status,
        "last_error_code": error_code,
    }
    if health:
        state["last_health"] = health
    write_state(state)
    print(
        f"[connector] revision {revision} : {status}"
        + (f" ({error_code})" if error_code else ""),
        flush=True,
    )
    return state


def run() -> int:
    state = read_state()
    if not state.get("control_plane_url") or not connector_credential(state):
        raise SystemExit("Connector non enrole — lancer `connector.py enroll` d'abord")

    if pending_credential(state):
        # Une rotation etait en cours quand ce processus s'est arrete. On la
        # termine avant toute autre chose : reprendre la synchronisation avec
        # un etat ambigu ferait osciller les deux secrets.
        print("[connector] reprise d'une rotation interrompue", flush=True)
        state = confirm_rotation(state)

    # Symetrique de la reprise de rotation : une bascule interrompue se termine
    # avant que la synchronisation ordinaire ne reprenne.
    state = verify_after_restart(state)

    stopping = {"now": False}

    def stop(*_):
        stopping["now"] = True

    signal.signal(signal.SIGINT, stop)
    signal.signal(signal.SIGTERM, stop)

    backoff = BACKOFF_INITIAL_SECONDS
    print(
        f"[connector] {state['connector_id'][:8]}… -> {state['control_plane_url']}",
        flush=True,
    )
    while not stopping["now"]:
        try:
            state, heartbeat = sync_once(state)
            backoff = BACKOFF_INITIAL_SECONDS
            delay = heartbeat
        except (httpx.HTTPError, ValueError, KeyError) as error:
            # Le plan de donnees local continue de servir : perdre le plan de
            # controle retarde une reconfiguration, il n'interrompt aucun usage.
            print(f"[connector] plan de controle injoignable ({type(error).__name__})", flush=True)
            delay = backoff
            backoff = min(backoff * 2, BACKOFF_MAX_SECONDS)
        # Un peu de dispersion : sans elle, mille Connectors redemarres ensemble
        # appelleraient ensemble, indefiniment.
        delay = max(1.0, delay * random.uniform(0.85, 1.15))
        deadline = time.monotonic() + delay
        while not stopping["now"] and time.monotonic() < deadline:
            time.sleep(0.25)
    print("[connector] arret", flush=True)
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="connector", description="Nova Private Connector")
    commands = parser.add_subparsers(dest="command", required=True)

    enrollment = commands.add_parser("enroll", help="enrole ce Connector, une seule fois")
    enrollment.add_argument("--control-plane", required=True)
    enrollment.add_argument("--token", required=True, help="code d'enrolement a usage unique")

    commands.add_parser("run", help="boucle de synchronisation")
    commands.add_parser("status", help="etat local, sans secret")

    args = parser.parse_args(argv)
    if args.command == "enroll":
        state = enroll(args.control_plane, args.token)
        print(f"[connector] enrole : {state['connector_id']}", flush=True)
        print(f"[connector] etat local : {state_path()}", flush=True)
        return 0
    if args.command == "status":
        state = read_state()
        print(
            json.dumps(
                {
                    "enrolled": bool(state.get("connector_id")),
                    "connector_id": state.get("connector_id"),
                    "control_plane_url": state.get("control_plane_url"),
                    "deployment_id": state.get("deployment_id"),
                    "last_applied_revision": state.get("last_applied_revision", 0),
                    "last_apply_status": state.get("last_apply_status"),
                },
                indent=2,
            )
        )
        return 0
    return run()


if __name__ == "__main__":
    sys.exit(main())
