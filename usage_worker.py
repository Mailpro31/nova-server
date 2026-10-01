"""Le tour qui releve la machine, replie l'historique et purge le passe.

## Pourquoi un service separe de l'API

Comme la synchronisation d'annuaire : un releve toutes les minutes et un repli
mensuel qui parcourt trois mois de lignes n'ont pas a etre payes par quelqu'un
qui dicte. Le service vit a cote, avec le meme code et la meme base.

## Pourquoi il tourne meme sans GPU

Un serveur sans carte graphique sert des modeles plus petits, et son tableau de
bord a la meme raison d'exister : combien de personnes, a quelle heure, combien
de temps d'attente. Le releve rend alors des valeurs vides pour le GPU, ce que
la console affiche comme « pas de carte » et non comme une panne.
"""

from __future__ import annotations

import time

import main
import usage_metrics


# Le tour passe chaque minute pour le releve machine. Le repli et la purge sont
# bien plus lourds et bien moins urgents : ils ne s'executent qu'une fois par
# heure, decides par l'horloge et non par un compteur de tours, pour qu'un
# redemarrage ne les fasse pas sauter ni se repeter.
TICK_SECONDS = usage_metrics.SAMPLE_INTERVAL_SECONDS
MAINTENANCE_INTERVAL_SECONDS = 3600.0


def run_once(now: float | None = None, *, maintain: bool = False) -> dict:
    """Un releve, et si on le demande, le repli et la purge.

    Aucune exception ne remonte : un tour manque plutot que d'arreter la boucle
    et de perdre tous les suivants.
    """
    outcome = {"sampled": 0, "rolled_up": 0, "purged": 0, "failed": 0}
    try:
        main.record_host_sample(usage_metrics.sample_host(now))
        outcome["sampled"] = 1
    except Exception as error:  # noqa: BLE001 — un tour ne s'interrompt jamais
        outcome["failed"] += 1
        print(f"[usage] releve : {type(error).__name__}", flush=True)
    if not maintain:
        return outcome
    for name, action in (("rolled_up", main.rollup_usage), ("purged", main.purge_usage)):
        try:
            outcome[name] = action(now)
        except Exception as error:  # noqa: BLE001
            outcome["failed"] += 1
            print(f"[usage] {name} : {type(error).__name__}", flush=True)
    return outcome


def run() -> int:
    print("[usage] worker started", flush=True)
    last_maintenance = 0.0
    while True:
        now = time.time()
        due = now - last_maintenance >= MAINTENANCE_INTERVAL_SECONDS
        outcome = run_once(now, maintain=due)
        if due:
            last_maintenance = now
            print(f"[usage] {outcome}", flush=True)
        elif outcome["failed"]:
            print(f"[usage] {outcome}", flush=True)
        time.sleep(TICK_SECONDS)


if __name__ == "__main__":
    raise SystemExit(run())
