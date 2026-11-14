"""Enveloppe commune d'IA (2.3, décision de Sash du 26/10).

Le montant est en euros, fixé au contrat et porté par la licence signée
(`ai_envelope_eur`) : une recharge est une nouvelle licence, jamais un chiffre
que l'école tape elle-même. Il se découpe par mois de licence ; ce qui n'est pas
dépensé reste pour les mois suivants. Seul le moteur externe coûte : le moteur
local ne compte pas.

Sans prix renseignés par l'exploitant, rien n'est compté plutôt qu'un chiffre
inventé, comme `estimated_cost_usd`.
"""

import datetime
import os

# Un élève ne dépense pas plus que cette part du mois : au-delà, il passe sur
# le moteur local, et les autres gardent l'enveloppe.
# shortcut: part fixe, à rendre réglable par l'école si un pilote la trouve trop basse.
STUDENT_MONTH_SHARE = 0.2
# Ce qui reste en dessous de cette marge compte comme vide : deux appels en vol
# ne font jamais dépasser le contrat (un appel coûte moins d'un centime).
MARGIN_EUR = 0.01
ALERT_PERCENTS = (80, 100)


def _price(name: str) -> float:
    try:
        return max(0.0, float(os.getenv(name, "") or 0))
    except ValueError:
        return 0.0


def priced() -> bool:
    return any(_price(name) > 0 for name in (
        "NOVA_AI_EUR_PER_MTOK_INPUT", "NOVA_AI_EUR_PER_MTOK_OUTPUT", "NOVA_AI_REFINE_EUR_PER_CALL"))


def call_cost(usage: dict) -> float:
    """Le coût d'un appel au moteur texte externe, d'après ses jetons."""
    return (int(usage.get("prompt_tokens", 0)) * _price("NOVA_AI_EUR_PER_MTOK_INPUT")
            + int(usage.get("completion_tokens", 0)) * _price("NOVA_AI_EUR_PER_MTOK_OUTPUT")) / 1e6


def refine_cost() -> float:
    """Le coût d'une relecture audio : un prix par appel, l'API ne rend pas ses jetons ici."""
    return _price("NOVA_AI_REFINE_EUR_PER_CALL")


try:  # le mois des écoles est celui de Paris, pas celui de l'horloge du conteneur (UTC)
    from zoneinfo import ZoneInfo
    _PARIS = ZoneInfo("Europe/Paris")
except Exception:  # base des fuseaux absente : UTC, comme avant
    _PARIS = datetime.timezone.utc


def month_of(stamp: float) -> str:
    return datetime.datetime.fromtimestamp(stamp, _PARIS).strftime("%Y-%m")


def _month_index(day: str) -> int:
    year, month = int(day[:4]), int(day[5:7])
    return year * 12 + month - 1


def _months(starts: str, ends: str) -> int:
    return max(1, _month_index(ends) - _month_index(starts) + 1)


def month_share(annual: float, starts: str, ends: str) -> float:
    return annual / _months(starts, ends)


def allowance(annual: float, starts: str, ends: str, now: float) -> float:
    """Ce qui est ouvert à `now` : les mois commencés, report compris."""
    opened = _month_index(month_of(now)) - _month_index(starts) + 1
    return month_share(annual, starts, ends) * max(0, min(opened, _months(starts, ends)))


def crossed(before: float, after: float, limit: float) -> list[int]:
    """Les seuils d'alerte que la dépense vient de franchir."""
    if limit <= 0:
        return []
    return [p for p in ALERT_PERCENTS if before < limit * p / 100 <= after]
