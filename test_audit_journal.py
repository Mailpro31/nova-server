"""Le journal d'audit se lit.

Chaque changement fait dans la console laisse une ligne. Encore faut-il qu'un
administrateur puisse la lire : un nom d'action interne
(`learning_custom_lesson.update`) ou un identifiant opaque en guise de cible
ne dit pas ce qui s'est passe.

- chaque action ecrite par la console porte un libelle lisible ;
- une ligne qui vise une lecon nomme la lecon.
"""

import re
import unittest
from pathlib import Path

from test_main import AdminTestCase  # noqa: F401

import learning
import main

main.init_db()

# Les domaines dont les actions sont ecrites par la console ou par le serveur
# pour elle. Un nom d'action a la forme `domaine.verbe`.
AUDITED_DOMAINS = (
    "admin_role", "admin_session", "admin_step_up", "ai_runtime", "cohort",
    "connector", "discovery", "group", "group_policy", "learning_custom_lesson",
    "learning_lesson", "member", "organization_policy", "package",
    "provider_config", "user",
)


def recorded_actions() -> set[str]:
    source = Path(main.__file__).read_text(encoding="utf-8")
    pattern = re.compile(r'"((?:%s)\.[a-z_.]+)"' % "|".join(AUDITED_DOMAINS))
    return set(pattern.findall(source))


class LabelTests(unittest.TestCase):
    def test_the_scan_finds_the_actions_it_should(self):
        actions = recorded_actions()
        for expected in ("organization_policy.update", "package.version_publish",
                         "learning_custom_lesson.delete", "member.delete"):
            self.assertIn(expected, actions)

    def test_every_recorded_action_has_a_readable_label(self):
        missing = sorted(recorded_actions() - set(main.AUDIT_ACTION_LABELS))
        self.assertEqual(missing, [])

    def test_labels_read_as_sentences_not_keys(self):
        for action, label in main.AUDIT_ACTION_LABELS.items():
            with self.subTest(action=action):
                self.assertNotIn("_", label)
                self.assertNotRegex(label, r"\w\.\w")
                self.assertTrue(label[0].isupper())


class TargetTests(AdminTestCase):
    def setUp(self):
        super().setUp()
        learning.reset_cache()
        with main.db() as conn:
            conn.execute("DELETE FROM organization_learning_lesson_settings")
            conn.execute("DELETE FROM organization_learning_custom_lessons")
        self.admin = self.admin_session()

    def audit(self) -> list[dict]:
        organization = main.nova_organization_id()
        response = self.client.get(f"/api/control/organizations/{organization}/audit",
                                   headers=self.auth(self.admin))
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()["entries"]

    def test_a_lesson_row_names_the_lesson(self):
        response = self.client.post(
            "/api/control/organization/learning/lessons/ask-better-questions/archive",
            headers=self.auth(self.admin), json={"expected_revision": 0})
        self.assertEqual(response.status_code, 200, response.text)
        entry = next(e for e in self.audit() if e["action"] == "learning_lesson.archive")
        self.assertEqual(entry["target"], "Ask better questions")
        self.assertEqual(entry["action_label"], "Archived a Learn lesson")

    def test_a_custom_lesson_row_names_the_lesson(self):
        body = {
            "module_id": "use-ai-foundations", "source_locale": "en",
            "estimated_minutes": 4, "difficulty": "beginner",
            "translations": {"en": {
                "title": "Our report rules", "description": "What every report needs.",
                "blocks": [{"id": "intro", "type": "text", "order": 1,
                            "content": {"body": "Summary first."}}]}},
        }
        created = self.client.post("/api/control/organization/learning/custom-lessons",
                                   headers=self.auth(self.admin), json=body)
        self.assertEqual(created.status_code, 201, created.text)
        entry = next(e for e in self.audit() if e["action"] == "learning_custom_lesson.create")
        self.assertEqual(entry["target"], "Our report rules")


if __name__ == "__main__":
    unittest.main()
