"""Learn x Business, Learn x Campus, Learn x Personal — l'integration.

## Ce que ce fichier prouve

`test_learning.py` prouve que Learn fonctionne. `test_business.py` prouve
qu'une organisation `business` traverse le lifecycle Organization sans rien
perdre. Aucun des deux ne prouve qu'ils tiennent **ensemble**, et c'est la
seule chose que ce fichier verifie :

    la nature de l'organisation n'entre nulle part dans la decision Learn.

Learn appartient au Nova Core. La capacite `learning` decide, et la policy
`learning_enabled` la ferme — pour une entreprise exactement comme pour un
etablissement. Un test qui rendrait Learn conditionnel a `org_type` decrirait
un « Business Learn » qui n'existe pas : il n'y en a aucun ici, et plusieurs
tests ci-dessous existent precisement pour rendre son apparition impossible.

## Fixtures

Aucune organisation reelle, aucun domaine reel, aucun tenant reel.
`example.com` est reserve par la RFC 2606 pour cet usage.
"""

import json
import unittest
import uuid
from unittest.mock import patch

from test_main import AdminTestCase  # noqa: F401
from test_business import make_organization, set_organization_type

import learning
import main

main.init_db()


LESSON = "ask-better-questions"


def clear_learning_progress() -> None:
    with main.db() as conn:
        conn.execute("DELETE FROM learning_progress")


def clear_policies() -> None:
    with main.db() as conn:
        conn.execute("DELETE FROM organization_policies")


def close_learning_for(organization_id: str) -> None:
    """Ferme Learn par la policy, ecrite en base.

    La policy est ecrite la ou le serveur la relit. Remplacer une fonction
    voisine rendrait le test vert sans passer par le chemin qui decide.
    """
    with main.db() as conn:
        conn.execute(
            "INSERT INTO organization_policies(organization_id, schema_version, "
            "revision, settings, updated_at, updated_by_user_id) VALUES (?,?,?,?,?,?) "
            "ON CONFLICT(organization_id) DO UPDATE SET settings=excluded.settings, "
            "revision=excluded.revision",
            (organization_id, main.POLICY_SCHEMA_VERSION, 1,
             json.dumps({"learning_enabled": False}), 0.0, None),
        )


def member_of(organization_id: str, email: str) -> dict:
    """Session Nova ordinaire d'un membre d'une organisation donnee."""
    session = main.issue_token(email, "poste-membre", organization_id)
    return {"Authorization": f"Bearer {session['token']}"}


LEARN_ROUTES = (
    ("get", "/api/learning/catalog", None),
    ("get", "/api/learning/progress", None),
    ("put", f"/api/learning/progress/{LESSON}", {"completed_blocks": []}),
    ("post", "/api/learning/exercises/adapt-domain-request/feedback",
     {"text": "Une demande."}),
)


class LearnMixin:
    def call(self, method: str, path: str, payload, headers: dict):
        request = getattr(self.client, method)
        if payload is None:
            return request(path, headers=headers)
        return request(path, headers=headers, json=payload)

    def blocks_of(self, lesson_id: str = LESSON) -> list:
        for path in learning.load_catalog()["paths"]:
            for module in path["modules"]:
                for lesson in module["lessons"]:
                    if lesson["id"] == lesson_id:
                        return [block["id"] for block in lesson["blocks"]]
        raise AssertionError(f"lecon absente du catalogue : {lesson_id}")


# -----------------------------------------------------------------------------
# 1. Learn dans une entreprise
# -----------------------------------------------------------------------------


