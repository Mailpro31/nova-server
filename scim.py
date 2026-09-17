"""SCIM 2.0 tenant-scoped provisioning for the Nova Control Plane.

The module deliberately knows nothing about Microsoft-specific headers or
payloads.  A credential authenticates one organization and the organization
always comes from that credential, never from a SCIM document.
"""

from __future__ import annotations

import hashlib
import json
import re
import secrets
import database
import ratelimit
import time
import uuid
from dataclasses import dataclass
from typing import Any, Callable

from fastapi import Depends, Header, Request
from fastapi.responses import JSONResponse, Response
from pydantic import BaseModel, ConfigDict, Field


SCIM_MEDIA_TYPE = "application/scim+json"
SCIM_USER_SCHEMA = "urn:ietf:params:scim:schemas:core:2.0:User"
SCIM_GROUP_SCHEMA = "urn:ietf:params:scim:schemas:core:2.0:Group"
SCIM_ENTERPRISE_SCHEMA = (
    "urn:ietf:params:scim:schemas:extension:enterprise:2.0:User"
)
SCIM_PATCH_SCHEMA = "urn:ietf:params:scim:api:messages:2.0:PatchOp"
SCIM_LIST_SCHEMA = "urn:ietf:params:scim:api:messages:2.0:ListResponse"
SCIM_ERROR_SCHEMA = "urn:ietf:params:scim:api:messages:2.0:Error"

MAX_BODY_BYTES = 64 * 1024
MAX_PAGE_COUNT = 100
MAX_PATCH_OPERATIONS = 20
MAX_GROUP_MEMBERS = 1000
MAX_EMAILS = 5
MAX_NAME = 200
MAX_IDENTIFIER = 320
MAX_EXTERNAL_ID = 255
# Les quotas viennent de `ratelimit` : une seule declaration, pour que la
# politique ne puisse pas diverger entre le module qui l'applique et celui qui
# la decrit. Les valeurs sont celles de la Phase 36, inchangees.
RATE_WINDOW_SECONDS = ratelimit.SCIM_PER_CREDENTIAL.window_seconds
RATE_PER_CREDENTIAL = ratelimit.SCIM_PER_CREDENTIAL
RATE_PER_TENANT = ratelimit.SCIM_PER_TENANT


def install_schema(conn: database.Connection) -> None:
    """Install the additive SCIM schema.  No existing identity row is rebuilt."""
    conn.executescript(
        """
        CREATE UNIQUE INDEX IF NOT EXISTS idx_users_org_user_id
            ON users(organization_id, user_id);
        CREATE TABLE IF NOT EXISTS scim_credentials(
            credential_id TEXT PRIMARY KEY,
            organization_id TEXT NOT NULL,
            name TEXT NOT NULL,
            provider_context TEXT NOT NULL DEFAULT 'generic',
            token_hash TEXT NOT NULL UNIQUE,
            created_at REAL NOT NULL,
            last_used_at REAL,
            revoked_at REAL,
            expires_at REAL,
            created_by_user_id TEXT,
            FOREIGN KEY (organization_id) REFERENCES organizations(id)
        );
        CREATE INDEX IF NOT EXISTS idx_scim_credentials_org
            ON scim_credentials(organization_id, revoked_at);

        CREATE TABLE IF NOT EXISTS scim_user_management(
            organization_id TEXT NOT NULL,
            user_id TEXT NOT NULL,
            external_id TEXT,
            user_name TEXT NOT NULL,
            normalized_user_name TEXT NOT NULL,
            given_name TEXT NOT NULL DEFAULT '',
            family_name TEXT NOT NULL DEFAULT '',
            display_name TEXT NOT NULL DEFAULT '',
            department TEXT,
            enterprise_organization TEXT,
            manager_value TEXT,
            employee_number TEXT,
            created_at REAL NOT NULL,
            updated_at REAL NOT NULL,
            last_provisioned_at REAL NOT NULL,
            deprovisioned_at REAL,
            PRIMARY KEY (organization_id, user_id),
            UNIQUE (organization_id, external_id),
            UNIQUE (organization_id, normalized_user_name),
            FOREIGN KEY (organization_id, user_id)
                REFERENCES users(organization_id, user_id)
        );
        CREATE INDEX IF NOT EXISTS idx_scim_users_external
            ON scim_user_management(organization_id, external_id);

        CREATE TABLE IF NOT EXISTS organization_groups(
            organization_id TEXT NOT NULL,
            group_id TEXT NOT NULL,
            external_id TEXT,
            display_name TEXT NOT NULL,
            created_at REAL NOT NULL,
            updated_at REAL NOT NULL,
            deleted_at REAL,
            PRIMARY KEY (organization_id, group_id),
            UNIQUE (organization_id, external_id),
            FOREIGN KEY (organization_id) REFERENCES organizations(id)
        );
        CREATE INDEX IF NOT EXISTS idx_scim_groups_name
            ON organization_groups(organization_id, display_name);

        CREATE TABLE IF NOT EXISTS organization_group_memberships(
            organization_id TEXT NOT NULL,
            group_id TEXT NOT NULL,
            user_id TEXT NOT NULL,
            created_at REAL NOT NULL,
            PRIMARY KEY (organization_id, group_id, user_id),
            FOREIGN KEY (organization_id, group_id)
                REFERENCES organization_groups(organization_id, group_id)
                ON DELETE CASCADE,
            FOREIGN KEY (organization_id, user_id)
                REFERENCES users(organization_id, user_id) ON DELETE CASCADE
        );
        CREATE INDEX IF NOT EXISTS idx_scim_group_members_user
            ON organization_group_memberships(organization_id, user_id);

        CREATE TABLE IF NOT EXISTS scim_activity(
            activity_id TEXT PRIMARY KEY,
            organization_id TEXT NOT NULL,
            credential_id TEXT,
            action TEXT NOT NULL,
            success INTEGER NOT NULL,
            error_code TEXT,
            target_type TEXT,
            target_id TEXT,
            created_at REAL NOT NULL,
            FOREIGN KEY (organization_id) REFERENCES organizations(id)
        );
        CREATE INDEX IF NOT EXISTS idx_scim_activity_org
            ON scim_activity(organization_id, created_at);
        """
    )


class ScimError(Exception):
    def __init__(
        self,
        status: int,
        detail: str,
        scim_type: str | None = None,
        code: str | None = None,
    ) -> None:
        super().__init__(detail)
        self.status = status
        self.detail = detail
        self.scim_type = scim_type
        self.code = code or f"SCIM_{status}"


class SCIMPrincipal:
    """Machine principal.  It is never interchangeable with a Nova admin."""

    def __init__(
        self,
        credential_id: str,
        organization_id: str,
        credential_name: str,
        provider_context: str,
    ) -> None:
        self.credential_id = credential_id
        self.organization_id = organization_id
        self.credential_name = credential_name
        self.provider_context = provider_context


@dataclass(frozen=True)
class Backend:
    database: Callable[[], Any]
    hash_token: Callable[[str], str]
    user_by_id: Callable[[database.Connection, str], Any]
    user_by_email: Callable[[database.Connection, str, str], Any]
    create_user: Callable[..., str]
    update_user_email: Callable[[database.Connection, str, str], None]
    normalize_email: Callable[[str], str]
    account_status: Callable[[Any], str]
    security_role_for: Callable[[Any], str]
    guard_last_identity_manager: Callable[[database.Connection, str, str], None]
    require_organization_access: Callable[[Any, str], None]
    admin_read_guard: Callable[..., Any]
    admin_manage_guard: Callable[..., Any]
    # `scope`, `identite`, `quota`. Leve une erreur HTTP si le quota est
    # depasse ou si le compteur partage est injoignable sur une route
    # fail-closed. Injectee plutot qu'importee : ce module ne connait ni Redis
    # ni FastAPI, et n'a pas a les connaitre.
    rate_limit: Callable[[str, str, Any], None]


class CredentialCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=120)
    provider_context: str = Field(default="generic", min_length=1, max_length=80)
    expires_at: float | None = None


# Le compteur en memoire de processus qui vivait ici a ete retire. Derriere
# quatre workers, il autorisait quatre fois la limite annoncee — et la console
# presentait la protection comme appliquee. La limitation passe desormais par
# `ratelimit`, partage entre tous les workers, injecte par le backend.


def _json(payload: Any, status: int = 200, headers: dict[str, str] | None = None):
    return JSONResponse(payload, status_code=status, media_type=SCIM_MEDIA_TYPE, headers=headers)


def _normalize_user_name(value: str) -> str:
    return value.strip().lower()


