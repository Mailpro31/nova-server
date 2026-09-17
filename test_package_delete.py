"""Supprimer un package archive.

Archiver retire un package de la distribution sans rien effacer. Un package
devenu inutile doit aussi pouvoir disparaitre, mais seulement apres avoir ete
archive : la suppression est definitive, et le passage par l'archive est ce qui
evite de retirer par erreur un contenu que les membres recoivent encore.
"""

import json
import unittest

from test_main import AdminTestCase  # noqa: F401

import main

main.init_db()

STYLE = {"name": "Example Style", "instruction": "Write in plain sentences."}


def clear_packages():
    with main.db() as conn:
        conn.execute("DELETE FROM organization_package_versions")
        conn.execute("DELETE FROM organization_packages")
        conn.execute("DELETE FROM organization_policies")


class PackageDeleteTests(AdminTestCase):
    def setUp(self):
        super().setUp()
        clear_packages()
        self.organization = main.nova_organization_id()
        self.token = self.admin_session()

    def base(self) -> str:
        return f"/api/control/organizations/{self.organization}/packages"

    def published(self) -> str:
        created = self.client.post(self.base(), headers=self.auth(self.token),
                                   json={"package_type": "style", "name": "Old Style"})
        self.assertIn(created.status_code, (200, 201), created.text)
        package_id = created.json()["package_id"]
        draft = self.client.post(f"{self.base()}/{package_id}/versions",
                                 headers=self.auth(self.token), json={"content": STYLE})
        self.assertIn(draft.status_code, (200, 201), draft.text)
        version_id = draft.json()["versions"][0]["version_id"]
        publish = self.client.post(
            f"{self.base()}/{package_id}/versions/{version_id}/publish",
            headers=self.auth(self.token), json={"activate": True})
        self.assertEqual(publish.status_code, 200, publish.text)
        return package_id

    def archive(self, package_id: str):
        response = self.client.post(f"{self.base()}/{package_id}/archive",
                                    headers=self.auth(self.token))
        self.assertEqual(response.status_code, 200, response.text)

    def delete(self, package_id: str, token: str | None = None):
        return self.client.delete(f"{self.base()}/{package_id}",
                                  headers=self.auth(token or self.token))

    def test_an_archived_package_is_deleted_with_its_versions(self):
        package_id = self.published()
        self.archive(package_id)
        response = self.delete(package_id)
        self.assertEqual(response.status_code, 204, response.text)
        self.assertEqual(self.client.get(f"{self.base()}/{package_id}",
                                         headers=self.auth(self.token)).status_code, 404)
        with main.db() as conn:
            versions = conn.execute(
                "SELECT COUNT(*) AS n FROM organization_package_versions WHERE package_id=?",
                (package_id,)).fetchone()["n"]
        self.assertEqual(versions, 0)

    def test_a_package_still_distributed_cannot_be_deleted(self):
        package_id = self.published()
        response = self.delete(package_id)
        self.assertEqual(response.status_code, 409, response.text)
        self.assertEqual(response.json()["detail"], "PACKAGE_NOT_ARCHIVED")
        self.assertEqual(self.client.get(f"{self.base()}/{package_id}",
                                         headers=self.auth(self.token)).status_code, 200)

    def test_an_unknown_package_is_not_found(self):
        self.assertEqual(self.delete("inconnu").status_code, 404)

    def test_read_only_cannot_delete(self):
        package_id = self.published()
        self.archive(package_id)
        reader = self.admin_session("lecture@example.com",
                                    main.SECURITY_ROLE_READ_ONLY, subject="oid-ro")
        self.assertEqual(self.delete(package_id, reader).status_code, 403)

    def test_the_deletion_is_audited_with_what_was_deleted(self):
        package_id = self.published()
        self.archive(package_id)
        self.delete(package_id)
        with main.db() as conn:
            row = conn.execute(
                "SELECT target_id, metadata FROM admin_audit_log "
                "WHERE action='package.delete'").fetchone()
        self.assertIsNotNone(row)
        self.assertEqual(row["target_id"], package_id)
        metadata = json.loads(row["metadata"])
        self.assertEqual(metadata["package_type"], "style")
        self.assertEqual(metadata["name"], "Old Style")
        self.assertIn("package.delete", main.AUDIT_ACTION_LABELS)


if __name__ == "__main__":
    unittest.main()
