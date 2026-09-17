"""Styles desactives par l'organisation.

Fermer la categorie Styles retire toute la page. Une organisation veut parfois
garder les Styles et en retirer un seul — « Prompt » pendant un examen, ou un
Style d'organisation devenu obsolete. La policy porte donc la liste des Styles
desactives :

- une liste d'identifiants, vide par defaut : rien n'est desactive tant que
  personne ne l'a decide ;
- annoncee au poste dans `/api/config` et `/api/me`, qui affiche le Style comme
  desactive et ne l'applique plus ;
- une surcharge de groupe remplace la liste de l'organisation, et deux groupes
  d'un membre additionnent leurs listes : la valeur la plus restrictive gagne ;
- le serveur ne l'applique jamais non plus : un poste ancien qui envoie un
  Style desactive recoit la reformulation par defaut, sans erreur.
"""

import json
import unittest
from unittest.mock import patch

from test_group_policies import GroupPolicyTestCase, POLICY, create_group  # noqa: F401
from test_main import AdminTestCase  # noqa: F401

import main

main.init_db()

KEY = "disabled_style_ids"


def clear_policies():
    with main.db() as conn:
        conn.execute("DELETE FROM organization_policies")
        conn.execute("DELETE FROM organization_group_policies")


class DisabledStylesSchemaTests(unittest.TestCase):
    def test_the_list_is_declared_empty_by_default_and_closes_no_capability(self):
        setting = main.POLICY_SETTINGS[KEY]
        self.assertIs(setting.kind, list)
        self.assertEqual(setting.default, [])
        self.assertIsNone(setting.capability)
        self.assertEqual(main.DEFAULT_ORGANIZATION_POLICY[KEY], [])

    def test_identifiers_are_kept_sorted_and_unique(self):
        self.assertEqual(
            main.validate_policy_settings(
                {KEY: ["nova_style_prompt", "nova_style_email", "nova_style_prompt"]}),
            {KEY: ["nova_style_email", "nova_style_prompt"]},
        )

    def test_an_empty_list_stores_no_override(self):
        self.assertEqual(main.validate_policy_settings({KEY: []}), {})

    def test_anything_but_a_short_list_of_identifiers_is_refused(self):
        for value in ("nova_style_email", None, True, 3, [1], [""], ["bad id!"],
                      ["x" * 121], [f"style_{index}" for index in range(101)]):
            with self.subTest(value=value):
                with self.assertRaises(ValueError) as raised:
                    main.validate_policy_settings({KEY: value})
                self.assertIn(f"POLICY_VALUE_INVALID:{KEY}", str(raised.exception))

    def test_a_damaged_stored_value_falls_back_to_the_default(self):
        self.assertEqual(main.read_policy_settings(json.dumps({KEY: "oops"})), {})


class DisabledStylesDeliveryTests(AdminTestCase):
    def setUp(self):
        super().setUp()
        clear_policies()

    def disable(self, *style_ids: str):
        token = self.admin_session()
        revision = self.client.get(POLICY, headers=self.auth(token)).json()["revision"]
        response = self.client.put(
            POLICY, headers=self.auth(token),
            json={"expected_revision": revision, "settings": {KEY: list(style_ids)}})
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()

    def test_nothing_is_disabled_by_default(self):
        self.assertEqual(main.public_campus_config()["style_policy"],
                         {"disabled_style_ids": []})
        token = self.sign_in("etudiant@example.com")
        body = self.client.get("/api/me", headers=self.auth(token)).json()
        self.assertEqual(body["style_policy"], {"disabled_style_ids": []})

    def test_the_console_reads_back_what_it_saved(self):
        view = self.disable("nova_style_prompt")
        self.assertEqual(view["settings"][KEY], ["nova_style_prompt"])

    def test_a_signed_in_member_learns_the_disabled_styles(self):
        self.disable("nova_style_prompt", "nova_style_todo")
        token = self.sign_in("etudiant@example.com")
        body = self.client.get("/api/me", headers=self.auth(token)).json()
        self.assertEqual(body["style_policy"],
                         {"disabled_style_ids": ["nova_style_prompt", "nova_style_todo"]})

    def test_disabling_a_style_moves_the_member_policy_marker(self):
        token = self.sign_in("etudiant@example.com")
        before = self.client.get("/api/organization/changes", headers=self.auth(token)).json()
        self.disable("nova_style_prompt")
        after = self.client.get("/api/organization/changes", headers=self.auth(token)).json()
        self.assertNotEqual(after["policy_revision"], before["policy_revision"])

    def reformulate(self, body: dict):
        token = self.sign_in("etudiant@example.com")
        seen = {}

        async def engine(task_type, instruction, text):
            seen["task_type"] = task_type
            seen["instruction"] = instruction
            return "Texte reformule."

        with patch.object(main, "execute_text", engine):
            response = self.client.post("/api/reformulate", headers=self.auth(token),
                                        json={"text": "bonjour", **body})
        return response, seen

    def test_a_disabled_style_sent_by_an_older_workstation_gets_the_default(self):
        self.disable("nova_style_prompt")
        response, seen = self.reformulate(
            {"style_id": "nova_style_prompt", "style_prompt": "Ecris un prompt."})
        self.assertEqual(response.status_code, 200, response.text)
        self.assertNotIn("Ecris un prompt.", seen["instruction"])
        self.assertEqual(seen["task_type"], main.TaskType.REFORMULATION)

    def test_a_disabled_organization_style_is_never_resolved(self):
        style_id = f"{main.ORGANIZATION_STYLE_PREFIX}retired"
        self.disable(style_id)
        response, seen = self.reformulate({"style_id": style_id})
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(seen["task_type"], main.TaskType.REFORMULATION)

    def test_a_style_that_stays_enabled_is_applied_as_before(self):
        self.disable("nova_style_prompt")
        response, seen = self.reformulate(
            {"style_id": "nova_style_email", "style_prompt": "Ecris un e-mail."})
        self.assertEqual(response.status_code, 200, response.text)
        self.assertIn("Ecris un e-mail.", seen["instruction"])


class DisabledStylesGroupTests(GroupPolicyTestCase):
    def styles_of(self, headers: dict) -> list[str]:
        body = self.client.get("/api/me", headers=headers).json()
        return body["style_policy"]["disabled_style_ids"]

    def test_a_group_list_replaces_the_organization_list(self):
        inside, outside = self.member("dedans@example.com"), self.member("dehors@example.com")
        group_id = create_group("Personnel", ["dedans@example.com"])
        self.set_organization({KEY: ["nova_style_prompt"]})
        self.assertEqual(self.set_group(group_id, {KEY: []}).status_code, 200)
        self.assertEqual(self.styles_of(inside), [])
        self.assertEqual(self.styles_of(outside), ["nova_style_prompt"])

    def test_two_groups_add_up_their_disabled_styles(self):
        inside = self.member("dedans@example.com")
        exam = create_group("Examen", ["dedans@example.com"])
        lab = create_group("Laboratoire", ["dedans@example.com"])
        self.set_group(exam, {KEY: ["nova_style_prompt"]})
        self.set_group(lab, {KEY: ["nova_style_todo", "nova_style_prompt"]})
        self.assertEqual(self.styles_of(inside), ["nova_style_prompt", "nova_style_todo"])

    def test_a_group_list_is_validated_like_the_organization_list(self):
        group_id = create_group("Examen", [])
        response = self.set_group(group_id, {KEY: ["bad id!"]})
        self.assertEqual(response.status_code, 400, response.text)


if __name__ == "__main__":
    unittest.main()
