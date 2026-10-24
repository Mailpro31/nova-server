"""Une commande ne propose pas à l'élève un texte qui change ce qu'il a écrit.

Banc du RTX du 06/10, sur 8780c3c, avec les consignes exactes du poste
(`src/lib/commands/catalog.ts`). `/api/command` rendait la sortie du modèle
telle quelle :

- « Expliquer », « Résumer » et « Améliorer » répondaient en anglais à un
  texte français ; « Améliorer » rendait donc une traduction ;
- « Améliorer » a perdu la moitié d'un message (« j'ai oublié de prendre mes
  notes… ») et changé son destinataire ;
- « mets ce texte en liste à puces » a écrit « puce fois V fois L » au lieu de
  « rho fois V fois L ».

Les sorties ci-dessous sont celles du RTX, mot pour mot.
"""

import unittest
from unittest.mock import patch

from test_main import AdminTestCase  # noqa: F401

import main

main.init_db()

COURS = ("Le nombre de Reynolds vaut rho fois V fois L divisé par mu. En dessous de 2 300, "
         "l'écoulement dans une conduite est laminaire ; au-dessus de 4 000, il est turbulent. "
         "Le partiel aura lieu le 15 novembre à 14 h en salle B204.")
MESSAGE = ("Je n'ai pas pu rendre mon devoir de mécanique hier parce que mon ordinateur est tombé "
           "en panne. Je peux vous l'envoyer avant vendredi 18 h.")
FAUTES = ("jai oublier de prendre mes note pendant le cour de thermo, est ce que quelqu'un peu "
          "me les envoyé avant jeudi")
ANGLAIS = ("The lab report is due on March 12. Each group of 3 students must measure the flow "
           "rate at 5 different pressures.")

# Les consignes du poste, telles qu'il les envoie.
EXPLAIN = ("Explain what the following text means to someone who is not a specialist: give its "
           "main idea in plain words, then define each technical term it uses. Do not simply "
           "rephrase it. Answer in the same language as the text. Return only the explanation.")
SUMMARIZE = ("Summarize the following text in a few short sentences, at most a third of its "
             "length, keeping only the essential points. Answer in the same language as the "
             "text. Return only the summary.")
IMPROVE = ("Improve the wording, grammar and clarity of the following text. Keep the same "
           "language, the same meaning and a similar length. Return only the improved text.")
TRANSLATE = "Translate the following text into {}. Return only the translation."
LIST = "Mets ce texte sous forme de liste à puces."
SPELLING = "Corrige seulement l'orthographe, ne change rien d'autre."

ENGLISH_MESSAGE = ("I couldn't finish my mechanical engineering homework yesterday because my "
                   "computer broke down. I can send it before Friday at 6 PM.")


class CommandKindTests(unittest.TestCase):
    def test_each_command_of_the_desktop_is_recognised(self):
        self.assertEqual(main.command_kind(EXPLAIN), "condense")
        self.assertEqual(main.command_kind(SUMMARIZE), "condense")
        self.assertEqual(main.command_kind(IMPROVE), "rewrite")
        self.assertEqual(main.command_kind(TRANSLATE.format("French")), "translate")
        self.assertEqual(main.command_kind(SPELLING), "rewrite")
        self.assertEqual(main.command_kind(LIST), "other")
        self.assertEqual(main.command_kind("Traduis en espagnol."), "translate")
        self.assertEqual(main.command_kind("Résume ce texte."), "condense")


class CommandLanguageLineTests(unittest.TestCase):
    def test_the_language_of_the_text_is_named_to_the_model(self):
        self.assertEqual(main.command_language_line(SUMMARIZE, COURS),
                         "\n\nThe text is written in French. Write the result in French.")
        self.assertIn("in English", main.command_language_line(IMPROVE, ANGLAIS))

    def test_nothing_is_added_for_a_translation_or_an_unclear_text(self):
        self.assertEqual(main.command_language_line(TRANSLATE.format("English"), MESSAGE), "")
        self.assertEqual(main.command_language_line(SUMMARIZE, "B204, 14 h"), "")