class LearnInABusinessTests(LearnMixin, AdminTestCase):
    """Un salarie ouvre Learn, et sa progression avance."""

    def setUp(self):
        super().setUp()
        clear_learning_progress()
        clear_policies()
        self.addCleanup(clear_policies)
        self.addCleanup(clear_learning_progress)
        self.organization = main.nova_organization_id()
        set_organization_type(self.organization, "business")
        self.addCleanup(set_organization_type, self.organization, "education")
        self.headers = {
            "Authorization": f"Bearer {self.sign_in('salarie@example.com')}"
        }

    # -- ce que le poste apprend -------------------------------------------

    def test_the_member_is_in_an_organization_of_type_business(self):
        body = self.client.get("/api/me", headers=self.headers).json()
        self.assertEqual(body["organization_type"], "business")
        # Le membre reste un membre d'organisation : c'est ce qui l'empeche de
        # retomber dans l'experience Personal cote poste.
        self.assertEqual(body["organization_id"], self.organization)

    def test_learn_is_announced_to_a_business_member(self):
        """La capacite est ouverte, donc l'entree Learn s'affiche."""
        config = self.client.get("/api/config").json()
        self.assertTrue(config["capabilities"]["learning"])
        me = self.client.get("/api/me", headers=self.headers).json()
        self.assertIn("learning", me["capabilities"])

    def test_the_business_member_reaches_the_catalogue(self):
        response = self.client.get("/api/learning/catalog", headers=self.headers)
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(len(response.json()["paths"]), 4)

    def test_the_catalogue_does_not_change_with_the_nature(self):
        """Il n'existe pas de catalogue d'entreprise.

        Un catalogue qui differerait selon `org_type` serait un « Business
        Learn » — exactement ce que l'architecture refuse.
        """
        business = self.client.get(
            "/api/learning/catalog", headers=self.headers).json()
        set_organization_type(self.organization, "education")
        education = self.client.get(
            "/api/learning/catalog", headers=self.headers).json()
        self.assertEqual(business, education)

    # -- la progression -----------------------------------------------------

    def test_the_progression_advances_for_a_business_member(self):
        blocks = self.blocks_of()
        first = self.client.put(
            f"/api/learning/progress/{LESSON}",
            headers=self.headers,
            json={"completed_blocks": blocks[:1], "last_block_id": blocks[0]},
        )
        self.assertEqual(first.status_code, 200, first.text)
        self.assertEqual(first.json()["status"], "in_progress")

        snapshot = self.client.get(
            "/api/learning/progress", headers=self.headers).json()
        self.assertEqual(
            [lesson["lesson_id"] for lesson in snapshot["lessons"]], [LESSON]
        )

    def test_a_business_member_earns_completion_like_anyone_else(self):
        """L'etat vient du serveur, et la nature n'y change rien."""
        blocks = self.blocks_of()
        response = self.client.put(
            f"/api/learning/progress/{LESSON}",
            headers=self.headers,
            json={"completed_blocks": blocks, "last_block_id": blocks[-1]},
        )
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["status"], "completed")

    # -- la policy ferme Learn ---------------------------------------------

    def test_a_business_policy_closes_learn(self):
        """`learning_enabled=false` ferme Learn dans une entreprise aussi."""
        close_learning_for(self.organization)
        for method, path, payload in LEARN_ROUTES:
            with self.subTest(path=path):
                response = self.call(method, path, payload, self.headers)
                self.assertEqual(response.status_code, 403, response.text)

    def test_a_closed_policy_is_announced_before_being_applied(self):
        """Le poste masque l'entree plutot que de laisser quelqu'un buter dessus."""
        close_learning_for(self.organization)
        config = self.client.get("/api/config").json()
        self.assertFalse(config["capabilities"]["learning"])
        me = self.client.get("/api/me", headers=self.headers).json()
        self.assertNotIn("learning", me["capabilities"])

    def test_closing_learn_leaves_the_rest_of_the_business_untouched(self):
        """Fermer Learn ne ferme pas le Core, et ne change pas la nature."""
        close_learning_for(self.organization)
        body = self.client.get("/api/me", headers=self.headers).json()
        self.assertEqual(body["organization_type"], "business")
        for capability in ("dictation", "rewrite", "styles"):
            with self.subTest(capability=capability):
                self.assertIn(capability, body["capabilities"])


# -----------------------------------------------------------------------------
# 2. Learn dans un etablissement — Campus non regresse
# -----------------------------------------------------------------------------


