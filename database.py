"""Accès base de données — un seul contrat, deux moteurs.

## Pourquoi cette couche existe

Le Control Plane a été écrit sur SQLite, et SQLite l'a bien servi : un fichier,
zéro administration, des transactions correctes. Ce qu'il ne fait pas, c'est
plusieurs processus qui écrivent en même temps depuis plusieurs machines. Un
plan de contrôle centralisé finit toujours par avoir besoin de cela.

La cible de production est donc PostgreSQL. SQLite reste le moteur du
développement, des tests et des recettes — pas par nostalgie, mais parce qu'un
test qui démarre en trois millisecondes se lance cent fois par jour, et qu'un
test qui demande un conteneur se lance beaucoup moins.

## Ce que cette couche n'est pas

Ce n'est pas un ORM. Le Control Plane écrit du SQL, il continuera d'en écrire :
c'est du SQL lisible, revu, et dont on peut prédire le plan d'exécution. Une
couche d'objets rendrait chaque requête plus courte et chaque comportement moins
prévisible.

Ce n'est pas non plus une réécriture des 580 appels existants. Les requêtes
gardent la syntaxe `?` de SQLite ; c'est l'adaptateur qui traduit vers `%s`
quand le moteur est PostgreSQL. Réécrire 580 sites d'appel à la main aurait
produit exactement la même sémantique, avec 580 occasions de se tromper.

## Ce qu'elle garantit

- une exception d'intégrité unique, quel que soit le moteur ;
- des lignes qui s'indexent par nom **et** par position, comme `sqlite3.Row` ;
- `?` traduit sans jamais toucher au contenu d'un littéral de chaîne ;
- les constructions propres à SQLite traduites explicitement, ou refusées —
  jamais transmises telles quelles à un moteur qui les comprendrait de travers.
"""

from __future__ import annotations

import os
import re
import sqlite3
import threading
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Any, Iterator, Sequence
from urllib.parse import urlparse


SQLITE = "sqlite"
POSTGRESQL = "postgresql"


class DatabaseError(Exception):
    """Erreur portant un code stable, sans détail d'hôte ni d'identifiant."""

    def __init__(self, code: str, detail: str = "") -> None:
        super().__init__(detail or code)
        self.code = code


class IntegrityError(Exception):
    """Violation de contrainte, quel que soit le moteur.

    Le code applicatif attrape **ceci** et non `sqlite3.IntegrityError` : une
    contrainte unique doit produire le même 409 sous les deux moteurs, sinon le
    passage en production changerait silencieusement un conflit en erreur 500.
    """


class OperationalError(Exception):
    """Base injoignable, verrou expiré, connexion perdue."""


# --------------------------------------------------------------------- config


@dataclass(frozen=True)
class DatabaseConfig:
    dialect: str
    url: str
    # Chemin de fichier pour SQLite ; `None` pour PostgreSQL.
    path: str | None


def _sqlite_path(url: str) -> str:
    """Extrait le chemin d'une URL `sqlite:`.

    Trois formes acceptées, parce que les trois circulent :
    `sqlite:///C:/x/nova.db`, `sqlite:///./nova.db` et `sqlite:nova.db`.
    """
    rest = url.split(":", 1)[1]
    rest = rest[2:] if rest.startswith("//") else rest
    rest = rest[1:] if rest.startswith("/") and re.match(r"^/[A-Za-z]:", rest) else rest
    return rest or ":memory:"


def parse_url(url: str) -> DatabaseConfig:
    raw = (url or "").strip()
    if not raw:
        raise DatabaseError("DATABASE_URL_MISSING", "NOVA_DATABASE_URL is not set.")
    scheme = urlparse(raw).scheme.lower()
    if scheme in ("sqlite", "sqlite3"):
        return DatabaseConfig(SQLITE, raw, _sqlite_path(raw))
    if scheme in ("postgres", "postgresql"):
        # psycopg accepte les deux, mais on normalise pour que les journaux et
        # les messages d'erreur ne fassent pas croire à deux moteurs distincts.
        normalized = re.sub(r"^postgres://", "postgresql://", raw)
        return DatabaseConfig(POSTGRESQL, normalized, None)
    raise DatabaseError(
        "DATABASE_URL_UNSUPPORTED",
        f"Unsupported database scheme: {scheme or '(none)'}",
    )


