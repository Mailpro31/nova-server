"""Le mode reunion se ferme comme les autres categories du Core.

Le poste sait deja retirer le mode reunion quand `/api/me` le nomme dans
`closed_capabilities` : la capacite y est declaree fermable depuis la PR
desktop #183. Le serveur, lui, ne la listait pas dans `closable_core` et
n'offrait aucun interrupteur. Le levier promis n'existait donc pas : une
organisation ne pouvait pas refuser la capture des autres participants d'une
reunion, alors que c'est precisement la fonction qu'un service juridique ou un
comite d'entreprise voudra encadrer.

Comme les autres categories, la cle ne reprend pas le mot de la fonction telle
qu'un utilisateur la voit : un document de policy ne doit jamais ressembler a
du contenu de personne.
"""

import unittest

from test_main import AdminTestCase  # noqa: F401

import main

main.init_db()

KEY = "meeting_enabled"


class MeetingPolicySchemaTests(unittest.TestCase):
    def test_the_key_governs_the_capability_and_is_closed_by_default(self):
        # Fonction optionnelle : fermée tant que l'organisation ne l'ouvre pas
        # (lot 3, 20/10 ; le mode réunion est suspendu depuis le 01/10).
        setting = main.POLICY_SETTINGS[KEY]
        self.assertIs(setting.kind, bool)
        self.assertFalse(setting.default)
        self.assertEqual(setting.capability, "meeting")

    def test_the_default_document_carries_it_closed(self):
        self.assertFalse(main.DEFAULT_ORGANIZATION_POLICY[KEY])

    def test_it_is_validated_like_the_others(self):
        self.assertEqual(main.validate_policy_settings({KEY: True}), {KEY: True})
        # Un type errone est refuse, pas ignore : une valeur que le serveur ne
        # comprend pas ne doit ni fermer la fonction, ni passer en silence.
        with self.assertRaises(ValueError):
            main.validate_policy_settings({KEY: "non"})


class MeetingIsAnnouncedClosed(AdminTestCase):
    PATH = "/api/control/organization/policy"

    def setUp(self):
        super().setUp()
        with main.db() as conn:
            conn.execute("DELETE FROM organization_policies")

    def close(self, **settings):
        token = self.admin_session()
        revision = self.client.get(self.PATH, headers=self.auth(token)).json()[
            "revision"
        ]
        response = self.client.put(
            self.PATH,
            headers=self.auth(token),
            json={"expected_revision": revision, "settings": settings},
        )
        self.assertEqual(response.status_code, 200, response.text)

    def announced(self):
        token = self.sign_in("salarie@example.com")
        return self.client.get("/api/me", headers=self.auth(token)).json()

    def test_closing_it_names_the_capability_to_the_desktop(self):
        self.close(meeting_enabled=False)
        self.assertIn("meeting", self.announced()["closed_capabilities"])

    def test_leaving_it_open_names_nothing(self):
        self.assertNotIn("meeting", self.announced()["closed_capabilities"])

    def test_closing_it_touches_no_other_category(self):
        self.close(meeting_enabled=False)
        closed = self.announced()["closed_capabilities"]
        for other in ("learning", "styles", "prompts", "history"):
            self.assertNotIn(other, closed)


if __name__ == "__main__":
    unittest.main()
