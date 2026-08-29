"""Santé, métriques, journaux et en-têtes — sans jamais exporter de contenu.

## La règle qui gouverne tout ce module

Le Control Plane ne contient aucun contenu de travail : ni audio, ni
transcription, ni prompt, ni presse-papiers. L'observabilité ne doit pas être
la porte par laquelle ce principe se perd. Rien de ce qui sort d'ici ne porte
de texte utilisateur, de secret, ni d'identifiant à cardinalité non bornée.

C'est aussi une contrainte technique, pas seulement morale : une métrique
étiquetée par `user_id` ou `organization_id` fabrique une série temporelle par
identifiant. Quelques milliers d'utilisateurs suffisent à faire tomber le
serveur de métriques — et à publier la liste des identifiants au passage.

## Séparation avec le journal d'audit

`admin_audit_log` répond à « qui a fait quoi dans cette organisation ». Les
journaux techniques d'ici répondent à « qu'a fait le processus ». Les deux ne
se remplacent pas : un journal technique tourne et se purge, un journal d'audit
doit rester.
"""

from __future__ import annotations

import json
import logging
import re
import sys
import threading
import time
import uuid
from collections import Counter
from typing import Any, Callable


# ------------------------------------------------------------- corrélation

REQUEST_ID_HEADER = "X-Request-ID"

# Un identifiant fourni par le client est accepté seulement s'il est
# manifestement un identifiant : jeu de caractères restreint, longueur bornée.
# Sans cela, il traverserait les journaux et les en-têtes de réponse comme un
# canal d'injection commode.
_REQUEST_ID = re.compile(r"^[A-Za-z0-9._-]{8,64}$")

_CURRENT_REQUEST_ID: threading.local = threading.local()


def accept_request_id(raw: str | None) -> str:
    """L'identifiant du client s'il est acceptable, sinon un nouveau.

    Un identifiant de corrélation **n'accorde aucune confiance** : il ne
    sélectionne pas de tenant, n'authentifie personne, et n'ouvre aucun accès.
    Il sert uniquement à relier des lignes de journal entre elles.
    """
    if raw and _REQUEST_ID.match(raw.strip()):
        return raw.strip()
    return uuid.uuid4().hex


def current_request_id() -> str | None:
    return getattr(_CURRENT_REQUEST_ID, "value", None)


def set_request_id(value: str | None) -> None:
    _CURRENT_REQUEST_ID.value = value


# --------------------------------------------------------------- journaux

# Ce qui ne doit jamais atteindre un journal, quel que soit le chemin qui l'y
# amènerait. La liste est courte à dessein : elle nomme les champs, et le
# formateur retire la valeur plutôt que de la tronquer.
FORBIDDEN_FIELDS = frozenset(
    {
        "token", "access_token", "refresh_token", "id_token", "credential",
        "secret", "client_secret", "password", "authorization", "code",
        "prompt", "transcript", "text", "content", "clipboard", "audio",
        "email", "message",
    }
)


class StructuredFormatter(logging.Formatter):
    """Une ligne JSON par événement, avec l'identifiant de requête."""

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "timestamp": time.time(),
            "level": record.levelname.lower(),
            "event": record.getMessage(),
            "logger": record.name,
        }
        request_id = getattr(record, "request_id", None) or current_request_id()
        if request_id:
            payload["request_id"] = request_id
        for key, value in getattr(record, "fields", {}).items():
            if key.lower() in FORBIDDEN_FIELDS:
                # Le champ est nommé, sa valeur ne l'est pas : un opérateur
                # voit qu'il y avait quelque chose, sans que la chose sorte.
                payload[key] = "<redacted>"
            else:
                payload[key] = value
        return json.dumps(payload, default=str, sort_keys=True)


def configure_logging(level: str = "INFO", stream: Any = None) -> logging.Logger:
    """Installe le journal structuré du service. Idempotent."""
    logger = logging.getLogger("nova")
    logger.setLevel(getattr(logging, level.upper(), logging.INFO))
    logger.propagate = False
    for existing in list(logger.handlers):
        logger.removeHandler(existing)
    handler = logging.StreamHandler(stream or sys.stdout)
    handler.setFormatter(StructuredFormatter())
    logger.addHandler(handler)
    return logger


