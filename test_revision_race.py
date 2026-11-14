"""Le controle `expected_revision` tient meme quand un autre administrateur
ecrit entre la lecture et l'ecriture (PostgreSQL, sans verrou de ligne).

On simule la course : au moment ou la requete va ecrire, une seconde connexion
fait avancer la revision. L'ecriture doit alors etre refusee en 409, jamais
ecraser le travail du collegue.
"""

import contextlib
import unittest

from test_main import AdminTestCase, grant_admin_session

import main
import test_group_policies as groups
import test_learning_organization as learning_org


@contextlib.contextmanager
def concurrent_write(table: str, bump_sql: str):
    real = main.db
    fired = []

    @contextlib.contextmanager
    def racing():
        with real() as conn:
            execute = conn.execute

            def execute_(sql, parameters=()):
                if not fired and sql.lstrip().startswith(f"INSERT INTO {table}"):
                    fired.append(True)
                    with real() as other:
                        other.execute(bump_sql)
                return execute(sql, parameters)

            conn.execute = execute_
            yield conn

    main.db = racing
    try:
        yield fired
    finally:
        main.db = real


def revision_of(table: str, column: str = "revision") -> int:
    with main.db() as conn:
        return conn.execute(f"SELECT {column} FROM {table}").fetchone()[column]


class OrganizationPolicyRace(AdminTestCase):
    def test_a_concurrent_write_is_a_conflict(self):
        with main.db() as conn:
            conn.execute("DELETE FROM organization_policies")
        token = self.admin_session()
        path = "/api/control/organization/policy"
        first = self.client.put(path, headers=self.auth(token), json={
            "expected_revision": 0, "settings": {"ai_skills_enabled": False}})
        self.assertEqual(first.status_code, 200, first.text)
        with concurrent_write("organization_policies",
                              "UPDATE organization_policies SET revision=revision+1"):
            response = self.client.put(path, headers=self.auth(token), json={
                "expected_revision": 1, "settings": {"ai_skills_enabled": True}})
        self.assertEqual(response.status_code, 409, response.text)
        self.assertEqual(response.json()["detail"], "POLICY_REVISION_CONFLICT")
        self.assertEqual(revision_of("organization_policies"), 2)


class GroupPolicyRace(groups.GroupPolicyTestCase):
    def test_a_concurrent_write_is_a_conflict(self):
        group_id = groups.create_group("Race", [])
        self.assertEqual(self.set_group(group_id, {"ai_skills_enabled": False}).status_code, 200)
        with concurrent_write("organization_group_policies",
                              "UPDATE organization_group_policies SET revision=revision+1"):
            response = self.set_group(group_id, {"ai_skills_enabled": True}, revision=1)
        self.assertEqual(response.status_code, 409, response.text)
        self.assertEqual(response.json()["detail"], "GROUP_POLICY_REVISION_CONFLICT")
        self.assertEqual(revision_of("organization_group_policies"), 2)


class AIRuntimeRace(AdminTestCase):
    def test_a_concurrent_write_is_a_conflict(self):
        headers = grant_admin_session("it@example.com", "it_admin")
        path = "/api/control/organization/ai-runtime"
        payload = {"expected_revision": 0, "profile": "efficient",
                   "reasoning_allowed": False, "concurrency_mode": "auto"}
        with main.db() as conn:
            conn.execute("DELETE FROM organization_ai_runtime_config")
        self.assertEqual(self.client.put(path, headers=headers, json=payload).status_code, 200)
        with concurrent_write(
                "organization_ai_runtime_config",
                "UPDATE organization_ai_runtime_config SET desired_revision=desired_revision+1"):
            response = self.client.put(path, headers=headers,
                                       json={**payload, "expected_revision": 1})
        self.assertEqual(response.status_code, 409, response.text)
        self.assertEqual(response.json()["detail"], "AI_RUNTIME_REVISION_CONFLICT")
        self.assertEqual(revision_of("organization_ai_runtime_config", "desired_revision"), 2)


class LearningSettingsRace(learning_org.AdminTestCase):
    def test_a_concurrent_write_is_a_conflict(self):
        learning_org.clear_learning_settings()
        token = self.admin_session()
        path = f"{learning_org.PATH}/lessons/{learning_org.LESSON}"
        first = self.client.put(path, headers=self.auth(token),
                                json={"expected_revision": 0, "required": True})
        self.assertEqual(first.status_code, 200, first.text)
        with concurrent_write(
                "organization_learning_lesson_settings",
                "UPDATE organization_learning_lesson_settings SET revision=revision+1"):
            response = self.client.put(path, headers=self.auth(token),
                                       json={"expected_revision": 1, "required": False})
        self.assertEqual(response.status_code, 409, response.text)
        self.assertEqual(response.json()["detail"], "LEARNING_REVISION_CONFLICT")
        self.assertEqual(revision_of("organization_learning_lesson_settings"), 2)


if __name__ == "__main__":
    unittest.main()
