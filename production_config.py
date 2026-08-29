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


# ------------------------------------------------------ invariants de production
#
# ## Pourquoi des tables plutôt que des `if`
#
# La liste des assouplissements était tenue à la main, et elle a fini par
# oublier trois choses — dont `NOVA_REQUIRE_AUTH`, qui désactive purement et
# simplement l'authentification. L'oubli n'est pas surprenant : rien ne reliait
# la liste au code qui lit réellement ces variables, et une quatrième catégorie
# de contrôle demandait un quatrième bloc écrit à la main.
#
# Les invariants sont donc déclarés ici, par catégorie, en une table que l'on
# relit d'un coup d'œil. Ajouter un contrôle est une ligne, pas un bloc — et un
# lecteur voit d'un seul regard ce que « production » signifie.
#
# Ce n'est pas un framework de configuration : il n'y a ni schéma, ni typage, ni
# résolution. Quatre tables et une boucle par table.


@dataclass(frozen=True)
class RequiredValue:
    """Une variable dont la valeur **effective** est imposée en production.

    `effective` reproduit la lecture faite par le module qui l'emploie —
    normalisation comprise. C'est délibéré : juger sur une valeur normalisée
    autrement que le lecteur réel donnerait un validateur qui approuve une
    configuration que le code interprète différemment.
    """

    variable: str
    default: str
    allowed: frozenset[str]
    code: str

    def problem(self) -> str | None:
        effective = os.getenv(self.variable, self.default).strip().lower()
        return None if effective in self.allowed else self.code


# `NOVA_REQUIRE_AUTH` : `main.py` calcule `REQUIRE_AUTH` avec `== "true"`. Toute
# autre valeur y désactive l'authentification, y compris `1`, `yes` ou une faute
# de frappe. On exige donc explicitement `true`, et rien d'autre : une valeur
# que l'on ne comprend pas ne vaut pas permission de servir sans authentifier.
#
# `NOVA_CONNECTOR_LOCAL_TRANSPORT` : `loopback` est le canal de développement —
# `serve.py` l'annonce lui-même comme tel — et il remplace l'identification par
# les permissions du système de fichiers par un jeton éphémère déposé dans un
# fichier. C'est adapté à un poste sans AF_UNIX, pas à un déploiement. Seul
# `unix` est accepté en production ; une valeur inconnue est refusée aussi,
# plutôt que laissée échouer plus tard au démarrage du canal.
PRODUCTION_REQUIRED_VALUES = (
    RequiredValue(
        "NOVA_REQUIRE_AUTH", "true", frozenset({"true"}),
        "NOVA_REQUIRE_AUTH_MUST_BE_ENABLED_IN_PRODUCTION",
    ),
    RequiredValue(
        "NOVA_CONNECTOR_LOCAL_TRANSPORT", "unix", frozenset({"unix"}),
        "NOVA_CONNECTOR_LOCAL_TRANSPORT_MUST_BE_UNIX_IN_PRODUCTION",
    ),
)


# Un manifeste distribué sans racine de confiance est un manifeste que personne
# ne vérifie. Les deux chaînes de distribution posent exactement la même
# question, et l'une des deux ne l'avait jamais posée : le manifeste Desktop
# était servi avec `signed: false` et ses empreintes SHA-256 livrées telles
# quelles à la console, alors que le Connector, lui, refusait déjà.
PRODUCTION_TRUST_ROOTS = (
    ("NOVA_CONNECTOR_RELEASE_MANIFEST", "NOVA_CONNECTOR_RELEASE_PUBLIC_KEY",
     "CONNECTOR_RELEASE_TRUST_ROOT_MISSING"),
    ("NOVA_DESKTOP_RELEASE_MANIFEST", "NOVA_DESKTOP_RELEASE_PUBLIC_KEY",
     "DESKTOP_RELEASE_TRUST_ROOT_MISSING"),
)


# Assouplissements réservés au développement. Chacun rend un poste de
# développement praticable, et chacun ouvre en production un trou précis :
# accepter du HTTP, sauter une vérification, rouvrir un chemin hérité.
#
# `NOVA_OIDC_ALLOW_PRIVATE_ISSUER` n'y figure **pas**, et c'est un choix. Un
# établissement qui héberge son propre Keycloak sur un réseau privé est un cas
# légitime, documenté comme tel, et l'interdire en production casserait un
# déploiement valide pour satisfaire une règle générale. Ce qui doit être
# corrigé de ce côté-là est ailleurs — la colonne `oidc_allow_private_issuer`
# permet à un administrateur d'ouvrir le mode privé par l'API sans que
# l'exploitant l'ait autorisé côté serveur — et cela relève d'une correction de
# code, pas d'un refus de configuration.
PRODUCTION_FORBIDDEN_FLAGS = (
    "NOVA_CONNECTOR_ALLOW_INSECURE",
    "NOVA_CONNECTOR_RELEASE_ALLOW_INSECURE",
    "NOVA_DISCOVERY_ALLOW_INSECURE_ENDPOINT",
    "NOVA_ALLOW_LEGACY_DEPLOYMENT_TOKEN",
)

TRUE_VALUES = frozenset({"1", "true", "yes", "on"})


# Authentification héritée, retirée du code depuis les Phases 33 et 36. Leur
# présence signale une configuration qui croit encore disposer d'un chemin qui
# n'existe plus — mieux vaut le dire que le laisser croire.
PRODUCTION_REMOVED_VARIABLES = ("NOVA_ADMIN_TOKEN", "NOVA_DEPLOYMENT_APPLY_TOKEN")


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

    # -- identité de l'organisation
    #
    # `NOVA_ORGANIZATION_ID`, `NOVA_ORGANIZATION_NAME` et `NOVA_DOMAINS` ont des
    # valeurs par défaut héritées du tout premier déploiement. Elles rendent le
    # développement praticable, et elles n'ont rien à faire en production : une
    # organisation qui oublie de se déclarer servirait sous le nom et les
    # domaines de quelqu'un d'autre — silencieusement, et avec les mêmes
    # apparences qu'une configuration correcte.
    for variable in ("NOVA_ORGANIZATION_ID", "NOVA_ORGANIZATION_NAME", "NOVA_DOMAINS"):
        if not os.getenv(variable, "").strip():
            report.refuse(f"{variable}_REQUIRED_IN_PRODUCTION")

    # -- valeurs imposées : ce que « production » veut dire, en clair
    #
    # L'authentification doit être active, et le canal local du Connector doit
    # être le socket unix. Ni l'une ni l'autre n'est un réglage de confort :
    # les deux décident qui peut parler au serveur.
    for required in PRODUCTION_REQUIRED_VALUES:
        problem = required.problem()
        if problem:
            report.refuse(problem)

    # -- racines de confiance, quand la vérification est obligatoire
    for manifest, public_key, code in PRODUCTION_TRUST_ROOTS:
        if os.getenv(manifest, "").strip() and not os.getenv(public_key, "").strip():
            report.refuse(code)

    # -- assouplissements réservés au développement
    for flag in PRODUCTION_FORBIDDEN_FLAGS:
        if os.getenv(flag, "").strip().lower() in TRUE_VALUES:
            report.refuse(f"{flag}_FORBIDDEN_IN_PRODUCTION")

    # -- authentification héritée, retirée du code
    for removed in PRODUCTION_REMOVED_VARIABLES:
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
