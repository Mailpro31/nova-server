"""Lecons creees par l'organisation.

Le catalogue par defaut ne couvre pas le metier de chaque organisation. Une
ecole d'ingenieurs veut une lecon sur ses propres regles de rapport, une
entreprise sur sa charte de donnees. La console les ecrit en blocs — les memes
que le catalogue — et dans plusieurs langues.

Ce que ces tests tiennent :

- une lecon creee arrive aux membres, dans le module choisi, dans leur langue
  quand elle existe et sinon dans la langue source ;
- archiver, rendre obligatoire, viser un groupe : les memes reglages que pour
  une lecon par defaut ;
- supprimer est definitif et ne vaut que pour une lecon de l'organisation ;
- chaque ecriture porte une revision et laisse une trace avant/apres ;
- une lecon d'une organisation n'existe pour aucune autre ;
- un bloc `ai_exercise` est refuse : son instruction vit dans le catalogue
  livre, jamais dans un contenu saisi en console.
"""

import json
import unittest

from test_main import AdminTestCase, grant_admin_session  # noqa: F401

import learning
import main

main.init_db()

BASE = "/api/control/organization/learning"
CUSTOM = f"{BASE}/custom-lessons"
MODULE = "use-ai-foundations"


def blocks(title_word: str = "Rules") -> list[dict]:
    return [
        {"id": "intro", "type": "text", "order": 1,
         "content": {"body": f"{title_word}: what our reports must contain."}},
        {"id": "check", "type": "multiple_choice", "order": 2,
         "content": {"question": "Which section comes first?",
                     "options": [{"id": "a", "label": "Summary"},
                                 {"id": "b", "label": "Annexes"}],
                     "correct_option_id": "a",
                     "explanation": "Readers decide from the summary."}},
        {"id": "remember", "type": "takeaway", "order": 3,
         "content": {"body": "Summary first, annexes last."}},
    ]


def french_blocks() -> list[dict]:
    return [
        {"id": "intro", "type": "text", "order": 1,
         "content": {"body": "Regles : ce que nos rapports doivent contenir."}},
        {"id": "check", "type": "multiple_choice", "order": 2,
         "content": {"question": "Quelle section vient en premier ?",
                     "options": [{"id": "a", "label": "Synthese"},
                                 {"id": "b", "label": "Annexes"}],
                     "correct_option_id": "a",
                     "explanation": "Le lecteur decide a partir de la synthese."}},
        {"id": "remember", "type": "takeaway", "order": 3,
         "content": {"body": "La synthese d'abord, les annexes a la fin."}},
    ]


def lesson_body(**overrides) -> dict:
    body = {
        "module_id": MODULE,
        "source_locale": "en",
        "estimated_minutes": 4,
        "difficulty": "beginner",
        "translations": {
            "en": {"title": "Our report rules", "description": "What every report needs.",
                   "blocks": blocks()},
        },
    }
    body.update(overrides)
    return body


def clear_custom_lessons():
    with main.db() as conn:
        conn.execute("DELETE FROM organization_learning_custom_lessons")
        conn.execute("DELETE FROM organization_learning_lesson_settings")
        conn.execute("DELETE FROM learning_progress")


def member_lessons(catalog: dict) -> dict[str, dict]:
    return {
        lesson["id"]: {**lesson, "module_id": module["id"]}
        for path in catalog["paths"]
        for module in path["modules"]
        for lesson in module["lessons"]
    }


class CustomLessonTestCase(AdminTestCase):
    def setUp(self):
        super().setUp()
        learning.reset_cache()
        clear_custom_lessons()
        self.admin = self.admin_session()

    def create(self, body: dict | None = None, headers: dict | None = None):
        return self.client.post(CUSTOM, headers=headers or self.auth(self.admin),
                                json=body if body is not None else lesson_body())

    def created(self, body: dict | None = None) -> dict:
        response = self.create(body)
        self.assertEqual(response.status_code, 201, response.text)
        return response.json()

    def catalog_for(self, email: str, locale: str | None = None) -> dict:
        token = self.sign_in(email)
        suffix = f"?locale={locale}" if locale else ""
        response = self.client.get(f"/api/learning/catalog{suffix}", headers=self.auth(token))
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()


