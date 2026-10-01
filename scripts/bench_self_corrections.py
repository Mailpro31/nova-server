"""Banc des reprises, sur le modèle de CE serveur.

Rejoue des dictées où l'élève se corrige à voix haute, passe chacune dans
`resolve_self_corrections` (le premier passage de /api/reformulate) et vérifie
que seule la version retenue reste. Six dictées ne ressemblent à aucun des
exemples envoyés au modèle : elles disent si le passage généralise.

À lancer sur le serveur, dans le conteneur de l'API :

    docker compose exec api python scripts/bench_self_corrections.py

Mesuré le 01/10 sur Qwen3-4B (modèle local de Nova) : 19/20 après Style.
Ce banc mesure le passage seul, sur le modèle réellement servi.
"""

import asyncio
import os
import re
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import main  # noqa: E402

# (dictée, à garder, à écarter, (avant, après) ou None)
CASES = [
    ("dire à Stéphanie que je dois aller chercher le chien faire les courses donc acheter "
     "des carottes en premier attends mais en premier des betteraves c'est le plus important "
     "après des carottes des patates puis je dois faire la piscine et appeler Lucas",
     ["betteraves", "carottes", "patates", "Lucas"], ["carottes en premier"],
     ("betteraves", "carottes")),
    ("on se voit mardi non attends mercredi à 14 heures", ["mercredi"], ["mardi"], None),
    ("envoie le dossier à Marc enfin non à Julie", ["Julie"], ["Marc"], None),
    ("le devoir est pour le 12 pardon le 15 octobre", ["15"], ["12"], None),
    ("rendez-vous en salle B204 euh non c'est la B206", ["B206"], ["B204"], None),
    ("j'ai eu 14 je veux dire 16 en maths", ["16"], ["14"], None),
    ("il faut acheter du lait oublie le lait prends plutôt du jus d'orange",
     ["jus d'orange"], ["lait"], None),
    ("je prends le train de 8 heures non le bus de 8 heures", ["bus"], ["train"], None),
    ("Paris est la capitale de l'Italie non pardon de la France", ["France"], ["Italie"], None),
    ("on achète trois cahiers plutôt quatre cahiers", ["quatre"], ["trois"], None),
    ("mets le titre en rouge attends en bleu", ["bleu"], ["rouge"], None),
    ("je ne viendrai pas demain non vraiment pas", ["demain", "pas"], [], None),
    ("appelle Thomas non Thomas et Léa", ["Thomas", "Léa"], [], None),
    ("le prof a dit non à la sortie de jeudi", ["non", "jeudi"], [], None),
    # Inédites : aucun exemple ne leur ressemble.
    ("le rendez-vous chez le dentiste c'est à 9 heures non 10 heures", ["10"], ["9"], None),
    ("je vais acheter un vélo bleu euh non rouge", ["rouge"], ["bleu"], None),
    ("on révise l'histoire ce soir enfin plutôt la géographie", ["géographie"], ["histoire"], None),
    ("envoie-moi le fichier lundi oublie lundi envoie-le demain", ["demain"], ["lundi"], None),
    ("j'habite au 12 rue Victor Hugo pardon au 21", ["21"], ["12"], None),
    ("le cours commence à 8 heures et finit à midi non sans pause", ["8", "midi"], [], None),
]


def norm(text: str) -> str:
    return text.lower().replace("’", "'")


def judge(out: str, keep, drop, order) -> bool:
    text = norm(out)
    if not all(norm(word) in text for word in keep):
        return False
    if any(re.search(r"(?<!\w)" + re.escape(norm(word)) + r"(?!\w)", text) for word in drop):
        return False
    if order:
        first, then = text.find(norm(order[0])), text.find(norm(order[1]))
        return first != -1 and then != -1 and first < then
    return True


async def run() -> int:
    good, timings = 0, []
    for dictated, keep, drop, order in CASES:
        started = time.monotonic()
        resolved = await main.resolve_self_corrections(dictated)
        timings.append(time.monotonic() - started)
        ok = judge(resolved, keep, drop, order)
        good += ok
        print(("OK  " if ok else "NON ") + resolved)
    timings.sort()
    print(f"\n{good}/{len(CASES)} reprises justes ; "
          f"temps ajouté médian {timings[len(timings) // 2]:.2f} s, max {timings[-1]:.2f} s")
    return 0 if good == len(CASES) else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(run()))
