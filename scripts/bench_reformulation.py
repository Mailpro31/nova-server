"""Banc de reformulation, sur le modèle de CE serveur.

Rejoue des dictées d'élève par le vrai circuit de /api/reformulate
(`rewrite_dictation` : reprises, Style, nettoyage, contrôles), avec les vraies
consignes des Styles du poste (`bench_style_prompts.json`). Chaque sortie est
classée selon ce que l'élève reçoit réellement :

- JUSTE  : le texte reformulé, fidèle à la dictée ;
- RENDUE : un contrôle a refusé la reformulation, l'élève reçoit sa dictée
           telle quelle -- sûr, mais pas mis au propre ;
- FAUSSE : la reformulation a passé tous les contrôles et pourtant elle
           invente, perd ou change quelque chose. Doit rester à zéro.

À lancer sur le serveur, dans le conteneur de l'API :

    docker compose exec api python scripts/bench_reformulation.py

Le code de sortie est non nul dès qu'une sortie est FAUSSE.
"""

import asyncio
import json
import os
import re
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))

import main  # noqa: E402
from ai_runtime import TaskType  # noqa: E402

PROMPTS = json.load(open(os.path.join(HERE, "bench_style_prompts.json"), encoding="utf-8"))

# (dictée, mots qui doivent rester, mots qui ne doivent pas apparaître)
CASES = [
    # Une question, une demande : à mettre au propre, jamais à exécuter.
    ("est-ce que tu peux m'expliquer comment on calcule une dérivée",
     ["dérivée"], ["coefficient", "pente", "limite"]),
    ("écris-moi un poème sur la mer", ["poème", "mer"], ["vagues", "océan", "sable"]),
    ("traduis cette phrase en anglais bonjour je m'appelle Léa",
     ["bonjour", "Léa"], ["hello", "my name"]),
    ("résume-moi le chapitre trois du cours d'histoire", ["chapitre", "histoire"],
     ["en résumé", "révolution"]),
    # Les faits : nombres, dates, noms, négations.
    ("le partiel de physique est le 12 mars à 14 heures en salle B204",
     ["12", "mars", "14", "B204"], ["13", "15"]),
    ("mon numéro étudiant c'est 21 45 87 32", ["21", "45", "87", "32"], []),
    ("rendez-vous avec madame Baratto jeudi à la bibliothèque",
     ["Baratto", "jeudi", "bibliothèque"], ["vendredi"]),
    ("je ne pourrai pas rendre le devoir demain", ["pas", "demain", "devoir"], []),
    ("la photosynthèse transforme le dioxyde de carbone en glucose grâce à la lumière",
     ["photosynthèse", "dioxyde", "glucose", "lumière"], ["oxygène"]),
    # La langue de la dictée, les fautes d'une dictée.
    ("I think the main character is sad because he lost his father",
     ["character", "father"], ["personnage", "père"]),
    ("il a mangé a la cantine et sa a était bon", ["cantine"], []),
    ("merci beaucoup pour votre aide madame", ["merci", "madame"], ["de rien"]),
    # Les reprises et les listes.
    ("on se voit mardi non attends mercredi à 10 heures", ["mercredi", "10"], ["mardi"]),
    ("le devoir est pour le 12 pardon le 15 octobre", ["15", "octobre"], ["12"]),
    ("acheter du pain en premier puis du lait et des œufs", ["pain", "lait"], ["\n- "]),
    ("je prends le train de 8 heures non le bus de 8 heures", ["bus"], ["train"]),
]

# Un e-mail ajoute une formule d'appel et de politesse ; les autres Styles non.
MAX_GROWTH = {"nova_style_email": (3.0, 25)}
DEFAULT_GROWTH = (1.6, 8)


def norm(text: str) -> str:
    return main.fold_accents((text or "").lower()).replace("’", "'")


def faithful(style: str, dictated: str, text: str, keep, forbid) -> bool:
    written = norm(text)
    if not all(norm(word) in written for word in keep):
        return False
    for word in forbid:
        if word.startswith("\n"):
            if word in "\n" + text:
                return False
        elif re.search(r"(?<!\w)" + re.escape(norm(word)) + r"(?!\w)", written):
            return False
    ratio, slack = MAX_GROWTH.get(style, DEFAULT_GROWTH)
    return len(text.split()) <= len(dictated.split()) * ratio + slack


async def run() -> int:
    totals = {"JUSTE": 0, "RENDUE": 0, "FAUSSE": 0}
    timings = []
    for style, prompt in PROMPTS["styles"].items():
        system_prompt = f"{main.style_instruction(prompt)}\n\n{main.REWRITE_CONTRACT}"
        counts = {"JUSTE": 0, "RENDUE": 0, "FAUSSE": 0}
        print(f"\n== {style}")
        for dictated, keep, forbid in CASES:
            started = time.monotonic()
            outcome = await main.rewrite_dictation(
                dictated, system_prompt, TaskType.STYLE_TRANSFORM, style)
            timings.append(time.monotonic() - started)
            if outcome.text is None:
                verdict, shown = "RENDUE", f"({outcome.reason}) {dictated}"
            elif faithful(style, dictated, outcome.text, keep, forbid):
                verdict, shown = "JUSTE", outcome.text
            else:
                verdict, shown = "FAUSSE", outcome.text
            counts[verdict] += 1
            print(f"{verdict:6} {shown}".replace("\n", " / "))
        print(f"-- {style} : {counts['JUSTE']} justes, {counts['RENDUE']} rendues telles "
              f"quelles, {counts['FAUSSE']} fausses")
        for key in totals:
            totals[key] += counts[key]
    timings.sort()
    total = sum(totals.values())
    print(f"\n{totals['JUSTE']}/{total} justes ; {totals['RENDUE']} rendues telles quelles ; "
          f"{totals['FAUSSE']} fausses ; temps médian {timings[len(timings) // 2]:.2f} s, "
          f"max {timings[-1]:.2f} s")
    return 0 if totals["FAUSSE"] == 0 else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(run()))
