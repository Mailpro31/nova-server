"""Rapport d'usage annuel d'une école, en PDF (preuve de valeur au renouvellement).

Usage :
    GRAFANA_PROM_URL=https://prometheus-prod-XX.grafana.net/api/prom \
    GRAFANA_PROM_USER=<identifiant de l'instance> \
    GRAFANA_PROM_TOKEN_FILE=<fichier du jeton, lecture seule> \
    python scripts/annual_report.py --school <slug> --from 2025-11-01 --to 2026-10-31 --out rapport.pdf

L'URL, l'utilisateur et le jeton ne viennent que de l'environnement : jamais
d'argument, jamais affichés. Les données sont celles que l'agent du panel
envoie (séries `nova_*`, étiquette `school`) : des totaux d'école, aucun nom,
aucune adresse. Une série absente donne « non mesuré ». Un effectif de moins de
5 personnes n'est jamais chiffré. Dépendance : `pip install -r scripts/requirements-report.txt`.
"""

from __future__ import annotations

import argparse
import datetime as dt
import os
import pathlib
import re
import sys
import time

import httpx

MIN_GROUP = 5  # en dessous, aucun chiffre
MAX_POINTS = 10000  # Prometheus refuse plus de 11 000 points par série
NOT_MEASURED = "non mesuré"
SLUG = re.compile(r"^[a-z0-9][a-z0-9_-]{0,63}$")
EMAIL = re.compile(r"[^\s@]+@[^\s@]+")
STAGE_NAMES = {"transcription": "Dictées transcrites", "relecture": "Relectures audio",
               "mise_en_forme": "Mises en forme (Styles)", "commande": "Commandes vocales"}


def prom_client():
    """Un `query(promql, start, end, step)` authentifié ; renvoie [(étiquettes, [(ts, valeur)])]."""
    base = os.environ["GRAFANA_PROM_URL"].rstrip("/")
    auth = (os.environ["GRAFANA_PROM_USER"],
            pathlib.Path(os.environ["GRAFANA_PROM_TOKEN_FILE"]).read_text().strip())

    def query(promql, start, end, step):
        response = httpx.get(f"{base}/api/v1/query_range", auth=auth, timeout=60,
                             params={"query": promql, "start": start, "end": end, "step": step})
        response.raise_for_status()
        return [(r["metric"], [(float(t), float(v)) for t, v in r["values"]])
                for r in response.json()["data"]["result"]]
    return query


def month_of(ts: float) -> str:
    return dt.datetime.fromtimestamp(ts, dt.timezone.utc).strftime("%Y-%m")


def months_between(start: dt.date, end: dt.date) -> list[str]:
    months, cursor = [], start.replace(day=1)
    while cursor <= end:
        months.append(cursor.strftime("%Y-%m"))
        cursor = (cursor + dt.timedelta(days=32)).replace(day=1)
    return months


def per_month(points, how) -> dict[str, float]:
    grouped: dict[str, list[float]] = {}
    for ts, value in sorted(points):
        grouped.setdefault(month_of(ts), []).append(value)
    return {month: how(values) for month, values in grouped.items()}