def _bounded_string(
    value: Any,
    field: str,
    maximum: int,
    *,
    required: bool = False,
) -> str | None:
    if value is None:
        if required:
            raise ScimError(400, f"{field} is required.", "invalidValue", "INVALID_VALUE")
        return None
    if not isinstance(value, str):
        raise ScimError(400, f"{field} must be a string.", "invalidValue", "INVALID_VALUE")
    value = value.strip()
    if required and not value:
        raise ScimError(400, f"{field} is required.", "invalidValue", "INVALID_VALUE")
    if len(value) > maximum:
        raise ScimError(400, f"{field} is too long.", "invalidValue", "LIMIT_EXCEEDED")
    return value


def _boolean(value: Any, field: str) -> bool:
    if isinstance(value, bool):
        return value
    # Entra has historically sent boolean PATCH values as strings.
    if isinstance(value, str) and value.lower() in {"true", "false"}:
        return value.lower() == "true"
    raise ScimError(400, f"{field} must be a boolean.", "invalidValue", "INVALID_VALUE")


def _email_like(value: str) -> bool:
    return bool(re.fullmatch(r"[^\s@]+@[^\s@]+\.[^\s@]+", value)) and len(value) <= 320


def _parse_emails(value: Any) -> str | None:
    if value is None:
        return None
    if not isinstance(value, list) or len(value) > MAX_EMAILS:
        raise ScimError(400, "emails must be a bounded array.", "invalidValue", "INVALID_VALUE")
    parsed: list[tuple[bool, str]] = []
    for item in value:
        if not isinstance(item, dict):
            raise ScimError(400, "Each email must be an object.", "invalidValue", "INVALID_VALUE")
        candidate = _bounded_string(item.get("value"), "emails.value", 320, required=True)
        if not _email_like(candidate or ""):
            raise ScimError(400, "emails.value is not a valid email address.", "invalidValue", "INVALID_VALUE")
        parsed.append((item.get("primary") is True, candidate or ""))
    if not parsed:
        return None
    primaries = [address for primary, address in parsed if primary]
    if len(primaries) > 1:
        raise ScimError(400, "Only one primary email is allowed.", "invalidValue", "INVALID_VALUE")
    return primaries[0] if primaries else parsed[0][1]


USER_ALLOWED = {
    "schemas", "id", "externalId", "userName", "active", "name",
    "displayName", "emails", "meta", SCIM_ENTERPRISE_SCHEMA,
}
GROUP_ALLOWED = {"schemas", "id", "externalId", "displayName", "members", "meta"}
FORBIDDEN_SECURITY_FIELDS = {
    "organization_id", "organizationId", "securityRole", "security_role",
    "memberType", "member_type", "businessRole", "role", "capabilities",
}


def _reject_unknown(payload: dict[str, Any], allowed: set[str]) -> None:
    forbidden = sorted(set(payload) & FORBIDDEN_SECURITY_FIELDS)
    if forbidden:
        raise ScimError(
            400,
            "Nova-managed authorization and tenant attributes are not SCIM-writable.",
            "mutability",
            "NOVA_ATTRIBUTE_FORBIDDEN",
        )
    unknown = sorted(set(payload) - allowed)
    if unknown:
        raise ScimError(400, "The request contains unsupported attributes.", "invalidValue", "UNSUPPORTED_ATTRIBUTE")


def _parse_user(payload: Any, *, require_user_name: bool) -> dict[str, Any]:
    if not isinstance(payload, dict):
        raise ScimError(400, "A SCIM User object is required.", "invalidSyntax", "INVALID_JSON")
    _reject_unknown(payload, USER_ALLOWED)
    schemas = payload.get("schemas")
    if schemas is not None and (not isinstance(schemas, list) or SCIM_USER_SCHEMA not in schemas):
        raise ScimError(400, "The core User schema is required.", "invalidValue", "SCHEMA_INVALID")
    user_name = _bounded_string(
        payload.get("userName"), "userName", MAX_IDENTIFIER, required=require_user_name
    )
    external_id = _bounded_string(payload.get("externalId"), "externalId", MAX_EXTERNAL_ID)
    active = _boolean(payload["active"], "active") if "active" in payload else None
    name = payload.get("name")
    if name is not None and not isinstance(name, dict):
        raise ScimError(400, "name must be an object.", "invalidValue", "INVALID_VALUE")
    name = name or {}
    unsupported_name = set(name) - {"givenName", "familyName"}
    if unsupported_name:
        raise ScimError(400, "The request contains unsupported name attributes.", "invalidValue", "UNSUPPORTED_ATTRIBUTE")
    enterprise = payload.get(SCIM_ENTERPRISE_SCHEMA)
    if enterprise is not None and not isinstance(enterprise, dict):
        raise ScimError(400, "The enterprise extension must be an object.", "invalidValue", "INVALID_VALUE")
    enterprise = enterprise or {}
    unsupported_enterprise = set(enterprise) - {
        "department", "organization", "manager", "employeeNumber"
    }
    if unsupported_enterprise:
        raise ScimError(400, "The request contains unsupported enterprise attributes.", "invalidValue", "UNSUPPORTED_ATTRIBUTE")
    manager = enterprise.get("manager")
    if manager is not None and not isinstance(manager, dict):
        raise ScimError(400, "manager must be an object.", "invalidValue", "INVALID_VALUE")
    return {
        "user_name": user_name,
        "external_id": external_id,
        "active": active,
        "given_name": _bounded_string(name.get("givenName"), "name.givenName", MAX_NAME),
        "family_name": _bounded_string(name.get("familyName"), "name.familyName", MAX_NAME),
        "display_name": _bounded_string(payload.get("displayName"), "displayName", MAX_NAME),
        "email": _parse_emails(payload.get("emails")),
        "department": _bounded_string(enterprise.get("department"), "department", MAX_NAME),
        "enterprise_organization": _bounded_string(enterprise.get("organization"), "organization", MAX_NAME),
        "manager_value": _bounded_string((manager or {}).get("value"), "manager.value", MAX_IDENTIFIER),
        "employee_number": _bounded_string(enterprise.get("employeeNumber"), "employeeNumber", MAX_IDENTIFIER),
    }


def _parse_group(payload: Any, *, require_display_name: bool) -> dict[str, Any]:
    if not isinstance(payload, dict):
        raise ScimError(400, "A SCIM Group object is required.", "invalidSyntax", "INVALID_JSON")
    _reject_unknown(payload, GROUP_ALLOWED)
    schemas = payload.get("schemas")
    if schemas is not None and (not isinstance(schemas, list) or SCIM_GROUP_SCHEMA not in schemas):
        raise ScimError(400, "The core Group schema is required.", "invalidValue", "SCHEMA_INVALID")
    members = payload.get("members")
    member_ids: list[str] | None = None
    if members is not None:
        if not isinstance(members, list) or len(members) > MAX_GROUP_MEMBERS:
            raise ScimError(400, "members must be a bounded array.", "invalidValue", "LIMIT_EXCEEDED")
        member_ids = []
        for member in members:
            if not isinstance(member, dict):
                raise ScimError(400, "Each member must be an object.", "invalidValue", "INVALID_VALUE")
            value = _bounded_string(member.get("value"), "members.value", MAX_IDENTIFIER, required=True)
            if value not in member_ids:
                member_ids.append(value or "")
    return {
        "external_id": _bounded_string(payload.get("externalId"), "externalId", MAX_EXTERNAL_ID),
        "display_name": _bounded_string(
            payload.get("displayName"), "displayName", MAX_NAME, required=require_display_name
        ),
        "members": member_ids,
    }


async def _body(request: Request) -> Any:
    content_type = request.headers.get("content-type", "").split(";", 1)[0].strip().lower()
    if content_type not in {SCIM_MEDIA_TYPE, "application/json"}:
        raise ScimError(415, "Content-Type must be application/scim+json or application/json.", code="CONTENT_TYPE_INVALID")
    declared = request.headers.get("content-length")
    if declared:
        try:
            if int(declared) > MAX_BODY_BYTES:
                raise ScimError(413, "SCIM request body is too large.", code="BODY_TOO_LARGE")
        except ValueError:
            raise ScimError(400, "Invalid Content-Length.", "invalidSyntax", "INVALID_SYNTAX")
    raw = await request.body()
    if len(raw) > MAX_BODY_BYTES:
        raise ScimError(413, "SCIM request body is too large.", code="BODY_TOO_LARGE")
    try:
        return json.loads(raw)
    except (UnicodeDecodeError, json.JSONDecodeError):
        raise ScimError(400, "Malformed JSON.", "invalidSyntax", "INVALID_JSON")


