# SCIM 2.0 organization provisioning

## Boundary and architecture

SCIM is an inbound Control Plane protocol. It provisions identity attributes,
organization membership, organizational groups, and group membership. It does
not authenticate a person and it does not grant Nova permissions.

```
Identity provider -- SCIM 2.0 --> Nova Control Plane
Identity provider -- OIDC/SSO --> Nova authentication
Nova Admin --------------------> Nova security roles and capabilities
```

The Control Plane may store the administrative identity attributes needed for
provisioning and group membership. SCIM never receives audio, transcripts,
prompts, selected text, clipboard data, AI output, or user history.

The implementation follows the resource and protocol contracts of RFC 7643 and
RFC 7644 for the supported subset. It has no dependency on a proprietary
Microsoft header. Microsoft Entra is the first validated payload shape, not a
separate protocol implementation.

## Identity audit and source of truth

`users` is the current tenant-scoped organization membership table. `user_id`
is Nova's stable identity and SCIM `id`; email is a mutable attribute unique in
one organization. `federated_identities` carries verified provider subjects.
`scim_user_management` records the fact that SCIM manages a membership and the
attributes owned by provisioning. A user may therefore be created by SSO and
later managed by SCIM, or created by SCIM and later acquire an SSO identity.

| Field | Authority | SCIM | SSO | Nova Admin | Conflict rule |
| --- | --- | --- | --- | --- | --- |
| `organization_id` | Nova credential/config | never | never | not on membership writes | immutable; principal-derived |
| `user_id` / SCIM `id` | Nova | read-only | read-only | read-only | stable, never email |
| email | membership, SCIM when managed | write | initial reconciliation | no current editor | unique within tenant; 409 |
| `userName` | SCIM management relation | write | no | read-only | unique within tenant |
| `externalId` | SCIM client | write | no | read-only | unique within tenant SCIM source |
| SSO subject/issuer/tenant | verified IdP + Nova provider config | no | write | provider configuration only | tenant + provider + issuer + subject |
| `active` / status | membership lifecycle | write | read/refuse only | write | last-manager guard; sessions revoked |
| names/display name | SCIM management relation | write | no | read-only | SCIM last writer |
| member type / `users.role` | Nova Admin | no | no | write | preserved; new SCIM user is `partner`/`other` |
| security role | Nova security | never | never | dedicated role route | never inferred; default `member` |
| SCIM groups/members | SCIM client | write | no | diagnostics only | tenant-scoped identifiers only |
| audit/session metadata | Nova | safe events only | session issuance | safe actions | never client-writable |

There is no `businessRole` authority in the current model. It is unsupported.
SCIM core does not write member type. A future Nova extension may add that
explicitly, but core attributes will never be interpreted as a profession or a
permission.

## SCIM principal and tenant isolation

`SCIMPrincipal` is a dedicated machine principal. It is distinct from user,
admin, connector, local connector, and future Nova operator principals. The
bearer credential lookup constructs it server-side with one `organization_id`.
Every User, Group, membership, filter, and diagnostics SQL statement includes
that organization. No request body or path can select another organization.

A foreign-tenant resource identifier is indistinguishable from a random one:
individual resources return 404; a foreign group member returns a safe
`invalidValue`. This prevents cross-tenant existence probing.

## Credential lifecycle

Nova Admin roles with `provisioning_manage` can create and independently revoke
multiple named credentials. Organization administrators and IT administrators
have that capability. Read-only administrators can see status but cannot
mutate credentials; members cannot enter Nova Admin.

Credentials use `secrets.token_urlsafe(32)` with a fixed `nscim_` prefix. The
plaintext is returned by the create response once. Only SHA-256 of the
high-entropy value is stored. GET responses, diagnostics, and audit contain no
plaintext or fingerprint. The row records creation, safe last use, revocation,
and optional expiration. Revocation is checked on every request and is
immediate.

Rotation requires no state machine: create C2, validate it, then revoke C1.
Both credentials address the same organization SCIM source during overlap, so
resource IDs and `externalId` values remain stable.