class RefusedOutputsTests(unittest.TestCase):
    """Les sorties fautives du RTX, refusées."""

    def test_an_answer_in_another_language_is_refused(self):
        for instruction, text in ((EXPLAIN, COURS), (SUMMARIZE, MESSAGE), (IMPROVE, MESSAGE)):
            with self.subTest(instruction=instruction[:20]):
                self.assertEqual(
                    main.command_refusal(instruction, text, ENGLISH_MESSAGE), "language-changed")

    def test_an_improvement_that_drops_half_the_message_is_refused(self):
        self.assertEqual(
            main.command_refusal(
                IMPROVE, FAUTES,
                "pourriez-vous m'envoyer mes notes du cours de thermodynamique avant jeudi ?"),
            "text-not-kept")

    def test_a_word_of_the_instruction_in_a_formula_is_refused(self):
        listed = ("- Le nombre de Reynolds (rho) vaut : **puce** fois **V** fois **L** divisé "
                  "par **mu**.\n- Dans une conduite :\n  - L'écoulement est laminaire si le "
                  "nombre de Reynolds est inférieur à 2 300 ;\n  - Il est turbulent si le nombre "
                  "de Reynolds est supérieur à 4 000.\n- Le partielle se déroulera le **15 "
                  "novembre** à **14 h** en salle **B204**.")
        # « Le partielle » ne nomme plus l'examen non plus : l'un ou l'autre motif.
        self.assertIn(main.command_refusal(LIST, COURS, listed),
                      ("instruction-leaked", "exam-lost"))
        self.assertTrue(main.instruction_word_leaked(LIST, COURS, listed))

    def test_an_invented_number_is_refused(self):
        self.assertEqual(
            main.command_refusal(SUMMARIZE, COURS,
                                 "Écoulement laminaire sous 2 300, turbulent au-dessus de 4 000. "
                                 "Partiel le 16 novembre à 14 h en salle B204."),
            "number-changed")

    def test_an_explanation_may_count_in_words(self):
        """PC fixe, 24/10 : « trois grandeurs », « inférieure à zéro » faisaient
        refuser de bonnes explications de la bulle (number-changed)."""
        source = "La loi d'Ohm relie la tension, l'intensité et la résistance."
        for output in ("Cette loi établit un lien entre trois grandeurs électriques.",
                       "Une valeur négative est inférieure à zéro."):
            with self.subTest(output=output):
                self.assertIsNone(main.command_refusal(EXPLAIN, source, output))

    def test_an_explanation_still_cannot_invent_figures(self):
        source = "La loi d'Ohm relie la tension, l'intensité et la résistance."
        for output in ("Cette loi relie 3 grandeurs.", "Il y a dix-sept cas.",
                       "Il y a trois cents cas."):
            with self.subTest(output=output):
                self.assertEqual(main.command_refusal(EXPLAIN, source, output),
                                 "number-changed")

    def test_one_half_in_words_and_in_figures_are_the_same_value(self):
        source = "La pression dynamique est égale à un demi rho v au carré."
        for output in ("La pression dynamique est égale à 0,5 ρv².",
                       "La pression dynamique vaut ½ ρ v au carré.",
                       "La pression dynamique vaut 1/2 rho v au carré."):
            with self.subTest(output=output):
                self.assertIsNone(main.command_refusal(SUMMARIZE, source, output))
        self.assertEqual(main.command_refusal(SUMMARIZE, source,
                                              "La pression dynamique est égale à 0,25 ρv²."),
                         "number-changed")

    def test_restating_the_text_label_is_not_a_chatbot_preamble(self):
        source = "À retenir : la loi d'Ohm relie la tension, l'intensité et la résistance."
        self.assertIsNone(main.command_refusal(
            EXPLAIN, source,
            "Voici ce qu'il faut retenir :\nLa loi d'Ohm relie la tension, l'intensité "
            "et la résistance d'un circuit."))
        self.assertEqual(main.command_refusal(
            EXPLAIN, source,
            "Voici une explication :\nLa loi d'Ohm relie la tension, l'intensité et la "
            "résistance d'un circuit."), "chatbot-answer")

    def test_a_rewrite_never_adds_a_number_even_small(self):
        self.assertEqual(
            main.command_refusal(SPELLING, "je rend le tp bientot",
                                 "Je rends le TP dans deux jours."),
            "number-changed")

    def test_an_improvement_that_loses_a_number_is_refused(self):
        self.assertEqual(
            main.command_refusal(IMPROVE, MESSAGE,
                                 "Je n'ai pas pu rendre mon devoir de mécanique hier, car mon "
                                 "ordinateur est tombé en panne. Je vous l'enverrai avant "
                                 "vendredi soir."),
            "number-lost")

    def test_an_improvement_that_changes_whose_it_is_is_refused(self):
        self.assertEqual(
            main.command_refusal(IMPROVE, "Je vous envoie mon devoir de mécanique demain.",
                                 "Je vous envoie votre devoir de mécanique demain."),
            "person-changed")

    def test_an_assistant_reply_is_refused_even_for_a_translation(self):
        self.assertEqual(
            main.command_refusal(TRANSLATE.format("English"), MESSAGE,
                                 "Could you clarify what you would like me to translate?"),
            "chatbot-answer")

    def test_an_empty_answer_is_refused(self):
        self.assertEqual(main.command_refusal(SUMMARIZE, COURS, "  "), "empty-output")