FILTER_RE = re.compile(
    r'^\s*(id|userName|externalId|active|displayName)\s+eq\s+(?:"((?:[^"\\]|\\.)*)"|(true|false))\s*$',
    re.IGNORECASE,
)


def _parse_filter(value: str | None, allowed: set[str]) -> tuple[str, Any] | None:
    if value is None or not value.strip():
        return None
    if len(value) > 512:
        raise ScimError(400, "Filter is too long.", "invalidFilter", "FILTER_INVALID")
    matched = FILTER_RE.fullmatch(value)
    if not matched:
        raise ScimError(400, "Unsupported or malformed SCIM filter.", "invalidFilter", "FILTER_INVALID")
    attribute = matched.group(1)
    canonical = {item.lower(): item for item in allowed}.get(attribute.lower())
    if canonical is None:
        raise ScimError(400, "Unsupported SCIM filter attribute.", "invalidFilter", "FILTER_INVALID")
    if matched.group(3) is not None:
        parsed: Any = matched.group(3).lower() == "true"
    else:
        try:
            parsed = json.loads(f'"{matched.group(2)}"')
        except json.JSONDecodeError:
            raise ScimError(400, "Malformed SCIM filter string.", "invalidFilter", "FILTER_INVALID")
    return canonical, parsed


def _pagination(start_index: int, count: int) -> tuple[int, int]:
    if start_index < 1 or count < 0:
        raise ScimError(400, "Invalid pagination parameters.", "invalidValue", "PAGINATION_INVALID")
    return start_index, min(count, MAX_PAGE_COUNT)


def _activity(
    conn: database.Connection,
    principal: SCIMPrincipal,
    action: str,
    success: bool,
    *,
    error_code: str | None = None,
    target_type: str | None = None,
    target_id: str | None = None,
) -> None:
    conn.execute(
        "INSERT INTO scim_activity(activity_id, organization_id, credential_id, "
        "action, success, error_code, target_type, target_id, created_at) "
        "VALUES (?,?,?,?,?,?,?,?,?)",
        (
            uuid.uuid4().hex, principal.organization_id, principal.credential_id,
            action, 1 if success else 0, error_code, target_type, target_id, time.time(),
        ),
    )


def _audit(
    conn: database.Connection,
    organization_id: str,
    action: str,
    target_type: str,
    target_id: str,
    metadata: dict[str, Any] | None,
    actor: Any | None = None,
) -> None:
    conn.execute(
        "INSERT INTO admin_audit_log(id, organization_id, actor_user_id, actor_role, "
        "action, target_type, target_id, request_id, metadata, created_at) "
        "VALUES (?,?,?,?,?,?,?,?,?,?)",
        (
            uuid.uuid4().hex,
            organization_id,
            getattr(actor, "user_id", None),
            getattr(actor, "security_role", "scim"),
            action,
            target_type,
            target_id,
            uuid.uuid4().hex,
            json.dumps(metadata, sort_keys=True) if metadata else None,
            time.time(),
        ),
    )


def _managed_user(conn: database.Connection, organization_id: str, user_id: str, *, include_deleted: bool = False):
    deleted = "" if include_deleted else "AND m.deprovisioned_at IS NULL"
    return conn.execute(
        "SELECT m.organization_id AS scim_organization_id, m.user_id AS scim_user_id, "
        "m.external_id, m.user_name, m.normalized_user_name, m.given_name, "
        "m.family_name, m.display_name, m.department, m.enterprise_organization, "
        "m.manager_value, m.employee_number, m.created_at AS scim_created_at, "
        "m.updated_at AS scim_updated_at, m.last_provisioned_at, m.deprovisioned_at, "
        "u.* FROM scim_user_management m JOIN users u ON u.user_id=m.user_id "
        "AND u.organization_id=m.organization_id "
        f"WHERE m.organization_id=? AND m.user_id=? {deleted}",
        (organization_id, user_id),
    ).fetchone()


def _user_location(request: Request, user_id: str) -> str:
    return str(request.base_url).rstrip("/") + f"/scim/v2/Users/{user_id}"


def _group_location(request: Request, group_id: str) -> str:
    return str(request.base_url).rstrip("/") + f"/scim/v2/Groups/{group_id}"


def _user_view(row: Any, request: Request, backend: Backend) -> dict[str, Any]:
    schemas = [SCIM_USER_SCHEMA]
    enterprise = {
        "department": row["department"],
        "organization": row["enterprise_organization"],
        "employeeNumber": row["employee_number"],
    }
    if row["manager_value"]:
        enterprise["manager"] = {"value": row["manager_value"]}
    enterprise = {key: value for key, value in enterprise.items() if value not in {None, ""}}
    result: dict[str, Any] = {
        "schemas": schemas,
        "id": row["scim_user_id"],
        "userName": row["user_name"],
        "active": backend.account_status(row) == "active" and row["deprovisioned_at"] is None,
        "name": {
            "givenName": row["given_name"],
            "familyName": row["family_name"],
        },
        "displayName": row["display_name"],
        "emails": [{"value": row["email"], "type": "work", "primary": True}],
        "meta": {
            "resourceType": "User",
            "created": row["scim_created_at"],
            "lastModified": row["scim_updated_at"],
            "location": _user_location(request, row["scim_user_id"]),
        },
    }
    if row["external_id"] is not None:
        result["externalId"] = row["external_id"]
    if enterprise:
        schemas.append(SCIM_ENTERPRISE_SCHEMA)
        result[SCIM_ENTERPRISE_SCHEMA] = enterprise
    return result


def _group_row(conn: database.Connection, organization_id: str, group_id: str, *, include_deleted: bool = False):
    deleted = "" if include_deleted else "AND deleted_at IS NULL"
    # Un groupe cree dans la console n'existe pas pour l'annuaire.
    return conn.execute(
        f"SELECT * FROM organization_groups WHERE organization_id=? AND group_id=? "
        f"AND source != 'console' {deleted}",
        (organization_id, group_id),
    ).fetchone()


def _group_view(conn: database.Connection, row: Any, request: Request) -> dict[str, Any]:
    members = conn.execute(
        "SELECT gm.user_id, u.email FROM organization_group_memberships gm "
        "JOIN users u ON u.user_id=gm.user_id AND u.organization_id=gm.organization_id "
        "WHERE gm.organization_id=? AND gm.group_id=? ORDER BY gm.user_id",
        (row["organization_id"], row["group_id"]),
    ).fetchall()
    result: dict[str, Any] = {
        "schemas": [SCIM_GROUP_SCHEMA],
        "id": row["group_id"],
        "displayName": row["display_name"],
        "members": [
            {
                "value": member["user_id"],
                "$ref": _user_location(request, member["user_id"]),
                "display": member["email"],
                "type": "User",
            }
            for member in members
        ],
        "meta": {
            "resourceType": "Group",
            "created": row["created_at"],
            "lastModified": row["updated_at"],
            "location": _group_location(request, row["group_id"]),
        },
    }
    if row["external_id"] is not None:
        result["externalId"] = row["external_id"]
    return result


def _ensure_group_members(
    conn: database.Connection,
    organization_id: str,
    member_ids: list[str],
) -> None:
    if len(member_ids) > MAX_GROUP_MEMBERS:
        raise ScimError(400, "Too many group members.", "tooMany", "LIMIT_EXCEEDED")
    for user_id in member_ids:
        row = _managed_user(conn, organization_id, user_id)
        if row is None:
            # Same response for a foreign-tenant identifier and a random one.
            raise ScimError(400, "A group member is not a User in this tenant.", "invalidValue", "MEMBER_INVALID")


def _set_active(conn: database.Connection, backend: Backend, organization_id: str, user_id: str, active: bool) -> tuple[str, str]:
    user = backend.user_by_id(conn, user_id)
    if user is None or user["organization_id"] != organization_id:
        raise ScimError(404, "Resource not found.", code="USER_NOT_FOUND")
    previous = backend.account_status(user)
    wanted = "active" if active else "disabled"
    if not active:
        try:
            backend.guard_last_identity_manager(conn, organization_id, user_id)
        except Exception as error:
            # The existing guard raises an HTTPException.  Never leak its localized detail.
            if getattr(error, "status_code", None) == 409:
                raise ScimError(409, "The last identity manager cannot be suspended.", "mutability", "LAST_IDENTITY_MANAGER")
            raise
    conn.execute(
        "UPDATE users SET disabled=?, status=?, updated_at=? WHERE user_id=? AND organization_id=?",
        (0 if active else 1, wanted, time.time(), user_id, organization_id),
    )
    if not active:
        conn.execute(
            "UPDATE tokens SET active=0 WHERE user_id=? AND organization_id=?",
            (user_id, organization_id),
        )
        conn.execute(
            "UPDATE admin_sessions SET revoked_at=? WHERE user_id=? AND organization_id=? "
            "AND revoked_at IS NULL",
            (time.time(), user_id, organization_id),
        )
    return previous, wanted


