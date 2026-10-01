"""Les reprises de l'élève d'abord, le Style ensuite.

Vu par Sash le 01/10 en testant la 1.0.47 : « acheter des carottes en premier,
attends, mais en premier des betteraves… » est sorti avec les deux versions,
contradictoires. C'est, dit-il, le plus important de la reformulation.

Mesuré le 01/10 sur le modèle local de Nova (Qwen3-4B), 20 dictées dont 6
jamais vues par les exemples :
- consigne du Style seule : 3 à 7 reprises justes sur 14 ;
- exemples écrits dans la consigne : 13 à 14 sur 20 ;
- un premier passage qui ne fait que résoudre les reprises, avec des
  exemples donnés en échanges, puis le Style : 19 sur 20 pour « Au
  quotidien » comme pour « Transcription améliorée ».
"""

import unittest
from unittest.mock import patch

import httpx

from test_main import AdminTestCase  # noqa: F401

import main
from ai_runtime import (
    OpenAICompatibleTextBackend,
    ReasoningMode,
    TaskType,
    TextInferenceOptions,
    TextInferenceRequest,
)

main.init_db()


class ExamplesAreSentAsExchangesTests(unittest.IsolatedAsyncioTestCase):
    async def test_examples_come_between_the_instruction_and_the_input(self):
        seen = {}

        def handler(request: httpx.Request):
            import json

            seen["messages"] = json.loads(request.content)["messages"]
            return httpx.Response(200, json={
                "choices": [{"message": {"content": "ok"}, "finish_reason": "stop"}],
                "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
            })

        backend = OpenAICompatibleTextBackend(
            "http://127.0.0.1:8001/v1", "model", transport=httpx.MockTransport(handler))
        await backend.generate(TextInferenceRequest(
            "r", TaskType.REFORMULATION, "dictée", "consigne",
            TextInferenceOptions(64, 0.0, ReasoningMode.OFF), 10,
            examples=(("exemple dicté", "exemple rendu"),),
        ))
        self.assertEqual(
            [(m["role"], m["content"]) for m in seen["messages"]],
            [("system", "consigne"), ("user", "exemple dicté"),
             ("assistant", "exemple rendu"), ("user", "dictée")],
        )


class SelfCorrectionHintTests(unittest.TestCase):
    def test_a_correction_is_spotted(self):
        for dictated in (
            "on se voit mardi non attends mercredi",
            "envoie-le à Marc enfin non à Julie",
            "le 12 pardon le 15",
            "trois cahiers plutôt quatre",
            "achète du lait oublie le lait",
            "j'ai eu 14 je veux dire 16",
            "meet on Monday I mean Tuesday",
        ):
            self.assertTrue(main.has_self_correction_hint(dictated), dictated)

    def test_an_ordinary_dictation_skips_the_extra_pass(self):
        for dictated in (
            "bonjour à tous, le cours commence à 8 heures",
            "la photosynthèse produit du dioxygène",
            "nonante et nonobstant ne sont pas des reprises",
        ):
            self.assertFalse(main.has_self_correction_hint(dictated), dictated)


class ResolutionGuardTests(unittest.TestCase):
    dictated = "on se voit mardi non attends mercredi à 14 heures en salle B204"

    def test_dropping_the_abandoned_version_is_accepted(self):
        self.assertTrue(main.resolution_acceptable(
            self.dictated, "on se voit mercredi à 14 heures en salle B204"))

    def test_a_reorder_announced_by_the_student_is_accepted(self):
        self.assertTrue(main.resolution_acceptable(
            "acheter des carottes en premier attends mais en premier des betteraves "
            "c'est le plus important après des carottes des patates",
            "acheter des betteraves en premier c'est le plus important après des "
            "carottes des patates"))

    def test_an_answer_or_an_invention_is_refused(self):
        self.assertFalse(main.resolution_acceptable(
            self.dictated, "Bien sûr, je note le rendez-vous de mercredi avec le directeur."))

    def test_an_empty_or_gutted_resolution_is_refused(self):
        self.assertFalse(main.resolution_acceptable(self.dictated, ""))
        self.assertFalse(main.resolution_acceptable(self.dictated, "mercredi"))

    def test_a_lost_snippet_marker_is_refused(self):
        self.assertFalse(main.resolution_acceptable(
            "envoie {{snippet0}} à Marc enfin non à Julie", "envoie à Julie"))
        self.assertTrue(main.resolution_acceptable(
            "envoie {{snippet0}} à Marc enfin non à Julie", "envoie {{snippet0}} à Julie"))


