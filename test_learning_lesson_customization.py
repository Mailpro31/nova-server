"""Personnaliser une lecon livree, et recevoir les mises a jour de Nova sans
qu'elles s'imposent.

Une organisation veut parfois garder une lecon par defaut en changeant un
exemple ou une question. Personnaliser en fait une copie de l'organisation,
dans toutes les langues livrees, qui remplace l'original pour les membres.

Quand Nova publie ensuite une nouvelle version de l'original, la copie ne
bouge pas : la console le signale, montre la nouvelle version, et
l'administrateur choisit de l'appliquer ou de garder la sienne.

Ce que ces tests tiennent :

- la copie porte toutes les langues livrees, son origine et sa version ;
- l'original est archive, et ses obligations passent a la copie ;
- une lecon avec un exercice IA ne se personnalise pas ;
- une nouvelle version livree est proposee, jamais appliquee d'office ;
- appliquer reprend le contenu de Nova, garder conserve celui de
  l'organisation, et les deux laissent une trace dans le journal ;
- l'origine ne se forge pas depuis l'editeur.
"""

import copy
import json
import unittest

from test_learning_custom_lessons import (  # noqa: F401
    CustomLessonTestCase,
    clear_custom_lessons,
    grant_admin_session,
    lesson_body,
    member_lessons,
)

import learning
import main

main.init_db()

BASE = "/api/control/organization/learning"
CUSTOM = f"{BASE}/custom-lessons"
LESSON = "ask-better-questions"
MEMBER = "etudiant@example.com"
NEW_BODY = "Nova rewrote this block for the new version."


def delivered(lesson_id: str, locale: str | None = None) -> dict:
    return learning.lessons_index(learning.load_catalog(locale=locale))[lesson_id]["lesson"]


def lesson_with_ai_exercise() -> str:
    return next(
        lesson_id for lesson_id, entry in learning.lessons_index().items()
        if any(block["type"] == "ai_exercise" for block in entry["lesson"]["blocks"])
    )


def publish_new_nova_version(lesson_id: str) -> None:
    """Simule un catalogue anglais livre avec une nouvelle version de la lecon."""
    catalog = copy.deepcopy(learning.load_catalog())
    for path in catalog["paths"]:
        for module in path["modules"]:
            for lesson in module["lessons"]:
                if lesson["id"] == lesson_id:
                    lesson["version"] += 1
                    first = sorted(lesson["blocks"], key=lambda block: block["order"])[0]
                    first["content"]["body"] = NEW_BODY
    learning._CACHE["en"] = catalog


class CustomizationTestCase(CustomLessonTestCase):
    def setUp(self):
        super().setUp()
        # Les copies, l'original archive et le catalogue simule ne doivent pas
        # survivre au test : les modules suivants lisent la meme base.
        self.addCleanup(clear_custom_lessons)
        self.addCleanup(learning.reset_cache)

    def customize(self, lesson_id: str = LESSON, headers: dict | None = None):
        return self.client.post(f"{BASE}/lessons/{lesson_id}/customize",
                                headers=headers or self.auth(self.admin), json={})

    def customized(self, lesson_id: str = LESSON) -> dict:
        response = self.customize(lesson_id)
        self.assertEqual(response.status_code, 201, response.text)
        return response.json()

    def console_lessons(self) -> dict[str, dict]:
        body = self.client.get(BASE, headers=self.auth(self.admin)).json()
        return {item["id"]: item for item in body["lessons"]}

    def decide(self, lesson_id: str, revision: int, decision: str):
        return self.client.post(f"{CUSTOM}/{lesson_id}/nova-update",
                                headers=self.auth(self.admin),
                                json={"expected_revision": revision, "decision": decision})


