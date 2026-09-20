"""Import d'une liste de membres, en CSV ou en XLSX.

L'IPSA a ses promos dans un tableur : une colonne d'adresses, une colonne de
groupe. Les saisir une par une dans la console n'est pas une option a plusieurs
centaines d'etudiants.

Ce que l'import garantit :

- **on voit avant d'ecrire.** Un apercu dit combien de lignes sont lues, ce qui
  serait cree, ajoute et retire, et ce qui serait refuse. Rien n'est ecrit tant
  que l'administrateur n'a pas confirme ;
- **deux modes.** `add` n'ajoute que ce qui manque ; `sync` fait du fichier la
  liste de reference des groupes qu'il nomme — qui n'y figure plus en sort ;
- **rien ne se perd.** Aucun compte n'est supprime, jamais ;
- **les groupes de l'annuaire restent a l'annuaire** : l'import ne remplit que
  les groupes de la console ;
- une ligne refusee n'empeche pas les autres, et dit pourquoi elle l'est.
"""

import io
import unittest

from test_main import AdminTestCase  # noqa: F401

import main

main.init_db()

CSV = "email,groupe,type\na@example.com,Aero 2,student\nb@example.com,Aero 2,\n"


def workbook(rows: list[list[str]]) -> bytes:
    from openpyxl import Workbook

    book = Workbook()
    sheet = book.active
    for row in rows:
        sheet.append(row)
    buffer = io.BytesIO()
    book.save(buffer)
    return buffer.getvalue()


def clear():
    with main.db() as conn:
        for table in ("organization_group_rules", "organization_group_memberships",
                      "organization_groups"):
            conn.execute(f"DELETE FROM {table}")


class ImportTestCase(AdminTestCase):
    def setUp(self):
        super().setUp()
        clear()
        self.organization = main.nova_organization_id()
        self.admin = self.admin_session()

    def path(self) -> str:
        return f"/api/control/organizations/{self.organization}/members/import"

    def send(self, content: bytes | str, name: str = "promo.csv", mode: str = "add",
             apply: bool = False, headers: dict | None = None):
        payload = content.encode("utf-8") if isinstance(content, str) else content
        return self.client.post(
            self.path(), headers=headers or self.auth(self.admin),
            files={"file": (name, payload, "text/csv")},
            data={"mode": mode, "apply": "true" if apply else "false"})

    def group_members(self, name: str) -> set[str]:
        with main.db() as conn:
            rows = conn.execute(
                "SELECT u.email FROM organization_groups g "
                "JOIN organization_group_memberships m ON m.organization_id=g.organization_id "
                "AND m.group_id=g.group_id "
                "JOIN users u ON u.user_id=m.user_id "
                "WHERE g.organization_id=? AND g.display_name=? AND g.deleted_at IS NULL",
                (self.organization, name)).fetchall()
        return {row["email"] for row in rows}


class PreviewTests(ImportTestCase):
    def test_the_preview_counts_everything_and_writes_nothing(self):
        response = self.send(CSV)
        self.assertEqual(response.status_code, 200, response.text)
        preview = response.json()["preview"]
        self.assertEqual(preview["rows"], 2)
        self.assertEqual(preview["accounts_to_create"], 2)
        self.assertEqual(preview["memberships_to_add"], 2)
        self.assertEqual(preview["memberships_to_remove"], 0)
        self.assertEqual(preview["groups_to_create"], ["Aero 2"])
        self.assertEqual(preview["invalid"], [])
        self.assertFalse(response.json()["applied"])
        self.assertEqual(self.group_members("Aero 2"), set())

    def test_applying_creates_the_group_the_accounts_and_the_memberships(self):
        response = self.send(CSV, apply=True)
        self.assertEqual(response.status_code, 200, response.text)
        self.assertTrue(response.json()["applied"])
        self.assertEqual(self.group_members("Aero 2"), {"a@example.com", "b@example.com"})

    def test_an_xlsx_file_is_read_like_a_csv(self):
        content = workbook([["Adresse", "Groupe"], ["a@example.com", "Aero 2"]])
        response = self.send(content, name="promo.xlsx", apply=True)
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(self.group_members("Aero 2"), {"a@example.com"})

    def test_a_second_import_of_the_same_file_changes_nothing(self):
        self.send(CSV, apply=True)
        preview = self.send(CSV).json()["preview"]
        self.assertEqual(preview["accounts_to_create"], 0)
        self.assertEqual(preview["memberships_to_add"], 0)


class ModeTests(ImportTestCase):
    def setUp(self):
        super().setUp()
        self.send("email,groupe\na@example.com,Aero 2\nb@example.com,Aero 2\n", apply=True)

    def test_add_never_removes_anyone(self):
        self.send("email,groupe\na@example.com,Aero 2\n", mode="add", apply=True)
        self.assertEqual(self.group_members("Aero 2"), {"a@example.com", "b@example.com"})

    def test_sync_makes_the_file_the_reference_for_the_groups_it_names(self):
        response = self.send("email,groupe\na@example.com,Aero 2\n", mode="sync", apply=True)
        self.assertEqual(response.json()["preview"]["memberships_to_remove"], 1)
        self.assertEqual(self.group_members("Aero 2"), {"a@example.com"})
        # Le compte reste : sortir d'un groupe n'est pas partir.
        with main.db() as conn:
            self.assertIsNotNone(main.user_by_email(conn, self.organization, "b@example.com"))

    def test_sync_leaves_alone_a_group_the_file_does_not_name(self):
        self.send("email,groupe\nc@example.com,Aero 3\n", apply=True)
        self.send("email,groupe\na@example.com,Aero 2\n", mode="sync", apply=True)
        self.assertEqual(self.group_members("Aero 3"), {"c@example.com"})

    def test_sync_never_removes_what_the_directory_put_there(self):
        with main.db() as conn:
            group_id = conn.execute(
                "SELECT group_id FROM organization_groups WHERE organization_id=? "
                "AND display_name=?", (self.organization, "Aero 2")).fetchone()["group_id"]
            conn.execute(
                "UPDATE organization_group_memberships SET origin='directory' "
                "WHERE organization_id=? AND group_id=? AND user_id=("
                "SELECT user_id FROM users WHERE organization_id=? AND normalized_email=?)",
                (self.organization, group_id, self.organization, "b@example.com"))
        self.send("email,groupe\na@example.com,Aero 2\n", mode="sync", apply=True)
        # L'annuaire le remettrait au tour suivant : le retirer ici ne ferait
        # que mentir sur le resultat de l'import.
        self.assertEqual(self.group_members("Aero 2"), {"a@example.com", "b@example.com"})