def configured_url() -> str:
    """L'URL effective.

    `NOVA_DATABASE_URL` fait autorité. À défaut, `NOVA_DB` — la variable
    historique, qui désigne un fichier SQLite — est acceptée pour que les
    recettes et les tests existants continuent de fonctionner sans réécriture.
    """
    explicit = os.getenv("NOVA_DATABASE_URL", "").strip()
    if explicit:
        return explicit
    legacy = os.getenv("NOVA_DB", "").strip()
    if legacy:
        return "sqlite:///" + legacy.replace("\\", "/")
    return "sqlite:///nova.db"


# ------------------------------------------------------------------ traduction

# Un littéral de chaîne SQL peut contenir un `?` qui n'est pas un paramètre —
# un message, une expression régulière, un caractère joker. Les découper avant
# de traduire est la seule façon de ne pas le corrompre.
_LITERAL = re.compile(r"'(?:[^']|'')*'|\"(?:[^\"]|\"\")*\"|--[^\n]*|/\*.*?\*/", re.DOTALL)


def translate_placeholders(sql: str) -> str:
    """`?` → `%s` hors littéral, et **tout** `%` doublé.

    Deux règles, et la seconde est celle qui surprend : psycopg analyse les `%`
    sur la requête entière, littéraux compris. Un `LIKE '%match'` laissé tel
    quel est donc lu comme un marqueur `%m` et rejeté. Il faut doubler les `%`
    partout — psycopg les ramène à un seul en interpolant — tout en laissant les
    `?` d'un littéral intacts, sans quoi le contenu stocké serait corrompu.

    Ceci suppose qu'une séquence de paramètres est toujours passée, même vide :
    sans paramètres, psycopg ne déséchapperait pas. `Connection.execute` le
    garantit.
    """
    out: list[str] = []
    last = 0
    for match in _LITERAL.finditer(sql):
        out.append(sql[last : match.start()].replace("%", "%%").replace("?", "%s"))
        # Dans un littéral ou un commentaire : le `%` est doublé pour psycopg,
        # le `?` reste ce que l'auteur a écrit.
        out.append(match.group(0).replace("%", "%%"))
        last = match.end()
    out.append(sql[last:].replace("%", "%%").replace("?", "%s"))
    return "".join(out)


def split_statements(script: str) -> list[str]:
    """Découpe un script en instructions, sans couper dans un littéral.

    Un `;` peut apparaître dans une chaîne ou un commentaire ; y découper
    produirait deux fragments invalides. Les littéraux et les commentaires sont
    donc masqués avant la découpe, puis restitués intacts.
    """
    masked = _LITERAL.sub(lambda match: "\x00" * len(match.group(0)), script)
    statements: list[str] = []
    start = 0
    for position, character in enumerate(masked):
        if character == ";":
            fragment = script[start:position].strip()
            if fragment:
                statements.append(fragment)
            start = position + 1
    tail = script[start:].strip()
    if tail:
        statements.append(tail)
    return statements


_UNSUPPORTED = (
    # Traduites explicitement ailleurs, ou interdites. La liste sert de garde :
    # une construction SQLite qui arriverait telle quelle à PostgreSQL y serait
    # soit refusée, soit — pire — comprise différemment.
    (re.compile(r"\bINSERT\s+OR\s+REPLACE\b", re.I), "INSERT_OR_REPLACE"),
    (re.compile(r"\bAUTOINCREMENT\b", re.I), "AUTOINCREMENT"),
    (re.compile(r"\bPRAGMA\b", re.I), "PRAGMA"),
    (re.compile(r"\bBEGIN\s+IMMEDIATE\b", re.I), "BEGIN_IMMEDIATE"),
)

_INSERT_OR_IGNORE = re.compile(r"\bINSERT\s+OR\s+IGNORE\b", re.I)