class SecondBenchTests(unittest.TestCase):
    """Banc du RTX sur e737ddc (#92), sorties brutes du modèle mot pour mot."""

    def test_a_summary_that_keeps_the_date_but_loses_the_exam_is_refused(self):
        # « Le partiel » devenu « La partie », date gardée.
        self.assertEqual(
            main.command_refusal(
                SUMMARIZE, COURS,
                "Le Reynolds vaut rho fois V fois L divisé par mu. Dans la plupart des cas, "
                "l'écoulement dans une conduite est laminaire sous 2 300 et turbulent au-dessus "
                "de 4 000. La partie se déroulera le 15 novembre à 14 heures dans la salle B204."),
            "exam-lost")

    def test_a_summary_may_leave_the_exam_out_entirely(self):
        self.assertIsNone(main.command_refusal(
            SUMMARIZE, COURS, "Laminaire sous 2 300, turbulent au-dessus de 4 000."))

    def test_a_summary_longer_than_the_text_is_refused(self):
        self.assertEqual(
            main.command_refusal(
                SUMMARIZE, MESSAGE,
                "Je n'ai pas pu rendre mon devoir de mécanique hier, parce que mon ordinateur "
                "est tombé en panne pendant la soirée. Je peux vous l'envoyer avant vendredi 18 h, "
                "sans faute."),
            "summary-not-shorter")

    def test_an_explanation_may_be_longer_than_the_text(self):
        self.assertIsNone(main.command_refusal(
            EXPLAIN, "Le Reynolds vaut rho fois V fois L divisé par mu.",
            "Le nombre de Reynolds compare l'inertie d'un fluide à sa viscosité. Rho est la "
            "masse volumique, V la vitesse, L une longueur caractéristique et mu la viscosité."))

    def test_zero_minutes_are_not_an_invented_number(self):
        self.assertIsNone(main.command_refusal(
            IMPROVE, MESSAGE,
            "Je n'ai pas pu rendre mon devoir de mécanique hier, car mon ordinateur est tombé en "
            "panne. Je peux vous l'envoyer avant vendredi, 18 h 00."))

    def test_the_raw_refused_outputs_stay_refused(self):
        for instruction, text, raw, reason in (
            (SUMMARIZE, MESSAGE,
             "Je ne peux pas rendre mon devoir de mécanique aujourd'hui car mon ordinateur est "
             "hors service. Il sera envoyé avant le vendredi à minuit.", "number-lost"),
            (IMPROVE, MESSAGE,
             "Je ne peux pas rendre mon devoir de mécanique hier car mon ordinateur est tombé en "
             "panne. Je peux le faire avant vendredi à minuit.", "number-lost"),
            (IMPROVE, FAUTES,
             "je m'excuse pour avoir oublié d'enregistrer mes notes pendant la classe de "
             "thermodynamique. si vous pouvez me les envoyer avant jeudi, cela serait grandement "
             "apprécié.", "text-not-kept"),
        ):
            with self.subTest(raw=raw[:30]):
                self.assertIsNotNone(main.command_refusal(instruction, text, raw))

    def test_formulas_are_to_be_copied_as_written(self):
        self.assertIn("Copy formulas, symbols and units exactly as they are written",
                      main.command_prompt(LIST, ""))


