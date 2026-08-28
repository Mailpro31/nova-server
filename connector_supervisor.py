"""Superviseur du Private Connector.

## Pourquoi un processus séparé

Un programme ne peut pas se remplacer lui-même de façon fiable : s'il échoue
juste après la bascule, il n'y a plus personne pour revenir en arrière. Il faut
un témoin qui survive au redémarrage.

Ce superviseur est ce témoin, et **rien d'autre**.

## Ce qu'il sait faire

Lancer `current`, observer son code de sortie, le relancer. Si un Connector
fraîchement activé n'arrive même pas à démarrer — donc n'est pas en état de
décider son propre retour arrière — c'est lui qui rebascule vers la version
précédente.

## Ce qu'il ne sait pas faire

Exécuter une commande. Il n'existe aucun chemin par lequel le plan de contrôle,
un administrateur ou un artefact pourrait lui faire lancer autre chose que le
`connector.py` de sa propre installation, avec l'interpréteur qui l'exécute
déjà. Pas de shell, pas d'argument venu du réseau, pas d'exécutable désigné de
l'extérieur.

    python connector_supervisor.py
"""

from __future__ import annotations

import os
import subprocess
import sys
import time
from pathlib import Path

import connector_release as release

# Une version qui échoue trois fois d'affilée ne se relance pas indéfiniment :
# la boucle consommerait la machine sans jamais converger.
MAX_CONSECUTIVE_FAILURES = 3
RESTART_DELAY_SECONDS = 2.0


def connector_entrypoint(base: Path) -> Path | None:
    """Le seul programme que ce superviseur lance.

    Le chemin est **dérivé** de la version courante, jamais reçu d'ailleurs :
    il n'y a pas de variable à empoisonner, seulement une arborescence
    d'installation. C'est ce qui rend l'exécution arbitraire impossible.

    La résolution passe par `installed_version` plutôt que par le lien
    `current` : là où les liens symboliques ne sont pas disponibles — Windows
    sans droit dédié — la bascule est un fichier de pointage, et `current/`
    n'existe alors pas comme répertoire.
    """
    version = release.installed_version(base)
    if not version:
        return None
    return base / "versions" / version / "connector.py"


def launch(base: Path) -> int:
    entrypoint = connector_entrypoint(base)
    if entrypoint is None or not entrypoint.exists():
        print(f"[superviseur] aucun Connector installé sous {base}", flush=True)
        return 2
    completed = subprocess.run(
        # `sys.executable` : le même interpréteur que celui-ci. Rien n'est lu
        # dans l'environnement pour décider quoi exécuter.
        [sys.executable, str(entrypoint), "run"],
        cwd=str(entrypoint.parent),
    )
    return completed.returncode


def roll_back_to_previous(base: Path) -> bool:
    """Le Connector n'a pas pu se prononcer : on décide à sa place.

    C'est le cas qu'un auto-update seul ne peut pas couvrir — une version qui
    ne démarre pas n'exécute aucun code de retour arrière. Le témoin extérieur
    existe pour celui-là.
    """
    transaction = release.read_transaction(base)
    previous = transaction.get("previous_version")
    if transaction.get("state") not in {
        release.UPDATE_RESTARTING,
        release.UPDATE_APPLYING,
    } or not previous:
        return False
    try:
        release.rollback(base, previous)
    except release.UpdateError as error:
        print(f"[superviseur] retour arrière impossible : {error.code}", flush=True)
        return False
    transaction.update(
        state=release.UPDATE_ROLLED_BACK,
        last_error_code=release.UPDATE_HEALTH_FAILED,
        failed_at=time.time(),
    )
    release.write_transaction(transaction, base)
    print(f"[superviseur] version défaillante — retour à {previous}", flush=True)
    return True


def run(base: Path | None = None) -> int:
    base = base or release.install_root()
    failures = 0
    print(f"[superviseur] installation : {base}", flush=True)
    while True:
        code = launch(base)
        if code == release.EXIT_STOP:
            print("[superviseur] arrêt demandé", flush=True)
            return 0
        if code in (release.EXIT_RESTART_FOR_UPDATE, release.EXIT_RESTART_AFTER_ROLLBACK):
            # Sortie attendue : le Connector a basculé, ou est revenu en
            # arrière, et demande simplement à repartir.
            failures = 0
            time.sleep(RESTART_DELAY_SECONDS)
            continue

        failures += 1
        print(f"[superviseur] sortie anormale ({code}), échec {failures}", flush=True)
        if roll_back_to_previous(base):
            failures = 0
            time.sleep(RESTART_DELAY_SECONDS)
            continue
        if failures >= MAX_CONSECUTIVE_FAILURES:
            print("[superviseur] abandon après échecs répétés", flush=True)
            return code
        time.sleep(RESTART_DELAY_SECONDS)


if __name__ == "__main__":
    sys.exit(run(Path(os.environ["NOVA_CONNECTOR_INSTALL_ROOT"])
                 if os.getenv("NOVA_CONNECTOR_INSTALL_ROOT") else None))