def translate_sql(sql: str, dialect: str) -> str:
    """Adapte une requête écrite en dialecte SQLite au moteur cible."""
    if dialect == SQLITE:
        return sql
    statement = sql
    # `INSERT OR IGNORE` a un équivalent exact et sûr.
    if _INSERT_OR_IGNORE.search(statement):
        statement = _INSERT_OR_IGNORE.sub("INSERT", statement)
        statement = statement.rstrip().rstrip(";") + " ON CONFLICT DO NOTHING"
    for pattern, code in _UNSUPPORTED:
        if pattern.search(statement):
            raise DatabaseError(
                "SQL_DIALECT_UNSUPPORTED",
                f"{code} has no direct PostgreSQL equivalent; write it explicitly.",
            )
    return translate_placeholders(statement)


# ----------------------------------------------------------------------- lignes


class Row:
    """Ligne indexable par nom et par position, comme `sqlite3.Row`.

    Le code existant fait les deux — `row["email"]` et `row[0]` — et
    `dict(row)`. Reproduire le contrat plutôt que de le changer évite d'avoir à
    relire six cents accès pour savoir lesquels utilisaient quelle forme.
    """

    __slots__ = ("_columns", "_values", "_index")

    def __init__(self, columns: Sequence[str], values: Sequence[Any]) -> None:
        self._columns = tuple(columns)
        self._values = tuple(values)
        self._index = {name: position for position, name in enumerate(self._columns)}

    def __getitem__(self, key: Any) -> Any:
        if isinstance(key, int):
            return self._values[key]
        try:
            return self._values[self._index[key]]
        except KeyError:
            raise IndexError(key) from None

    def __contains__(self, key: Any) -> bool:
        return key in self._index

    def __iter__(self) -> Iterator[Any]:
        return iter(self._values)

    def __len__(self) -> int:
        return len(self._values)

    def __eq__(self, other: Any) -> bool:
        if isinstance(other, Row):
            return self._columns == other._columns and self._values == other._values
        return NotImplemented

    def __hash__(self) -> int:
        return hash((self._columns, self._values))

    def keys(self) -> list[str]:
        return list(self._columns)

    def __repr__(self) -> str:
        return f"Row({dict(zip(self._columns, self._values))!r})"


class _Result:
    """Ce que renvoie `execute`, avec la même surface que sous SQLite."""

    __slots__ = ("_rows", "_position", "rowcount", "lastrowid")

    def __init__(self, rows: list[Row], rowcount: int, lastrowid: Any = None) -> None:
        self._rows = rows
        self._position = 0
        self.rowcount = rowcount
        self.lastrowid = lastrowid

    def fetchone(self) -> Row | None:
        if self._position >= len(self._rows):
            return None
        row = self._rows[self._position]
        self._position += 1
        return row

    def fetchall(self) -> list[Row]:
        rows = self._rows[self._position :]
        self._position = len(self._rows)
        return rows

    def __iter__(self) -> Iterator[Row]:
        return iter(self.fetchall())


# ------------------------------------------------------------------ connexions