def _find_reconciliation_user(
    conn: database.Connection,
    backend: Backend,
    principal: SCIMPrincipal,
    external_id: str | None,
    email: str,
):
    if external_id and principal.provider_context != "generic":
        strong = conn.execute(
            "SELECT DISTINCT u.* FROM federated_identities f JOIN users u "
            "ON u.user_id=f.user_id AND u.organization_id=f.organization_id "
            "WHERE f.organization_id=? AND f.provider=? AND f.external_subject=? "
            "AND f.verification='verified'",
            (principal.organization_id, principal.provider_context, external_id),
        ).fetchall()
        if len(strong) > 1:
            raise ScimError(409, "The external identity is ambiguous.", "uniqueness", "IDENTITY_CONFLICT")
        if strong:
            return strong[0]
    # Email fallback is tenant-scoped and only possible because the users table
    # enforces one normalized address per organization.
    return backend.user_by_email(conn, principal.organization_id, email)


def _apply_user_fields(
    conn: database.Connection,
    backend: Backend,
    principal: SCIMPrincipal,
    row: Any,
    fields: dict[str, Any],
    *,
    replace: bool,
) -> tuple[Any, set[str], str | None]:
    user_id = row["scim_user_id"]
    changed: set[str] = set()
    current_email = row["email"]
    email = fields.get("email")
    if email is not None and backend.normalize_email(email) != backend.normalize_email(current_email):
        collision = backend.user_by_email(conn, principal.organization_id, email)
        if collision is not None and collision["user_id"] != user_id:
            raise ScimError(409, "Email already belongs to another User.", "uniqueness", "EMAIL_CONFLICT")
        backend.update_user_email(conn, user_id, email)
        conn.execute(
            "UPDATE federated_identities SET email=? WHERE user_id=? AND organization_id=?",
            (email, user_id, principal.organization_id),
        )
        changed.add("emails")

    mapping = {
        "external_id": "external_id",
        "user_name": "user_name",
        "given_name": "given_name",
        "family_name": "family_name",
        "display_name": "display_name",
        "department": "department",
        "enterprise_organization": "enterprise_organization",
        "manager_value": "manager_value",
        "employee_number": "employee_number",
    }
    updates: dict[str, Any] = {}
    for field, column in mapping.items():
        value = fields.get(field)
        if value is None and not replace:
            continue
        if field == "user_name" and value is None:
            continue  # userName is required and cannot be removed by PUT.
        wanted = "" if value is None and column in {"given_name", "family_name", "display_name"} else value
        if row[column] != wanted:
            updates[column] = wanted
            changed.add("name" if column in {"given_name", "family_name"} else field)
    if "user_name" in updates:
        updates["normalized_user_name"] = _normalize_user_name(updates["user_name"])
    if updates:
        assignments = ", ".join(f"{column}=?" for column in updates)
        try:
            conn.execute(
                f"UPDATE scim_user_management SET {assignments}, updated_at=?, "
                "last_provisioned_at=? WHERE organization_id=? AND user_id=?",
                (*updates.values(), time.time(), time.time(), principal.organization_id, user_id),
            )
        except database.IntegrityError:
            raise ScimError(409, "externalId or userName already exists.", "uniqueness", "IDENTITY_CONFLICT")
    else:
        conn.execute(
            "UPDATE scim_user_management SET last_provisioned_at=? "
            "WHERE organization_id=? AND user_id=?",
            (time.time(), principal.organization_id, user_id),
        )

    status_action: str | None = None
    if fields.get("active") is not None:
        before = backend.account_status(row)
        wanted_active = fields["active"]
        if (before == "active") != wanted_active or row["deprovisioned_at"] is not None:
            previous, wanted = _set_active(
                conn, backend, principal.organization_id, user_id, wanted_active
            )
            if wanted_active:
                conn.execute(
                    "UPDATE scim_user_management SET deprovisioned_at=NULL, updated_at=? "
                    "WHERE organization_id=? AND user_id=?",
                    (time.time(), principal.organization_id, user_id),
                )
                status_action = "scim_user_reactivated"
            else:
                status_action = "scim_user_suspended"
            changed.add("active")
    return _managed_user(conn, principal.organization_id, user_id, include_deleted=True), changed, status_action