`provider_context` is optional reconciliation metadata (`generic` by default).
It does not change protocol behavior. When it names an already configured SSO
provider, Nova may use `externalId` to match an existing verified provider
subject. There is no provider-specific authorization branch.

## Endpoints and schemas

The base path is `/scim/v2`:

- `GET /ServiceProviderConfig`
- `GET /ResourceTypes`
- `GET /Schemas` and `GET /Schemas/{id}`
- `POST`, `GET /Users`; `GET`, `PUT`, `PATCH`, `DELETE /Users/{id}`
- `POST`, `GET /Groups`; `GET`, `PATCH`, `DELETE /Groups/{id}`

Requests accept `application/scim+json` and `application/json`. SCIM JSON
responses use `application/scim+json`.

Supported User core attributes are `id`, `externalId`, `userName`, `active`,
`name.givenName`, `name.familyName`, `displayName`, `emails`, and `meta`.
`id`, organization, security role, audit metadata, and provider trust data are
not writable. `userName` is stored separately from email; a UPN may initially
serve as email only when it is syntactically an email and no primary email was
provided.

Supported Group attributes are `id`, `externalId`, `displayName`, `members`,
and `meta`. Groups are organizational data only. A group named `Admins`, `IT`,
or `Nova Admins` has exactly zero effect on `security_role`.

The optional enterprise User extension supports `department`, `organization`,
`manager.value`, and `employeeNumber`. These values are administrative profile
data only and never feed authorization, member type, policies, or deployment.

No ETag or `meta.version` is emitted because there is no concurrency contract to
enforce. `etag.supported` is false. Bulk, sort, and password change are also
declared unsupported. PATCH and filter are declared supported.

## User lifecycle and reconciliation

Create resolves identity in this order:

1. an existing tenant-scoped SCIM `externalId` or `userName` mapping;
2. a verified federated subject with the configured provider context and the
   same tenant;
3. one normalized email match in the same tenant;
4. a new Nova membership with `security_role=member` and member type `other`.

Ambiguity or a uniqueness collision fails closed. Domain, display name, job
title, group name, member type, and email domain are never reconciliation
identifiers. No lookup crosses an organization boundary.

When SSO occurs after SCIM, the existing SSO resolver finds the tenant-scoped
email membership and attaches the verified subject. When SCIM follows SSO, the
verified provider subject wins over email. SCIM email changes keep `user_id`
stable and update the readable email on existing federated links, allowing the
next SSO login by immutable subject to reach the same account.

`active:false` sets the membership to disabled, revokes all user tokens and all
administration sessions for that user in the same transaction, and preserves
the account and identities. `active:true` clears suspension without restoring
old sessions. The user signs in again. The existing last identity-manager guard
applies to SCIM, so a malicious or mistaken provisioning client cannot suspend
the last account capable of administering identities.

SCIM `DELETE /Users/{id}` is idempotent deprovisioning, not Nova Admin hard
delete. It disables the membership, revokes sessions, removes organizational
group relations, sets `status=deprovisioned`, and retains the user, stable ID,
federated identities, and audit. A subsequent retry returns 204. A later POST
with the same SCIM identity reactivates the same user. Nova Admin's dedicated
delete route remains the explicit destructive/privacy-erasure operation.

PUT replaces supported profile attributes but cannot replace Nova-owned data.
For safety, omission of `active` does not reactivate a suspended user.

PATCH accepts bounded `add`, `replace`, and `remove` operations. User paths are
`active`, `userName`, `externalId`, `displayName`, `name`, its supported
subattributes, `emails`, Entra's work-email value path, and the supported
enterprise paths. Group PATCH supports display name/external ID, add or replace
members, and Entra-style `members[value eq "id"]` removal. The parser is an
allowlist and never becomes SQL.

## Filters, pagination, idempotency, and concurrency

User filters support exact `eq` on `id`, `userName`, `externalId`, and `active`.
Group filters support exact `eq` on `id`, `externalId`, and `displayName`.
Compound expressions and unsupported operators fail with `invalidFilter`.
Values are bound SQL parameters. Filter length is bounded.

List endpoints implement `startIndex` (one-based) and `count`, returning a SCIM
ListResponse with `totalResults`, `startIndex`, `itemsPerPage`, and `Resources`.
`count` is capped at 100.

