"""Le tour horaire de synchronisation d'annuaire.

Un administrateur ne devrait pas avoir a cliquer « synchroniser » pour qu'une
promo arrive. Ce service passe, toutes les heures, sur les organisations dont
la synchronisation est activee et dont le tour est venu.

## Pourquoi un service separe de l'API

Un tour de synchronisation lit un annuaire entier et ecrit des centaines de
lignes. Le poser dans le processus qui sert les dictees ferait payer cette
lecture a quelqu'un qui dicte. Le service vit donc a cote, avec le meme code et
la meme base.

Il ne decide rien de lui-meme : ce qu'il lit, quels groupes, et pour quelle
organisation viennent de la console.
"""

from __future__ import annotations

import time

import google_directory
import main

# Le tour repasse souvent, mais ne synchronise que ce qui est du : c'est
# `due_organizations` qui decide, pas la frequence de la boucle.
TICK_SECONDS = 300.0


def pending(now: float | None = None) -> list[str]:
    stamp = time.time() if now is None else now
    with main.db() as conn:
        rows = conn.execute(
            "SELECT organization_id, enabled, last_sync_at FROM organization_directory_sync "
            "WHERE provider=?",
            (google_directory.SOURCE,),
        ).fetchall()
    return google_directory.due_organizations(rows, stamp)


def run_once(now: float | None = None) -> dict:
    """Synchronise ce qui est du, et ne s'arrete pas a la premiere erreur.

    Une organisation injoignable ne doit pas empecher la suivante : son echec
    est deja enregistre par `run_directory_sync`, et la console le montre.
    """
    done, failed = 0, 0
    for organization_id in pending(now):
        try:
            main.run_directory_sync(organization_id)
            done += 1
        except Exception as error:  # noqa: BLE001 — un tour ne s'interrompt jamais
            failed += 1
            print(f"[directory-sync] {organization_id}: {type(error).__name__}", flush=True)
    return {"synchronized": done, "failed": failed}


def run() -> int:
    print("[directory-sync] worker started", flush=True)
    while True:
        outcome = run_once()
        if outcome["synchronized"] or outcome["failed"]:
            print(f"[directory-sync] {outcome}", flush=True)
        time.sleep(TICK_SECONDS)


if __name__ == "__main__":
    raise SystemExit(run())