def log(event: str, level: str = "info", **fields: Any) -> None:
    """Journalise un événement. Les champs interdits sont caviardés."""
    logger = logging.getLogger("nova")
    logger.log(
        getattr(logging, level.upper(), logging.INFO),
        event,
        extra={"fields": fields, "request_id": current_request_id()},
    )


# --------------------------------------------------------------- métriques

# Classes de routes. Une étiquette par classe, jamais par chemin : `/users/{id}`
# doit rester une seule série, sinon chaque identifiant en crée une.
_ROUTE_CLASSES = (
    (re.compile(r"^/scim/"), "scim"),
    (re.compile(r"^/api/control/organizations/[^/]+/deployment"), "deployment"),
    (re.compile(r"^/api/control/organizations/[^/]+/provisioning"), "provisioning"),
    (re.compile(r"^/api/control/connectors"), "connector"),
    (re.compile(r"^/api/control/"), "control"),
    (re.compile(r"^/api/admin/"), "admin"),
    (re.compile(r"^/api/auth/"), "auth"),
    (re.compile(r"^/api/deployment/"), "deployment"),
    (re.compile(r"^/api/(health|readyz|metrics)"), "operational"),
    (re.compile(r"^/api/"), "api"),
)


def route_class(path: str) -> str:
    """La classe d'une route. Bornée, et sans identifiant."""
    for pattern, label in _ROUTE_CLASSES:
        if pattern.match(path or ""):
            return label
    return "other"


class Metrics:
    """Compteurs et histogrammes, au format d'exposition Prometheus.

    Écrit à la main plutôt qu'avec une bibliothèque : ce qui est mesuré tient
    en cinq séries, et une dépendance de plus aurait coûté plus que le code
    qu'elle remplace.
    """

    _BUCKETS = (0.005, 0.025, 0.1, 0.5, 1.0, 2.5, 10.0)

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._requests: Counter = Counter()
        self._errors: Counter = Counter()
        self._latency_sum: dict[str, float] = {}
        self._latency_count: dict[str, int] = {}
        self._latency_buckets: dict[tuple[str, float], int] = {}
        self._events: Counter = Counter()

    def observe_request(self, route: str, status: int, seconds: float) -> None:
        family = f"{status // 100}xx"
        with self._lock:
            self._requests[(route, family)] += 1
            if status >= 500:
                self._errors[route] += 1
            self._latency_sum[route] = self._latency_sum.get(route, 0.0) + seconds
            self._latency_count[route] = self._latency_count.get(route, 0) + 1
            for bucket in self._BUCKETS:
                if seconds <= bucket:
                    key = (route, bucket)
                    self._latency_buckets[key] = self._latency_buckets.get(key, 0) + 1

    def count(self, event: str, outcome: str = "ok") -> None:
        """Un compteur d'événement métier. `event` et `outcome` sont bornés."""
        with self._lock:
            self._events[(event, outcome)] += 1

    def render(self, gauges: dict[str, float] | None = None) -> str:
        lines: list[str] = []
        with self._lock:
            lines.append("# HELP nova_http_requests_total HTTP requests by route class.")
            lines.append("# TYPE nova_http_requests_total counter")
            for (route, family), value in sorted(self._requests.items()):
                lines.append(
                    f'nova_http_requests_total{{route="{route}",status="{family}"}} {value}'
                )

            lines.append("# HELP nova_http_errors_total Server errors by route class.")
            lines.append("# TYPE nova_http_errors_total counter")
            for route, value in sorted(self._errors.items()):
                lines.append(f'nova_http_errors_total{{route="{route}"}} {value}')

            lines.append("# HELP nova_http_latency_seconds Request latency.")
            lines.append("# TYPE nova_http_latency_seconds histogram")
            for route in sorted(self._latency_count):
                cumulative = 0
                for bucket in self._BUCKETS:
                    cumulative = self._latency_buckets.get((route, bucket), 0)
                    lines.append(
                        f'nova_http_latency_seconds_bucket{{route="{route}",'
                        f'le="{bucket}"}} {cumulative}'
                    )
                total = self._latency_count[route]
                lines.append(
                    f'nova_http_latency_seconds_bucket{{route="{route}",le="+Inf"}} {total}'
                )
                lines.append(
                    f'nova_http_latency_seconds_sum{{route="{route}"}} '
                    f"{self._latency_sum[route]:.6f}"
                )
                lines.append(
                    f'nova_http_latency_seconds_count{{route="{route}"}} {total}'
                )

            lines.append("# HELP nova_events_total Operational events.")
            lines.append("# TYPE nova_events_total counter")
            for (event, outcome), value in sorted(self._events.items()):
                lines.append(
                    f'nova_events_total{{event="{event}",outcome="{outcome}"}} {value}'
                )

        for name, value in sorted((gauges or {}).items()):
            lines.append(f"# TYPE nova_{name} gauge")
            lines.append(f"nova_{name} {value}")
        return "\n".join(lines) + "\n"

    def reset(self) -> None:
        with self._lock:
            self._requests.clear()
            self._errors.clear()
            self._latency_sum.clear()
            self._latency_count.clear()
            self._latency_buckets.clear()
            self._events.clear()