class Connection:
    """Connexion unifiée. Ne s'instancie pas directement : voir `connect`."""

    def __init__(self, raw: Any, dialect: str) -> None:
        self._raw = raw
        self.dialect = dialect
        # `sqlite3.Connection.total_changes` n'a pas d'équivalent PostgreSQL.
        # On le compte nous-mêmes, pour les deux moteurs, afin que le code qui
        # mesure « combien de lignes cette suite d'ordres a-t-elle changées »
        # se comporte identiquement.
        self._changes = 0

    # -- lecture/écriture

    def execute(self, sql: str, parameters: Sequence[Any] = ()) -> _Result:
        statement = translate_sql(sql, self.dialect)
        try:
            if self.dialect == SQLITE:
                cursor = self._raw.execute(statement, tuple(parameters))
            else:
                cursor = self._raw.cursor()
                cursor.execute(statement, tuple(parameters))
        except Exception as error:
            raise _translate_error(error) from error
        return self._result(cursor)

    def executemany(self, sql: str, seq: Sequence[Sequence[Any]]) -> _Result:
        statement = translate_sql(sql, self.dialect)
        rows = [tuple(item) for item in seq]
        try:
            if self.dialect == SQLITE:
                cursor = self._raw.executemany(statement, rows)
            else:
                cursor = self._raw.cursor()
                cursor.executemany(statement, rows)
        except Exception as error:
            raise _translate_error(error) from error
        count = max(cursor.rowcount or 0, 0)
        self._changes += count
        return _Result([], count)

    def executescript(self, script: str) -> None:
        """Exécute un script DDL, **dans la transaction en cours**.

        Aucune traduction de dialecte n'est tentée ici : un script de schéma
        doit être écrit pour son moteur. C'est ce que fait `migrations.py`.

        Le point délicat est SQLite. `sqlite3.Connection.executescript` émet un
        `COMMIT` avant de commencer : un script qui échoue à sa troisième
        instruction laisse donc les deux premières validées. Une migration
        interrompue laisserait ainsi des tables à moitié créées et une version
        non enregistrée — l'état exact qu'un système de migrations existe pour
        rendre impossible. On exécute donc les instructions une par une, ce qui
        les garde dans la transaction ouverte par `connect()`.

        PostgreSQL n'a pas ce défaut : un script multi-instructions y est déjà
        atomique.
        """
        try:
            if self.dialect == SQLITE:
                for statement in split_statements(script):
                    self._raw.execute(statement)
            else:
                self._raw.execute(script)
        except Exception as error:
            raise _translate_error(error) from error

    def _result(self, cursor: Any) -> _Result:
        rowcount = cursor.rowcount if cursor.rowcount is not None else -1
        if rowcount > 0:
            self._changes += rowcount
        if cursor.description is None:
            return _Result([], max(rowcount, 0), getattr(cursor, "lastrowid", None))
        columns = [column[0] for column in cursor.description]
        rows = [Row(columns, values) for values in cursor.fetchall()]
        return _Result(rows, max(rowcount, 0), getattr(cursor, "lastrowid", None))

    # -- transactions

    @property
    def total_changes(self) -> int:
        return self._changes

    def begin_immediate(self) -> None:
        """Ouvre une transaction qui prend le verrou d'écriture tout de suite.

        Sous SQLite c'est `BEGIN IMMEDIATE` : sans lui, deux processus peuvent
        tous deux décider qu'un identifiant est libre avant que l'un des deux
        n'écrive. Sous PostgreSQL, la transaction est déjà ouverte et les
        contraintes uniques tranchent le conflit ; il n'y a rien à faire, et
        surtout rien à simuler.
        """
        if self.dialect == SQLITE:
            self._raw.execute("BEGIN IMMEDIATE")

    def commit(self) -> None:
        self._raw.commit()

    def rollback(self) -> None:
        self._raw.rollback()

    def close(self) -> None:
        self._raw.close()

    # -- introspection

    def columns(self, table: str) -> list[str]:
        """Colonnes d'une table, sans `PRAGMA` propre à SQLite."""
        if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", table):
            raise DatabaseError("IDENTIFIER_INVALID", "Unsafe table identifier.")
        if self.dialect == SQLITE:
            return [row[1] for row in self._raw.execute(f"PRAGMA table_info({table})")]
        result = self.execute(
            "SELECT column_name FROM information_schema.columns "
            "WHERE table_schema=current_schema() AND table_name=? "
            "ORDER BY ordinal_position",
            (table,),
        )
        return [row[0] for row in result.fetchall()]

    def table_exists(self, table: str) -> bool:
        if self.dialect == SQLITE:
            found = self._raw.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)
            ).fetchone()
            return found is not None
        return (
            self.execute(
                "SELECT 1 FROM information_schema.tables "
                "WHERE table_schema=current_schema() AND table_name=?",
                (table,),
            ).fetchone()
            is not None
        )


def _translate_error(error: Exception) -> Exception:
    """Ramène l'erreur du pilote à l'un des trois types du contrat."""
    if isinstance(error, (DatabaseError, IntegrityError, OperationalError)):
        return error
    if isinstance(error, sqlite3.IntegrityError):
        return IntegrityError(str(error))
    if isinstance(error, sqlite3.OperationalError):
        return OperationalError(str(error))
    module = type(error).__module__ or ""
    if module.startswith("psycopg"):
        name = type(error).__name__
        # psycopg expose une hiérarchie DB-API : on s'y raccroche par nom pour
        # ne pas importer psycopg quand seul SQLite est installé.
        if "Integrity" in name or "UniqueViolation" in name or "ForeignKeyViolation" in name:
            return IntegrityError(str(error))
        return OperationalError(str(error))
    return error


