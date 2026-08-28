"""Deterministic fake-client recipe for tenant-scoped SCIM provisioning.

The recipe creates its own temporary database before importing ``main``.  It
never opens ``nova.db`` and the temporary directory is removed on exit.
"""

from __future__ import annotations

import json
import os
import tempfile
import time
import uuid


def run() -> dict:
    with tempfile.TemporaryDirectory(prefix="nova-scim-recipe-") as recipe_directory:
        recipe_database = os.path.join(recipe_directory, "scim-recipe.db")
        os.environ["NOVA_DB"] = recipe_database
        os.environ["NOVA_DOMAINS"] = "example.test"
        os.environ["NOVA_ORGANIZATION_ID"] = "scim-recipe-a"
        os.environ["NOVA_ORGANIZATION_NAME"] = "SCIM Recipe A"

        # The guard must run before main's import-time migration.
        import recipe_env

        assert str(recipe_env.DATABASE) == os.path.abspath(recipe_database)

        from fastapi.testclient import TestClient
        import main

        client = TestClient(main.app)
        tenant_a = main.nova_organization_id()
        tenant_b = str(uuid.uuid4())
        with main.db() as conn:
            conn.execute(
                "INSERT INTO organizations(id, slug, display_name, org_type, created_at) "
                "VALUES (?,?,?,?,?)",
                (tenant_b, "scim-recipe-b", "SCIM Recipe B", "business", time.time()),
            )

        def admin(organization_id: str, email: str) -> dict[str, str]:
            with main.db() as conn:
                user_id = main.create_user(
                    conn,
                    organization_id,
                    email,
                    security_role="organization_admin",
                )
                account = main.user_by_id(conn, user_id)
            session = main.create_admin_session(account, "recipe")
            return {"Authorization": f"Bearer {session['token']}"}

        admin_a = admin(tenant_a, "admin-a@example.test")
        admin_b = admin(tenant_b, "admin-b@example.test")

        def credential(
            organization_id: str, headers: dict[str, str], name: str
        ) -> tuple[str, str]:
            response = client.post(
                f"/api/control/organizations/{organization_id}/provisioning/credentials",
                headers=headers,
                json={"name": name, "provider_context": "generic"},
            )
            assert response.status_code == 200, response.text
            return response.json()["token"], response.json()["credential"]["credential_id"]

        c1, c1_id = credential(tenant_a, admin_a, "Recipe C1")
        token_b, _ = credential(tenant_b, admin_b, "Recipe B")
        scim_a = {
            "Authorization": f"Bearer {c1}",
            "Content-Type": "application/scim+json",
        }
        scim_b = {
            "Authorization": f"Bearer {token_b}",
            "Content-Type": "application/scim+json",
        }

        user_document = {
            "schemas": ["urn:ietf:params:scim:schemas:core:2.0:User"],
            "externalId": "recipe-user-a",
            "userName": "controlled-a@example.test",
            "active": True,
            "name": {"givenName": "Controlled", "familyName": "A"},
            "displayName": "Controlled A",
            "emails": [{"value": "controlled-a@example.test", "primary": True}],
        }
        created = client.post("/scim/v2/Users", headers=scim_a, json=user_document)
        assert created.status_code == 201, created.text
        user_id = created.json()["id"]

        filtered = client.get(
            "/scim/v2/Users",
            headers=scim_a,
            params={"filter": 'externalId eq "recipe-user-a"', "count": 1},
        )
        assert filtered.status_code == 200
        assert filtered.json()["totalResults"] == 1

        patch = {
            "schemas": ["urn:ietf:params:scim:api:messages:2.0:PatchOp"],
            "Operations": [
                {"op": "replace", "path": "displayName", "value": "Controlled A Updated"}
            ],
        }
        assert client.patch(f"/scim/v2/Users/{user_id}", headers=scim_a, json=patch).status_code == 200

        session = main.issue_token("controlled-a@example.test", "recipe-device", tenant_a)
        disable = {
            "schemas": ["urn:ietf:params:scim:api:messages:2.0:PatchOp"],
            "Operations": [{"op": "replace", "path": "active", "value": False}],
        }
        assert client.patch(f"/scim/v2/Users/{user_id}", headers=scim_a, json=disable).status_code == 200
        refused_session = client.get(
            "/api/me", headers={"Authorization": f"Bearer {session['token']}"}
        )
        assert refused_session.status_code in {401, 403}

        enable = {
            "schemas": ["urn:ietf:params:scim:api:messages:2.0:PatchOp"],
            "Operations": [{"op": "replace", "path": "active", "value": True}],
        }
        reactivated = client.patch(
            f"/scim/v2/Users/{user_id}", headers=scim_a, json=enable
        )
        assert reactivated.status_code == 200
        assert reactivated.json()["id"] == user_id

        group = client.post(
            "/scim/v2/Groups",
            headers=scim_a,
            json={
                "schemas": ["urn:ietf:params:scim:schemas:core:2.0:Group"],
                "externalId": "recipe-group-a",
                "displayName": "Admins",
            },
        )
        assert group.status_code == 201, group.text
        group_id = group.json()["id"]
        member_patch = {
            "schemas": ["urn:ietf:params:scim:api:messages:2.0:PatchOp"],
            "Operations": [
                {"op": "add", "path": "members", "value": [{"value": user_id}]}
            ],
        }
        first_add = client.patch(
            f"/scim/v2/Groups/{group_id}", headers=scim_a, json=member_patch
        )
        second_add = client.patch(
            f"/scim/v2/Groups/{group_id}", headers=scim_a, json=member_patch
        )
        assert first_add.status_code == second_add.status_code == 204
        current_group = client.get(f"/scim/v2/Groups/{group_id}", headers=scim_a)
        assert len(current_group.json()["members"]) == 1
        with main.db() as conn:
            account = main.user_by_id(conn, user_id)
        assert account["security_role"] == "member"  # Group name grants nothing.

        # Tenant B cannot read A, and A cannot add a B user to its group.
        assert client.get(f"/scim/v2/Users/{user_id}", headers=scim_b).status_code == 404
        user_b_document = {
            **user_document,
            "externalId": "recipe-user-b",
            "userName": "controlled-b@example.test",
            "emails": [{"value": "controlled-b@example.test", "primary": True}],
        }
        user_b = client.post("/scim/v2/Users", headers=scim_b, json=user_b_document)
        assert user_b.status_code == 201
        cross_member = {
            "schemas": ["urn:ietf:params:scim:api:messages:2.0:PatchOp"],
            "Operations": [{
                "op": "add", "path": "members",
                "value": [{"value": user_b.json()["id"]}],
            }],
        }
        assert client.patch(
            f"/scim/v2/Groups/{group_id}", headers=scim_a, json=cross_member
        ).status_code == 400

        # Rotation overlap: both work, then C1 is refused and C2 remains valid.
        c2, _ = credential(tenant_a, admin_a, "Recipe C2")
        scim_c2 = {
            "Authorization": f"Bearer {c2}",
            "Content-Type": "application/scim+json",
        }
        assert client.get("/scim/v2/Users", headers=scim_a).status_code == 200
        assert client.get("/scim/v2/Users", headers=scim_c2).status_code == 200
        assert client.post(
            f"/api/control/organizations/{tenant_a}/provisioning/credentials/{c1_id}/revoke",
            headers=admin_a,
        ).status_code == 200
        assert client.get("/scim/v2/Users", headers=scim_a).status_code == 401
        assert client.get("/scim/v2/Users", headers=scim_c2).status_code == 200

        assert client.delete(f"/scim/v2/Users/{user_id}", headers=scim_c2).status_code == 204
        assert client.delete(f"/scim/v2/Users/{user_id}", headers=scim_c2).status_code == 204

        with main.db() as conn:
            audit_payload = json.dumps(
                [dict(row) for row in conn.execute("SELECT * FROM admin_audit_log")],
                sort_keys=True,
            )
            assert c1 not in audit_payload and c2 not in audit_payload
            assert "Authorization" not in audit_payload
            counts = {
                "users": conn.execute(
                    "SELECT COUNT(*) AS c FROM scim_user_management"
                ).fetchone()["c"],
                "groups": conn.execute(
                    "SELECT COUNT(*) AS c FROM organization_groups"
                ).fetchone()["c"],
                "audit": conn.execute(
                    "SELECT COUNT(*) AS c FROM admin_audit_log"
                ).fetchone()["c"],
            }

        # No secret is returned or printed. TemporaryDirectory removes the DB.
        return {"status": "ok", **counts}


if __name__ == "__main__":
    print(json.dumps(run(), sort_keys=True))