METRICS = Metrics()


# ------------------------------------------------------- en-têtes de sécurité

# `frame-ancestors 'none'` remplace `X-Frame-Options`, que les navigateurs
# modernes n'ont plus besoin de lire. `default-src 'none'` convient à une API
# qui ne sert aucune page : elle n'a besoin de charger ni script, ni style, ni
# image. La console d'administration est servie ailleurs et porte sa propre
# politique — durcir ici ne la contraint pas.
API_CSP = "default-src 'none'; frame-ancestors 'none'; base-uri 'none'; form-action 'none'"

SECURITY_HEADERS = {
    "X-Content-Type-Options": "nosniff",
    "Referrer-Policy": "no-referrer",
    "Content-Security-Policy": API_CSP,
    "Cross-Origin-Resource-Policy": "same-site",
}

# HSTS n'a de sens que servi en HTTPS : l'envoyer en clair n'apporte rien et
# peut verrouiller un environnement de développement sur un port réutilisé.
HSTS = "max-age=31536000; includeSubDomains"


def security_headers(*, production: bool) -> dict[str, str]:
    headers = dict(SECURITY_HEADERS)
    if production:
        headers["Strict-Transport-Security"] = HSTS
    return headers


# ------------------------------------------------------------ proxy de confiance


def client_address(
    peer: str | None, forwarded_for: str | None, trusted_proxies: frozenset[str]
) -> str:
    """L'adresse du client, en ne croyant l'en-tête que d'un proxy déclaré.

    `X-Forwarded-For` est écrit par le client autant que par le proxy. Le croire
    sans condition rendrait toute limite par adresse contournable en une ligne.
    On ne le lit donc que si la connexion vient d'un proxy explicitement
    configuré — et on prend le **dernier** élément, celui que ce proxy a ajouté,
    plutôt que le premier, que le client contrôle.
    """
    direct = (peer or "").strip()
    if not direct or direct not in trusted_proxies or not forwarded_for:
        return direct or "unknown"
    hops = [hop.strip() for hop in forwarded_for.split(",") if hop.strip()]
    return hops[-1] if hops else direct


def trusted_proxies_from_environment(raw: str) -> frozenset[str]:
    """Liste explicite d'adresses. Ni joker, ni plage : cette phase n'en a pas besoin."""
    return frozenset(entry.strip() for entry in raw.split(",") if entry.strip())


# ----------------------------------------------------------------- readiness


def readiness(checks: dict[str, Callable[[], bool]]) -> dict[str, Any]:
    """Agrège des vérifications en réponse **sûre**.

    Ce qui sort : un booléen global et un état par composant. Ce qui ne sort
    pas : URL de base, URL Redis, nom d'hôte, chemin de secret, identifiant de
    tenant. Une sonde de disponibilité est souvent la première chose qu'on
    expose sans authentification.
    """
    components: dict[str, str] = {}
    for name, probe in checks.items():
        try:
            components[name] = "ok" if probe() else "unavailable"
        except Exception:
            components[name] = "error"
    return {
        "ready": all(state == "ok" for state in components.values()),
        "components": components,
    }
