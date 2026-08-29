"""Sauvegarde et restauration PostgreSQL.

## Pourquoi un enrobage plutôt qu'une réimplémentation

`pg_dump` est correct, éprouvé, et connaît le format de sa version. Le
réécrire en Python produirait un outil moins fiable pour un besoin déjà couvert.
Ce module ne fait donc que ce que `pg_dump` ne fait pas :

- refuser une destination dangereuse plutôt que d'écrire n'importe où ;
- écrire un manifeste à côté du dump — empreinte, taille, version de schéma,
  version du serveur — pour qu'une archive puisse être vérifiée sans être
  restaurée ;
- **refuser de restaurer par-dessus une base non vide**, sauf demande
  explicite.

## Ce qu'une sauvegarde contient

Le Control Plane porte des identités, de la configuration et des métadonnées.
Il ne porte ni audio, ni transcription, ni prompt, ni presse-papiers, ni
historique de dictée — cela n'a jamais quitté l'infrastructure de
l'organisation. Une sauvegarde du Control Plane reste néanmoins une donnée
personnelle : elle contient des adresses e-mail et des empreintes de session.

## Ce qu'elle ne contient pas

Aucun identifiant de connexion. Le mot de passe vient de l'environnement
(`PGPASSWORD`, `.pgpass` ou l'URL passée par l'appelant) et n'est écrit ni dans
le manifeste, ni dans un journal, ni dans un nom de fichier.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import shlex
import shutil
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path

import database
import migrations


MANIFEST_SUFFIX = ".manifest.json"
SCHEMA_VERSION = 1


class BackupError(Exception):
    """Code stable. Ne porte jamais d'URL complète ni de mot de passe."""

    def __init__(self, code: str, detail: str = "") -> None:
        super().__init__(detail or code)
        self.code = code


@dataclass(frozen=True)
class BackupResult:
    path: Path
    manifest_path: Path
    manifest: dict


def _command(name: str) -> list[str]:
    """La commande qui execute un outil PostgreSQL.

    `NOVA_PG_COMMAND_PREFIX` permet de l'invoquer ailleurs que sur cette
    machine — un conteneur voisin, un sidecar — sans que ce module ait a
    connaitre l'agencement du deploiement. Le dump transite par la sortie
    standard, donc le fichier produit est toujours local a l'appelant, meme
    quand l'outil ne l'est pas.
    """
    prefix = os.getenv("NOVA_PG_COMMAND_PREFIX", "").strip()
    if prefix:
        return shlex.split(prefix) + [name]
    found = shutil.which(name)
    if not found:
        raise BackupError(
            "TOOL_MISSING",
            f"{name} is not on PATH; set NOVA_PG_COMMAND_PREFIX to reach it.",
        )
    return [found]


def _redact(url: str) -> str:
    """Une URL sans identifiants, pour un message ou un journal."""
    return re.sub(r"//[^/@]*@", "//", url)


def server_version(url: str) -> str:
    db = database.Database(url)
    try:
        with db.connect() as conn:
            return str(conn.execute("SELECT version()").fetchone()[0]).split(",")[0]
    finally:
        db.close()


def schema_version(url: str) -> int:
    db = database.Database(url)
    try:
        return migrations.status(db)["current_version"]
    finally:
        db.close()


def digest(path: Path) -> str:
    hasher = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            hasher.update(chunk)
    return hasher.hexdigest()


def dump(url: str, destination: str | Path, *, tool_url: str | None = None) -> BackupResult:
    """Sauvegarde une base PostgreSQL, et décrit ce qui a été sauvegardé.

    `tool_url` est l'adresse **telle que l'outil la voit**. Elle differe de
    `url` quand `pg_dump` s'execute ailleurs — un conteneur voisin, un
    sidecar — ou l'hote et le port ne sont pas les memes. Par defaut, les
    deux sont identiques.
    """
    config = database.parse_url(url)
    if config.dialect != database.POSTGRESQL:
        raise BackupError(
            "BACKUP_POSTGRESQL_ONLY",
            "Only a PostgreSQL control plane is backed up by this tool.",
        )

    target = Path(destination)
    if target.exists():
        # Écraser une sauvegarde est la façon la plus simple de perdre la seule
        # qui était bonne.
        raise BackupError("BACKUP_DESTINATION_EXISTS", "Destination already exists.")
    target.parent.mkdir(parents=True, exist_ok=True)

    version = schema_version(url)
    server = server_version(url)

    # Le dump sort sur la sortie standard : le fichier est ecrit ici, par nous,
    # a un chemin que nous controlons. `pg_dump --file` l'ecrirait la ou l'outil
    # s'execute, ce qui n'est pas forcement la meme machine.
    completed = subprocess.run(
        _command("pg_dump")
        + ["--format=custom", "--no-owner", "--no-privileges", tool_url or config.url],
        capture_output=True,
    )
    if completed.returncode != 0:
        raise BackupError(
            "BACKUP_FAILED",
            _redact(completed.stderr.decode("utf-8", "replace").strip()[-500:]),
        )
    target.write_bytes(completed.stdout)

    manifest = {
        "manifest_schema_version": SCHEMA_VERSION,
        "created_at": time.time(),
        "sha256": digest(target),
        "size": target.stat().st_size,
        "schema_version": version,
        "server_version": server,
        "artifact": target.name,
    }
    manifest_path = target.with_name(target.name + MANIFEST_SUFFIX)
    manifest_path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    return BackupResult(target, manifest_path, manifest)


