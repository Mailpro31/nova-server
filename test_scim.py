import json
import os
import tempfile
import threading
import time
import unittest
import uuid
from pathlib import Path


SCIM_TEST_DIRECTORY = tempfile.TemporaryDirectory()
os.environ.setdefault("NOVA_DB", os.path.join(SCIM_TEST_DIRECTORY.name, "nova-scim-test.db"))
os.environ.setdefault("NOVA_DOMAINS", "example.com")
os.environ.setdefault("NOVA_ORGANIZATION_NAME", "Nova SCIM Test")

from fastapi.testclient import TestClient

import main

# Le schema s'applique **explicitement**. Importer `main` ne migre plus rien :
# c'est precisement l'effet de bord qui a migre une base de production en
# Phase 30 puis en Phase 37. Un appel visible dans le test remplace un import
# qui decidait tout seul.
main.init_db()


FIXTURES = Path(__file__).parent / "tests" / "fixtures" / "scim"
SCIM = {"Content-Type": "application/scim+json"}


def fixture(name: str) -> dict:
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


class ScimProvisioningTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.client = TestClient(main.app)

    def setUp(self):
        with main.db() as conn:
            for table in (
                "organization_group_memberships",
                "organization_groups",
                "scim_user_management",
                "scim_activity",
                "scim_credentials",
                "federated_identities",
                "admin_audit_log",
                "admin_sessions",
                "tokens",
                "users",
            ):
                conn.execute(f"DELETE FROM {table}")
        self.organization_id = main.nova_organization_id()
        self.admin_headers = self._admin(self.organization_id)
        self.token, self.credential_id = self._credential(
            self.organization_id, self.admin_headers, "C1"
        )
        self.scim_headers = {**SCIM, "Authorization": f"Bearer {self.token}"}

    def _admin(self, organization_id: str, role: str = "organization_admin") -> dict:
        with main.db() as conn:
            user_id = main.create_user(
                conn,
                organization_id,
                f"admin-{uuid.uuid4().hex[:8]}@example.com",
                security_role=role,
            )
            account = main.user_by_id(conn, user_id)
        session = main.create_admin_session(account, "test")
        return {"Authorization": f"Bearer {session['token']}"}

    def _credential(
        self,
        organization_id: str,
        headers: dict,
        name: str,
        provider_context: str = "generic",
    ) -> tuple[str, str]:
        response = self.client.post(
            f"/api/control/organizations/{organization_id}/provisioning/credentials",
            headers=headers,
            json={"name": name, "provider_context": provider_context},
        )
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()["token"], response.json()["credential"]["credential_id"]

    def _create_user(self, payload: dict | None = None):
        response = self.client.post(
            "/scim/v2/Users",
            headers=self.scim_headers,
            json=payload or fixture("entra-create-user.json"),
        )
        self.assertIn(response.status_code, {200, 201}, response.text)
        return response

    def test_service_documents_are_honest_and_scim_json(self):
        response = self.client.get("/scim/v2/ServiceProviderConfig", headers=self.scim_headers)
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.headers["content-type"].startswith("application/scim+json"))
        body = response.json()
        self.assertTrue(body["patch"]["supported"])
        self.assertTrue(body["filter"]["supported"])
        self.assertFalse(body["bulk"]["supported"])
        self.assertFalse(body["sort"]["supported"])
        self.assertFalse(body["etag"]["supported"])

    def test_credential_plaintext_once_hash_only_and_revoke_immediate(self):
        with main.db() as conn:
            row = conn.execute(
                "SELECT * FROM scim_credentials WHERE credential_id=?", (self.credential_id,)
            ).fetchone()
            audit = conn.execute(
                "SELECT metadata FROM admin_audit_log WHERE action='scim_credential_created' "
                "AND target_id=?",
                (self.credential_id,),
            ).fetchone()
        self.assertNotIn(self.token, dict(row).values())
        self.assertEqual(row["token_hash"], main.hash_token(self.token))
        self.assertNotIn(self.token, audit["metadata"] or "")
        status = self.client.get(
            f"/api/control/organizations/{self.organization_id}/provisioning",
            headers=self.admin_headers,
        ).json()
        self.assertNotIn("token", json.dumps(status))
        response = self.client.post(
            f"/api/control/organizations/{self.organization_id}/provisioning/credentials/{self.credential_id}/revoke",
            headers=self.admin_headers,
        )
        self.assertEqual(response.status_code, 200)
        refused = self.client.get("/scim/v2/Users", headers=self.scim_headers)
        self.assertEqual(refused.status_code, 401)
        self.assertEqual(refused.json()["schemas"], ["urn:ietf:params:scim:api:messages:2.0:Error"])

    def test_rotation_overlap_then_independent_revocation(self):
        self._create_user()
        token2, _ = self._credential(self.organization_id, self.admin_headers, "C2")
        headers2 = {**SCIM, "Authorization": f"Bearer {token2}"}
        self.assertEqual(self.client.get("/scim/v2/Users", headers=self.scim_headers).status_code, 200)
        self.assertEqual(self.client.get("/scim/v2/Users", headers=headers2).status_code, 200)
        self.client.post(
            f"/api/control/organizations/{self.organization_id}/provisioning/credentials/{self.credential_id}/revoke",
            headers=self.admin_headers,
        )
        self.assertEqual(self.client.get("/scim/v2/Users", headers=self.scim_headers).status_code, 401)
        self.assertEqual(self.client.get("/scim/v2/Users", headers=headers2).status_code, 200)

    def test_create_filter_pagination_and_no_privilege_escalation(self):
        created = self._create_user()
        body = created.json()
        self.assertNotEqual(body["id"], body["userName"])
        with main.db() as conn:
            account = main.user_by_id(conn, body["id"])
        self.assertEqual(account["security_role"], "member")
        self.assertEqual(account["role"], "partner")
        filtered = self.client.get(
            '/scim/v2/Users?filter=externalId%20eq%20%22entra-object-controlled-001%22&startIndex=1&count=1',
            headers=self.scim_headers,
        )
        self.assertEqual(filtered.status_code, 200, filtered.text)
        self.assertEqual(filtered.json()["totalResults"], 1)
        self.assertEqual(filtered.json()["itemsPerPage"], 1)

    def test_scim_rejects_tenant_and_security_fields(self):
        for field, value in (
            ("organization_id", "forged"),
            ("securityRole", "organization_admin"),
            ("memberType", "teacher"),
        ):
            payload = fixture("entra-create-user.json")
            payload[field] = value
            response = self.client.post("/scim/v2/Users", headers=self.scim_headers, json=payload)
            self.assertEqual(response.status_code, 400, (field, response.text))
        self._create_user()
        with main.db() as conn:
            row = conn.execute("SELECT security_role, role FROM users WHERE email=?", ("controlled.user@example.com",)).fetchone()
        self.assertEqual(tuple(row), ("member", "partner"))

    def test_patch_email_keeps_user_id_and_updates_federated_link(self):
        created = self._create_user().json()
        with main.db() as conn:
            conn.execute(
                "INSERT INTO federated_identities(provider, external_subject, external_tenant_id, "
                "organization_id, email, user_id, verification, external_issuer, created_at, last_seen) "
                "VALUES (?,?,?,?,?,?,?,?,?,?)",
                (
                    "oidc", "subject-1", "issuer", self.organization_id,
                    "controlled.user@example.com", created["id"], "verified", "issuer", time.time(), time.time(),
                ),
            )
        patch = {
            "schemas": ["urn:ietf:params:scim:api:messages:2.0:PatchOp"],
            "Operations": [{"op": "replace", "path": 'emails[type eq "work"].value', "value": "renamed.user@example.com"}],
        }
        response = self.client.patch(
            f"/scim/v2/Users/{created['id']}", headers=self.scim_headers, json=patch
        )
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["id"], created["id"])
        with main.db() as conn:
            account = main.user_by_id(conn, created["id"])
            identity = conn.execute("SELECT email FROM federated_identities WHERE user_id=?", (created["id"],)).fetchone()
        self.assertEqual(account["email"], "renamed.user@example.com")
        self.assertEqual(identity["email"], "renamed.user@example.com")

    def test_disable_revokes_user_and_admin_sessions_then_reactivates_same_id(self):
        created = self._create_user().json()
        user_session = main.issue_token("controlled.user@example.com", "controlled-device", self.organization_id)
        with main.db() as conn:
            conn.execute("UPDATE users SET security_role='read_only' WHERE user_id=?", (created["id"],))
            account = main.user_by_id(conn, created["id"])
        main.create_admin_session(account, "test")
        response = self.client.patch(
            f"/scim/v2/Users/{created['id']}",
            headers=self.scim_headers,
            json=fixture("entra-disable-user.json"),
        )
        self.assertEqual(response.status_code, 200, response.text)
        self.assertFalse(response.json()["active"])
        with main.db() as conn:
            active_tokens = conn.execute("SELECT COUNT(*) AS c FROM tokens WHERE user_id=? AND active=1", (created["id"],)).fetchone()["c"]
            admin_sessions = conn.execute("SELECT COUNT(*) AS c FROM admin_sessions WHERE user_id=? AND revoked_at IS NULL", (created["id"],)).fetchone()["c"]
        self.assertEqual(active_tokens, 0)
        self.assertEqual(admin_sessions, 0)
        refused = self.client.get(
            "/api/me", headers={"Authorization": f"Bearer {user_session['token']}"}
        )
        self.assertIn(refused.status_code, {401, 403})
        enable = {
            "schemas": ["urn:ietf:params:scim:api:messages:2.0:PatchOp"],
            "Operations": [{"op": "replace", "path": "active", "value": True}],
        }
        enabled = self.client.patch(
            f"/scim/v2/Users/{created['id']}", headers=self.scim_headers, json=enable
        )
        self.assertEqual(enabled.status_code, 200, enabled.text)
        self.assertTrue(enabled.json()["active"])
        self.assertEqual(enabled.json()["id"], created["id"])

    def test_delete_is_idempotent_deprovision_not_hard_delete(self):
        created = self._create_user().json()
        for _ in range(2):
            response = self.client.delete(
                f"/scim/v2/Users/{created['id']}", headers=self.scim_headers
            )
            self.assertEqual(response.status_code, 204, response.text)
        with main.db() as conn:
            user = main.user_by_id(conn, created["id"])
        self.assertIsNotNone(user)
        self.assertEqual(user["status"], "deprovisioned")
        self.assertEqual(user["disabled"], 1)
        self.assertEqual(
            self.client.get(f"/scim/v2/Users/{created['id']}", headers=self.scim_headers).status_code,
            404,
        )

    def test_last_identity_manager_guard_applies_to_scim(self):
        email = "last-admin@example.com"
        with main.db() as conn:
            user_id = main.create_user(
                conn, self.organization_id, email, security_role="organization_admin"
            )
        payload = fixture("entra-create-user.json")
        payload.update({"externalId": "last-admin-ext", "userName": email, "emails": [{"value": email, "primary": True}]})
        created = self._create_user(payload).json()
        self.assertEqual(created["id"], user_id)
        # The setUp administrator created the credential; demote it so the
        # SCIM-managed account is now genuinely the last identity manager.
        with main.db() as conn:
            conn.execute(
                "UPDATE users SET security_role='member' WHERE organization_id=? AND user_id<>?",
                (self.organization_id, user_id),
            )
        response = self.client.patch(
            f"/scim/v2/Users/{user_id}", headers=self.scim_headers,
            json=fixture("entra-disable-user.json"),
        )
        self.assertEqual(response.status_code, 409, response.text)
        with main.db() as conn:
            account = main.user_by_id(conn, user_id)
        self.assertEqual(main.account_status(account), "active")

    def test_sso_first_then_scim_strong_reconciliation(self):
        email = "sso-first@example.com"
        with main.db() as conn:
            user_id = main.create_user(conn, self.organization_id, email)
            conn.execute(
                "INSERT INTO federated_identities(provider, external_subject, external_tenant_id, "
                "organization_id, email, user_id, verification, external_issuer, created_at, last_seen) "
                "VALUES (?,?,?,?,?,?,?,?,?,?)",
                (
                    "microsoft_entra", "strong-subject-1", "tenant", self.organization_id,
                    email, user_id, "verified", "", time.time(), time.time(),
                ),
            )
        token, _ = self._credential(
            self.organization_id, self.admin_headers, "Entra", "microsoft_entra"
        )
        headers = {**SCIM, "Authorization": f"Bearer {token}"}
        payload = fixture("entra-create-user.json")
        payload.update({"externalId": "strong-subject-1", "userName": email, "emails": [{"value": email, "primary": True}]})
        response = self.client.post("/scim/v2/Users", headers=headers, json=payload)
        self.assertEqual(response.status_code, 201, response.text)
        self.assertEqual(response.json()["id"], user_id)
        with main.db() as conn:
            count = conn.execute("SELECT COUNT(*) AS c FROM users WHERE organization_id=? AND normalized_email=?", (self.organization_id, email)).fetchone()["c"]
        self.assertEqual(count, 1)

    def test_federated_subject_and_profile_update_are_tenant_scoped(self):
        tenant_b = str(uuid.uuid4())
        shared_email = "same-person-label@example.com"
        with main.db() as conn:
            conn.execute(
                "INSERT INTO organizations(id, slug, display_name, org_type, created_at) "
                "VALUES (?,?,?,?,?)",
                (tenant_b, f"identity-b-{tenant_b[:8]}", "Identity B", "business", time.time()),
            )
            user_a = main.create_user(conn, self.organization_id, shared_email)
            user_b = main.create_user(conn, tenant_b, shared_email)
            now = time.time()
            for organization_id, user_id in (
                (self.organization_id, user_a),
                (tenant_b, user_b),
            ):
                conn.execute(
                    "INSERT INTO federated_identities(provider, external_subject, "
                    "organization_id, email, user_id, verification, external_issuer, "
                    "created_at, last_seen) VALUES (?,?,?,?,?,?,?,?,?)",
                    (
                        "microsoft_entra", "same-subject-value", organization_id,
                        shared_email, user_id, "verified", "", now, now,
                    ),
                )
        main.record_federated_identity(
            shared_email,
            "microsoft_entra",
            "same-subject-value",
            "tenant-a",
            verification="verified",
        )
        with main.db() as conn:
            rows = conn.execute(
                "SELECT organization_id FROM federated_identities "
                "WHERE provider=? AND external_subject=?",
                ("microsoft_entra", "same-subject-value"),
            ).fetchall()
            account_a = main.user_by_id(conn, user_a)
            account_b = main.user_by_id(conn, user_b)
        self.assertEqual({row["organization_id"] for row in rows}, {self.organization_id, tenant_b})
        self.assertEqual(account_a["identity_provider"], "microsoft_entra")
        self.assertIsNone(account_b["identity_provider"])

    def test_scim_first_then_sso_email_reconciliation(self):
        created = self._create_user().json()
        resolved = main.resolve_member_for_identity(
            "controlled.user@example.com",
            self.organization_id,
            "new-sso-subject",
            "microsoft_entra",
        )
        self.assertEqual(resolved, "controlled.user@example.com")
        main.record_federated_identity(
            resolved,
            "microsoft_entra",
            "new-sso-subject",
            "tenant",
            verification="verified",
        )
        with main.db() as conn:
            identity = conn.execute("SELECT user_id FROM federated_identities WHERE external_subject=?", ("new-sso-subject",)).fetchone()
            count = conn.execute("SELECT COUNT(*) AS c FROM users WHERE organization_id=?", (self.organization_id,)).fetchone()["c"]
        self.assertEqual(identity["user_id"], created["id"])
        # Includes the admin created by setUp and the provisioned member only.
        self.assertEqual(count, 2)

    def test_groups_membership_idempotency_and_no_security_mapping(self):
        user = self._create_user().json()
        group = self.client.post(
            "/scim/v2/Groups", headers=self.scim_headers,
            json=fixture("entra-create-group.json"),
        )
        self.assertEqual(group.status_code, 201, group.text)
        group_id = group.json()["id"]
        add = fixture("entra-add-member.json")
        add["Operations"][0]["value"][0]["value"] = user["id"]
        for _ in range(2):
            response = self.client.patch(
                f"/scim/v2/Groups/{group_id}", headers=self.scim_headers, json=add
            )
            self.assertEqual(response.status_code, 204, response.text)
            current = self.client.get(
                f"/scim/v2/Groups/{group_id}", headers=self.scim_headers
            )
            self.assertEqual(len(current.json()["members"]), 1)
        with main.db() as conn:
            account = main.user_by_id(conn, user["id"])
        self.assertEqual(account["security_role"], "member")
        remove = fixture("entra-remove-member.json")
        remove["Operations"][0]["path"] = f'members[value eq "{user["id"]}"]'
        for _ in range(2):
            response = self.client.patch(
                f"/scim/v2/Groups/{group_id}", headers=self.scim_headers, json=remove
            )
            self.assertEqual(response.status_code, 204, response.text)
            current = self.client.get(
                f"/scim/v2/Groups/{group_id}", headers=self.scim_headers
            )
            self.assertEqual(current.json()["members"], [])
        self.assertEqual(self.client.delete(f"/scim/v2/Groups/{group_id}", headers=self.scim_headers).status_code, 204)
        with main.db() as conn:
            self.assertIsNotNone(main.user_by_id(conn, user["id"]))

    def test_cross_tenant_ids_and_group_members_are_rejected(self):
        user_a = self._create_user().json()
        tenant_b = str(uuid.uuid4())
        with main.db() as conn:
            conn.execute(
                "INSERT INTO organizations(id, slug, display_name, org_type, created_at) VALUES (?,?,?,?,?)",
                (tenant_b, f"tenant-b-{tenant_b[:8]}", "Tenant B", "business", time.time()),
            )
        admin_b = self._admin(tenant_b)
        token_b, _ = self._credential(tenant_b, admin_b, "B")
        headers_b = {**SCIM, "Authorization": f"Bearer {token_b}"}
        payload_b = fixture("entra-create-user.json")
        payload_b.update({"externalId": "tenant-b-user", "userName": "tenant-b@example.com", "emails": [{"value": "tenant-b@example.com", "primary": True}]})
        user_b = self.client.post("/scim/v2/Users", headers=headers_b, json=payload_b).json()
        self.assertEqual(self.client.get(f"/scim/v2/Users/{user_b['id']}", headers=self.scim_headers).status_code, 404)
        group = self.client.post("/scim/v2/Groups", headers=self.scim_headers, json=fixture("entra-create-group.json")).json()
        attack = fixture("entra-add-member.json")
        attack["Operations"][0]["value"][0]["value"] = user_b["id"]
        refused = self.client.patch(f"/scim/v2/Groups/{group['id']}", headers=self.scim_headers, json=attack)
        self.assertEqual(refused.status_code, 400, refused.text)
        self.assertEqual(self.client.get(f"/scim/v2/Users/{user_a['id']}", headers=headers_b).status_code, 404)

    def test_malformed_filter_oversized_patch_and_injection_are_bounded(self):
        user = self._create_user().json()
        response = self.client.get(
            "/scim/v2/Users", headers=self.scim_headers,
            params={"filter": 'userName eq "x" or id eq "y"'},
        )
        self.assertEqual(response.status_code, 400, response.text)
        self.assertEqual(response.json()["scimType"], "invalidFilter")
        # Injection syntax inside a quoted filter value is data, not SQL.
        injection = self.client.get(
            "/scim/v2/Users", headers=self.scim_headers,
            params={"filter": 'userName eq "\' OR 1=1 --"'},
        )
        self.assertEqual(injection.status_code, 200, injection.text)
        self.assertEqual(injection.json()["totalResults"], 0)
        patch = {
            "schemas": ["urn:ietf:params:scim:api:messages:2.0:PatchOp"],
            "Operations": [
                {"op": "replace", "path": "displayName", "value": str(index)}
                for index in range(21)
            ],
        }
        response = self.client.patch(
            f"/scim/v2/Users/{user['id']}", headers=self.scim_headers, json=patch
        )
        self.assertEqual(response.status_code, 400)

        oversized = json.dumps({
            "schemas": ["urn:ietf:params:scim:api:messages:2.0:PatchOp"],
            "Operations": [
                {"op": "replace", "path": "displayName", "value": "x" * 70_000}
            ],
        })
        response = self.client.patch(
            f"/scim/v2/Users/{user['id']}",
            headers=self.scim_headers,
            content=oversized,
        )
        self.assertEqual(response.status_code, 413)

    def test_group_patch_is_atomic_and_uses_entra_204_contract(self):
        group = self.client.post(
            "/scim/v2/Groups", headers=self.scim_headers,
            json=fixture("entra-create-group.json"),
        ).json()
        patch = {
            "schemas": ["urn:ietf:params:scim:api:messages:2.0:PatchOp"],
            "Operations": [
                {"op": "replace", "path": "displayName", "value": "Must Roll Back"},
                {"op": "replace", "path": "unsupported", "value": "failure"},
            ],
        }
        failed = self.client.patch(
            f"/scim/v2/Groups/{group['id']}", headers=self.scim_headers, json=patch
        )
        self.assertEqual(failed.status_code, 400, failed.text)
        current = self.client.get(
            f"/scim/v2/Groups/{group['id']}", headers=self.scim_headers
        )
        self.assertEqual(current.json()["displayName"], "Controlled Test Group")

    def test_admin_provisioning_permissions(self):
        read_only = self._admin(self.organization_id, "read_only")
        self.assertEqual(
            self.client.get(
                f"/api/control/organizations/{self.organization_id}/provisioning",
                headers=read_only,
            ).status_code,
            200,
        )
        self.assertEqual(
            self.client.post(
                f"/api/control/organizations/{self.organization_id}/provisioning/credentials",
                headers=read_only,
                json={"name": "forbidden", "provider_context": "generic"},
            ).status_code,
            403,
        )
        it_admin = self._admin(self.organization_id, "it_admin")
        token, _ = self._credential(self.organization_id, it_admin, "IT rotation")
        self.assertTrue(token.startswith("nscim_"))
        member = self._admin(self.organization_id, "member")
        self.assertEqual(
            self.client.get(
                f"/api/control/organizations/{self.organization_id}/provisioning",
                headers=member,
            ).status_code,
            403,
        )

    def test_put_replaces_supported_attributes_but_preserves_nova_fields(self):
        created = self._create_user().json()
        with main.db() as conn:
            conn.execute(
                "UPDATE users SET role='teacher', security_role='read_only' WHERE user_id=?",
                (created["id"],),
            )
        replacement = {
            "schemas": ["urn:ietf:params:scim:schemas:core:2.0:User"],
            "externalId": "replacement-external",
            "userName": "replacement.user@example.com",
            "active": True,
            "displayName": "Replacement User",
            "emails": [{"value": "replacement.user@example.com", "primary": True}],
        }
        response = self.client.put(
            f"/scim/v2/Users/{created['id']}", headers=self.scim_headers, json=replacement
        )
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["id"], created["id"])
        with main.db() as conn:
            account = main.user_by_id(conn, created["id"])
        self.assertEqual(account["organization_id"], self.organization_id)
        self.assertEqual(account["role"], "teacher")
        self.assertEqual(account["security_role"], "read_only")

    def test_concurrent_duplicate_external_id_creates_one_membership(self):
        payload = fixture("entra-create-user.json")
        statuses: list[int] = []
        barrier = threading.Barrier(2)

        def attempt():
            barrier.wait()
            response = self.client.post("/scim/v2/Users", headers=self.scim_headers, json=payload)
            statuses.append(response.status_code)

        threads = [threading.Thread(target=attempt) for _ in range(2)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        self.assertTrue(all(status in {200, 201} for status in statuses), statuses)
        with main.db() as conn:
            managed = conn.execute(
                "SELECT COUNT(*) AS c FROM scim_user_management WHERE organization_id=? AND external_id=?",
                (self.organization_id, payload["externalId"]),
            ).fetchone()["c"]
            accounts = conn.execute(
                "SELECT COUNT(*) AS c FROM users WHERE organization_id=? AND normalized_email=?",
                (self.organization_id, main.normalize_email(payload["emails"][0]["value"])),
            ).fetchone()["c"]
        self.assertEqual(managed, 1)
        self.assertEqual(accounts, 1)


if __name__ == "__main__":
    unittest.main()
