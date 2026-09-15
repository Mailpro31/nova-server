"""Apprendre, configure par l'organisation.

Le catalogue par defaut est le meme pour tout le monde. Chaque organisation
decide, lecon par lecon, de ce que ses membres en voient :

- archiver une lecon la retire des membres sans rien effacer — la progression
  deja faite reste, et restaurer la fait revenir telle quelle ;
- une lecon peut etre obligatoire, avec une echeance ;
- une lecon peut viser certains groupes seulement.

Chaque changement porte une revision (deux consoles ouvertes ne s'ecrasent
pas) et laisse une trace d'audit avec l'etat avant et apres.
"""

import json
import time
import unittest
import uuid

from test_main import AdminTestCase  # noqa: F401

import learning
import main

main.init_db()

LESSON = "ask-better-questions"
PATH = "/api/control/organization/learning"


def clear_learning_settings():
    with main.db() as conn:
        conn.execute("DELETE FROM organization_learning_lesson_settings")
        conn.execute("DELETE FROM learning_progress")


def create_group(name: str, emails: list[str]) -> str:
    organization_id = main.nova_organization_id()
    group_id = str(uuid.uuid4())
    now = time.time()
    with main.db() as conn:
        conn.execute(
            "INSERT INTO organization_groups(organization_id, group_id, external_id, "
            "display_name, created_at, updated_at) VALUES (?,?,?,?,?,?)",
            (organization_id, group_id, None, name, now, now))
        for email in emails:
            user_id = conn.execute(
                "SELECT user_id FROM users WHERE organization_id=? AND email=?",
                (organization_id, email)).fetchone()["user_id"]
            conn.execute(
                "INSERT INTO organization_group_memberships(organization_id, group_id, "
                "user_id, created_at) VALUES (?,?,?,?)",
                (organization_id, group_id, user_id, now))
    return group_id


def member_lessons(catalog: dict) -> dict[str, dict]:
    return {
        lesson["id"]: lesson
        for path in catalog["paths"]
        for module in path["modules"]
        for lesson in module["lessons"]
    }


class OrganizationLearningTestCase(AdminTestCase):
    def setUp(self):
        super().setUp()
        learning.reset_cache()
        clear_learning_settings()
        self.admin = self.admin_session()

    def lesson_view(self, lesson_id: str = LESSON) -> dict:
        response = self.client.get(PATH, headers=self.auth(self.admin))
        self.assertEqual(response.status_code, 200, response.text)
        return next(item for item in response.json()["lessons"] if item["id"] == lesson_id)

    def update(self, body: dict, lesson_id: str = LESSON, token: str | None = None):
        return self.client.put(
            f"{PATH}/lessons/{lesson_id}", headers=self.auth(token or self.admin), json=body)

    def action(self, verb: str, revision: int, lesson_id: str = LESSON):
        return self.client.post(
            f"{PATH}/lessons/{lesson_id}/{verb}", headers=self.auth(self.admin),
            json={"expected_revision": revision})

    def catalog_for(self, email: str) -> dict:
        token = self.sign_in(email)
        response = self.client.get("/api/learning/catalog", headers=self.auth(token))
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()


class AdministrationViewTests(OrganizationLearningTestCase):
    def test_every_default_lesson_is_listed_with_neutral_settings(self):
        response = self.client.get(PATH, headers=self.auth(self.admin))
        self.assertEqual(response.status_code, 200, response.text)
        lessons = {item["id"]: item for item in response.json()["lessons"]}
        self.assertEqual(set(lessons), set(learning.lessons_index()))
        self.assertEqual(
            {key: lessons[LESSON][key] for key in
             ("status", "required", "due_at", "group_ids", "revision")},
            {"status": "active", "required": False, "due_at": None,
             "group_ids": [], "revision": 0},
        )
        self.assertEqual(lessons[LESSON]["path_id"], "asking-well")

    def test_the_view_lists_the_groups_that_can_be_targeted(self):
        self.sign_in("etudiant@example.com")
        group_id = create_group("Promo 2027", ["etudiant@example.com"])
        groups = self.client.get(PATH, headers=self.auth(self.admin)).json()["groups"]
        self.assertIn({"id": group_id, "label": "Promo 2027"}, groups)

    def test_a_member_cannot_reach_the_console_routes(self):
        token = self.sign_in("etudiant@example.com")
        self.assertIn(self.client.get(PATH, headers=self.auth(token)).status_code, (401, 403))
        response = self.update({"expected_revision": 0, "required": True}, token=token)
        self.assertIn(response.status_code, (401, 403))