def collect(school: str, start: dt.date, end: dt.date, query, now: float | None = None) -> dict:
    """Les chiffres du rapport. Toute série absente reste `None`."""
    if not SLUG.match(school):
        raise ValueError("identifiant d'école invalide")
    t0 = dt.datetime.combine(start, dt.time(), dt.timezone.utc).timestamp()
    t1 = dt.datetime.combine(end + dt.timedelta(days=1), dt.time(), dt.timezone.utc).timestamp() - 1
    hours = int((t1 - t0) // 3600) + 1
    if hours > MAX_POINTS:
        raise ValueError("période trop longue pour un pas d'une heure (13 mois au plus)")
    s = f'school="{school}"'
    now = time.time() if now is None else now

    def span(step):
        # Points calés sur min(fin, maintenant) : sinon le dernier jour s'évalue à minuit
        # et rate tout ce qui a été mesuré depuis.
        width = {"1d": 86400, "1h": 3600}[step]
        end = min(t1, now)
        return end - int((end - t0) // width) * width, end

    def first(promql, step="1d"):
        result = query(promql, *span(step), step)
        return result[0][1] if result else None

    def last(promql):
        points = first(promql)
        return sorted(points)[-1][1] if points else None

    # Chaque pas regarde toute sa fenêtre (1 j ou 1 h) : une évaluation instantanée
    # ne remonte que de 5 minutes et rate une série échantillonnée à la minute.
    members = first(f'last_over_time(nova_members{{{s},state="active"}}[1d])')
    active = first(f'max_over_time(nova_active_users{{{s},period="month"}}[1d])')
    stages = query(f"sum by (stage) (increase(nova_stage_seconds_count{{{s}}}[1d]))", *span("1d"), "1d")
    uptime = first(f"count_over_time(nova_uptime_seconds{{{s}}}[1h])", "1h")
    envelope = {kind: last(f'last_over_time(nova_ai_envelope_eur{{{s},kind="{kind}"}}[1d])') for kind in ("annual", "spent")}
    transcription = next((p for m, p in stages if m.get("stage") == "transcription"), None)
    return {
        "school": school, "start": start, "end": end, "months": months_between(start, end),
        "members": per_month(members, lambda v: v[-1]) if members else None,
        "active": per_month(active, max) if active else None,
        "dictations": per_month(transcription, sum) if transcription else None,
        "stages": {m.get("stage", "autre"): sum(v for _, v in p) for m, p in stages} if stages else None,
        "envelope": envelope if any(v is not None for v in envelope.values()) else None,
        "envelope_percent": last(f"last_over_time(nova_ai_envelope_percent{{{s}}}[1d])"),
        # Part des heures avec une mesure, du premier point mesuré jusqu'à maintenant
        # (ou la fin de la période) : avant le début des mesures, rien n'est dû.
        "availability": availability(uptime, t1, now) if uptime else None,
        "measured_since": dt.datetime.fromtimestamp(min(t for t, _ in uptime), dt.timezone.utc).date() if uptime else None,
    }


def availability(points, end: float, now: float) -> float:
    hours = {int(t // 3600) for t, _ in points}
    return min(1.0, len(hours) / (int(min(end, now) // 3600) - min(hours) + 1))


def headcount_ok(data: dict, month: str) -> bool:
    """Un mois n'est chiffré que si l'effectif est connu et d'au moins 5 personnes."""
    known = [series[month] for series in (data["members"], data["active"]) if series and month in series]
    return bool(known) and min(known) >= MIN_GROUP


def pdf_text(text: str) -> str:
    """Polices PDF de base : Latin-1. Garde-fou : jamais d'adresse e-mail."""
    if EMAIL.search(text):
        raise ValueError("une adresse e-mail ne doit jamais figurer dans le rapport")
    text = text.replace("’", "'").replace("–", "-").replace("œ", "oe").replace("€", "EUR")
    return text.encode("latin-1", "replace").decode("latin-1")


def render_pdf(data: dict, out: str) -> None:
    from fpdf import FPDF

    pdf = FPDF()
    pdf.set_auto_page_break(True, 15)
    pdf.add_page()
    months = data["months"]

    def write(size, text, style="", gap=0):
        pdf.set_font("Helvetica", style, size)
        pdf.multi_cell(0, size * 0.5 + 1.5, pdf_text(text), new_x="LMARGIN", new_y="NEXT")
        pdf.ln(gap)

    def bars(title, series, own_headcount=False):
        """Histogramme mensuel d'une série {mois: valeur}. Un mois sans point est « non mesuré »,
        un mois d'effectif inférieur à 5 est « non chiffré »."""
        write(11, title, "B")
        if series is None:
            return write(10, NOT_MEASURED, gap=3)
        shown = {m: series[m] for m in months if m in series and (series[m] >= MIN_GROUP if own_headcount else headcount_ok(data, m))}
        top = max(shown.values(), default=0) or 1
        y = pdf.get_y()
        for month in months:
            pdf.set_font("Helvetica", "", 8)
            pdf.set_xy(10, y)
            pdf.cell(18, 4.5, month)
            if month in shown:
                pdf.set_fill_color(40, 90, 160)
                pdf.rect(30, y + 0.5, max(0.5, 105 * shown[month] / top), 3.5, "F")
                pdf.set_xy(30 + 105 * shown[month] / top + 2, y)
                pdf.cell(25, 4.5, str(round(shown[month])))
            else:
                pdf.cell(60, 4.5, pdf_text(NOT_MEASURED if month not in series
                                           else f"non chiffré (moins de {MIN_GROUP} personnes)"))
            y += 5
        pdf.set_y(y + 3)

    write(18, "Rapport d'usage annuel de Nova", "B")
    write(11, f"École : {data['school']}\nPériode : du {data['start']:%d/%m/%Y} au {data['end']:%d/%m/%Y}", gap=2)
    write(9, "Totaux d'école uniquement, issus des mesures du serveur. Aucun nom, aucune adresse, "
             f"aucun chiffre pour un effectif de moins de {MIN_GROUP} personnes.", "I", gap=4)

    bars("Membres actifs en fin de mois", data["members"], own_headcount=True)
    bars("Personnes ayant utilisé Nova dans le mois", data["active"], own_headcount=True)
    bars("Dictées par mois (estimation d'après les compteurs du serveur)", data["dictations"])
    write(11, "Heures d'audio transcrites", "B")
    write(10, f"{NOT_MEASURED} : le serveur ne compte pas la durée de l'audio.", gap=3)

    write(11, "Fonctions les plus utilisées (étapes de traitement, total de la période)", "B")
    if data["stages"] and any(headcount_ok(data, m) for m in months):
        for stage, count in sorted(data["stages"].items(), key=lambda item: -item[1]):
            write(10, f"- {STAGE_NAMES.get(stage, 'Autres traitements')} : {round(count)}")
        pdf.ln(3)
    else:
        write(10, NOT_MEASURED, gap=3)

    write(11, "Enveloppe d'IA", "B")
    env = data["envelope"]
    if env and env.get("annual"):
        pct = data["envelope_percent"]
        write(10, f"Consommé : {env.get('spent') or 0:.2f} € sur {env['annual']:.2f} € prévus au contrat"
                  + (f" ({pct:.0f} % de l'enveloppe ouverte)." if pct is not None else "."), gap=3)
    else:
        write(10, NOT_MEASURED, gap=3)

    write(11, "Disponibilité du serveur", "B")
    avail = data["availability"]
    since = data["measured_since"]
    write(10, NOT_MEASURED if avail is None
          else f"{avail * 100:.1f} % des heures avec un serveur joignable"
               + (f", depuis le {since:%d/%m/%Y}, début des mesures." if since > data["start"] else "."))
    pdf.output(out)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--school", required=True)
    parser.add_argument("--from", dest="start", required=True, type=dt.date.fromisoformat)
    parser.add_argument("--to", dest="end", required=True, type=dt.date.fromisoformat)
    parser.add_argument("--out", required=True)
    args = parser.parse_args()
    if args.end < args.start:
        sys.exit("--to doit suivre --from")
    render_pdf(collect(args.school, args.start, args.end, prom_client()), args.out)
    print("Rapport écrit :", args.out)


if __name__ == "__main__":
    main()
