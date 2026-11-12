"""Droit a l'effacement : supprimer un membre efface toutes ses lignes personnelles.

Le test remplit chaque table personnelle, supprime le membre par la route de
la console, puis parcourt tout le schema : aucune ligne ne doit plus porter
son identifiant ni son adresse, hors journal d'audit. Une table ajoutee plus
tard et oubliee dans `erase_member_data` fait echouer ce parcours.
"""

import time
import unittest

from test_main import AdminTestCase  # noqa: F401

import main

main.init_db()

EMAIL = "efface.moi@example.com"
KEPT = {"admin_audit_log"}  # obligation de tracabilite : identifiants seuls
KEPT_COLUMNS = {"created_by_user_id", "updated_by_user_id", "actor_user_id"}


class MemberErasureTests(AdminTestCase):
    def setUp(self):
        super().setUp()
        self.org = main.nova_organization_id()

    def seed(self, conn, user_id: str):
        now, org, e = time.time(), self.org, EMAIL
        rows = [
            ("INSERT INTO usage_hourly(email, hour, user_id, organization_id) VALUES (?,?,?,?)", (e, "2026-11-01T10", user_id, org)),
            ("INSERT INTO usage_monthly(email, month, organization_id) VALUES (?,?,?)", (e, "2026-11", org)),
            ("INSERT INTO ai_spend(month, email, eur) VALUES (?,?,?)", ("2026-11", e, 0.5)),
            ("INSERT INTO survey_answered(email, month) VALUES (?,?)", (e, "2026-11")),
            ("INSERT INTO learning_progress(progress_id, organization_id, email, lesson_id, status, lesson_version, updated_at) VALUES (?,?,?,?,?,?,?)", ("p1", org, e, "l1", "started", 1, now)),
            ("INSERT INTO organization_member_group_display(organization_id, user_id, visible, updated_at) VALUES (?,?,?,?)", (org, user_id, 1, now)),
            ("INSERT INTO organization_groups(organization_id, group_id, display_name, created_at, updated_at) VALUES (?,?,?,?,?)", (org, "g1", "G", now, now)),
            ("INSERT INTO organization_group_memberships(organization_id, group_id, user_id, created_at) VALUES (?,?,?,?)", (org, "g1", user_id, now)),
            ("INSERT INTO scim_user_management(organization_id, user_id, user_name, normalized_user_name, created_at, updated_at, last_provisioned_at) VALUES (?,?,?,?,?,?,?)", (org, user_id, e, e, now, now, now)),
            ("INSERT INTO personal_dictionary(email, term, replacement, user_id, organization_id) VALUES (?,?,?,?,?)", (e, "a", "b", user_id, org)),
            ("INSERT INTO snippets(email, trigger, content, user_id, organization_id) VALUES (?,?,?,?,?)", (e, "t", "c", user_id, org)),
            ("INSERT INTO formatting_rules(email, rule, user_id, organization_id) VALUES (?,?,?,?)", (e, "r", user_id, org)),
            ("INSERT INTO auth_codes(email, code, expires_at, organization_id, user_id) VALUES (?,?,?,?,?)", (e, "123456", now + 60, org, user_id)),
        ]
        for sql, params in rows:
            conn.execute(sql, params)

    def leftovers(self, conn, user_id: str):
        found = []
        tables = [r[0] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'").fetchall()]
        for table in tables:
            if table in KEPT:
                continue
            for col in conn.columns(table):
                if col in KEPT_COLUMNS:
                    continue
                if col.endswith("user_id") or col == "expected_user_id":
                    needle = user_id
                elif col in ("email", "normalized_email", "user_name"):
                    needle = EMAIL
                else:
                    continue
                n = conn.execute(
                    f"SELECT COUNT(*) AS n FROM {table} WHERE lower({col})=lower(?)",
                    (needle,)).fetchone()["n"]
                if n:
                    found.append((table, col, n))
        return found

    def test_deleting_a_member_leaves_no_personal_row_outside_the_audit(self):
        token = self.admin_session()
        self.sign_in(EMAIL, machine="poste-efface")  # tokens, usage, users...
        with main.db() as conn:
            user_id = main.user_by_email(conn, self.org, EMAIL)["user_id"]
            self.seed(conn, user_id)
            conn.execute(
                "INSERT INTO usage(email, day, user_id, organization_id) VALUES (?,?,?,?) "
                "ON CONFLICT DO NOTHING", (EMAIL, "2026-11-01", user_id, self.org))
            self.assertTrue(self.leftovers(conn, user_id))  # le semis a pris
        response = self.client.delete(
            f"/api/control/organizations/{self.org}/members/{user_id}",
            headers=self.auth(token))
        self.assertEqual(response.status_code, 200, response.text)
        with main.db() as conn:
            self.assertEqual(self.leftovers(conn, user_id), [])
            audit = conn.execute(
                "SELECT metadata FROM admin_audit_log WHERE target_id=? AND action='member.delete'",
                (user_id,)).fetchall()
            self.assertEqual(len(audit), 1)
            self.assertNotIn(EMAIL, str(dict(audit[0])))


if __name__ == "__main__":
    unittest.main()