class CreateTests(CustomLessonTestCase):
    def test_a_created_lesson_reaches_members_in_its_module(self):
        created = self.created()
        self.assertTrue(created["id"].startswith("org-"))
        self.assertEqual(created["revision"], 1)
        self.assertEqual(created["version"], 1)
        lesson = member_lessons(self.catalog_for("etudiant@example.com"))[created["id"]]
        self.assertEqual(lesson["module_id"], MODULE)
        self.assertEqual(lesson["title"], "Our report rules")
        self.assertEqual([block["id"] for block in lesson["blocks"]],
                         ["intro", "check", "remember"])
        self.assertTrue(lesson["custom"])

    def test_it_comes_after_the_default_lessons_of_the_module(self):
        created = self.created()
        catalog = self.catalog_for("etudiant@example.com")
        module = next(module for path in catalog["paths"] for module in path["modules"]
                      if module["id"] == MODULE)
        ordered = sorted(module["lessons"], key=lambda lesson: lesson["order"])
        self.assertEqual(ordered[-1]["id"], created["id"])
        self.assertEqual(len({lesson["order"] for lesson in module["lessons"]}),
                         len(module["lessons"]))

    def test_the_console_lists_it_with_the_default_lessons(self):
        created = self.created()
        body = self.client.get(BASE, headers=self.auth(self.admin)).json()
        listed = {item["id"]: item for item in body["lessons"]}
        self.assertTrue(listed[created["id"]]["custom"])
        self.assertFalse(listed["ask-better-questions"]["custom"])
        self.assertIn(MODULE, [module["id"] for module in body["modules"]])

    def test_the_editor_reads_every_translation_back(self):
        created = self.created(lesson_body(translations={
            "en": {"title": "Our report rules", "description": "What every report needs.",
                   "blocks": blocks()},
            "fr": {"title": "Nos regles de rapport", "description": "Ce que chaque rapport contient.",
                   "blocks": french_blocks()},
        }))
        response = self.client.get(f"{CUSTOM}/{created['id']}", headers=self.auth(self.admin))
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(set(response.json()["translations"]), {"en", "fr"})

    def test_a_malformed_lesson_is_refused_without_writing(self):
        invalid = [
            lesson_body(module_id="no-such-module"),
            lesson_body(difficulty="expert"),
            lesson_body(estimated_minutes=45),
            lesson_body(source_locale="fr"),  # la langue source doit etre fournie
            lesson_body(source_locale="xx"),
            lesson_body(translations={"en": {"title": "", "description": "d",
                                             "blocks": blocks()}}),
            lesson_body(translations={"en": {"title": "t", "description": "d",
                                             "blocks": []}}),
            lesson_body(translations={"en": {"title": "t", "description": "d",
                                             "blocks": [{"id": "x", "type": "video",
                                                         "order": 1, "content": {}}]}}),
            lesson_body(unexpected=True),
        ]
        for body in invalid:
            with self.subTest(body=json.dumps(body)[:120]):
                self.assertEqual(self.create(body).status_code, 400)
        with main.db() as conn:
            count = conn.execute(
                "SELECT COUNT(*) AS n FROM organization_learning_custom_lessons").fetchone()["n"]
        self.assertEqual(count, 0)

    def test_an_ai_exercise_block_is_refused(self):
        body = lesson_body(translations={"en": {
            "title": "t", "description": "d",
            "blocks": [{"id": "ai", "type": "ai_exercise", "order": 1,
                        "content": {"instruction": "Try it",
                                    "exercise_id": "adapt-domain-request"}}]}})
        self.assertEqual(self.create(body).status_code, 400)

    def test_translations_must_keep_the_same_blocks(self):
        drifted = french_blocks()[:2]
        body = lesson_body(translations={
            "en": {"title": "t", "description": "d", "blocks": blocks()},
            "fr": {"title": "t", "description": "d", "blocks": drifted},
        })
        self.assertEqual(self.create(body).status_code, 400)

    def test_creation_is_audited(self):
        created = self.created()
        with main.db() as conn:
            row = conn.execute(
                "SELECT target_id, metadata FROM admin_audit_log "
                "WHERE action='learning_custom_lesson.create'").fetchone()
        self.assertEqual(row["target_id"], created["id"])
        metadata = json.loads(row["metadata"])
        self.assertIsNone(metadata["before"])
        self.assertEqual(metadata["after"]["titles"], {"en": "Our report rules"})


