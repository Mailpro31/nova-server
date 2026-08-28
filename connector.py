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

CONNECTOR_VERSION = "0.1.0"
PROTOCOL_VERSION = 1

# Ce que ce Connector sait faire. Le plan de controle ne lui enverra rien
# d'autre : une capacite absente d'ici n'est jamais poussee.
CAPABILITIES = ["ai_runtime_config_v1", "health_reporting_v1", "diagnostics_v1"]

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


def data_plane_url() -> str:
    return os.getenv("NOVA_DATA_PLANE_URL", "http://127.0.0.1:8000").rstrip("/")


def deployment_apply_token() -> str:
    return os.getenv("NOVA_DEPLOYMENT_APPLY_TOKEN", "")


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
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
        json.dump(state, handle, indent=2)
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


# ── Plan de donnees local ────────────────────────────────────────────────


def local_health() -> dict:
    """Un resume de sante, et rien de plus.

    Aucune requete, aucune transcription, aucune sortie de modele : le plan de
    controle n'en a pas besoin pour dire si un deploiement va bien.
    """
    empty = {
        "runtime_status": "unavailable",
        "text_engine": "unavailable",
        "speech_engine": "unavailable",
        "queue_length": 0,
    }
    token = deployment_apply_token()
    if not token:
        return empty
    try:
        response = httpx.get(
            f"{data_plane_url()}/api/health", timeout=REQUEST_TIMEOUT_SECONDS
        )
        if response.status_code != 200:
            return empty
    except httpx.HTTPError:
        return empty
    # `/api/health` reste une sonde de vivacite volontairement pauvre. L'etat
    # detaille du runtime vient de l'application elle-meme, rapportee ensuite.
    return {**empty, "runtime_status": "healthy"}


def apply_locally(desired: dict) -> tuple[str, str | None, dict | None]:
    """Applique une configuration au nova-server local.

    Renvoie `(statut, code d'erreur, sante)`. Le statut n'est « applique » que
    si le plan de donnees l'a confirme : deviner reviendrait a rapporter au plan
    de controle un succes que personne n'a observe.
    """
    token = deployment_apply_token()
    if not token:
        return APPLY_STATUS_FAILED, "DEPLOYMENT_APPLY_DISABLED", None
    payload = {
        "revision": desired["revision"],
        "profile": desired["profile"],
        "reasoning_allowed": desired["reasoning_allowed"],
        "max_concurrent_text": desired["max_concurrent_text"],
        "max_concurrent_speech": desired["max_concurrent_speech"],
        "max_queue": desired["max_queue"],
    }
    try:
        response = httpx.post(
            f"{data_plane_url()}/api/deployment/ai-runtime/apply",
            headers={"Authorization": f"Bearer {token}"},
            json=payload,
            timeout=APPLY_TIMEOUT_SECONDS,
        )
    except httpx.HTTPError:
        return APPLY_STATUS_FAILED, "DATA_PLANE_UNREACHABLE", None
    if response.status_code != 200:
        return APPLY_STATUS_FAILED, "DATA_PLANE_REFUSED", None
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
            "capabilities": CAPABILITIES,
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
        "capabilities": CAPABILITIES,
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