# ------------------------------------------------------------------------ pool


class _PostgresPool:
    """Pool borné, volontairement minimal.

    Une connexion par requête coûte un aller-retour TCP et une authentification
    à chaque appel ; sans borne, un pic de trafic ouvre plus de connexions que
    PostgreSQL n'en accepte et le service tombe au moment où il est le plus
    sollicité. Les deux problèmes se règlent avec un pool borné, et rien de plus
    n'est nécessaire ici.
    """

    def __init__(self, url: str, minimum: int, maximum: int, timeout: float) -> None:
        self._url = url
        self._maximum = maximum
        self._timeout = timeout
        self._idle: list[Any] = []
        self._leased = 0
        self._lock = threading.Condition()
        for _ in range(max(0, minimum)):
            self._idle.append(self._open())

    def _open(self) -> Any:
        import psycopg

        try:
            return psycopg.connect(self._url, autocommit=False)
        except Exception as error:
            raise _translate_error(error) from error

    def acquire(self) -> Any:
        with self._lock:
            deadline = self._timeout
            while True:
                if self._idle:
                    self._leased += 1
                    return self._idle.pop()
                if self._leased < self._maximum:
                    self._leased += 1
                    break
                if not self._lock.wait(timeout=deadline):
                    raise OperationalError("Database connection pool exhausted.")
        try:
            return self._open()
        except Exception:
            with self._lock:
                self._leased -= 1
                self._lock.notify()
            raise

    def release(self, raw: Any, reusable: bool) -> None:
        with self._lock:
            self._leased -= 1
            if reusable and len(self._idle) < self._maximum:
                self._idle.append(raw)
            else:
                try:
                    raw.close()
                except Exception:
                    pass
            self._lock.notify()

    def close(self) -> None:
        with self._lock:
            for raw in self._idle:
                try:
                    raw.close()
                except Exception:
                    pass
            self._idle.clear()

    @property
    def stats(self) -> dict[str, int]:
        with self._lock:
            return {"idle": len(self._idle), "leased": self._leased, "max": self._maximum}


class Database:
    """Point d'entrée. Un objet par processus."""

    def __init__(self, url: str | None = None) -> None:
        self.config = parse_url(url or configured_url())
        self._pool: _PostgresPool | None = None
        if self.config.dialect == POSTGRESQL:
            self._pool = _PostgresPool(
                self.config.url,
                minimum=int(os.getenv("NOVA_DB_POOL_MIN", "1")),
                maximum=int(os.getenv("NOVA_DB_POOL_MAX", "10")),
                timeout=float(os.getenv("NOVA_DB_POOL_TIMEOUT", "10")),
            )

    @property
    def dialect(self) -> str:
        return self.config.dialect

    @contextmanager
    def connect(self) -> Iterator[Connection]:
        """Une transaction. Validée à la sortie normale, annulée sur exception.

        C'est le contrat qu'avait déjà `main.db()`, conservé mot pour mot : une
        mutation qui échoue à mi-chemin ne doit jamais laisser une ressource à
        moitié modifiée.
        """
        if self.config.dialect == SQLITE:
            raw = sqlite3.connect(self.config.path)
            connection = Connection(raw, SQLITE)
            try:
                yield connection
            except Exception:
                connection.rollback()
                raise
            else:
                connection.commit()
            finally:
                raw.close()
            return

        assert self._pool is not None
        raw = self._pool.acquire()
        connection = Connection(raw, POSTGRESQL)
        reusable = True
        try:
            yield connection
        except Exception as error:
            try:
                connection.rollback()
            except Exception:
                reusable = False
            # Une connexion sur laquelle le pilote a perdu pied ne retourne pas
            # au pool : elle y empoisonnerait la requête suivante.
            if isinstance(error, OperationalError):
                reusable = False
            raise
        else:
            connection.commit()
        finally:
            self._pool.release(raw, reusable)

    def close(self) -> None:
        if self._pool is not None:
            self._pool.close()

    @property
    def pool_stats(self) -> dict[str, int]:
        return self._pool.stats if self._pool else {"idle": 0, "leased": 0, "max": 0}