class AcceptedOutputsTests(unittest.TestCase):
    """Ce qu'une commande doit pouvoir rendre sans être refusée."""

    def test_good_answers_pass(self):
        for instruction, text, answer in (
            (EXPLAIN, COURS,
             "Le nombre de Reynolds sert à savoir si un fluide s'écoule de façon régulière ou "
             "agitée dans une conduite. Laminaire : l'écoulement est régulier, en couches. "
             "Turbulent : il devient désordonné. Rho est la masse volumique, V la vitesse, L la "
             "longueur et mu la viscosité."),
            (SUMMARIZE, COURS,
             "Reynolds : laminaire sous 2 300, turbulent au-dessus de 4 000. Partiel le "
             "15 novembre à 14 h, salle B204."),
            (IMPROVE, MESSAGE,
             "Je n'ai pas pu rendre mon devoir de mécanique hier, car mon ordinateur est tombé en "
             "panne. Je peux vous l'envoyer avant vendredi 18 h."),
            (IMPROVE, FAUTES,
             "J'ai oublié de prendre mes notes pendant le cours de thermo. Est-ce que quelqu'un "
             "peut me les envoyer avant jeudi ?"),
            (SPELLING, FAUTES,
             "j'ai oublié de prendre mes notes pendant le cours de thermo, est-ce que quelqu'un "
             "peut me les envoyer avant jeudi"),
            (LIST, COURS,
             "- Le nombre de Reynolds vaut rho fois V fois L divisé par mu.\n"
             "- En dessous de 2 300 : écoulement laminaire dans une conduite.\n"
             "- Au-dessus de 4 000 : écoulement turbulent.\n"
             "- Partiel : 15 novembre à 14 h, salle B204."),
            (IMPROVE, ANGLAIS,
             "The lab report is due on March 12. Each group of 3 students must measure the flow "
             "rate at 5 different pressures."),
        ):
            with self.subTest(answer=answer[:30]):
                self.assertIsNone(main.command_refusal(instruction, text, answer))

    def test_the_rtx_translations_pass(self):
        self.assertIsNone(main.command_refusal(
            TRANSLATE.format("French"), ANGLAIS,
            "Le rapport de laboratoire doit être livré le 12 mars. Chaque groupe composé de trois "
            "étudiants doit mesurer la vitesse d'écoulement à cinq pressions différentes."))
        self.assertIsNone(main.command_refusal(
            TRANSLATE.format("English"), MESSAGE,
            "I couldn't hand in my mechanical engineering homework yesterday because my computer "
            "broke down. I can send it before Friday at 6 PM."))


# La nouvelle consigne d'Améliorer du poste (nova-desktop d5ee348).
MINIMAL = ("Correct the spelling, grammar and punctuation of the following text. Keep every "
           "word that is correctly spelled, in the same order; do not add, remove or reword "
           "ideas, and keep who the text is addressed to. Keep the same language. Return only "
           "the corrected text.")