def install_routes(app: Any, backend: Backend) -> None:
    """Register SCIM protocol and Nova Admin provisioning endpoints."""

    async def scim_principal(
        request: Request, authorization: str = Header(default="")
    ) -> SCIMPrincipal:
        if not authorization.startswith("Bearer "):
            raise ScimError(401, "A valid SCIM bearer credential is required.", code="INVALID_CREDENTIAL")
        token = authorization.removeprefix("Bearer ").strip()
        if not token or len(token) > 512:
            raise ScimError(401, "A valid SCIM bearer credential is required.", code="INVALID_CREDENTIAL")
        digest = backend.hash_token(token)
        now = time.time()
        with backend.database() as conn:
            row = conn.execute(
                "SELECT * FROM scim_credentials WHERE token_hash=?",
                (digest,),
            ).fetchone()
            if row is None or row["revoked_at"] is not None or (
                row["expires_at"] is not None and row["expires_at"] <= now
            ):
                raise ScimError(401, "A valid SCIM bearer credential is required.", code="INVALID_CREDENTIAL")
            principal = SCIMPrincipal(
                row["credential_id"], row["organization_id"], row["name"], row["provider_context"]
            )
            backend.rate_limit("credential", principal.credential_id, RATE_PER_CREDENTIAL)
            backend.rate_limit("tenant", principal.organization_id, RATE_PER_TENANT)
            conn.execute(
                "UPDATE scim_credentials SET last_used_at=? WHERE credential_id=?",
                (now, principal.credential_id),
            )
        request.state.scim_principal = principal
        return principal

    @app.exception_handler(ScimError)
    async def scim_error_handler(request: Request, error: ScimError):
        principal = getattr(request.state, "scim_principal", None)
        if principal is not None:
            try:
                with backend.database() as conn:
                    _activity(conn, principal, request.method.lower(), False, error_code=error.code)
            except Exception:
                # The safe protocol error must not turn into a stack trace because
                # diagnostics storage is unavailable.
                pass
        payload: dict[str, Any] = {
            "schemas": [SCIM_ERROR_SCHEMA],
            "status": str(error.status),
            "detail": error.detail,
        }
        if error.scim_type:
            payload["scimType"] = error.scim_type
        headers = {"WWW-Authenticate": "Bearer"} if error.status == 401 else None
        return _json(payload, error.status, headers)

    # --------------------------- discovery documents
    @app.get("/scim/v2/ServiceProviderConfig")
    def service_provider_config(
        principal: SCIMPrincipal = Depends(scim_principal),
    ):
        return _json({
            "schemas": ["urn:ietf:params:scim:schemas:core:2.0:ServiceProviderConfig"],
            "patch": {"supported": True},
            "bulk": {"supported": False, "maxOperations": 0, "maxPayloadSize": 0},
            "filter": {"supported": True, "maxResults": MAX_PAGE_COUNT},
            "changePassword": {"supported": False},
            "sort": {"supported": False},
            "etag": {"supported": False},
            "authenticationSchemes": [{
                "type": "oauthbearertoken",
                "name": "Bearer Token",
                "description": "Tenant-scoped Nova SCIM credential",
                "primary": True,
            }],
        })

    @app.get("/scim/v2/ResourceTypes")
    def resource_types(principal: SCIMPrincipal = Depends(scim_principal)):
        return _json({
            "schemas": [SCIM_LIST_SCHEMA],
            "totalResults": 2,
            "startIndex": 1,
            "itemsPerPage": 2,
            "Resources": [
                {
                    "schemas": ["urn:ietf:params:scim:schemas:core:2.0:ResourceType"],
                    "id": "User", "name": "User", "endpoint": "/Users",
                    "schema": SCIM_USER_SCHEMA,
                    "schemaExtensions": [{"schema": SCIM_ENTERPRISE_SCHEMA, "required": False}],
                },
                {
                    "schemas": ["urn:ietf:params:scim:schemas:core:2.0:ResourceType"],
                    "id": "Group", "name": "Group", "endpoint": "/Groups",
                    "schema": SCIM_GROUP_SCHEMA,
                },
            ],
        })

    @app.get("/scim/v2/Schemas")
    def schemas(principal: SCIMPrincipal = Depends(scim_principal)):
        resources = [
            {
                "schemas": ["urn:ietf:params:scim:schemas:core:2.0:Schema"],
                "id": SCIM_USER_SCHEMA,
                "name": "User",
                "description": "Nova SCIM User",
                "attributes": [
                    {"name": "userName", "type": "string", "multiValued": False, "required": True, "mutability": "readWrite", "returned": "default", "uniqueness": "server"},
                    {"name": "externalId", "type": "string", "multiValued": False, "required": False, "mutability": "readWrite", "returned": "default", "uniqueness": "none"},
                    {"name": "active", "type": "boolean", "multiValued": False, "required": False, "mutability": "readWrite", "returned": "default", "uniqueness": "none"},
                    {"name": "displayName", "type": "string", "multiValued": False, "required": False, "mutability": "readWrite", "returned": "default", "uniqueness": "none"},
                    {"name": "name", "type": "complex", "multiValued": False, "required": False, "mutability": "readWrite", "returned": "default", "uniqueness": "none", "subAttributes": [
                        {"name": "givenName", "type": "string", "multiValued": False, "required": False, "mutability": "readWrite", "returned": "default", "uniqueness": "none"},
                        {"name": "familyName", "type": "string", "multiValued": False, "required": False, "mutability": "readWrite", "returned": "default", "uniqueness": "none"},
                    ]},
                    {"name": "emails", "type": "complex", "multiValued": True, "required": False, "mutability": "readWrite", "returned": "default", "uniqueness": "none", "subAttributes": [
                        {"name": "value", "type": "string", "multiValued": False, "required": True, "mutability": "readWrite", "returned": "default", "uniqueness": "none"},
                        {"name": "type", "type": "string", "multiValued": False, "required": False, "mutability": "readWrite", "returned": "default", "uniqueness": "none"},
                        {"name": "primary", "type": "boolean", "multiValued": False, "required": False, "mutability": "readWrite", "returned": "default", "uniqueness": "none"},
                    ]},
                ],
            },
            {
                "schemas": ["urn:ietf:params:scim:schemas:core:2.0:Schema"],
                "id": SCIM_GROUP_SCHEMA,
                "name": "Group",
                "description": "Nova organizational group (never a security role)",
                "attributes": [
                    {"name": "displayName", "type": "string", "multiValued": False, "required": True, "mutability": "readWrite", "returned": "default", "uniqueness": "none"},
                    {"name": "externalId", "type": "string", "multiValued": False, "required": False, "mutability": "readWrite", "returned": "default", "uniqueness": "none"},
                    {"name": "members", "type": "complex", "multiValued": True, "required": False, "mutability": "readWrite", "returned": "default", "uniqueness": "none", "subAttributes": [
                        {"name": "value", "type": "string", "multiValued": False, "required": True, "mutability": "immutable", "returned": "default", "uniqueness": "none"},
                        {"name": "$ref", "type": "reference", "referenceTypes": ["User"], "multiValued": False, "required": False, "mutability": "immutable", "returned": "default", "uniqueness": "none"},
                        {"name": "type", "type": "string", "canonicalValues": ["User"], "multiValued": False, "required": False, "mutability": "immutable", "returned": "default", "uniqueness": "none"},
                    ]},
                ],
            },
            {
                "schemas": ["urn:ietf:params:scim:schemas:core:2.0:Schema"],
                "id": SCIM_ENTERPRISE_SCHEMA,
                "name": "EnterpriseUser",
                "description": "Supported non-authorizing enterprise attributes",
                "attributes": [
                    {
                        "name": name,
                        "type": "complex" if name == "manager" else "string",
                        "multiValued": False,
                        "required": False,
                        "mutability": "readWrite",
                        "returned": "default",
                        "uniqueness": "none",
                        **({"subAttributes": [
                            {"name": "value", "type": "string", "multiValued": False, "required": False, "mutability": "readWrite", "returned": "default", "uniqueness": "none"}
                        ]} if name == "manager" else {}),
                    }
                    for name in ("department", "organization", "manager", "employeeNumber")
                ],
            },
        ]
        return _json({"schemas": [SCIM_LIST_SCHEMA], "totalResults": 3, "startIndex": 1, "itemsPerPage": 3, "Resources": resources})

    @app.get("/scim/v2/Schemas/{schema_id:path}")
    def schema_by_id(schema_id: str, principal: SCIMPrincipal = Depends(scim_principal)):
        collection = json.loads(schemas(principal).body)
        resource = next(
            (item for item in collection["Resources"] if item["id"] == schema_id),
            None,
        )
        if resource is None:
            raise ScimError(404, "Resource not found.", code="SCHEMA_NOT_FOUND")
        return _json(resource)

    # --------------------------- Users
    @app.post("/scim/v2/Users")
    async def create_scim_user(request: Request, principal: SCIMPrincipal = Depends(scim_principal)):
        fields = _parse_user(await _body(request), require_user_name=True)
        user_name = fields["user_name"] or ""
        email = fields["email"] or (user_name if _email_like(user_name) else None)
        if email is None:
            raise ScimError(400, "A primary email is required when userName is not an email address.", "invalidValue", "EMAIL_REQUIRED")
        now = time.time()
        with backend.database() as conn:
            # Serialize the lookup/create window.  UNIQUE constraints remain the
            # final guard, while BEGIN IMMEDIATE prevents two workers from both
            # deciding that the same externalId/userName is absent.
            conn.begin_immediate()
            existing_management = None
            if fields["external_id"]:
                existing_management = conn.execute(
                    "SELECT user_id FROM scim_user_management WHERE organization_id=? AND external_id=?",
                    (principal.organization_id, fields["external_id"]),
                ).fetchone()
            if existing_management is None:
                existing_management = conn.execute(
                    "SELECT user_id FROM scim_user_management WHERE organization_id=? AND normalized_user_name=?",
                    (principal.organization_id, _normalize_user_name(user_name)),
                ).fetchone()
            if existing_management is not None:
                row = _managed_user(conn, principal.organization_id, existing_management["user_id"], include_deleted=True)
                updated, changed, status_action = _apply_user_fields(
                    conn, backend, principal, row, {**fields, "active": fields["active"] if fields["active"] is not None else True}, replace=False
                )
                if status_action:
                    _audit(conn, principal.organization_id, status_action, "user", updated["scim_user_id"], {"fields": ["active"]})
                _activity(conn, principal, "user_retry", True, target_type="user", target_id=updated["scim_user_id"])
                return _json(_user_view(updated, request, backend), 200)

            user = _find_reconciliation_user(conn, backend, principal, fields["external_id"], email)
            created = user is None
            if created:
                user_id = backend.create_user(
                    conn,
                    principal.organization_id,
                    email,
                    role="partner",
                    security_role="member",
                    disabled=fields["active"] is False,
                    status="disabled" if fields["active"] is False else "active",
                )
            else:
                user_id = user["user_id"]
                if fields["active"] is False:
                    _set_active(conn, backend, principal.organization_id, user_id, False)
            try:
                conn.execute(
                    "INSERT INTO scim_user_management(organization_id, user_id, external_id, "
                    "user_name, normalized_user_name, given_name, family_name, display_name, "
                    "department, enterprise_organization, manager_value, employee_number, "
                    "created_at, updated_at, last_provisioned_at) "
                    "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    (
                        principal.organization_id, user_id, fields["external_id"], user_name,
                        _normalize_user_name(user_name), fields["given_name"] or "",
                        fields["family_name"] or "", fields["display_name"] or "",
                        fields["department"], fields["enterprise_organization"],
                        fields["manager_value"], fields["employee_number"], now, now, now,
                    ),
                )
            except database.IntegrityError:
                raise ScimError(409, "externalId or userName already exists.", "uniqueness", "IDENTITY_CONFLICT")
            row = _managed_user(conn, principal.organization_id, user_id)
            # Invariant asserted at the trust boundary, including reconciliation.
            if created and backend.security_role_for(row) != "member":
                raise ScimError(500, "Provisioning invariant failed.", code="SECURITY_INVARIANT")
            _audit(
                conn, principal.organization_id, "scim_user_created", "user", user_id,
                {"reconciled": not created, "attributes": sorted(key for key, value in fields.items() if value is not None)},
            )
            _activity(conn, principal, "user_create", True, target_type="user", target_id=user_id)
        return _json(
            _user_view(row, request, backend), 201,
            {"Location": _user_location(request, user_id)},
        )

    @app.get("/scim/v2/Users")
    def list_scim_users(
        request: Request,
        filter: str | None = None,
        startIndex: int = 1,
        count: int = 100,
        principal: SCIMPrincipal = Depends(scim_principal),
    ):
        start, bounded_count = _pagination(startIndex, count)
        parsed = _parse_filter(filter, {"id", "userName", "externalId", "active"})
        clauses = ["m.organization_id=?", "m.deprovisioned_at IS NULL"]
        values: list[Any] = [principal.organization_id]
        if parsed:
            attribute, value = parsed
            if attribute == "id":
                clauses.append("m.user_id=?")
                values.append(value)
            elif attribute == "userName":
                clauses.append("m.normalized_user_name=?")
                values.append(_normalize_user_name(value))
            elif attribute == "externalId":
                clauses.append("m.external_id=?")
                values.append(value)
            else:
                clauses.append("u.disabled=? AND u.status=?")
                values.extend([0 if value else 1, "active" if value else "disabled"])
        where = " AND ".join(clauses)
        with backend.database() as conn:
            total = conn.execute(
                "SELECT COUNT(*) AS c FROM scim_user_management m JOIN users u "
                f"ON u.user_id=m.user_id AND u.organization_id=m.organization_id WHERE {where}",
                tuple(values),
            ).fetchone()["c"]
            rows = conn.execute(
                "SELECT m.organization_id AS scim_organization_id, m.user_id AS scim_user_id, "
                "m.external_id, m.user_name, m.normalized_user_name, m.given_name, m.family_name, "
                "m.display_name, m.department, m.enterprise_organization, m.manager_value, "
                "m.employee_number, m.created_at AS scim_created_at, m.updated_at AS scim_updated_at, "
                "m.last_provisioned_at, m.deprovisioned_at, u.* FROM scim_user_management m "
                "JOIN users u ON u.user_id=m.user_id AND u.organization_id=m.organization_id "
                f"WHERE {where} ORDER BY m.user_id LIMIT ? OFFSET ?",
                (*values, bounded_count, start - 1),
            ).fetchall()
        return _json({
            "schemas": [SCIM_LIST_SCHEMA],
            "totalResults": total,
            "startIndex": start,
            "itemsPerPage": len(rows),
            "Resources": [_user_view(row, request, backend) for row in rows],
        })

    @app.get("/scim/v2/Users/{user_id}")
    def get_scim_user(request: Request, user_id: str, principal: SCIMPrincipal = Depends(scim_principal)):
        with backend.database() as conn:
            row = _managed_user(conn, principal.organization_id, user_id)
        if row is None:
            raise ScimError(404, "Resource not found.", code="USER_NOT_FOUND")
        return _json(_user_view(row, request, backend))

    @app.put("/scim/v2/Users/{user_id}")
    async def put_scim_user(request: Request, user_id: str, principal: SCIMPrincipal = Depends(scim_principal)):
        fields = _parse_user(await _body(request), require_user_name=True)
        with backend.database() as conn:
            row = _managed_user(conn, principal.organization_id, user_id)
            if row is None:
                raise ScimError(404, "Resource not found.", code="USER_NOT_FOUND")
            updated, changed, status_action = _apply_user_fields(conn, backend, principal, row, fields, replace=True)
            action = status_action or "scim_user_updated"
            _audit(conn, principal.organization_id, action, "user", user_id, {"attributes": sorted(changed)})
            _activity(conn, principal, "user_put", True, target_type="user", target_id=user_id)
        return _json(_user_view(updated, request, backend))

    @app.patch("/scim/v2/Users/{user_id}")
    async def patch_scim_user(request: Request, user_id: str, principal: SCIMPrincipal = Depends(scim_principal)):
        document = await _body(request)
        if not isinstance(document, dict) or set(document) - {"schemas", "Operations"}:
            raise ScimError(400, "A strict PatchOp document is required.", "invalidSyntax", "PATCH_INVALID")
        schemas_value = document.get("schemas")
        if not isinstance(schemas_value, list) or SCIM_PATCH_SCHEMA not in schemas_value:
            raise ScimError(400, "PatchOp schema is required.", "invalidValue", "SCHEMA_INVALID")
        operations = document.get("Operations")
        if not isinstance(operations, list) or not operations or len(operations) > MAX_PATCH_OPERATIONS:
            raise ScimError(400, "PATCH Operations is empty or exceeds the limit.", "invalidValue", "LIMIT_EXCEEDED")
        fields: dict[str, Any] = {}

        def assign(path: str, value: Any, remove: bool) -> None:
            normalized = path.strip().lower()
            if normalized in {item.lower() for item in FORBIDDEN_SECURITY_FIELDS}:
                raise ScimError(400, "Nova-managed authorization and tenant attributes are not SCIM-writable.", "mutability", "NOVA_ATTRIBUTE_FORBIDDEN")
            if normalized == "active":
                if remove:
                    raise ScimError(400, "active cannot be removed.", "mutability", "PATCH_INVALID")
                fields["active"] = _boolean(value, "active")
            elif normalized == "username":
                if remove:
                    raise ScimError(400, "userName cannot be removed.", "mutability", "PATCH_INVALID")
                fields["user_name"] = _bounded_string(value, "userName", MAX_IDENTIFIER, required=True)
            elif normalized == "externalid":
                fields["external_id"] = None if remove else _bounded_string(value, "externalId", MAX_EXTERNAL_ID)
            elif normalized == "displayname":
                fields["display_name"] = "" if remove else _bounded_string(value, "displayName", MAX_NAME)
            elif normalized in {"name.givenname", "name.familyname"}:
                field = "given_name" if normalized.endswith("givenname") else "family_name"
                fields[field] = "" if remove else _bounded_string(value, path, MAX_NAME)
            elif normalized == "name":
                if remove:
                    fields.update({"given_name": "", "family_name": ""})
                else:
                    parsed = _parse_user({"name": value}, require_user_name=False)
                    fields.update({"given_name": parsed["given_name"], "family_name": parsed["family_name"]})
            elif normalized in {"emails", 'emails[type eq "work"].value'}:
                if remove:
                    raise ScimError(400, "The primary email cannot be removed.", "mutability", "PATCH_INVALID")
                fields["email"] = (
                    _parse_emails(value) if normalized == "emails"
                    else _bounded_string(value, "emails.value", 320, required=True)
                )
                if not _email_like(fields["email"] or ""):
                    raise ScimError(400, "emails.value is not valid.", "invalidValue", "INVALID_VALUE")
            elif normalized.startswith(SCIM_ENTERPRISE_SCHEMA.lower() + ":"):
                suffix = normalized.split(":")[-1]
                enterprise_map = {
                    "department": "department", "organization": "enterprise_organization",
                    "manager.value": "manager_value", "employeenumber": "employee_number",
                }
                if suffix not in enterprise_map:
                    raise ScimError(400, "Unsupported PATCH path.", "invalidPath", "PATCH_PATH_INVALID")
                fields[enterprise_map[suffix]] = None if remove else _bounded_string(value, path, MAX_NAME)
            else:
                raise ScimError(400, "Unsupported PATCH path.", "invalidPath", "PATCH_PATH_INVALID")

        for operation in operations:
            if not isinstance(operation, dict) or set(operation) - {"op", "path", "value"}:
                raise ScimError(400, "Malformed PATCH operation.", "invalidSyntax", "PATCH_INVALID")
            op = str(operation.get("op", "")).lower()
            if op not in {"add", "replace", "remove"}:
                raise ScimError(400, "Unsupported PATCH operation.", "invalidValue", "PATCH_INVALID")
            path = operation.get("path")
            if path is None:
                value = operation.get("value")
                if op == "remove" or not isinstance(value, dict):
                    raise ScimError(400, "A pathless add/replace requires an object value.", "invalidSyntax", "PATCH_INVALID")
                _reject_unknown(value, USER_ALLOWED - {"schemas", "id", "meta"})
                parsed = _parse_user(value, require_user_name=False)
                fields.update({key: val for key, val in parsed.items() if val is not None})
            elif not isinstance(path, str):
                raise ScimError(400, "PATCH path must be a string.", "invalidPath", "PATCH_PATH_INVALID")
            else:
                assign(path, operation.get("value"), op == "remove")
        with backend.database() as conn:
            row = _managed_user(conn, principal.organization_id, user_id)
            if row is None:
                raise ScimError(404, "Resource not found.", code="USER_NOT_FOUND")
            updated, changed, status_action = _apply_user_fields(conn, backend, principal, row, fields, replace=False)
            action = status_action or "scim_user_updated"
            _audit(conn, principal.organization_id, action, "user", user_id, {"attributes": sorted(changed)})
            _activity(conn, principal, "user_patch", True, target_type="user", target_id=user_id)
        return _json(_user_view(updated, request, backend))

    @app.delete("/scim/v2/Users/{user_id}")
    def delete_scim_user(user_id: str, principal: SCIMPrincipal = Depends(scim_principal)):
        with backend.database() as conn:
            row = _managed_user(conn, principal.organization_id, user_id, include_deleted=True)
            if row is None:
                raise ScimError(404, "Resource not found.", code="USER_NOT_FOUND")
            if row["deprovisioned_at"] is not None:
                return Response(status_code=204)
            _set_active(conn, backend, principal.organization_id, user_id, False)
            now = time.time()
            conn.execute(
                "UPDATE users SET status='deprovisioned', disabled=1, updated_at=? "
                "WHERE user_id=? AND organization_id=?",
                (now, user_id, principal.organization_id),
            )
            conn.execute(
                "UPDATE scim_user_management SET deprovisioned_at=?, updated_at=?, "
                "last_provisioned_at=? WHERE organization_id=? AND user_id=?",
                (now, now, now, principal.organization_id, user_id),
            )
            conn.execute(
                "DELETE FROM organization_group_memberships WHERE organization_id=? AND user_id=?",
                (principal.organization_id, user_id),
            )
            _audit(conn, principal.organization_id, "scim_user_deprovisioned", "user", user_id, None)
            _activity(conn, principal, "user_delete", True, target_type="user", target_id=user_id)
        return Response(status_code=204)

    # --------------------------- Groups
    @app.post("/scim/v2/Groups")
    async def create_scim_group(request: Request, principal: SCIMPrincipal = Depends(scim_principal)):
        fields = _parse_group(await _body(request), require_display_name=True)
        now = time.time()
        with backend.database() as conn:
            conn.begin_immediate()
            if fields["external_id"]:
                existing = conn.execute(
                    "SELECT * FROM organization_groups WHERE organization_id=? AND external_id=?",
                    (principal.organization_id, fields["external_id"]),
                ).fetchone()
                if existing:
                    if existing["deleted_at"] is not None:
                        conn.execute(
                            "UPDATE organization_groups SET deleted_at=NULL, display_name=?, updated_at=? "
                            "WHERE organization_id=? AND group_id=?",
                            (fields["display_name"], now, principal.organization_id, existing["group_id"]),
                        )
                    row = _group_row(conn, principal.organization_id, existing["group_id"])
                    return _json(_group_view(conn, row, request), 200)
            group_id = str(uuid.uuid4())
            try:
                conn.execute(
                    "INSERT INTO organization_groups(organization_id, group_id, external_id, "
                    "display_name, created_at, updated_at) VALUES (?,?,?,?,?,?)",
                    (principal.organization_id, group_id, fields["external_id"], fields["display_name"], now, now),
                )
            except database.IntegrityError:
                raise ScimError(409, "externalId already exists.", "uniqueness", "GROUP_CONFLICT")
            members = fields["members"] or []
            _ensure_group_members(conn, principal.organization_id, members)
            conn.executemany(
                "INSERT OR IGNORE INTO organization_group_memberships(organization_id, group_id, user_id, created_at) VALUES (?,?,?,?)",
                [(principal.organization_id, group_id, member, now) for member in members],
            )
            _audit(conn, principal.organization_id, "scim_group_created", "group", group_id, {"member_count": len(members)})
            _activity(conn, principal, "group_create", True, target_type="group", target_id=group_id)
            row = _group_row(conn, principal.organization_id, group_id)
            view = _group_view(conn, row, request)
        return _json(view, 201, {"Location": _group_location(request, group_id)})

    @app.get("/scim/v2/Groups")
    def list_scim_groups(
        request: Request,
        filter: str | None = None,
        startIndex: int = 1,
        count: int = 100,
        principal: SCIMPrincipal = Depends(scim_principal),
    ):
        start, bounded_count = _pagination(startIndex, count)
        parsed = _parse_filter(filter, {"id", "externalId", "displayName"})
        clauses = ["organization_id=?", "deleted_at IS NULL", "source != 'console'"]
        values: list[Any] = [principal.organization_id]
        if parsed:
            attribute, value = parsed
            column = {"id": "group_id", "externalId": "external_id", "displayName": "display_name"}[attribute]
            clauses.append(f"{column}=?")
            values.append(value)
        where = " AND ".join(clauses)
        with backend.database() as conn:
            total = conn.execute(
                f"SELECT COUNT(*) AS c FROM organization_groups WHERE {where}", tuple(values)
            ).fetchone()["c"]
            rows = conn.execute(
                f"SELECT * FROM organization_groups WHERE {where} ORDER BY group_id LIMIT ? OFFSET ?",
                (*values, bounded_count, start - 1),
            ).fetchall()
            resources = [_group_view(conn, row, request) for row in rows]
        return _json({"schemas": [SCIM_LIST_SCHEMA], "totalResults": total, "startIndex": start, "itemsPerPage": len(rows), "Resources": resources})

    @app.get("/scim/v2/Groups/{group_id}")
    def get_scim_group(request: Request, group_id: str, principal: SCIMPrincipal = Depends(scim_principal)):
        with backend.database() as conn:
            row = _group_row(conn, principal.organization_id, group_id)
            if row is None:
                raise ScimError(404, "Resource not found.", code="GROUP_NOT_FOUND")
            view = _group_view(conn, row, request)
        return _json(view)

    @app.patch("/scim/v2/Groups/{group_id}")
    async def patch_scim_group(request: Request, group_id: str, principal: SCIMPrincipal = Depends(scim_principal)):
        document = await _body(request)
        if not isinstance(document, dict) or set(document) - {"schemas", "Operations"}:
            raise ScimError(400, "A strict PatchOp document is required.", "invalidSyntax", "PATCH_INVALID")
        if not isinstance(document.get("schemas"), list) or SCIM_PATCH_SCHEMA not in document["schemas"]:
            raise ScimError(400, "PatchOp schema is required.", "invalidValue", "SCHEMA_INVALID")
        operations = document.get("Operations")
        if not isinstance(operations, list) or not operations or len(operations) > MAX_PATCH_OPERATIONS:
            raise ScimError(400, "PATCH Operations is empty or exceeds the limit.", "invalidValue", "LIMIT_EXCEEDED")
        with backend.database() as conn:
            row = _group_row(conn, principal.organization_id, group_id)
            if row is None:
                raise ScimError(404, "Resource not found.", code="GROUP_NOT_FOUND")
            changed: set[str] = set()
            membership_changed = 0
            for operation in operations:
                if not isinstance(operation, dict) or set(operation) - {"op", "path", "value"}:
                    raise ScimError(400, "Malformed PATCH operation.", "invalidSyntax", "PATCH_INVALID")
                op = str(operation.get("op", "")).lower()
                path = operation.get("path")
                value = operation.get("value")
                if op not in {"add", "replace", "remove"} or not isinstance(path, str):
                    raise ScimError(400, "Unsupported PATCH operation.", "invalidValue", "PATCH_INVALID")
                normalized = path.strip().lower()
                if normalized in {"displayname", "externalid"}:
                    if op == "remove" and normalized == "displayname":
                        raise ScimError(400, "displayName cannot be removed.", "mutability", "PATCH_INVALID")
                    wanted = None if op == "remove" else _bounded_string(value, path, MAX_NAME if normalized == "displayname" else MAX_EXTERNAL_ID, required=normalized == "displayname")
                    column = "display_name" if normalized == "displayname" else "external_id"
                    try:
                        conn.execute(
                            f"UPDATE organization_groups SET {column}=?, updated_at=? WHERE organization_id=? AND group_id=?",
                            (wanted, time.time(), principal.organization_id, group_id),
                        )
                    except database.IntegrityError:
                        raise ScimError(409, "externalId already exists.", "uniqueness", "GROUP_CONFLICT")
                    changed.add("displayName" if normalized == "displayname" else "externalId")
                    continue
                remove_match = re.fullmatch(r'members\[value\s+eq\s+"([^"\\]+)"\]', path, re.IGNORECASE)
                if remove_match and op == "remove":
                    member_id = remove_match.group(1)
                    membership_changed += conn.execute(
                        "DELETE FROM organization_group_memberships WHERE organization_id=? AND group_id=? AND user_id=?",
                        (principal.organization_id, group_id, member_id),
                    ).rowcount
                    changed.add("members")
                    continue
                if normalized != "members":
                    raise ScimError(400, "Unsupported PATCH path.", "invalidPath", "PATCH_PATH_INVALID")
                members = _parse_group({"members": value}, require_display_name=False)["members"] or []
                _ensure_group_members(conn, principal.organization_id, members)
                if op == "replace":
                    current_count = conn.execute(
                        "SELECT COUNT(*) AS c FROM organization_group_memberships WHERE organization_id=? AND group_id=?",
                        (principal.organization_id, group_id),
                    ).fetchone()["c"]
                    conn.execute(
                        "DELETE FROM organization_group_memberships WHERE organization_id=? AND group_id=?",
                        (principal.organization_id, group_id),
                    )
                    membership_changed += current_count
                elif op == "remove":
                    membership_changed += conn.executemany(
                        "DELETE FROM organization_group_memberships WHERE organization_id=? AND group_id=? AND user_id=?",
                        [(principal.organization_id, group_id, member) for member in members],
                    ).rowcount
                    changed.add("members")
                    continue
                now = time.time()
                before = conn.total_changes
                conn.executemany(
                    "INSERT OR IGNORE INTO organization_group_memberships(organization_id, group_id, user_id, created_at) VALUES (?,?,?,?)",
                    [(principal.organization_id, group_id, member, now) for member in members],
                )
                membership_changed += conn.total_changes - before
                changed.add("members")
            conn.execute(
                "UPDATE organization_groups SET updated_at=? WHERE organization_id=? AND group_id=?",
                (time.time(), principal.organization_id, group_id),
            )
            _audit(conn, principal.organization_id, "scim_group_updated", "group", group_id, {"attributes": sorted(changed)})
            if "members" in changed:
                _audit(conn, principal.organization_id, "scim_group_membership_changed", "group", group_id, {"changes": membership_changed})
            _activity(conn, principal, "group_patch", True, target_type="group", target_id=group_id)
        # RFC 7644 permits 200 with the resource or 204. Entra's documented
        # provisioning profile expects 204 for Group PATCH.
        return Response(status_code=204)

    @app.delete("/scim/v2/Groups/{group_id}")
    def delete_scim_group(group_id: str, principal: SCIMPrincipal = Depends(scim_principal)):
        with backend.database() as conn:
            row = _group_row(conn, principal.organization_id, group_id, include_deleted=True)
            if row is None:
                raise ScimError(404, "Resource not found.", code="GROUP_NOT_FOUND")
            if row["deleted_at"] is not None:
                return Response(status_code=204)
            members = conn.execute(
                "DELETE FROM organization_group_memberships WHERE organization_id=? AND group_id=?",
                (principal.organization_id, group_id),
            ).rowcount
            conn.execute(
                "UPDATE organization_groups SET deleted_at=?, updated_at=? WHERE organization_id=? AND group_id=?",
                (time.time(), time.time(), principal.organization_id, group_id),
            )
            _audit(conn, principal.organization_id, "scim_group_deleted", "group", group_id, {"members_removed": members})
            _activity(conn, principal, "group_delete", True, target_type="group", target_id=group_id)
        return Response(status_code=204)

    # --------------------------- Nova Admin provisioning surface
    @app.get("/api/control/organizations/{organization_id}/provisioning")
    def provisioning_status(
        request: Request,
        organization_id: str,
        principal: Any = Depends(backend.admin_read_guard),
    ):
        backend.require_organization_access(principal, organization_id)
        with backend.database() as conn:
            credentials = conn.execute(
                "SELECT credential_id, name, provider_context, created_at, last_used_at, "
                "revoked_at, expires_at FROM scim_credentials WHERE organization_id=? "
                "ORDER BY created_at DESC",
                (organization_id,),
            ).fetchall()
            users = conn.execute(
                "SELECT COUNT(*) AS c FROM scim_user_management WHERE organization_id=? AND deprovisioned_at IS NULL",
                (organization_id,),
            ).fetchone()["c"]
            groups = conn.execute(
                "SELECT COUNT(*) AS c FROM organization_groups WHERE organization_id=? "
                "AND deleted_at IS NULL AND source != 'console'",
                (organization_id,),
            ).fetchone()["c"]
            managed_groups = conn.execute(
                "SELECT g.group_id, g.external_id, g.display_name, g.updated_at, "
                "COUNT(gm.user_id) AS member_count FROM organization_groups g "
                "LEFT JOIN organization_group_memberships gm "
                "ON gm.organization_id=g.organization_id AND gm.group_id=g.group_id "
                "WHERE g.organization_id=? AND g.deleted_at IS NULL AND g.source != 'console' "
                "GROUP BY g.organization_id, g.group_id ORDER BY g.display_name LIMIT 100",
                (organization_id,),
            ).fetchall()
            last_request = conn.execute(
                "SELECT created_at, action, success, error_code FROM scim_activity "
                "WHERE organization_id=? ORDER BY created_at DESC LIMIT 1",
                (organization_id,),
            ).fetchone()
            last_success = conn.execute(
                "SELECT created_at, action FROM scim_activity WHERE organization_id=? AND success=1 ORDER BY created_at DESC LIMIT 1",
                (organization_id,),
            ).fetchone()
            last_error = conn.execute(
                "SELECT created_at, error_code FROM scim_activity WHERE organization_id=? AND success=0 ORDER BY created_at DESC LIMIT 1",
                (organization_id,),
            ).fetchone()
        return {
            "endpoint": str(request.base_url).rstrip("/") + "/scim/v2",
            "status": "active" if any(row["revoked_at"] is None and (row["expires_at"] is None or row["expires_at"] > time.time()) for row in credentials) else "not_configured",
            "credentials": [dict(row) for row in credentials],
            "users_managed": users,
            "groups_managed": groups,
            "managed_groups": [dict(row) for row in managed_groups],
            "last_request": dict(last_request) if last_request else None,
            "last_success": dict(last_success) if last_success else None,
            "last_error": dict(last_error) if last_error else None,
        }

    @app.post("/api/control/organizations/{organization_id}/provisioning/credentials")
    def create_scim_credential(
        organization_id: str,
        body: CredentialCreate,
        principal: Any = Depends(backend.admin_manage_guard),
    ):
        backend.require_organization_access(principal, organization_id)
        # La creation d'un credential est un chemin d'authentification : elle
        # produit un secret. Une console compromise ne doit pas pouvoir en
        # fabriquer mille.
        backend.rate_limit(
            "principal", getattr(principal, "user_id", "unknown"),
            ratelimit.ADMIN_CREDENTIAL_CREATE,
        )
        provider_context = body.provider_context.strip().lower()
        if not re.fullmatch(r"[a-z0-9_.-]{1,80}", provider_context):
            raise ScimError(400, "provider_context is invalid.", "invalidValue", "PROVIDER_CONTEXT_INVALID")
        now = time.time()
        if body.expires_at is not None and body.expires_at <= now:
            raise ScimError(400, "expires_at must be in the future.", "invalidValue", "EXPIRATION_INVALID")
        credential_id = str(uuid.uuid4())
        token = "nscim_" + secrets.token_urlsafe(32)
        with backend.database() as conn:
            conn.execute(
                "INSERT INTO scim_credentials(credential_id, organization_id, name, provider_context, "
                "token_hash, created_at, expires_at, created_by_user_id) VALUES (?,?,?,?,?,?,?,?)",
                (
                    credential_id, organization_id, body.name.strip(), provider_context,
                    backend.hash_token(token), now, body.expires_at, principal.user_id,
                ),
            )
            _audit(conn, organization_id, "scim_credential_created", "scim_credential", credential_id, {"provider_context": provider_context}, principal)
        # The only response that ever contains plaintext.  No GET path can reconstruct it.
        return {
            "credential": {
                "credential_id": credential_id,
                "name": body.name.strip(),
                "provider_context": provider_context,
                "created_at": now,
                "last_used_at": None,
                "revoked_at": None,
                "expires_at": body.expires_at,
            },
            "token": token,
        }

    @app.post("/api/control/organizations/{organization_id}/provisioning/credentials/{credential_id}/revoke")
    def revoke_scim_credential(
        organization_id: str,
        credential_id: str,
        principal: Any = Depends(backend.admin_manage_guard),
    ):
        backend.require_organization_access(principal, organization_id)
        with backend.database() as conn:
            row = conn.execute(
                "SELECT revoked_at FROM scim_credentials WHERE organization_id=? AND credential_id=?",
                (organization_id, credential_id),
            ).fetchone()
            if row is None:
                # Foreign tenant and random IDs are indistinguishable.
                raise ScimError(404, "Resource not found.", code="CREDENTIAL_NOT_FOUND")
            if row["revoked_at"] is None:
                conn.execute(
                    "UPDATE scim_credentials SET revoked_at=? WHERE organization_id=? AND credential_id=?",
                    (time.time(), organization_id, credential_id),
                )
                _audit(conn, organization_id, "scim_credential_revoked", "scim_credential", credential_id, None, principal)
        return {"revoked": True}
