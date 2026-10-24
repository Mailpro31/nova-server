"""Les fonctions optionnelles sont fermées tant qu'une organisation ne les ouvre pas.

Lot 3 du plan de corrections (décision du 20/10) : AI Skills, Commandes Nova,
Notes structurées, Learn, Prompts et Réunion encombraient l'application d'un
élève dys. Une école les ouvre une à une dans la console.
"""

import unittest

from test_main import OPTIONAL_FEATURE_KEYS

import main

main.init_db()

CAPABILITIES = ("aiSkills", "commands", "engineeringNotes", "learning", "prompts", "meeting")


class OptionalFeaturesTests(unittest.TestCase):
    def test_every_optional_policy_is_closed_by_default(self):
        for key in OPTIONAL_FEATURE_KEYS:
            with self.subTest(key=key):
                self.assertFalse(main.POLICY_SETTINGS[key].default)

    def test_an_organization_that_set_nothing_announces_them_closed(self):
        config = main.public_campus_config()
        for capability in CAPABILITIES:
            with self.subTest(capability=capability):
                # Une capacité absente du document est fermée pour le poste.
                self.assertFalse(config["capabilities"].get(capability, False))
        self.assertFalse(config["ai_skills"]["enabled"])

    def test_dictation_styles_and_history_stay_open(self):
        capabilities = main.public_campus_config()["capabilities"]
        for capability in ("dictation", "styles", "history", "dictionary"):
            with self.subTest(capability=capability):
                self.assertTrue(capabilities[capability])


if __name__ == "__main__":
    unittest.main()