class ThirdBenchTests(unittest.TestCase):
    """Banc du RTX sur 6112404 : sorties brutes, mot pour mot."""

    def test_je_m_oublie_and_on_instead_of_quelqu_un_are_refused(self):
        self.assertEqual(
            main.command_refusal(
                MINIMAL, FAUTES,
                "je m'oublie de prendre mes notes pendant le cours de thermodynamique. "
                "Est-ce qu'on pourrait me les envoyer avant jeudi ?"),
            "person-changed")

    def test_vous_instead_of_quelqu_un_is_refused(self):
        self.assertIsNotNone(main.command_refusal(
            IMPROVE, FAUTES,
            "je m'excuse pour avoir oublié d'apporter mes notes pendant la classe de "
            "thermodynamique. serait-il possible que vous m'en fassiez parvenir avant jeudi ?"))

    def test_a_correction_may_restore_a_missing_apostrophe(self):
        # « jai » devient « j'ai » : un « j' » de plus, aucun pronom perdu.
        self.assertIsNone(main.command_refusal(
            MINIMAL, FAUTES,
            "J'ai oublié de prendre mes notes pendant le cours de thermo, est-ce que quelqu'un "
            "peut me les envoyer avant jeudi ?"))
        self.assertIsNone(main.command_refusal(
            SPELLING, FAUTES,
            "J'ai oublié de prendre mes notes pendant le cours de thermodynamique. Est-ce que "
            "quelqu'un pourrait m'en envoyer avant jeudi ?"))

    def test_losing_the_addressee_is_refused(self):
        self.assertTrue(main.pronouns_swapped(
            MESSAGE,
            "Je n'ai pas pu rendre mon devoir de mécanique hier, car mon ordinateur est tombé en "
            "panne. Il pourra être envoyé avant vendredi 18 h."))

    def test_a_preamble_is_an_assistant_answer(self):
        self.assertEqual(
            main.command_refusal(
                MINIMAL, MESSAGE,
                "Le texte correcte serait : \"Je n'ai pas pu rendre mon devoir de mécanique hier "
                "parce que mon ordinateur est tombé en panne. Je peux vous l'envoyer avant "
                "vendredi 18 h.\""),
            "chatbot-answer")
        self.assertEqual(
            main.command_refusal(LIST, COURS, "Voici la liste à puces :\n- Le nombre de Reynolds."),
            "chatbot-answer")

    def test_a_heading_from_the_text_is_not_a_preamble(self):
        self.assertIsNone(main.command_refusal(
            SUMMARIZE, "Liste des courses : du pain, du lait, des œufs et du beurre pour demain.",
            "Liste des courses : pain, lait, œufs."))
        self.assertIsNone(main.command_refusal(
            LIST, COURS,
            "Reynolds :\n- vaut rho fois V fois L divisé par mu ;\n- laminaire sous 2 300 ;\n"
            "- turbulent au-dessus de 4 000 ;\n- partiel le 15 novembre à 14 h, salle B204."))


class EnglishNumberWordsTests(unittest.TestCase):
    """Banc du RTX sur 6112404 : « 3 » corrigé en « three » était refusé."""

    def test_a_number_written_in_english_words_keeps_its_value(self):
        self.assertIsNone(main.command_refusal(
            MINIMAL, ANGLAIS,
            "The lab report is due on March 12. Each group of three students must measure "
            "the flow rate at five different pressures."))

    def test_a_changed_english_number_is_still_refused(self):
        self.assertIsNotNone(main.command_refusal(
            MINIMAL, ANGLAIS,
            "The lab report is due on March 12. Each group of four students must measure "
            "the flow rate at five different pressures."))

    def test_one_is_not_a_number(self):
        self.assertEqual(main.command_numbers("the one who came, no one else", True),
                         "the one who came, no one else")

    def test_french_words_are_never_read_as_english_numbers(self):
        self.assertEqual(main.command_numbers("le four est chaud", False), "le four est chaud")
        self.assertIsNone(main.command_refusal(
            SUMMARIZE,
            "Le four de la cantine chauffe à 250 degrés pendant que le cours de thermique "
            "explique la conduction, la convection et le rayonnement en détail.",
            "Le four chauffe à 250 degrés ; conduction, convection, rayonnement."))