class RefusalTests(ImportTestCase):
    def test_a_bad_line_is_reported_and_the_others_pass(self):
        response = self.send(
            "email,groupe\npas-une-adresse,Aero 2\ndehors@ailleurs.test,Aero 2\n"
            "bon@example.com,Aero 2\n", apply=True)
        self.assertEqual(response.status_code, 200, response.text)
        invalid = response.json()["preview"]["invalid"]
        self.assertEqual([entry["line"] for entry in invalid], [2, 3])
        self.assertEqual([entry["code"] for entry in invalid],
                         ["MEMBER_EMAIL_INVALID", "MEMBER_EMAIL_OUTSIDE_DOMAINS"])
        self.assertEqual(self.group_members("Aero 2"), {"bon@example.com"})

    def test_a_file_without_an_email_column_is_refused(self):
        response = self.send("nom,groupe\nJean,Aero 2\n")
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json()["detail"], "IMPORT_EMAIL_COLUMN_MISSING")

    def test_a_directory_group_is_never_filled_by_an_import(self):
        with main.db() as conn:
            conn.execute(
                "INSERT INTO organization_groups(organization_id, group_id, external_id, "
                "display_name, created_at, updated_at, source) VALUES (?,?,?,?,?,?,?)",
                (self.organization, "g-scim", "ext-1", "Annuaire", 1.0, 1.0, "scim"))
        response = self.send("email,groupe\na@example.com,Annuaire\n", apply=True)
        self.assertEqual(response.json()["preview"]["invalid"][0]["code"],
                         "GROUP_MANAGED_BY_DIRECTORY")
        self.assertEqual(self.group_members("Annuaire"), set())

    def test_an_unknown_member_type_is_refused_without_stopping_the_import(self):
        response = self.send("email,groupe,type\na@example.com,Aero 2,pilote\n", apply=True)
        self.assertEqual(response.json()["preview"]["invalid"][0]["code"], "MEMBER_ROLE_INVALID")

    def test_a_file_that_is_neither_csv_nor_xlsx_is_refused(self):
        response = self.send(b"%PDF-1.4 rien", name="promo.pdf")
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json()["detail"], "IMPORT_FILE_UNSUPPORTED")

    def test_a_file_with_too_many_rows_is_refused(self):
        rows = "".join(f"e{index}@example.com,Aero 2\n" for index in range(main.MAX_IMPORT_ROWS + 1))
        response = self.send(f"email,groupe\n{rows}")
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json()["detail"], "IMPORT_TOO_MANY_ROWS")

    def test_importing_needs_identity_management_and_is_audited(self):
        member = self.sign_in("curieux@example.com")
        self.assertIn(self.send(CSV, headers=self.auth(member)).status_code, (401, 403))
        self.send(CSV, apply=True)
        with main.db() as conn:
            actions = [row["action"] for row in conn.execute(
                "SELECT action FROM admin_audit_log WHERE action LIKE 'member.import%'")]
        self.assertEqual(actions, ["member.import"])

    def test_a_preview_leaves_no_trace_in_the_journal(self):
        self.send(CSV)
        with main.db() as conn:
            count = conn.execute(
                "SELECT COUNT(*) AS n FROM admin_audit_log WHERE action LIKE 'member.import%'"
            ).fetchone()["n"]
        self.assertEqual(count, 0)


class ParserTests(unittest.TestCase):
    def test_columns_are_recognised_in_french_and_english(self):
        for header in ("email,groupe,type", "Adresse,Groupe,Type",
                       "E-Mail,Group,Member type", "mail;groupe;type"):
            with self.subTest(header=header):
                separator = ";" if ";" in header else ","
                rows = main.read_member_rows(
                    f"{header}\na@example.com{separator}Aero 2{separator}student\n".encode("utf-8"),
                    "x.csv")
                self.assertEqual(rows, [{"line": 2, "email": "a@example.com",
                                         "groups": ["Aero 2"], "member_role": "student"}])

    def test_several_groups_on_one_line(self):
        rows = main.read_member_rows(
            b"email,groupe\na@example.com,\"Aero 2; Club robotique\"\n", "x.csv")
        self.assertEqual(rows[0]["groups"], ["Aero 2", "Club robotique"])

    def test_empty_lines_and_spaces_are_ignored(self):
        rows = main.read_member_rows(
            b"email,groupe\n\n  a@example.com , Aero 2 \n\n", "x.csv")
        self.assertEqual(rows, [{"line": 3, "email": "a@example.com",
                                 "groups": ["Aero 2"], "member_role": ""}])


if __name__ == "__main__":
    unittest.main()