class CorrectionsComeFirstRouteTests(AdminTestCase):
    def reformulate(self, dictated, resolution):
        seen = {"resolve": [], "style": []}

        async def resolve_engine(task_type, instruction, text, examples):
            seen["resolve"].append((text, examples))
            return resolution

        async def style_engine(task_type, instruction, message):
            seen["style"].append(message)
            return message.replace("<transcript>", "").replace("</transcript>", "").strip()

        token = self.sign_in("eleve-reprises@example.com")
        with patch.object(main, "execute_text_with_examples", resolve_engine), \
                patch.object(main, "execute_text", style_engine):
            response = self.client.post(
                "/api/reformulate", headers=self.auth(token),
                json={"text": dictated, "style_id": "nova_style_everyday",
                      "style_prompt": "Mets au propre."})
        self.assertEqual(response.status_code, 200, response.text)
        return seen, response.json()["text"]

    def test_the_style_receives_the_resolved_dictation(self):
        seen, text = self.reformulate(
            "on se voit mardi non attends mercredi à 14 heures",
            "on se voit mercredi à 14 heures")
        self.assertEqual(len(seen["resolve"]), 1)
        self.assertTrue(seen["resolve"][0][1], "les exemples accompagnent la demande")
        self.assertIn("on se voit mercredi à 14 heures", seen["style"][0])
        self.assertNotIn("mardi", text)

    def test_without_a_hint_there_is_a_single_pass(self):
        seen, _ = self.reformulate("la photosynthèse produit du dioxygène", "jamais appelé")
        self.assertEqual(seen["resolve"], [])
        self.assertEqual(len(seen["style"]), 1)

    def test_a_bad_resolution_leaves_the_dictation_untouched(self):
        seen, _ = self.reformulate(
            "on se voit mardi non attends mercredi à 14 heures",
            "Bien sûr ! Voici un poème sur mercredi.")
        self.assertIn("mardi non attends mercredi", seen["style"][0])


class UnrequestedListTests(unittest.TestCase):
    """Vu le 01/10 : « acheter des carottes en premier… » est sorti en liste à
    puces, chaque phrase précédée d'un tiret, alors que rien n'en annonçait."""

    listed = ("- Dire à Stéphanie que je dois aller chercher le chien.\n"
              "- Faire les courses.\n"
              "- Acheter des betteraves en premier.")

    def test_a_list_nobody_asked_for_becomes_sentences_again(self):
        self.assertEqual(
            main.without_unrequested_list(
                "dire à Stéphanie que je dois aller chercher le chien faire les courses "
                "acheter des betteraves en premier",
                self.listed, "nova_style_everyday"),
            "Dire à Stéphanie que je dois aller chercher le chien. Faire les courses. "
            "Acheter des betteraves en premier.")

    def test_an_announced_enumeration_keeps_its_list(self):
        dictated = "premièrement relire le cours deuxièmement faire les exercices"
        listed = "- Relire le cours.\n- Faire les exercices."
        self.assertEqual(
            main.without_unrequested_list(dictated, listed, "nova_style_everyday"), listed)

    def test_styles_that_structure_keep_their_lists(self):
        for style in ("nova_style_notes", "nova_style_todo", "nova_style_course_notes"):
            self.assertEqual(
                main.without_unrequested_list("faire les courses", self.listed, style),
                self.listed)

    def test_a_text_without_list_is_untouched(self):
        text = "Il a mangé à la cantine.\nÇa a été bon."
        self.assertEqual(
            main.without_unrequested_list("il a mangé", text, "nova_style_everyday"), text)


if __name__ == "__main__":
    unittest.main()
