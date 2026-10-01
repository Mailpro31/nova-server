"""Jamais la version que l'élève vient de rejeter.

Mesuré le 01/10 sur le serveur RTX (Qwen2.5-1.5B), deux passages identiques du
banc des reprises : 14 justes sur 20. Trois des six échecs rendaient la
version que l'élève venait d'écarter, sans qu'aucun contrôle ne le voie :
« le train de 8 heures non le bus » → « le train », « un vélo bleu euh non
rouge » → « bleu », « la capitale de l'Italie non pardon de la France » →
« l'Italie ». Aucun chiffre ne bougeait : le contrôle des nombres ne pouvait
rien.

La règle : après une reprise, l'élève dit quelque chose de nouveau (« bus »,
« rouge », « France »). Une sortie qui a perdu tous ces mots-là a gardé la
mauvaise version. Elle est refusée, et l'élève récupère sa dictée, ses deux
versions visibles : inoffensif.
"""

import unittest
from unittest.mock import patch

from test_main import AdminTestCase  # noqa: F401

import main

main.init_db()

REJECTED = [
    ("je prends le train de 8 heures non le bus de 8 heures", "je prends le train de 8 heures"),
    ("je vais acheter un vélo bleu euh non rouge", "je vais acheter un vélo bleu"),
    ("Paris est la capitale de l'Italie non pardon de la France",
     "Paris est la capitale de l'Italie"),
    ("le rendez-vous chez le dentiste c'est à 9 heures non 10 heures",
     "le rendez-vous chez le dentiste c'est à 9 heures"),
]

RESOLVED = [
    ("je prends le train de 8 heures non le bus de 8 heures", "je prends le bus de 8 heures"),
    ("je vais acheter un vélo bleu euh non rouge", "je vais acheter un vélo rouge"),
    ("Paris est la capitale de l'Italie non pardon de la France",
     "Paris est la capitale de la France"),
    ("il faut acheter du lait oublie le lait prends plutôt du jus d'orange",
     "il faut acheter du jus d'orange"),
    ("dire à Stéphanie que je dois faire les courses donc acheter des carottes en premier "
     "attends mais en premier des betteraves c'est le plus important après des carottes",
     "dire à Stéphanie que je dois faire les courses donc acheter des betteraves en premier "
     "c'est le plus important après des carottes"),
]

# Un « non » qui n'est pas une reprise : la sortie qui le garde est juste.
UNTOUCHED = [
    ("le prof a dit non à la sortie de jeudi", "Le prof a dit non à la sortie de jeudi."),
    ("appelle Thomas non Thomas et Léa", "Appelle Thomas et Léa."),
    ("le cours commence à 8 heures et finit à midi non sans pause",
     "Le cours commence à 8 heures et finit à midi, sans pause."),
]


class RejectedVersionTests(unittest.TestCase):
    def test_keeping_the_rejected_version_is_detected(self):
        for dictated, output in REJECTED:
            self.assertTrue(main.correction_lost(dictated, output), output)

    def test_keeping_the_retained_version_is_fine(self):
        for dictated, output in RESOLVED + UNTOUCHED:
            self.assertFalse(main.correction_lost(dictated, output), output)

    def test_a_dictation_without_correction_is_never_flagged(self):
        self.assertFalse(main.correction_lost(
            "la photosynthèse produit du dioxygène", "La photosynthèse produit du dioxygène."))

    def test_the_resolution_pass_rejects_the_rejected_version(self):
        for dictated, output in REJECTED:
            self.assertFalse(main.resolution_acceptable(dictated, output), output)

    def test_the_style_output_is_refused_when_it_keeps_the_rejected_version(self):
        for style in ("nova_style_everyday", "default_improve_transcriptions",
                      "nova_style_email", "nova_style_course_notes"):
            self.assertEqual(
                main.rewrite_refusal(
                    "je vais acheter un vélo bleu euh non rouge",
                    "Je vais acheter un vélo bleu.", style),
                "correction-lost", style)


class RejectedVersionRouteTests(AdminTestCase):
    def test_the_student_gets_both_versions_rather_than_the_wrong_one(self):
        dictated = "je prends le train de 8 heures non le bus de 8 heures"

        async def resolve_engine(task_type, instruction, text, examples):
            return "je prends le train de 8 heures"

        async def style_engine(task_type, instruction, message):
            return "Je prends le train de 8 heures."

        token = self.sign_in("eleve-version@example.com")
        with patch.object(main, "execute_text_with_examples", resolve_engine), \
                patch.object(main, "execute_text", style_engine):
            response = self.client.post(
                "/api/reformulate", headers=self.auth(token),
                json={"text": dictated, "style_id": "nova_style_everyday",
                      "style_prompt": "Mets au propre."})
        self.assertEqual(response.status_code, 200, response.text)
        self.assertIn("bus", response.json()["text"])


if __name__ == "__main__":
    unittest.main()