POST retries with the same SCIM identity return the existing stable resource.
DELETE and group membership mutations are idempotent. SQLite uniqueness
constraints cover tenant + `externalId` and tenant + normalized `userName`.
The create lookup/write window uses `BEGIN IMMEDIATE`; the database constraint
remains the final guard against duplicate concurrent provisioning.

## Limits and rate protection

- request body: 64 KiB;
- page count: 100;
- PATCH operations: 20;
- members per group: 1,000;
- email values: 5;
- bounded identifiers and display strings;
- 300 requests/minute per credential;
- 900 requests/minute per tenant.

Rate limiting is currently process-local. A multi-worker/region production
deployment must move buckets to shared infrastructure. Database uniqueness and
tenant isolation do not depend on that limiter.

## Errors, diagnostics, audit, and privacy

Protocol errors use the RFC error schema, string `status`, optional `scimType`,
and a safe `detail`. Authentication failure returns 401 and
`WWW-Authenticate: Bearer`. Stack traces and request payloads are never sent.

Nova Admin shows the endpoint, active/revoked credential metadata, safe last
request, last successful provisioning, last error code, managed user/group
counts, and group membership counts. It never shows full payloads, bearer
headers, provider tokens, or token hashes.

Audit actions are:

- `scim_credential_created`, `scim_credential_revoked`;
- `scim_user_created`, `scim_user_updated`, `scim_user_suspended`,
  `scim_user_reactivated`, `scim_user_deprovisioned`;
- `scim_group_created`, `scim_group_updated`, `scim_group_deleted`,
  `scim_group_membership_changed`.

Metadata contains stable IDs, safe counts, reconciliation boolean, and changed
attribute names only. It never contains a credential, Authorization header,
full User/Group document, or personal content.

## Microsoft Entra setup

1. Generate a named credential in **Identity / Provisioning**, selecting the
   existing Microsoft Entra reconciliation context when applicable.
2. In the controlled Enterprise application, open **Provisioning**, choose
   automatic provisioning, and paste Nova's Tenant URL and the one-time Secret
   Token.
3. Use **Test Connection**.
4. Scope assignment to one explicitly controlled test user or test group.
   Never assign a broad organization group for validation.
5. Validate create, attribute update, disable, re-enable, optional group
   membership, and deprovisioning in Nova Admin.
6. Create C2 and test it before revoking C1 when rotating.

Nova never asks for a Microsoft password, Entra client secret, or Graph
credential for inbound SCIM.

## Threat model

- **Stolen SCIM credential:** tenant scope limits blast radius; hash-only
  storage, independent revocation, optional expiration, rate limits, safe audit,
  and rotation reduce exposure. A stolen credential can still mutate ordinary
  membership in that tenant until revoked.
- **Malicious IdP administrator/provisioning client:** cannot write Nova roles,
  organization IDs, capabilities, member type, or provider trust; cannot turn a
  group or title into privilege; last-manager guard remains active.
- **Replay and retry:** opaque bearer transport requires TLS; mutations are
  idempotent where provisioning clients retry; duplicates are constrained.
- **Cross-tenant ID guessing:** every resource and relation query is
  principal-scoped and foreign IDs receive non-enumerating errors.
- **Email reassignment:** immutable verified subjects take priority; fallback is
  tenant-only and refuses uniqueness conflicts. Operators must investigate an
  ambiguous external identity instead of forcing a merge.
- **Duplicate provisioning:** serialized create window plus database uniqueness;
  stable Nova IDs survive retries and email changes.
- **Oversized PATCH/group:** body, operation, string, page, email, and group
  membership limits fail closed.
- **Compromised provisioning client:** it can provision/suspend ordinary users
  in its one organization but cannot grant Nova privilege, cross tenants,
  hard-delete user data, or bypass session/last-admin controls.

## Unsupported features

Bulk, sort, password management, ETag/version preconditions, arbitrary SCIM
extensions, group-to-policy/package/deployment assignments, SCIM-controlled
member type, and automatic group-to-security-role mapping are deliberately out
of scope. The service documents declare these limits rather than simulating
support.