class LanguageTests(CustomLessonTestCase):
    def setUp(self):
        super().setUp()
        self.lesson = self.created(lesson_body(translations={
            "en": {"title": "Our report rules", "description": "What every report needs.",
                   "blocks": blocks()},
            "fr": {"title": "Nos regles de rapport", "description": "Ce que chaque rapport contient.",
                   "blocks": french_blocks()},
        }))

    def test_a_member_reads_it_in_their_language(self):
        lesson = member_lessons(self.catalog_for("etudiant@example.com", "fr-FR"))[self.lesson["id"]]
        self.assertEqual(lesson["title"], "Nos regles de rapport")
        self.assertEqual(lesson["content_locale"], "fr")

    def test_a_language_without_translation_falls_back_to_the_source(self):
        lesson = member_lessons(self.catalog_for("etudiant@example.com", "de"))[self.lesson["id"]]
        self.assertEqual(lesson["title"], "Our report rules")
        self.assertEqual(lesson["content_locale"], "en")


class UpdateTests(CustomLessonTestCase):
    def test_a_text_edit_keeps_the_version_and_moves_the_revision(self):
        created = self.created()
        edited = lesson_body()["translations"]
        edited["en"]["title"] = "Report rules, revised"
        response = self.client.put(
            f"{CUSTOM}/{created['id']}", headers=self.auth(self.admin),
            json={"expected_revision": 1, "translations": edited})
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["revision"], 2)
        self.assertEqual(response.json()["version"], 1)
        lesson = member_lessons(self.catalog_for("etudiant@example.com"))[created["id"]]
        self.assertEqual(lesson["title"], "Report rules, revised")

    def test_changing_the_blocks_moves_the_version(self):
        created = self.created()
        changed = lesson_body()["translations"]
        changed["en"]["blocks"] = blocks()[:1] + blocks()[2:]
        changed["en"]["blocks"][1]["order"] = 2
        response = self.client.put(
            f"{CUSTOM}/{created['id']}", headers=self.auth(self.admin),
            json={"expected_revision": 1, "translations": changed})
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["version"], 2)

    def test_a_stale_revision_is_refused(self):
        created = self.created()
        body = {"expected_revision": 1, "estimated_minutes": 5}
        self.assertEqual(self.client.put(f"{CUSTOM}/{created['id']}",
                                         headers=self.auth(self.admin), json=body).status_code, 200)
        response = self.client.put(f"{CUSTOM}/{created['id']}",
                                   headers=self.auth(self.admin), json=body)
        self.assertEqual(response.status_code, 409, response.text)

    def test_a_default_lesson_cannot_be_edited_here(self):
        response = self.client.put(
            f"{CUSTOM}/ask-better-questions", headers=self.auth(self.admin),
            json={"expected_revision": 0, "estimated_minutes": 5})
        self.assertEqual(response.status_code, 404, response.text)

    def test_an_update_is_audited_with_before_and_after(self):
        created = self.created()
        self.client.put(f"{CUSTOM}/{created['id']}", headers=self.auth(self.admin),
                        json={"expected_revision": 1, "estimated_minutes": 6})
        with main.db() as conn:
            row = conn.execute(
                "SELECT metadata FROM admin_audit_log "
                "WHERE action='learning_custom_lesson.update'").fetchone()
        metadata = json.loads(row["metadata"])
        self.assertEqual(metadata["before"]["estimated_minutes"], 4)
        self.assertEqual(metadata["after"]["estimated_minutes"], 6)


class SettingsTests(CustomLessonTestCase):
    def test_it_can_be_archived_and_restored_like_a_default_lesson(self):
        created = self.created()
        archive = self.client.post(f"{BASE}/lessons/{created['id']}/archive",
                                   headers=self.auth(self.admin), json={"expected_revision": 0})
        self.assertEqual(archive.status_code, 200, archive.text)
        self.assertNotIn(created["id"], member_lessons(self.catalog_for("etudiant@example.com")))
        restore = self.client.post(f"{BASE}/lessons/{created['id']}/restore",
                                   headers=self.auth(self.admin), json={"expected_revision": 1})
        self.assertEqual(restore.status_code, 200, restore.text)
        self.assertIn(created["id"], member_lessons(self.catalog_for("etudiant@example.com")))

    def test_it_can_be_required_with_a_due_date(self):
        created = self.created()
        response = self.client.put(
            f"{BASE}/lessons/{created['id']}", headers=self.auth(self.admin),
            json={"expected_revision": 0, "required": True, "due_at": 1_900_000_000.0})
        self.assertEqual(response.status_code, 200, response.text)
        lesson = member_lessons(self.catalog_for("etudiant@example.com"))[created["id"]]
        self.assertTrue(lesson["required"])

    def test_members_progress_through_it_and_complete_it(self):
        created = self.created()
        token = self.sign_in("etudiant@example.com")
        response = self.client.put(
            f"/api/learning/progress/{created['id']}", headers=self.auth(token),
            json={"completed_blocks": ["intro", "check", "remember"],
                  "last_block_id": "remember"})
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["status"], "completed")