def verify(path: str | Path) -> dict:
    """Vérifie une archive contre son manifeste, sans restaurer.

    C'est la question qu'on se pose à trois heures du matin : cette sauvegarde
    est-elle intacte ? Y répondre ne doit pas demander une base.
    """
    target = Path(path)
    manifest_path = target.with_name(target.name + MANIFEST_SUFFIX)
    if not target.is_file():
        raise BackupError("BACKUP_MISSING")
    if not manifest_path.is_file():
        raise BackupError("BACKUP_MANIFEST_MISSING")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    actual = digest(target)
    if actual != manifest.get("sha256"):
        raise BackupError("BACKUP_DIGEST_MISMATCH")
    if target.stat().st_size != manifest.get("size"):
        raise BackupError("BACKUP_SIZE_MISMATCH")
    return manifest


def restore(
    path: str | Path,
    url: str,
    *,
    allow_non_empty: bool = False,
    tool_url: str | None = None,
) -> dict:
    """Restaure une archive **dans une base neuve**.

    Le défaut refuse une base non vide. Une commande de restauration qui écrase
    la production sans rien demander finit toujours par être lancée sur la
    mauvaise base : `allow_non_empty` rend ce geste explicite.
    """
    manifest = verify(path)
    config = database.parse_url(url)
    if config.dialect != database.POSTGRESQL:
        raise BackupError("RESTORE_POSTGRESQL_ONLY")

    db = database.Database(url)
    try:
        with db.connect() as conn:
            occupied = [
                table for table in migrations.CORE_TABLES if conn.table_exists(table)
            ]
    finally:
        db.close()
    if occupied and not allow_non_empty:
        raise BackupError(
            "RESTORE_TARGET_NOT_EMPTY",
            "Restore into a fresh database, or pass allow_non_empty explicitly.",
        )

    completed = subprocess.run(
        _command("pg_restore")
        + ["--no-owner", "--no-privileges", "--dbname", tool_url or config.url],
        input=Path(path).read_bytes(),
        capture_output=True,
    )
    if completed.returncode != 0:
        raise BackupError(
            "RESTORE_FAILED",
            _redact(completed.stderr.decode("utf-8", "replace").strip()[-500:]),
        )
    return manifest


def main(argv: list[str] | None = None) -> int:
    """`python -m backup <dump|verify|restore>`."""
    import argparse
    import sys

    parser = argparse.ArgumentParser(prog="python -m backup")
    subparsers = parser.add_subparsers(dest="command", required=True)

    dump_parser = subparsers.add_parser("dump")
    dump_parser.add_argument("destination")
    dump_parser.add_argument("--url", default=None)

    verify_parser = subparsers.add_parser("verify")
    verify_parser.add_argument("path")

    restore_parser = subparsers.add_parser("restore")
    restore_parser.add_argument("path")
    restore_parser.add_argument("--url", required=True)
    restore_parser.add_argument(
        "--allow-non-empty",
        action="store_true",
        help="restaure dans une base deja peuplee — geste explicite",
    )

    arguments = parser.parse_args(argv)
    try:
        if arguments.command == "dump":
            url = arguments.url or database.configured_url()
            print(json.dumps(dump(url, arguments.destination).manifest, indent=2))
        elif arguments.command == "verify":
            print(json.dumps(verify(arguments.path), indent=2))
        else:
            print(
                json.dumps(
                    restore(
                        arguments.path,
                        arguments.url,
                        allow_non_empty=arguments.allow_non_empty,
                    ),
                    indent=2,
                )
            )
    except BackupError as error:
        print(json.dumps({"error": error.code, "detail": str(error)}), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