class LearnInACampusTests(LearnMixin, AdminTestCase):
    def setUp(self):
        super().setUp()
        clear_learning_progress()
        clear_policies()
        self.addCleanup(clear_policies)
        self.addCleanup(clear_learning_progress)
        self.organization = main.nova_organization_id()
        set_organization_type(self.organization, "education")
        self.headers = {
            "Authorization": f"Bearer {self.sign_in('etudiant@example.com')}"
        }

    def test_the_campus_contract_is_untouched(self):
        body = self.client.get("/api/config").json()
        self.assertEqual(body["organization_type"], "education")
        # Les champs Campus historiques restent servis, aux memes types.
        self.assertIn(body["education_mode"], {"normal", "classroom", "assessment"})
        self.assertIsInstance(body["ai_skills"]["enabled"], bool)
        self.assertTrue(body["organization"]["managed"])

    def test_learn_reaches_a_student(self):
        response = self.client.get("/api/learning/catalog", headers=self.headers)
        self.assertEqual(response.status_code, 200, response.text)

    def test_a_campus_policy_closes_learn(self):
        close_learning_for(self.organization)
        for method, path, payload in LEARN_ROUTES:
            with self.subTest(path=path):
                self.assertEqual(
                    self.call(method, path, payload, self.headers).status_code, 403
                )


# -----------------------------------------------------------------------------
# 3. Learn reste du Core — aucune edition n'y entre
# -----------------------------------------------------------------------------


class LearnStaysCoreTests(unittest.TestCase):
    """Le contrat Personal, vu du serveur.

    Un poste Personal n'a pas d'organisation : il n'y a donc rien a servir de
    different, et c'est le point. Ce que le serveur doit garantir, c'est que
    la decision Learn ne cite aucune edition ni aucune nature — sans quoi
    Personal la perdrait le jour ou une condition serait ajoutee ailleurs.
    """

    def test_the_capability_is_declared_for_every_edition(self):
        self.assertIn("learning", main.public_campus_config()["capabilities"])

    def test_the_capability_is_closed_until_the_organization_opens_it(self):
        """Learn est une fonction optionnelle : fermée par défaut (lot 3, 20/10)."""
        self.assertFalse(main.POLICY_SETTINGS["learning_enabled"].default)

    def test_no_learn_route_asks_which_nature_serves_it(self):
        """Aucune route Learn ne lit `org_type`, ni directement ni par un voisin."""
        import inspect

        for name in ("learning_catalog", "learning_progress",
                     "update_learning_progress", "learning_exercise_feedback"):
            with self.subTest(route=name):
                source = inspect.getsource(getattr(main, name))
                for forbidden in ("org_type", "organization_type",
                                  "ORGANIZATION_TYPE", "business", "education"):
                    self.assertNotIn(forbidden, source)

    def test_the_policy_key_governs_the_capability_not_a_nature(self):
        """`learning_enabled` gouverne Learn, pas une nature d'organisation."""
        self.assertEqual(
            main.POLICY_SETTINGS["learning_enabled"].capability, "learning"
        )


# -----------------------------------------------------------------------------
# 4. L'exercice IA dans une entreprise
# -----------------------------------------------------------------------------


class BusinessExerciseRuntimeTests(LearnMixin, AdminTestCase):
    """Le retour pedagogique passe par le moteur de l'organisation.

    Il n'existe pas d'AI Router Business : c'est le meme routeur, avec la meme
    politique. Ce qui compte ici est qu'aucun repli cloud silencieux
    n'apparaisse parce que l'organisation est une entreprise.
    """

    def setUp(self):
        super().setUp()
        clear_learning_progress()
        clear_policies()
        self.addCleanup(clear_policies)
        self.addCleanup(clear_learning_progress)
        self.organization = main.nova_organization_id()
        set_organization_type(self.organization, "business")
        self.addCleanup(set_organization_type, self.organization, "education")
        self.headers = {
            "Authorization": f"Bearer {self.sign_in('salarie@example.com')}"
        }

    def test_the_exercise_goes_through_the_organization_router(self):
        expected = learning.exercise("adapt-domain-request")["instruction"]
        with patch.object(main, "execute_text") as engine:
            engine.return_value = "retour"
            response = self.client.post(
                "/api/learning/exercises/adapt-domain-request/feedback",
                headers=self.headers,
                json={"text": "Ma demande."},
            )
        self.assertEqual(response.status_code, 200, response.text)
        task_type, instruction, text = engine.call_args.args
        self.assertEqual(task_type.value, "learning_feedback")
        # L'instruction vient du catalogue du serveur, pas du poste — la nature
        # de l'organisation n'ouvre aucun chemin par lequel elle arriverait.
        self.assertEqual(instruction, expected)
        self.assertEqual(text, "Ma demande.")

    def test_no_silent_cloud_fallback_for_a_business_organization(self):
        self.assertEqual(main.ORGANIZATION_AI_ROUTER.fallback_policy, "disabled")

    def test_the_free_text_of_an_exercise_is_never_persisted(self):
        """La garantie Learn tient dans une entreprise aussi."""
        secret = f"texte-libre-{uuid.uuid4().hex}"
        with patch.object(main, "execute_text") as engine:
            engine.return_value = "retour"
            self.client.post(
                "/api/learning/exercises/adapt-domain-request/feedback",
                headers=self.headers,
                json={"text": secret},
            )
        with main.db() as conn:
            rows = conn.execute("SELECT * FROM learning_progress").fetchall()
        for row in rows:
            self.assertNotIn(secret, json.dumps(dict(row), default=str))