class DeleteTests(CustomLessonTestCase):
    def delete(self, lesson_id: str, revision: int):
        return self.client.delete(f"{CUSTOM}/{lesson_id}?expected_revision={revision}",
                                  headers=self.auth(self.admin))

    def test_deleting_removes_the_lesson_its_settings_and_progress(self):
        created = self.created()
        self.client.put(f"{BASE}/lessons/{created['id']}", headers=self.auth(self.admin),
                        json={"expected_revision": 0, "required": True})
        token = self.sign_in("etudiant@example.com")
        self.client.put(f"/api/learning/progress/{created['id']}", headers=self.auth(token),
                        json={"completed_blocks": ["intro"], "last_block_id": "intro"})

        response = self.delete(created["id"], 1)
        self.assertEqual(response.status_code, 204, response.text)
        self.assertNotIn(created["id"], member_lessons(self.catalog_for("etudiant@example.com")))
        self.assertEqual(self.client.get(f"{CUSTOM}/{created['id']}",
                                         headers=self.auth(self.admin)).status_code, 404)
        with main.db() as conn:
            for table in ("organization_learning_lesson_settings", "learning_progress"):
                with self.subTest(table=table):
                    count = conn.execute(
                        f"SELECT COUNT(*) AS n FROM {table} WHERE lesson_id=?",
                        (created["id"],)).fetchone()["n"]
                    self.assertEqual(count, 0)

    def test_a_stale_revision_deletes_nothing(self):
        created = self.created()
        self.assertEqual(self.delete(created["id"], 7).status_code, 409)
        self.assertEqual(self.client.get(f"{CUSTOM}/{created['id']}",
                                         headers=self.auth(self.admin)).status_code, 200)

    def test_a_default_lesson_cannot_be_deleted(self):
        self.assertEqual(self.delete("ask-better-questions", 0).status_code, 404)

    def test_deletion_is_audited(self):
        created = self.created()
        self.delete(created["id"], 1)
        with main.db() as conn:
            row = conn.execute(
                "SELECT metadata FROM admin_audit_log "
                "WHERE action='learning_custom_lesson.delete'").fetchone()
        metadata = json.loads(row["metadata"])
        self.assertEqual(metadata["before"]["titles"], {"en": "Our report rules"})
        self.assertIsNone(metadata["after"])


class IsolationAndRoleTests(CustomLessonTestCase):
    def test_another_organization_never_sees_the_lesson(self):
        created = self.created()
        with main.db() as conn:
            conn.execute("UPDATE organization_learning_custom_lessons SET organization_id=? "
                         "WHERE lesson_id=?", ("another-organization", created["id"]))
        self.assertNotIn(created["id"], member_lessons(self.catalog_for("etudiant@example.com")))
        token = self.sign_in("etudiant@example.com")
        response = self.client.put(f"/api/learning/progress/{created['id']}",
                                   headers=self.auth(token),
                                   json={"completed_blocks": ["intro"]})
        self.assertEqual(response.status_code, 404, response.text)
        self.assertEqual(self.client.get(f"{CUSTOM}/{created['id']}",
                                         headers=self.auth(self.admin)).status_code, 404)

    def test_a_content_editor_can_create_a_lesson(self):
        editor = grant_admin_session("redaction@example.com", "content_editor")
        self.assertEqual(self.create(headers=editor).status_code, 201)

    def test_read_only_and_members_cannot_create(self):
        reader = grant_admin_session("lecture@example.com", "read_only")
        self.assertEqual(self.create(headers=reader).status_code, 403)
        member = self.auth(self.sign_in("etudiant@example.com"))
        self.assertIn(self.create(headers=member).status_code, (401, 403))

    def test_creating_a_lesson_moves_the_learning_marker(self):
        member = self.auth(self.sign_in("etudiant@example.com"))
        before = self.client.get("/api/organization/changes", headers=member).json()
        created = self.created()
        after = self.client.get("/api/organization/changes", headers=member).json()
        self.assertNotEqual(after["learning_version"], before["learning_version"])
        self.client.put(f"{CUSTOM}/{created['id']}", headers=self.auth(self.admin),
                        json={"expected_revision": 1, "estimated_minutes": 5})
        edited = self.client.get("/api/organization/changes", headers=member).json()
        self.assertNotEqual(edited["learning_version"], after["learning_version"])


if __name__ == "__main__":
    unittest.main()
