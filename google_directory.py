"""Lecture de l'annuaire Google Workspace, et report dans les groupes de Nova.

Une organisation sous Google a deja ses groupes : « aero2@example.com » existe, et
personne ne veut le recreer a la main dans Nova. Ce module lit l'API Directory
avec un compte de service, puis reporte ce qu'il a lu.

## Deux moities, separees exprès

- `fetch_directory` parle au reseau et ne touche a aucune base ;
- `reconcile` ne parle qu'a la base et ne fait aucun appel reseau.

Cette separation est ce qui rend l'ensemble testable sans Google : le transport
est injecte, et la reconciliation se verifie sur une base de test.

## Ce que ce module refuse de faire

- **Ecrire chez Google.** Les portees demandees sont en lecture seule. Nova
  n'est pas l'auteur de l'annuaire d'une organisation.
- **Detenir la cle.** La configuration ne porte qu'une reference de secret
  (`env://…`, `file://…`) : la base est sauvegardee et restauree ailleurs, une
  cle de compte de service n'y a pas sa place.
- **Supprimer un compte.** Quelqu'un retire d'un groupe le quitte ; son compte
  reste, avec son historique. Un depart se traite par la suspension.
"""

from __future__ import annotations

import json
import time
import uuid
from typing import Any, Callable

import jwt

import secrets_provider

# En lecture seule, et rien d'autre : c'est ce que Google presentera a
# l'administrateur au moment d'autoriser la delegation.
SCOPES = (
    "https://www.googleapis.com/auth/admin.directory.group.readonly "
    "https://www.googleapis.com/auth/admin.directory.group.member.readonly"
)
DIRECTORY = "https://admin.googleapis.com/admin/directory/v1"
ASSERTION_TTL = 3600
PAGE_SIZE = 200
SOURCE = "google"
ORIGIN = "directory"
SYNC_INTERVAL_SECONDS = 3600.0

#: `method, url, headers=…, data=…, params=…` → `(status, payload)`.
Transport = Callable[..., tuple[int, dict]]


class DirectoryError(Exception):
    """Code stable, jamais la cle ni le jeton."""

    def __init__(self, code: str, detail: str = "") -> None:
        super().__init__(detail or code)
        self.code = code


def load_service_account(reference: str | None) -> dict:
    """La cle du compte de service, lue depuis une reference de secret."""
    try:
        resolved = secrets_provider.resolve(reference, allow_literal=False)
    except secrets_provider.SecretError as error:
        raise DirectoryError("GOOGLE_KEY_MISSING", error.code) from None
    if resolved is None or not resolved.value:
        raise DirectoryError("GOOGLE_KEY_MISSING")
    try:
        account = json.loads(resolved.value)
    except ValueError:
        raise DirectoryError("GOOGLE_KEY_INVALID") from None
    if not isinstance(account, dict):
        raise DirectoryError("GOOGLE_KEY_INVALID")
    missing = [key for key in ("client_email", "private_key", "token_uri") if not account.get(key)]
    if missing:
        raise DirectoryError("GOOGLE_KEY_INVALID")
    return account


def assertion(account: dict, subject: str, now: float | None = None) -> str:
    """Le jeton signe qui demande a agir **au nom** de l'administrateur nomme.

    Google n'accorde l'annuaire qu'a une identite humaine de l'organisation :
    un compte de service seul ne voit rien. `subject` est cette identite, et
    l'organisation l'a explicitement deleguee dans sa console Google.
    """
    issued = time.time() if now is None else now
    try:
        return jwt.encode(
            {
                "iss": account["client_email"],
                "sub": subject,
                "aud": account["token_uri"],
                "scope": SCOPES,
                "iat": int(issued),
                "exp": int(issued) + ASSERTION_TTL,
            },
            account["private_key"],
            algorithm="RS256",
        )
    except Exception:
        raise DirectoryError("GOOGLE_KEY_INVALID") from None


def access_token(account: dict, subject: str, transport: Transport,
                 now: float | None = None) -> str:
    status, payload = transport(
        "POST", account["token_uri"],
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        data={
            "grant_type": "urn:ietf:params:oauth:grant-type:jwt-bearer",
            "assertion": assertion(account, subject, now),
        },
    )
    if status != 200 or not payload.get("access_token"):
        # Le refus d'un jeton signale presque toujours une delegation absente
        # ou une adresse d'administrateur qui n'existe pas.
        raise DirectoryError("GOOGLE_UNAUTHORIZED")
    return payload["access_token"]


def _read(url: str, token: str, transport: Transport, params: dict) -> dict:
    status, payload = transport(
        "GET", url, headers={"Authorization": f"Bearer {token}"}, params=params)
    if status == 401:
        raise DirectoryError("GOOGLE_UNAUTHORIZED")
    if status == 403:
        raise DirectoryError("GOOGLE_FORBIDDEN")
    if status == 404:
        raise DirectoryError("GOOGLE_NOT_FOUND")
    if status != 200:
        raise DirectoryError("GOOGLE_UNAVAILABLE")
    return payload


