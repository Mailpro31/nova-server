"""Résolution des secrets de production.

## Pourquoi ne pas se contenter d'une variable d'environnement

Une variable d'environnement convient tant qu'une personne configure une
machine. Elle convient moins dès qu'un orchestrateur monte les secrets en
fichiers (`/run/secrets/...`), qu'un audit demande où un secret est passé, ou
qu'une rotation doit se faire sans redémarrer tout le parc. Et un `.env` sur
disque, recommandé comme cible, finit invariablement dans une sauvegarde, une
image, ou un dépôt.

Ce module introduit donc une **référence** plutôt qu'une valeur :

    env://NOVA_MICROSOFT_CLIENT_SECRET
    file:///run/secrets/nova_microsoft_client_secret

La valeur brute reste acceptée pour ne pas casser les déploiements existants,
mais elle est signalée comme telle : en production, une configuration qui porte
le secret en clair dans son environnement est un choix, pas un défaut.

## Ce que ce module ne fait pas

Il ne parle à aucun gestionnaire de secrets externe. Vault, AWS Secrets Manager,
Azure Key Vault et GCP Secret Manager sont trois intégrations à écrire et à
maintenir ; en construire trois maintenant produirait trois chemins peu testés.
La forme `scheme://reference` est là pour qu'un quatrième schéma s'ajoute sans
toucher aux points d'appel.

Il ne stocke jamais un secret en base : la base est sauvegardée, répliquée et
restaurée dans des environnements de test.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass


# Un secret plus grand que cela n'est pas un secret, c'est un fichier monté par
# erreur. Le refuser évite de charger un binaire entier en mémoire.
MAX_SECRET_BYTES = 64 * 1024

ENV = "env"
FILE = "file"
LITERAL = "literal"

_ENV_NAME = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


class SecretError(Exception):
    """Code stable. Ne contient jamais la valeur, ni le chemin complet."""

    def __init__(self, code: str, detail: str = "") -> None:
        super().__init__(detail or code)
        self.code = code


@dataclass(frozen=True)
class ResolvedSecret:
    """Un secret et sa provenance. La valeur ne sort jamais du `repr`."""

    value: str
    source: str
    # Description non sensible, destinée aux diagnostics et aux journaux.
    reference: str

    def __repr__(self) -> str:  # pragma: no cover - trivial mais essentiel
        return f"ResolvedSecret(source={self.source!r}, reference={self.reference!r})"

    def __str__(self) -> str:
        # Un secret interpolé par mégarde dans un message ne doit pas fuir.
        return f"<secret {self.source}:{self.reference}>"


def _resolve_env(name: str) -> ResolvedSecret:
    if not _ENV_NAME.fullmatch(name):
        raise SecretError("SECRET_ENV_NAME_INVALID", "Invalid environment variable name.")
    value = os.getenv(name)
    if value is None or not value.strip():
        raise SecretError("SECRET_NOT_FOUND", f"Environment variable {name} is empty or unset.")
    return ResolvedSecret(value.strip(), ENV, name)


def _resolve_file(raw_path: str) -> ResolvedSecret:
    path = raw_path
    # `file:///run/secrets/x` laisse `/run/secrets/x` ; sous Windows,
    # `file:///C:/x` laisse `/C:/x`, dont il faut retirer la barre de tête.
    if re.match(r"^/[A-Za-z]:", path):
        path = path[1:]
    if not path:
        raise SecretError("SECRET_PATH_INVALID", "Empty secret path.")
    if not os.path.isabs(path):
        # Un chemin relatif dépend du répertoire de travail du processus, donc
        # d'un détail de lancement. Un secret ne doit pas en dépendre.
        raise SecretError("SECRET_PATH_NOT_ABSOLUTE", "Secret path must be absolute.")

    try:
        stat = os.stat(path)
    except FileNotFoundError:
        raise SecretError("SECRET_NOT_FOUND", "Secret file does not exist.") from None
    except OSError:
        raise SecretError("SECRET_UNREADABLE", "Secret file cannot be read.") from None

    if not os.path.isfile(path):
        raise SecretError("SECRET_PATH_INVALID", "Secret path is not a regular file.")
    if stat.st_size > MAX_SECRET_BYTES:
        raise SecretError("SECRET_TOO_LARGE", "Secret file exceeds the allowed size.")

    # Permissions : significatives sur POSIX, sans équivalent direct sous
    # Windows. On refuse un secret lisible par tout le monde là où le système
    # sait le dire, et on ne prétend pas le vérifier là où il ne le sait pas.
    if os.name == "posix" and (stat.st_mode & 0o004):
        raise SecretError(
            "SECRET_PERMISSIONS_TOO_OPEN", "Secret file is world-readable."
        )

    try:
        with open(path, "rb") as handle:
            raw = handle.read(MAX_SECRET_BYTES + 1)
    except OSError:
        raise SecretError("SECRET_UNREADABLE", "Secret file cannot be read.") from None
    if len(raw) > MAX_SECRET_BYTES:
        raise SecretError("SECRET_TOO_LARGE", "Secret file exceeds the allowed size.")

    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError:
        raise SecretError("SECRET_ENCODING_INVALID", "Secret file is not valid UTF-8.") from None

    # Un fichier écrit par un éditeur ou par `echo` finit par un saut de ligne.
    # Le laisser produirait un secret qui ne correspond à rien, et une erreur
    # d'authentification impossible à diagnostiquer.
    value = text.strip("\r\n")
    if not value.strip():
        raise SecretError("SECRET_EMPTY", "Secret file is empty.")
    # Seul le nom de base est journalisable : le chemin complet décrit
    # l'agencement du système de fichiers de production.
    return ResolvedSecret(value, FILE, os.path.basename(path))


def resolve(reference: str | None, *, allow_literal: bool = True) -> ResolvedSecret | None:
    """Résout une référence. `None` ou vide → `None`, ce qui n'est pas une erreur.

    Un secret absent est traité par l'appelant : c'est lui qui sait si la
    fonctionnalité est optionnelle ou si le démarrage doit échouer.
    """
    if reference is None:
        return None
    raw = reference.strip()
    if not raw:
        return None

    if raw.startswith("env://"):
        return _resolve_env(raw[len("env://") :].strip())
    if raw.startswith("file://"):
        return _resolve_file(raw[len("file://") :].strip())
    if "://" in raw.split()[0] and re.match(r"^[a-z][a-z0-9+.-]*://", raw):
        # Un schéma inconnu est refusé plutôt que pris pour une valeur : sinon
        # une faute de frappe dans `env://` deviendrait silencieusement un
        # secret littéral valant « env:/NOVA_... ».
        scheme = raw.split("://", 1)[0]
        raise SecretError("SECRET_SCHEME_UNSUPPORTED", f"Unsupported secret scheme: {scheme}")

    if not allow_literal:
        raise SecretError(
            "SECRET_LITERAL_REFUSED",
            "This secret must be given as env:// or file://.",
        )
    return ResolvedSecret(raw, LITERAL, "inline")


def resolve_value(reference: str | None, *, allow_literal: bool = True) -> str | None:
    """La valeur seule, quand la provenance n'intéresse pas l'appelant."""
    resolved = resolve(reference, allow_literal=allow_literal)
    return resolved.value if resolved else None


def describe(reference: str | None) -> dict[str, object]:
    """Ce qu'un diagnostic peut afficher d'un secret : jamais sa valeur."""
    raw = (reference or "").strip()
    if not raw:
        return {"configured": False, "source": None, "reference": None}
    try:
        resolved = resolve(raw)
    except SecretError as error:
        return {"configured": True, "source": None, "reference": None, "error": error.code}
    if resolved is None:
        return {"configured": False, "source": None, "reference": None}
    return {
        "configured": True,
        "source": resolved.source,
        "reference": resolved.reference,
        "error": None,
    }