class CustomizeTests(CustomizationTestCase):
    def test_the_copy_carries_every_delivered_language_and_its_origin(self):
        copied = self.customized()
        self.assertTrue(copied["id"].startswith("org-"))
        self.assertEqual(copied["source_locale"], "en")
        self.assertEqual(copied["based_on"], {
            "lesson_id": LESSON, "version": delivered(LESSON)["version"],
            "latest_version": delivered(LESSON)["version"], "update_available": False,
        })
        self.assertIn("fr", copied["translations"])
        self.assertEqual(copied["translations"]["en"]["title"], delivered(LESSON)["title"])
        self.assertEqual(copied["translations"]["fr"]["title"],
                         delivered(LESSON, "fr")["title"])
        self.assertEqual([block["id"] for block in copied["translations"]["en"]["blocks"]],
                         [block["id"] for block in delivered(LESSON)["blocks"]])

    def test_the_copy_replaces_the_original_for_members(self):
        copied = self.customized()
        lessons = member_lessons(self.catalog_for(MEMBER))
        self.assertIn(copied["id"], lessons)
        self.assertNotIn(LESSON, lessons)
        listed = self.console_lessons()
        self.assertEqual(listed[LESSON]["status"], "archived")
        self.assertEqual(listed[copied["id"]]["based_on_lesson_id"], LESSON)

    def test_the_copy_keeps_the_original_obligations(self):
        response = self.client.put(
            f"{BASE}/lessons/{LESSON}", headers=self.auth(self.admin),
            json={"expected_revision": 0, "required": True, "due_at": 1_900_000_000.0})
        self.assertEqual(response.status_code, 200, response.text)
        copied = self.customized()
        listed = self.console_lessons()[copied["id"]]
        self.assertTrue(listed["required"])
        self.assertEqual(listed["due_at"], 1_900_000_000.0)

    def test_a_lesson_with_an_ai_exercise_cannot_be_customized(self):
        response = self.customize(lesson_with_ai_exercise())
        self.assertEqual(response.status_code, 409, response.text)
        self.assertEqual(response.json()["detail"], "LEARNING_LESSON_NOT_CUSTOMIZABLE")

    def test_only_a_delivered_lesson_can_be_customized(self):
        created = self.created()
        for lesson_id in (created["id"], "no-such-lesson"):
            with self.subTest(lesson_id=lesson_id):
                response = self.customize(lesson_id)
                self.assertEqual(response.status_code, 404, response.text)

    def test_customizing_is_audited(self):
        copied = self.customized()
        with main.db() as conn:
            row = conn.execute(
                "SELECT target_id, metadata FROM admin_audit_log "
                "WHERE action='learning_custom_lesson.customize'").fetchone()
        self.assertEqual(row["target_id"], copied["id"])
        self.assertEqual(json.loads(row["metadata"])["based_on_lesson_id"], LESSON)

    def test_read_only_cannot_customize_and_a_content_editor_can(self):
        reader = grant_admin_session("lecture@example.com", "read_only")
        self.assertEqual(self.customize(headers=reader).status_code, 403)
        editor = grant_admin_session("redaction@example.com", "content_editor")
        self.assertEqual(self.customize(headers=editor).status_code, 201)

    def test_a_lesson_written_from_scratch_has_no_origin(self):
        created = self.created()
        self.assertIsNone(created["based_on"])
        self.assertIsNone(self.console_lessons()[created["id"]]["based_on_lesson_id"])

    def test_the_origin_cannot_be_forged_from_the_editor(self):
        created = self.created()
        response = self.client.put(
            f"{CUSTOM}/{created['id']}", headers=self.auth(self.admin),
            json={"expected_revision": 1, "based_on_lesson_id": LESSON})
        self.assertEqual(response.status_code, 400, response.text)
        forged = self.client.post(CUSTOM, headers=self.auth(self.admin),
                                  json={**lesson_body(), "based_on_version": 1})
        self.assertEqual(forged.status_code, 400, forged.text)


