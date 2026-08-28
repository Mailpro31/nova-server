"""Point d'entrée de nova-server : l'API publique et le canal local.

Deux points d'écoute, un seul processus. Le processus unique n'est pas un
détail de confort : le routeur d'inférence est un état de processus, et le
canal local doit pouvoir le reconfigurer réellement. Deux processus séparés
partageraient un nom de module, pas un routeur.

Deux applications ASGI distinctes, en revanche, sont essentielles. L'API
publique ne monte aucune route interne ; aucune session Desktop ni
d'administration n'atteint la surface du Connector, quels que soient ses
droits.

    python serve.py

En mode `unix`, le canal local est un socket dont les permissions font
l'authentification. En mode `loopback`, c'est 127.0.0.1 avec un jeton éphémère
régénéré à chaque démarrage.
"""

from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path

import uvicorn

import local_channel
import main

PUBLIC_HOST = os.getenv("NOVA_HOST", "0.0.0.0")
PUBLIC_PORT = int(os.getenv("NOVA_PORT", "8000"))
LOG_LEVEL = os.getenv("NOVA_LOG_LEVEL", "info")


async def restrict_socket(path: str, mode: int) -> None:
    """Resserre les permissions du socket dès qu'uvicorn l'a créé.

    uvicorn le crée avec le masque du processus ; l'attendre pour le restreindre
    laisse une fenêtre de quelques millisecondes. Elle est bornée et locale, et
    la refermer autrement demanderait de pré-créer le socket à la main — un
    échange que la documentation décrit plutôt que de le passer sous silence.
    """
    socket_path = Path(path)
    for _ in range(100):
        if socket_path.exists():
            try:
                os.chmod(socket_path, mode)
            except OSError as error:
                print(f"[serve] permissions du socket non appliquées : {error}", flush=True)
            return
        await asyncio.sleep(0.05)
    print("[serve] socket local jamais apparu", flush=True)


async def run() -> int:
    plan = local_channel.transport_plan()
    if not plan["available"]:
        # Fail-closed. Retomber en silence sur un transport plus faible ferait
        # tourner un déploiement de production sur le canal de développement
        # sans que personne ne l'ait décidé.
        print(
            f"[serve] canal local indisponible : {plan['error_code']} "
            f"({plan.get('reason', '')})",
            flush=True,
        )
        return 1

    public = uvicorn.Server(
        uvicorn.Config(main.app, host=PUBLIC_HOST, port=PUBLIC_PORT, log_level=LOG_LEVEL)
    )

    tasks = []
    if plan["transport"] == local_channel.LOCAL_TRANSPORT_UNIX:
        # Avant d'ecouter, pas apres. uvicorn passe le socket a 0o666 des sa
        # creation ; un repertoire non traversable rend cette fenetre
        # inatteignable, la ou un chmod apres coup ne fait que la raccourcir.
        directory = local_channel.prepare_socket_directory(plan["uds"])
        internal_config = uvicorn.Config(
            local_channel.internal_app, uds=plan["uds"], log_level=LOG_LEVEL
        )
        # Defense en profondeur : le repertoire suffit deja, mais un socket
        # resserre coute une ligne et ne depend pas des permissions heritees.
        tasks.append(restrict_socket(plan["uds"], plan["socket_mode"]))
        print(
            f"[serve] canal local : socket unix {plan['uds']} "
            f"(repertoire {directory} en {oct(plan['directory_mode'])})",
            flush=True,
        )
    else:
        published = local_channel.publish_loopback_token()
        internal_config = uvicorn.Config(
            local_channel.internal_app,
            host=plan["host"],
            port=plan["port"],
            log_level=LOG_LEVEL,
        )
        print(
            f"[serve] canal local : loopback {plan['host']}:{plan['port']} "
            f"(jeton éphémère dans {published}) — mode développement",
            flush=True,
        )
    internal = uvicorn.Server(internal_config)

    print(f"[serve] API publique : {PUBLIC_HOST}:{PUBLIC_PORT}", flush=True)
    await asyncio.gather(public.serve(), internal.serve(), *tasks)
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(run()))