def fetch_directory(account: dict, config: dict, transport: Transport,
                    now: float | None = None) -> list[dict]:
    """Les groupes retenus et leurs membres, tels que Google les donne."""
    token = access_token(account, config["admin_subject"], transport, now)
    wanted = {str(email).strip().casefold()
              for email in (config.get("group_filter") or []) if str(email).strip()}
    groups: list[dict] = []
    page: str | None = None
    while True:
        params: dict[str, Any] = {
            "customer": config.get("customer_id") or "my_customer",
            "maxResults": PAGE_SIZE,
        }
        if page:
            params["pageToken"] = page
        payload = _read(f"{DIRECTORY}/groups", token, transport, params)
        for group in payload.get("groups") or []:
            email = (group.get("email") or "").strip()
            if wanted and email.casefold() not in wanted:
                continue
            groups.append({
                "id": group.get("id") or email,
                "email": email,
                "name": (group.get("name") or email).strip(),
                "members": [],
            })
        page = payload.get("nextPageToken")
        if not page:
            break

    for group in groups:
        members: list[str] = []
        page = None
        while True:
            params = {"maxResults": PAGE_SIZE}
            if page:
                params["pageToken"] = page
            payload = _read(
                f"{DIRECTORY}/groups/{group['email'].replace('@', '%40')}/members",
                token, transport, params)
            for member in payload.get("members") or []:
                # Les groupes imbriques et les comptes suspendus ne donnent pas
                # d'acces : les ranger produirait des membres qui ne se
                # connectent jamais.
                if (member.get("type") or "USER").upper() != "USER":
                    continue
                if (member.get("status") or "ACTIVE").upper() != "ACTIVE":
                    continue
                email = (member.get("email") or "").strip()
                if email:
                    members.append(email)
            page = payload.get("nextPageToken")
            if not page:
                break
        group["members"] = members
    return groups


def reconcile(conn, organization_id: str, directory: list[dict], *,
              create_user: Callable[..., str], normalize_email: Callable[[str], str],
              now: float | None = None) -> dict:
    """Reporte l'annuaire dans les groupes de Nova, sans rien supprimer d'autre.

    Les groupes crees dans la console ne sont jamais touches : ils n'ont pas
    d'equivalent chez Google, et les effacer ferait disparaitre le travail de
    l'administrateur a la premiere synchronisation.
    """
    stamp = time.time() if now is None else now
    outcome = {"groups": 0, "accounts_created": 0, "added": 0, "removed": 0}
    seen: set[str] = set()

    for entry in directory:
        external_id = str(entry["id"])
        seen.add(external_id)
        row = conn.execute(
            "SELECT group_id, display_name FROM organization_groups "
            "WHERE organization_id=? AND source=? AND external_id=?",
            (organization_id, SOURCE, external_id),
        ).fetchone()
        if row is None:
            group_id = str(uuid.uuid4())
            conn.execute(
                "INSERT INTO organization_groups(organization_id, group_id, external_id, "
                "display_name, created_at, updated_at, source) VALUES (?,?,?,?,?,?,?)",
                (organization_id, group_id, external_id, entry["name"], stamp, stamp, SOURCE),
            )
        else:
            group_id = row["group_id"]
            if row["display_name"] != entry["name"]:
                conn.execute(
                    "UPDATE organization_groups SET display_name=?, updated_at=?, "
                    "deleted_at=NULL WHERE organization_id=? AND group_id=?",
                    (entry["name"], stamp, organization_id, group_id),
                )
            else:
                conn.execute(
                    "UPDATE organization_groups SET deleted_at=NULL "
                    "WHERE organization_id=? AND group_id=? AND deleted_at IS NOT NULL",
                    (organization_id, group_id),
                )
        outcome["groups"] += 1

        wanted: set[str] = set()
        for email in entry["members"]:
            account = conn.execute(
                "SELECT user_id FROM users WHERE organization_id=? AND normalized_email=?",
                (organization_id, normalize_email(email)),
            ).fetchone()
            if account is None:
                user_id = create_user(conn, organization_id, email)
                outcome["accounts_created"] += 1
            else:
                user_id = account["user_id"]
            wanted.add(user_id)

        held = {
            membership["user_id"]
            for membership in conn.execute(
                "SELECT user_id FROM organization_group_memberships "
                "WHERE organization_id=? AND group_id=?",
                (organization_id, group_id),
            ).fetchall()
        }
        for user_id in sorted(wanted - held):
            conn.execute(
                "INSERT INTO organization_group_memberships(organization_id, group_id, "
                "user_id, created_at, origin) VALUES (?,?,?,?,?)",
                (organization_id, group_id, user_id, stamp, ORIGIN),
            )
            outcome["added"] += 1
        for user_id in sorted(held - wanted):
            conn.execute(
                "DELETE FROM organization_group_memberships "
                "WHERE organization_id=? AND group_id=? AND user_id=?",
                (organization_id, group_id, user_id),
            )
            outcome["removed"] += 1

    # Un groupe disparu chez Google disparait ici : il ne s'applique plus, et
    # ses appartenances partent avec lui. Le groupe lui-meme reste en base,
    # marque supprime, pour que le journal garde de quoi il s'agissait.
    gone = conn.execute(
        "SELECT group_id, external_id FROM organization_groups "
        "WHERE organization_id=? AND source=? AND deleted_at IS NULL",
        (organization_id, SOURCE),
    ).fetchall()
    for row in gone:
        if row["external_id"] in seen:
            continue
        outcome["removed"] += conn.execute(
            "DELETE FROM organization_group_memberships WHERE organization_id=? AND group_id=?",
            (organization_id, row["group_id"]),
        ).rowcount
        conn.execute(
            "UPDATE organization_groups SET deleted_at=?, updated_at=? "
            "WHERE organization_id=? AND group_id=?",
            (stamp, stamp, organization_id, row["group_id"]),
        )
    return outcome


def due_organizations(rows, now: float, interval: float = SYNC_INTERVAL_SECONDS) -> list[str]:
    """Celles dont la synchronisation est activee et dont l'heure est passee.

    Jamais synchronisee compte comme due : c'est le premier tour, et c'est
    celui qui remplit tout.
    """
    due = []
    for row in rows:
        if not row["enabled"]:
            continue
        last = row["last_sync_at"]
        if last is None or now - last >= interval:
            due.append(row["organization_id"])
    return due
