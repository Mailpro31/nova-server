"""Aucun Style integre ne change, n'invente ni ne perd un nombre ou une date.

Le poste a ce controle depuis nova-desktop#205 (`rewrite::guard`). Le serveur
doit refuser exactement la meme chose, pour la meme raison : un membre dont la
reformulation passe par l'organisation ne doit pas recevoir ce que le poste
aurait refuse. Les cas ci-dessous sont ceux des tests du poste, a l'identique.

Avant, seules les notes de cours etaient controlees sur les nombres dits en
lettres, et aucun Style sur les dates : un e-mail pouvait passer de « jeudi »
a « vendredi », un compte rendu inventer une echeance.
"""

import unittest

from test_main import AdminTestCase  # noqa: F401

import main

main.init_db()


def refusal(dictated, answer, style):
    return main.rewrite_refusal(dictated, answer, style)


class AllStylesFactsTests(unittest.TestCase):
    def test_a_meeting_summary_cannot_invent_a_deadline(self):
        self.assertEqual(refusal(
            "Sasha commande les hélices avant vendredi",
            "Sasha a commandé les hélices avant. La batterie doit être testée la semaine prochaine.",
            "nova_style_meeting",
        ), "date-changed")

    def test_a_changed_day_or_number_is_refused_in_every_built_in_style(self):
        self.assertEqual(refusal(
            "le rendez-vous est jeudi à 14 heures en salle B204",
            "Bonjour,\n\nLe rendez-vous est vendredi à 14 heures en salle B204.\n\nCordialement",
            "nova_style_email",
        ), "date-changed")
        self.assertEqual(refusal(
            "il faut relire les pages douze à quinze pour demain",
            "- Relire les pages 12 à 16 pour demain",
            "nova_style_todo",
        ), "number-changed")
        self.assertEqual(refusal(
            "il faut trois exemplaires et deux copies du dossier",
            "Il faut 3 exemplaires du dossier.",
            "default_improve_transcriptions",
        ), "number-lost")

    def test_a_self_correction_may_drop_the_abandoned_value(self):
        self.assertIsNone(refusal(
            "le rendez-vous est le 12 non pardon le 13 en salle B204",
            "Le rendez-vous est le 13 en salle B204.",
            "default_improve_transcriptions",
        ))
        self.assertIsNone(refusal(
            "on se retrouve mardi non pardon mercredi à 14 heures en salle B204",
            "On se retrouve mercredi à 14 heures en salle B204.",
            "nova_style_email",
        ))

    def test_a_self_correction_never_lets_the_final_value_be_lost(self):
        # Le modele a garde la version abandonnee et perdu la bonne.
        self.assertIsNotNone(refusal(
            "le rendez-vous est le 12 non pardon le 13 en salle B204",
            "Le rendez-vous est le 12 en salle B204.",
            "default_improve_transcriptions",
        ))

    def test_list_numbering_is_not_an_invented_number(self):
        self.assertIsNone(refusal(
            "il faut lancer le développement puis préparer la maquette avec Marc",
            "1. Lancer le développement.\n2. Préparer la maquette avec Marc.",
            "nova_style_notes",
        ))

    def test_a_meeting_summary_may_still_leave_out_a_date(self):
        self.assertIsNone(refusal(
            "on se voit lundi pour faire le point et on livre vendredi au client",
            "## Résumé\nLivraison au client vendredi.",
            "nova_style_meeting",
        ))

    def test_a_faithful_style_cannot_add_content(self):
        self.assertEqual(refusal(
            "je passe te voir cet après-midi",
            "Je passe te voir cet après-midi pour discuter du budget, des priorités "
            "et des prochaines étapes du projet.",
            "nova_style_messages",
        ), "content-added")

    def test_a_faithful_style_may_fix_a_misheard_word_in_a_short_dictation(self):
        self.assertIsNone(refusal(
            "on se voit au sinéma ce soir",
            "On se voit au cinéma ce soir.",
            "default_improve_transcriptions",
        ))

    def test_a_marker_is_not_a_dictated_number(self):
        self.assertIsNone(refusal(
            "je relis {{nvxlex0}} avec Paul ce soir",
            "Je relis avec Paul ce soir.",
            "default_improve_transcriptions",
        ))
        self.assertIsNone(refusal(
            "je relis la fiche ce soir",
            "Je relis la fiche {{nvxlex3}} ce soir.",
            "nova_style_notes",
        ))

    def test_a_self_correction_abandons_only_what_precedes_it(self):
        kept = main.without_abandoned("le rendez-vous est le 12 non pardon le 13 en salle B204")
        self.assertNotIn("12", kept)
        self.assertIn("13", kept)
        self.assertIn("B204", kept)
        plain = "on a 3 exemplaires et 2 copies"
        self.assertEqual(main.without_abandoned(plain), plain)

    def test_dates_said_in_words_are_recognised(self):
        self.assertIn("vendredi", main.dates_in("avant vendredi"))
        self.assertIn("semaine prochaine", main.dates_in("la semaine prochaine"))
        self.assertIn("mars", main.dates_in("le 3 mars, puis demain"))
        self.assertIn("friday", main.dates_in("before Friday, next week"))
        self.assertEqual(main.dates_in("un cours sur les nombres"), set())

    def test_school_and_personal_styles_are_left_alone(self):
        # Un Style d'organisation peut traduire ou resumer : seuls les nombres
        # en chiffres y restent exiges, comme avant.
        self.assertIsNone(refusal("merci beaucoup pour tout", "Thanks a lot.", "org:acme:style"))
        self.assertIsNone(refusal("on livre vendredi", "On livre la semaine prochaine.", "custom_x"))


if __name__ == "__main__":
    unittest.main()
