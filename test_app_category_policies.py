"""Les categories de l'application, ouvertes ou fermees par l'organisation.

Apprendre, les Notes structurees, les AI Skills et les commandes vocales ont
deja leur cle. Il manquait Styles, Prompts et Historique.

## Ce que chaque cle ferme, exactement

- `styles_enabled` ferme la capacite `styles` : la categorie Styles disparait
  du poste, et le serveur cesse de distribuer les Styles de l'organisation. La
  reformulation des dictees continue avec le Style par defaut — la fermer
  couperait le coeur du produit sur une case cochee.
- `prompts_enabled` et `history_enabled` ferment des categories dont le contenu
  reste sur le poste de la personne. Aucune route serveur ne les sert : la
  decision est annoncee au poste, qui retire la categorie. Le serveur ne peut
  rien refuser de plus, et la cle ne pretend pas le contraire.
"""

import unittest
from unittest.mock import patch

from test_main import AdminTestCase  # noqa: F401

import main

main.init_db()

NEW_KEYS = {
    "styles_enabled": "styles",
    "prompts_enabled": "prompts",
    "history_enabled": "history",
}


class CategoryPolicySchemaTests(unittest.TestCase):
    def test_each_category_key_governs_its_capability_and_is_open_by_default(self):
        for key, capability in NEW_KEYS.items():
            with self.subTest(key=key):
                setting = main.POLICY_SETTINGS[key]
                self.assertIs(setting.kind, bool)
                self.assertTrue(setting.default)
                self.assertEqual(setting.capability, capability)

    def test_prompts_and_history_are_announced_open_by_default(self):
        capabilities = main.public_campus_config()["capabilities"]
        self.assertTrue(capabilities["prompts"])
        self.assertTrue(capabilities["history"])

    def test_closing_a_category_closes_its_capability(self):
        for key, capability in NEW_KEYS.items():
            with self.subTest(key=key):
                effective = main.resolve_effective_product_capabilities(
                    {capability: True}, {key: False})
                self.assertFalse(effective[capability])


class CategoryPolicyDeliveryTests(AdminTestCase):
    PATH = "/api/control/organization/policy"

    def setUp(self):
        super().setUp()
        with main.db() as conn:
            conn.execute("DELETE FROM organization_policies")

    def close(self, **settings):
        token = self.admin_session()
        revision = self.client.get(self.PATH, headers=self.auth(token)).json()["revision"]
        response = self.client.put(
            self.PATH, headers=self.auth(token),
            json={"expected_revision": revision, "settings": settings})
        self.assertEqual(response.status_code, 200, response.text)

    def test_a_member_is_told_which_categories_are_closed(self):
        self.close(prompts_enabled=False, history_enabled=False, styles_enabled=False)
        token = self.sign_in("etudiant@example.com")
        announced = self.client.get("/api/me", headers=self.auth(token)).json()["capabilities"]
        for capability in ("prompts", "history", "styles"):
            with self.subTest(capability=capability):
                self.assertNotIn(capability, announced)
        self.assertIn("dictation", announced)

    def test_closing_styles_never_stops_the_reformulation_of_dictations(self):
        self.close(styles_enabled=False)
        token = self.sign_in("etudiant@example.com")

        async def engine(task_type, instruction, text):
            return "Texte reformule."

        with patch.object(main, "execute_text", engine):
            response = self.client.post(
                "/api/reformulate", headers=self.auth(token),
                json={"text": "bonjour", "style_id": "default_improve_transcriptions",
                      "style_prompt": "Corrige le texte."})
        self.assertEqual(response.status_code, 200, response.text)

    def test_the_other_categories_stay_open_when_one_closes(self):
        self.close(history_enabled=False)
        capabilities = main.public_campus_config()["capabilities"]
        self.assertFalse(capabilities["history"])
        self.assertTrue(capabilities["prompts"])
        self.assertTrue(capabilities["styles"])


if __name__ == "__main__":
    unittest.main()