# -----------------------------------------------------------------------------
# 5. Cloisonnement de la progression entre tenants
# -----------------------------------------------------------------------------


class LearnTenantIsolationTests(LearnMixin, AdminTestCase):
    """La meme adresse dans deux organisations, deux progressions distinctes.

    C'est la raison pour laquelle `organization_id` est dans la cle d'unicite
    de `learning_progress` et pas seulement dans la ligne. Le cas se produit
    des qu'un prestataire travaille pour deux clients.
    """

    EMAIL = "meme.personne@example.com"

    def setUp(self):
        super().setUp()
        clear_learning_progress()
        clear_policies()
        self.addCleanup(clear_policies)
        self.addCleanup(clear_learning_progress)
        self.business_a = make_organization(
            "business", f"la-{uuid.uuid4().hex[:6]}", "Acme")
        self.business_b = make_organization(
            "business", f"lb-{uuid.uuid4().hex[:6]}", "Globex")
        self.education = make_organization(
            "education", f"le-{uuid.uuid4().hex[:6]}", "Ecole")
        self.addCleanup(self.drop_organizations)
        self.member_a = member_of(self.business_a, self.EMAIL)
        self.member_b = member_of(self.business_b, self.EMAIL)
        self.member_edu = member_of(self.education, self.EMAIL)

    def drop_organizations(self):
        with main.db() as conn:
            conn.execute(
                "DELETE FROM organizations WHERE id IN (?,?,?)",
                (self.business_a, self.business_b, self.education),
            )

    def advance(self, headers: dict) -> None:
        blocks = self.blocks_of()
        response = self.client.put(
            f"/api/learning/progress/{LESSON}",
            headers=headers,
            json={"completed_blocks": blocks[:1], "last_block_id": blocks[0]},
        )
        self.assertEqual(response.status_code, 200, response.text)

    def lessons(self, headers: dict) -> list:
        response = self.client.get("/api/learning/progress", headers=headers)
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()["lessons"]

    def test_business_a_progress_does_not_reach_business_b(self):
        self.advance(self.member_a)
        self.assertEqual(len(self.lessons(self.member_a)), 1)
        self.assertEqual(self.lessons(self.member_b), [])

    def test_business_progress_does_not_reach_an_education_tenant(self):
        self.advance(self.member_a)
        self.assertEqual(self.lessons(self.member_edu), [])

    def test_education_progress_does_not_reach_a_business_tenant(self):
        self.advance(self.member_edu)
        self.assertEqual(self.lessons(self.member_a), [])

    def test_two_tenants_hold_two_rows_for_the_same_address(self):
        self.advance(self.member_a)
        self.advance(self.member_b)
        with main.db() as conn:
            rows = conn.execute(
                "SELECT organization_id FROM learning_progress WHERE email=?",
                (self.EMAIL,),
            ).fetchall()
        self.assertEqual(
            sorted(row["organization_id"] for row in rows),
            sorted((self.business_a, self.business_b)),
        )

    def test_closing_learn_in_one_tenant_leaves_the_other_open(self):
        """La policy est celle de l'organisation du membre, pas de l'instance."""
        close_learning_for(self.business_a)
        self.assertEqual(
            self.client.get(
                "/api/learning/catalog", headers=self.member_a).status_code, 403
        )
        self.assertEqual(
            self.client.get(
                "/api/learning/catalog", headers=self.member_b).status_code, 200
        )


if __name__ == "__main__":
    unittest.main()
