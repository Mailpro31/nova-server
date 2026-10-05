"""Banc de transcription, sur le moteur de CE serveur.

Rejoue des dictées enregistrées par le vrai moteur de transcription
(`ORGANIZATION_AI_ROUTER.transcribe`), avec la langue devinée puis imposée,
et compare chaque sortie au texte que l'élève voulait dire (taux d'erreur de
mots, WER).

Les dictées sont un dossier de `.wav` avec un `attendu.json` :

    {"handy-1791153352.wav": "Définition, l'hypoténuse est un triangle rectangle."}

Le dossier `recordings` du poste contient les `.wav` ; le texte attendu est
celui que l'élève a corrigé dans l'historique.

    docker compose exec api python scripts/bench_transcription.py /data/bench-dictees fr

Mesure locale du 05/10 (5 dictées, voix cassée, ventilateurs, large-v3-turbo
sur CPU) : 0,47 de WER en langue devinée, 0,27 en français imposé.
"""

import asyncio
import json
import os
import re
import sys
import time
import unicodedata

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))

import main  # noqa: E402
from ai_runtime import TaskType  # noqa: E402


def words(text: str) -> list[str]:
    text = unicodedata.normalize("NFC", (text or "").lower().replace("’", "'"))
    return re.findall(r"[^\W_]+", text)


def wer(reference: str, hypothesis: str) -> float:
    ref, hyp = words(reference), words(hypothesis)
    row = list(range(len(hyp) + 1))
    for i, ref_word in enumerate(ref, 1):
        previous, row[0] = row[0], i
        for j, hyp_word in enumerate(hyp, 1):
            current = min(row[j] + 1, row[j - 1] + 1, previous + (ref_word != hyp_word))
            previous, row[j] = row[j], current
    return row[len(hyp)] / max(len(ref), 1)


async def measure(folder: str, language: str) -> dict[str, float]:
    expected = json.load(open(os.path.join(folder, "attendu.json"), encoding="utf-8"))
    totals: dict[str, float] = {}
    for label, forced in (("langue devinée", None), (f"langue imposée ({language})", language)):
        print(f"\n== {label}")
        scores = []
        for name, reference in expected.items():
            audio = open(os.path.join(folder, name), "rb").read()
            started = time.monotonic()
            result = await main.ORGANIZATION_AI_ROUTER.transcribe(
                TaskType.TRANSCRIPTION_REALTIME, audio, name, language=forced)
            text = "" if main.only_hesitations(result.text) else result.text
            score = wer(reference, text)
            scores.append(score)
            print(f"  WER {score:4.2f}  {time.monotonic() - started:4.1f} s  « {text} »")
        totals[label] = sum(scores) / max(len(scores), 1)
    print("\n== WER moyen")
    for label, score in totals.items():
        print(f"  {score:4.2f}  {label}")
    return totals


if __name__ == "__main__":
    if len(sys.argv) < 2:
        raise SystemExit("usage : bench_transcription.py <dossier> [langue]")
    asyncio.run(measure(sys.argv[1], sys.argv[2] if len(sys.argv) > 2 else "fr"))