class RequiredLessonTests(OrganizationLearningTestCase):
    def test_a_required_lesson_with_a_due_date_reaches_members(self):
        due = 1_900_000_000.0
        response = self.update({"expected_revision": 0, "required": True, "due_at": due})
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["revision"], 1)
        lesson = member_lessons(self.catalog_for("etudiant@example.com"))[LESSON]
        self.assertTrue(lesson["required"])
        self.assertEqual(lesson["due_at"], due)

    def test_an_untouched_lesson_is_optional_without_due_date(self):
        lesson = member_lessons(self.catalog_for("etudiant@example.com"))["context-and-tokens"]
        self.assertFalse(lesson["required"])
        self.assertIsNone(lesson["due_at"])

    def test_a_stale_revision_is_refused_without_writing(self):
        self.update({"expected_revision": 0, "required": True})
        response = self.update({"expected_revision": 0, "required": False})
        self.assertEqual(response.status_code, 409, response.text)
        self.assertTrue(self.lesson_view()["required"])

    def test_an_unknown_lesson_is_refused(self):
        response = self.update({"expected_revision": 0, "required": True},
                               lesson_id="does-not-exist")
        self.assertEqual(response.status_code, 404, response.text)

    def test_a_due_date_or_group_of_the_wrong_shape_is_refused(self):
        for body in ({"due_at": "demain"}, {"due_at": -1}, {"group_ids": "tous"},
                     {"group_ids": ["groupe-inconnu"]}, {"required": "oui"}):
            with self.subTest(body=body):
                response = self.update({"expected_revision": 0, **body})
                self.assertEqual(response.status_code, 400, response.text)
        self.assertEqual(self.lesson_view()["revision"], 0)

    def test_every_change_is_audited_with_before_and_after(self):
        self.update({"expected_revision": 0, "required": True, "due_at": 1_900_000_000.0})
        with main.db() as conn:
            row = conn.execute(
                "SELECT target_id, metadata FROM admin_audit_log "
                "WHERE action='learning_lesson.update' ORDER BY created_at DESC LIMIT 1"
            ).fetchone()
        self.assertEqual(row["target_id"], LESSON)
        metadata = json.loads(row["metadata"])
        self.assertEqual(metadata["before"]["required"], False)
        self.assertEqual(metadata["after"]["required"], True)
        self.assertEqual(metadata["after"]["due_at"], 1_900_000_000.0)
        self.assertEqual(metadata["new_revision"], 1)


class ArchiveTests(OrganizationLearningTestCase):
    def test_an_archived_lesson_is_hidden_from_members(self):
        response = self.action("archive", 0)
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["status"], "archived")
        self.assertNotIn(LESSON, member_lessons(self.catalog_for("etudiant@example.com")))

    def test_archiving_keeps_the_progress_and_restoring_brings_the_lesson_back(self):
        token = self.sign_in("etudiant@example.com")
        lesson = learning.lessons_index()[LESSON]["lesson"]
        first_block = sorted(lesson["blocks"], key=lambda b: b["order"])[0]["id"]
        saved = self.client.put(
            f"/api/learning/progress/{LESSON}", headers=self.auth(token),
            json={"completed_blocks": [first_block], "last_block_id": first_block})
        self.assertEqual(saved.status_code, 200, saved.text)

        self.action("archive", 0)
        self.assertEqual(self.action("restore", 1).status_code, 200)

        self.assertIn(LESSON, member_lessons(self.catalog_for("etudiant@example.com")))
        progress = self.client.get("/api/learning/progress", headers=self.auth(token)).json()
        self.assertIn(LESSON, [item["lesson_id"] for item in progress["lessons"]])

    def test_a_module_left_empty_by_archiving_disappears_for_members(self):
        index = learning.lessons_index()
        module_id = index[LESSON]["module_id"]
        lesson_ids = [lesson_id for lesson_id, entry in index.items()
                      if entry["module_id"] == module_id]
        for lesson_id in lesson_ids:
            self.assertEqual(self.action("archive", 0, lesson_id).status_code, 200)
        catalog = self.catalog_for("etudiant@example.com")
        modules = [module["id"] for path in catalog["paths"] for module in path["modules"]]
        self.assertNotIn(module_id, modules)
        self.assertNotIn(index[LESSON]["path_id"], [path["id"] for path in catalog["paths"]])

    def test_archive_and_restore_are_audited(self):
        self.action("archive", 0)
        self.action("restore", 1)
        with main.db() as conn:
            actions = [row["action"] for row in conn.execute(
                "SELECT action FROM admin_audit_log WHERE target_id=? "
                "AND action LIKE 'learning_lesson.%' ORDER BY created_at", (LESSON,))]
        self.assertEqual(actions, ["learning_lesson.archive", "learning_lesson.restore"])


class GroupTargetingTests(OrganizationLearningTestCase):
    def test_a_lesson_aimed_at_a_group_reaches_only_its_members(self):
        self.sign_in("dans-le-groupe@example.com")
        self.sign_in("hors-groupe@example.com")
        group_id = create_group("Ingenierie", ["dans-le-groupe@example.com"])
        response = self.update({"expected_revision": 0, "group_ids": [group_id]})
        self.assertEqual(response.status_code, 200, response.text)

        self.assertIn(LESSON, member_lessons(self.catalog_for("dans-le-groupe@example.com")))
        self.assertNotIn(LESSON, member_lessons(self.catalog_for("hors-groupe@example.com")))

    def test_no_group_means_everyone(self):
        self.update({"expected_revision": 0, "group_ids": []})
        self.assertIn(LESSON, member_lessons(self.catalog_for("etudiant@example.com")))


if __name__ == "__main__":
    unittest.main()