class FormulaTests(unittest.TestCase):
    """Banc du RTX, 0ff7a2a à 2795421 : Expliquer recopiait mal la formule."""

    RTX_EXPLAIN = (
        "Ce texte décrit la loi des nombres de Reynolds. Il dit que le nombre de Reynolds (rho) "
        "multiplié par V (la vitesse) multiplié par L (l'épaisseur) divisé par mu (la viscosité), "
        "donne un résultat qui détermine si l'écoulement dans une conduite est laminaire ou "
        "turbulent. Si ce nombre est inférieur à 2 300, l'écoulement est laminaire et au-dessus "
        "de 4 000, il est turbulent. La partie du texte mentionne également les dates et lieux "
        "d'un événement scientifique.")

    def test_the_rtx_explanation_with_a_wrong_formula_is_refused(self):
        self.assertEqual(main.command_refusal(EXPLAIN, COURS, self.RTX_EXPLAIN),
                         "formula-changed")

    def test_a_formula_reordered_is_refused(self):
        self.assertTrue(main.formula_changed("Delta = b² − 4 × a × c",
                                             "Delta vaut b² moins 4 fois c fois a"))

    def test_a_faithful_formula_passes(self):
        for answer in (
            "Le nombre de Reynolds vaut rho fois V fois L divisé par mu : rho est la masse "
            "volumique, V la vitesse.",
            # Une précision entre parenthèses ne change pas la formule.
            "Le nombre de Reynolds vaut rho (la masse volumique) fois V (la vitesse) fois L "
            "divisé par mu.",
            # Un morceau intact de la formule.
            "On multiplie V fois L, puis on tient compte de la viscosité mu.",
            # Écrite en symboles : non jugée.
            "Re = ρVL/μ compare l'inertie à la viscosité.",
            # Pas de formule citée.
            "Le nombre de Reynolds compare l'inertie d'un fluide à sa viscosité.",
        ):
            with self.subTest(answer=answer[:30]):
                self.assertFalse(main.formula_changed(COURS, answer))

    def test_ordinary_words_are_not_formulas(self):
        self.assertFalse(main.formula_changed(
            COURS, "Il faut relire le cours deux fois avant le partiel du 15/11."))
        self.assertFalse(main.formula_changed(
            "Le cours est sur https://moodle.ipsa.fr/course/view.php",
            "Le cours : https://moodle.ipsa.fr/course/view.php"))


class CommandRouteTests(AdminTestCase):
    def post(self, answer, instruction=SUMMARIZE, text=COURS, path="/api/command"):
        token = self.sign_in("etudiant@example.com")
        seen = {}

        async def engine(task_type, system_prompt, delimited):
            seen["prompt"] = system_prompt
            return answer

        with patch.object(main, "execute_text", engine):
            response = self.client.post(path, headers=self.auth(token),
                                        json={"instruction": instruction, "text": text})
        return response, seen

    def test_a_faithful_answer_is_returned(self):
        response, seen = self.post("Laminaire sous 2 300, turbulent au-dessus de 4 000.")
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["text"],
                         "Laminaire sous 2 300, turbulent au-dessus de 4 000.")
        self.assertTrue(seen["prompt"].endswith(
            "The text is written in French. Write the result in French."))

    def test_a_refused_answer_is_never_shown(self):
        response, _ = self.post(ENGLISH_MESSAGE, instruction=IMPROVE, text=MESSAGE)
        self.assertEqual(response.status_code, 422)
        self.assertEqual(response.json()["detail"],
                         {"code": "COMMAND_REFUSED", "reason": "language-changed"})
        # Ni le texte de l'élève ni la sortie refusée ne repartent.
        self.assertNotIn("homework", response.text)
        self.assertNotIn("devoir", response.text)


if __name__ == "__main__":
    unittest.main()
