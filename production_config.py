"""Ce qu'une configuration de production doit prouver avant de servir.

## Pourquoi un validateur plutôt que des vérifications éparpillées

Une configuration dangereuse ne se manifeste presque jamais au démarrage. Elle
se manifeste le jour où quelqu'un exploite le secret par défaut resté en place,
ou le jour où quatre workers découvrent qu'ils comptaient chacun dans leur coin.
Entre les deux, le service a l'air de fonctionner — ce qui est précisément le
problème.

Ce module rassemble donc tout ce qui doit être vrai pour qu'un déploiement
puisse se dire « de production », et le vérifie **une fois, au démarrage**, en
refusant plutôt qu'en avertissant. Un avertissement dans un journal que
personne ne lit ne protège rien.

## Ce qu'il ne fait pas

Il ne devine pas l'environnement. `NOVA_ENV` le déclare — jamais un nom d'hôte,
qui change de sens d'un hébergeur à l'autre et rend un contrôle de sécurité
dépendant d'une convention de nommage.

Il ne s'exécute pas à l'import. Comme tout le reste depuis la Phase 38A, il
faut l'appeler.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass, field

import database
import secrets_provider


PRODUCTION = "production"
DEVELOPMENT = "development"
TEST = "test"


class ConfigurationRefused(RuntimeError):
    """Configuration refusée. Porte des codes, jamais une valeur."""

    def __init__(self, problems: list[str]) -> None:
        super().__init__("; ".join(problems))
        self.problems = problems


@dataclass
class Report:
    environment: str
    problems: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.problems

    def refuse(self, code: str) -> None:
        if code not in self.problems:
            self.problems.append(code)


def environment() -> str:
    return os.getenv("NOVA_ENV", PRODUCTION).strip().lower()


# --------------------------------------------------------------- valeurs faibles

# Valeurs qui trahissent une configuration de démonstration restée en place.
# La comparaison est faite sur la valeur **résolue**, en minuscules : un secret
# lu depuis un fichier monté peut très bien contenir « changeme ».
WEAK_VALUES = frozenset(
    {
        "changeme", "change-me", "changethis", "secret", "password", "test",
        "dev", "development", "placeholder", "example", "todo", "xxx",
        "default", "insecure", "nova", "admin", "1234", "12345678",
    }
)

MINIMUM_SECRET_LENGTH = 16


def _secret_problem(name: str, raw: str) -> str | None:
    """Ce qui cloche avec un secret, ou `None`.

    On valide la valeur résolue, jamais la référence : `env://X` peut désigner
    une variable qui vaut « changeme ».
    """
    if not raw.strip():
        return f"{name}_MISSING"
    try:
        resolved = secrets_provider.resolve_value(raw)
    except secrets_provider.SecretError as error:
        return f"{name}_UNRESOLVABLE_{error.code}"
    if not resolved:
        return f"{name}_MISSING"
    if resolved.strip().lower() in WEAK_VALUES:
        return f"{name}_WEAK"
    if len(resolved.strip()) < MINIMUM_SECRET_LENGTH:
        return f"{name}_TOO_SHORT"
    return None


# ------------------------------------------------------------------- origines

_LOCAL_HOST = re.compile(
    r"^(localhost|127\.\d+\.\d+\.\d+|\[?::1\]?|0\.0\.0\.0)(:\d+)?$", re.I
)


def _origin_problem(origin: str) -> str | None:
    """Une origine publique doit être joignable et chiffrée.

    C'est l'**origine publique** qui est jugée, pas l'adresse d'écoute : un
    service peut parfaitement écouter sur `127.0.0.1` derrière un reverse proxy.
    Ce qui est refusé, c'est d'annoncer au monde une adresse en clair ou une
    adresse que seul le serveur lui-même peut joindre.
    """
    value = origin.strip().rstrip("/")
    if not value:
        return None
    if value.startswith("http://"):
        return "ORIGIN_NOT_TLS"
    if not value.startswith("https://"):
        return "ORIGIN_SCHEME_INVALID"
    host = value[len("https://") :].split("/", 1)[0]
    if _LOCAL_HOST.match(host):
        return "ORIGIN_NOT_PUBLIC"
    return None


# ------------------------------------------------------------------- contrôles


def check(*, require_redis: bool = True) -> Report:
    """Tous les contrôles, sans court-circuit.

    On collecte au lieu de s'arrêter au premier problème : un opérateur qui
    corrige une configuration veut la liste, pas une découverte à chaque
    redémarrage.
    """
    report = Report(environment())
    if report.environment != PRODUCTION:
        return report

    # -- base de données
    url = os.getenv("NOVA_DATABASE_URL", "").strip()
    if not url:
        report.refuse("DATABASE_URL_REQUIRED_IN_PRODUCTION")
    else:
        try:
            if database.parse_url(url).dialect != database.POSTGRESQL:
                report.refuse("SQLITE_REFUSED_IN_PRODUCTION")
        except database.DatabaseError:
            report.refuse("DATABASE_URL_INVALID")

    # -- compteur partagé
    #
    # Sans lui, les routes fail-closed refusent tout dès le premier appel : le
    # service serait « démarré » et inutilisable. Mieux vaut le dire ici.
    if require_redis and not os.getenv("NOVA_REDIS_URL", "").strip():
        report.refuse("REDIS_URL_REQUIRED_IN_PRODUCTION")

    # -- origines annoncées
    for variable in ("NOVA_SERVICE_ENDPOINT", "NOVA_CONTROL_PLANE_ORIGIN"):
        problem = _origin_problem(os.getenv(variable, ""))
        if problem:
            report.refuse(f"{variable}_{problem}")

    for origin in os.getenv("NOVA_ADMIN_ORIGINS", "").split(","):
        problem = _origin_problem(origin)
        if problem:
            report.refuse(f"NOVA_ADMIN_ORIGINS_{problem}")

    # -- secrets, quand la fonctionnalité qui les emploie est activée
    #
    # Un secret n'est exigé que si son fournisseur est configuré : un
    # déploiement sans Google n'a aucune raison de porter un secret Google.
    conditional = (
        ("NOVA_GOOGLE_CLIENT_SECRET", "NOVA_GOOGLE_CLIENT_ID"),
        ("NOVA_OIDC_CLIENT_SECRET", "NOVA_OIDC_CLIENT_ID"),
        ("NOVA_SMTP_PASS", "NOVA_SMTP_USER"),
        ("NOVA_AI_RUNTIME_TOKEN", "NOVA_AI_RUNTIME_TOKEN"),
    )
    for secret_name, trigger in conditional:
        if not os.getenv(trigger, "").strip():
            continue
        problem = _secret_problem(secret_name, os.getenv(secret_name, ""))
        if problem:
            report.refuse(problem)

    # -- racines de confiance, quand la vérification est obligatoire
    if os.getenv("NOVA_CONNECTOR_RELEASE_MANIFEST", "").strip() and not os.getenv(
        "NOVA_CONNECTOR_RELEASE_PUBLIC_KEY", ""
    ).strip():
        report.refuse("CONNECTOR_RELEASE_TRUST_ROOT_MISSING")

    # -- assouplissements réservés au développement
    #
    # Chacun d'eux existe pour rendre un poste de développement praticable.
    # Aucun n'a de raison d'être en production, et chacun y ouvrirait un trou
    # précis : accepter du HTTP, sauter une vérification, faire confiance à un
    # émetteur privé.
    for flag in (
        "NOVA_CONNECTOR_ALLOW_INSECURE",
        "NOVA_CONNECTOR_RELEASE_ALLOW_INSECURE",
        "NOVA_DISCOVERY_ALLOW_INSECURE_ENDPOINT",
        "NOVA_ALLOW_LEGACY_DEPLOYMENT_TOKEN",
    ):
        if os.getenv(flag, "").strip().lower() in {"1", "true", "yes", "on"}:
            report.refuse(f"{flag}_FORBIDDEN_IN_PRODUCTION")

    # -- authentification héritée, retirée du code depuis les Phases 33 et 36
    for removed in ("NOVA_ADMIN_TOKEN", "NOVA_DEPLOYMENT_APPLY_TOKEN"):
        if os.getenv(removed, "").strip():
            report.refuse(f"{removed}_NO_LONGER_SUPPORTED")

    return report


def enforce(*, require_redis: bool = True) -> Report:
    """Applique les contrôles, ou refuse de démarrer."""
    report = check(require_redis=require_redis)
    if not report.ok:
        raise ConfigurationRefused(report.problems)
    return report


def describe() -> dict:
    """Ce qu'un diagnostic peut montrer : des codes, jamais une valeur."""
    report = check()
    return {
        "environment": report.environment,
        "ok": report.ok,
        "problems": list(report.problems),
    }
