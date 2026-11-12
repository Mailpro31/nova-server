"""Activation de la licence par code à usage unique (côté serveur d'école).

La DSI pose `NOVA_ACTIVATION_CODE` ; au démarrage, le serveur l'échange
auprès du service d'activation (`NOVA_ACTIVATION_URL`) contre la licence déjà
signée, vérifie sa signature (`licence.licence_state`) puis la garde dans un
fichier à côté de la base : le code, consommé, n'est plus nécessaire.

`NOVA_LICENCE`, quand elle est posée, reste prioritaire.
"""

from __future__ import annotations

import logging
import os
from pathlib import Path

import httpx

import licence

log = logging.getLogger("nova.activation")


def _stored(path: Path) -> str:
    try:
        token = path.read_text(encoding="utf-8").strip()
    except OSError:
        return ""
    # Un fichier modifié à la main n'est plus signé : on l'ignore.
    return token if licence.licence_state(token)["status"] not in ("none", "invalid") else ""


def resolve_licence(env_licence: str, code: str, url: str, store: Path,
                    post=httpx.post) -> str:
    """La licence à appliquer : NOVA_LICENCE, sinon la licence enregistrée,
    sinon celle obtenue en échangeant le code. Jamais d'exception : sans
    licence, Nova tourne comme avant."""
    if env_licence.strip():
        return env_licence
    if token := _stored(store):
        return token
    code, url = code.strip(), url.strip()
    if not code or not url:
        return ""
    try:
        response = post(url.rstrip("/") + "/redeem", json={"code": code}, timeout=15)
        response.raise_for_status()
        token = str(response.json()["licence"]).strip()
    except (httpx.HTTPError, ValueError, KeyError, TypeError) as error:
        log.warning("activation refusée (%s) ; demandez un nouveau code", type(error).__name__)
        return _stored(store)  # un autre processus l'a peut-être déjà fait
    if licence.licence_state(token)["status"] in ("none", "invalid"):
        log.warning("activation : la licence reçue n'a pas une signature valide, ignorée")
        return ""
    tmp = store.with_suffix(".tmp")
    tmp.write_text(token, encoding="utf-8")
    os.replace(tmp, store)
    log.info("licence activée et enregistrée")
    return token