class NovaUpdateTests(CustomizationTestCase):
    def edited_copy(self) -> dict:
        copied = self.customized()
        translations = copy.deepcopy(copied["translations"])
        translations["en"]["title"] = "Our version of the lesson"
        response = self.client.put(
            f"{CUSTOM}/{copied['id']}", headers=self.auth(self.admin),
            json={"expected_revision": copied["revision"], "translations": translations})
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()

    def test_a_new_nova_version_is_proposed_never_imposed(self):
        ours = self.edited_copy()
        original_body = sorted(ours["translations"]["en"]["blocks"],
                               key=lambda block: block["order"])[0]["content"]["body"]
        publish_new_nova_version(LESSON)

        read = self.client.get(f"{CUSTOM}/{ours['id']}", headers=self.auth(self.admin)).json()
        self.assertTrue(read["based_on"]["update_available"])
        self.assertEqual(read["based_on"]["latest_version"], read["based_on"]["version"] + 1)
        self.assertTrue(self.console_lessons()[ours["id"]]["update_available"])

        lesson = member_lessons(self.catalog_for(MEMBER))[ours["id"]]
        self.assertEqual(lesson["title"], "Our version of the lesson")
        first = sorted(lesson["blocks"], key=lambda block: block["order"])[0]
        self.assertEqual(first["content"]["body"], original_body)

    def test_the_proposed_version_can_be_previewed(self):
        ours = self.edited_copy()
        publish_new_nova_version(LESSON)
        response = self.client.get(f"{CUSTOM}/{ours['id']}/nova-update",
                                   headers=self.auth(self.admin))
        self.assertEqual(response.status_code, 200, response.text)
        proposal = response.json()
        self.assertEqual(proposal["version"], delivered(LESSON)["version"])
        self.assertEqual(proposal["locale"], "en")
        first = sorted(proposal["blocks"], key=lambda block: block["order"])[0]
        self.assertEqual(first["content"]["body"], NEW_BODY)

    def test_applying_takes_novas_content(self):
        ours = self.edited_copy()
        publish_new_nova_version(LESSON)
        response = self.decide(ours["id"], ours["revision"], "apply")
        self.assertEqual(response.status_code, 200, response.text)
        applied = response.json()
        self.assertEqual(applied["revision"], ours["revision"] + 1)
        self.assertEqual(applied["translations"]["en"]["title"], delivered(LESSON)["title"])
        first = sorted(applied["translations"]["en"]["blocks"],
                       key=lambda block: block["order"])[0]
        self.assertEqual(first["content"]["body"], NEW_BODY)
        self.assertEqual(applied["based_on"]["version"], delivered(LESSON)["version"])
        self.assertFalse(applied["based_on"]["update_available"])
        with main.db() as conn:
            row = conn.execute(
                "SELECT metadata FROM admin_audit_log "
                "WHERE action='learning_custom_lesson.update_apply'").fetchone()
        self.assertIsNotNone(row)
        metadata = json.loads(row["metadata"])
        self.assertEqual(metadata["before"]["titles"]["en"], "Our version of the lesson")

    def test_keeping_our_version_dismisses_the_proposal(self):
        ours = self.edited_copy()
        publish_new_nova_version(LESSON)
        response = self.decide(ours["id"], ours["revision"], "dismiss")
        self.assertEqual(response.status_code, 200, response.text)
        kept = response.json()
        self.assertEqual(kept["translations"], ours["translations"])
        self.assertEqual(kept["version"], ours["version"])
        self.assertEqual(kept["based_on"]["version"], delivered(LESSON)["version"])
        self.assertFalse(kept["based_on"]["update_available"])
        with main.db() as conn:
            count = conn.execute(
                "SELECT COUNT(*) AS n FROM admin_audit_log "
                "WHERE action='learning_custom_lesson.update_dismiss'").fetchone()["n"]
        self.assertEqual(count, 1)

    def test_without_a_new_version_there_is_nothing_to_decide(self):
        copied = self.customized()
        response = self.decide(copied["id"], copied["revision"], "apply")
        self.assertEqual(response.status_code, 409, response.text)
        self.assertEqual(response.json()["detail"], "LEARNING_UPDATE_NOT_AVAILABLE")
        preview = self.client.get(f"{CUSTOM}/{copied['id']}/nova-update",
                                  headers=self.auth(self.admin))
        self.assertEqual(preview.status_code, 409, preview.text)

    def test_a_lesson_written_from_scratch_has_no_update(self):
        created = self.created()
        response = self.decide(created["id"], created["revision"], "apply")
        self.assertEqual(response.status_code, 409, response.text)
        self.assertEqual(response.json()["detail"], "LEARNING_UPDATE_NOT_AVAILABLE")

    def test_a_stale_revision_or_an_unknown_decision_is_refused(self):
        ours = self.edited_copy()
        publish_new_nova_version(LESSON)
        stale = self.decide(ours["id"], ours["revision"] - 1, "apply")
        self.assertEqual(stale.status_code, 409, stale.text)
        self.assertEqual(stale.json()["detail"], "LEARNING_REVISION_CONFLICT")
        unknown = self.decide(ours["id"], ours["revision"], "merge")
        self.assertEqual(unknown.status_code, 400, unknown.text)

    def test_a_decision_moves_the_learning_marker(self):
        ours = self.edited_copy()
        publish_new_nova_version(LESSON)
        member = self.auth(self.sign_in(MEMBER))
        before = self.client.get("/api/organization/changes", headers=member).json()
        self.assertEqual(self.decide(ours["id"], ours["revision"], "apply").status_code, 200)
        after = self.client.get("/api/organization/changes", headers=member).json()
        self.assertNotEqual(after["learning_version"], before["learning_version"])


if __name__ == "__main__":
    unittest.main()
