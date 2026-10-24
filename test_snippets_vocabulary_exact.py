"""Un snippet et le vocabulaire sortent a l'identique, quoi que fasse le modele.

Le texte d'un snippet etait substitue **avant** le modele : le modele le
recevait donc comme du texte a reformuler, et pouvait le reecrire -- un IBAN
regroupe autrement, une adresse « amelioree ». Et quand la sortie du modele
etait refusee (reponse, traduction, reponse coupee), le serveur rendait la
dictee d'origine : le declencheur, pas le snippet.

Le vocabulaire de l'organisation, lui, n'etait qu'une indication dans la
consigne : un modele qui l'ignorait rendait « ipsa » ou « aeroelasticite ».

Desormais, comme « Mes informations » sur le poste :

- chaque declencheur devient un repere `{{snippetN}}` que le modele doit
  garder ; le contenu exact est remis apres. Repere perdu : la dictee est
  rendue, snippets appliques ;
- le vocabulaire est applique au texte rendu, de facon deterministe, hors
  adresses e-mail et liens, et jamais dans le contenu d'un snippet ;
- toute dictee rendue a la place d'une sortie refusee a ses snippets et son
  vocabulaire.
"""

import unittest
from unittest.mock import patch

from test_main import AdminTestCase  # noqa: F401

import main
from ai_runtime import AI_RUNTIME_UNAVAILABLE, AIRuntimeError, TextInferenceResult

main.init_db()

EMAIL = "etudiant@example.com"


class SnippetProtectionTests(unittest.TestCase):
    ROWS = [
        {"trigger": "mon adresse", "content": "12 rue des Lilas, 31000 Toulouse"},
        {"trigger": "mon adresse pro", "content": "3 avenue Aeroport, 31700 Blagnac"},
        {"trigger": "mon iban", "content": "FR76 3000 6000 0112 3456 7890 189"},
    ]

    def test_the_longest_trigger_wins_and_the_content_never_reaches_the_model(self):
        protected, restores = main.protect_snippets(
            "envoie mon adresse pro et Mon IBAN", self.ROWS)
        self.assertNotIn("Blagnac", protected)
        self.assertNotIn("FR76", protected)
        self.assertEqual(len(restores), 2)
        # Les mots dictes restent : « mon adresse pro » n'est pas coupe en deux
        # par « mon adresse », et la phrase garde son sens.
        self.assertEqual(
            main.restore_snippets(protected, restores),
            "envoie mon adresse pro : 3 avenue Aeroport, 31700 Blagnac et "
            "Mon IBAN : FR76 3000 6000 0112 3456 7890 189")

    def test_a_lost_marker_cannot_be_restored(self):
        protected, restores = main.protect_snippets("voici mon iban", self.ROWS)
        # Le déclencheur a disparu lui aussi : rien ne dit où remettre la valeur.
        self.assertIsNone(main.restore_snippets("Voici le numéro.", restores))

    def test_expansion_is_longest_first(self):
        self.assertEqual(main.expand_snippets("mon adresse pro", self.ROWS),
                         "mon adresse pro : 3 avenue Aeroport, 31700 Blagnac")

    def test_the_value_is_placed_where_the_sentence_needs_it(self):
        _, restores = main.protect_snippets("Voici mon adresse.", self.ROWS)
        marker = next(iter(restores))
        address = restores[marker][1]
        # Apres des mots : la valeur est annoncee par « : ».
        self.assertEqual(
            main.restore_snippets(f"Voici mon adresse {marker} Jeremy.", restores),
            f"Voici mon adresse : {address} Jeremy.")
        # Deja annoncee : rien n'est ajoute.
        self.assertEqual(
            main.restore_snippets(f"Voici mon adresse : {marker}", restores),
            f"Voici mon adresse : {address}")
        # Mise en evidence sur sa propre ligne par le modele : elle y reste.
        self.assertEqual(
            main.restore_snippets(f"Viens a 17h.\n\nMon adresse\n{marker}", restores),
            f"Viens a 17h.\n\nMon adresse\n{address}")
        # Repere deplace ailleurs : la valeur est posee, sans deux-points.
        self.assertEqual(
            main.restore_snippets(f"Viens a 17h chez moi {marker}", restores),
            f"Viens a 17h chez moi {address}")


class VocabularyEnforcementTests(unittest.TestCase):
    VOCAB = [("IPSA", ""), ("aeroelasticite", "aéroélasticité"), ("Aero 2", "")]

    def test_terms_are_spelled_exactly(self):
        self.assertEqual(
            main.enforce_vocabulary("le cours d'aeroelasticite a l'ipsa en aero 2", self.VOCAB),
            "le cours d'aéroélasticité a l'IPSA en Aero 2")

    def test_emails_and_links_are_left_alone(self):
        text = "écris à scolarite@ipsa.fr ou va sur www.ipsa.fr"
        self.assertEqual(main.enforce_vocabulary(text, self.VOCAB), text)

    def test_markers_are_left_alone(self):
        self.assertEqual(main.enforce_vocabulary("{{snippet0}} ipsa", self.VOCAB),
                         "{{snippet0}} IPSA")


class DoubledLetterNameTests(unittest.TestCase):
    """Banc du RTX sur 0da852a (11/10) : « Sacha Barato » restait tel quel,
    alors que le vocabulaire avait « Baratto »."""

    VOCAB = [("Baratto", ""), ("Bernoulli", ""), ("Pascal", ""), ("ipsa", "IPSA")]

    def test_a_name_heard_without_its_doubled_letter_is_written_right(self):
        for heard, written in (
            ("Sacha Barato a rendu le rapport.", "Sacha Baratto a rendu le rapport."),
            ("Le théorème de Bernouli.", "Le théorème de Bernoulli."),
            ("Le théorème de Bernoullli.", "Le théorème de Bernoulli."),
        ):
            with self.subTest(heard=heard):
                self.assertEqual(main.enforce_vocabulary(heard, self.VOCAB), written)

    def test_other_words_are_left_alone(self):
        for said in (
            "Pascale a rendu son devoir.",   # une autre personne, pas « Pascal »
            "le barato du marché",           # pas une majuscule : pas un nom
            "Barrette et Bateau",            # pas le même nom
            "écrire à baratto@ecole.example",
        ):
            with self.subTest(said=said):
                self.assertEqual(main.enforce_vocabulary(said, self.VOCAB), said)


class ReformulateSnippetTests(AdminTestCase):
    def setUp(self):
        super().setUp()
        with main.db() as conn:
            for table in ("snippets", "personal_dictionary", "shared_dictionary"):
                conn.execute(f"DELETE FROM {table}")
            conn.execute("INSERT INTO snippets(email,trigger,content) VALUES (?,?,?)",
                         (EMAIL, "mon iban", "FR76 3000 6000 0112 3456 7890 189"))
            conn.execute("INSERT INTO snippets(email,trigger,content) VALUES (?,?,?)",
                         (EMAIL, "mon mail", "contact@ipsa.fr"))
            conn.execute("INSERT INTO shared_dictionary(term,replacement) VALUES (?,?)",
                         ("IPSA", ""))
        self.addCleanup(self.remove_entries)

    @staticmethod
    def remove_entries():
        # Le compte de test est partage avec d'autres fichiers : un snippet
        # « mon iban » laisse ici changerait leurs dictees.
        with main.db() as conn:
            for table in ("snippets", "personal_dictionary", "shared_dictionary"):
                conn.execute(f"DELETE FROM {table}")

    def reformulate(self, text, answer, style_id="nova_style_messages"):
        token = self.sign_in(EMAIL)
        seen = {}

        async def engine(task_type, instruction, message):
            seen["message"] = message
            return answer(message) if callable(answer) else answer

        with patch.object(main, "execute_text", engine):
            response = self.client.post(
                "/api/reformulate", headers=self.auth(token),
                json={"text": text, "style_id": style_id, "style_prompt": "Message court."})
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()["text"], seen

    def test_the_snippet_comes_back_exactly(self):
        def model(message):
            # Le modele reformule autour du repere, qu'il garde.
            marker = message[message.index("{{"):message.index("}}") + 2]
            return f"Voici mon IBAN : {marker}. Merci de faire le virement a l'ipsa."

        text, seen = self.reformulate("voici mon iban merci de faire le virement a l'ipsa", model)
        self.assertNotIn("FR76", seen["message"])
        # Le modele a vu « mon iban », il garde donc une phrase qui a du sens.
        self.assertIn("mon iban", seen["message"])
        self.assertEqual(
            text,
            "Voici mon IBAN : FR76 3000 6000 0112 3456 7890 189. Merci de faire le virement a l'IPSA.")

    def test_a_lost_marker_returns_the_dictation_with_its_snippet(self):
        text, _ = self.reformulate("voici mon iban merci de faire le virement",
                                   "Voici le numéro, merci de faire le virement.")
        self.assertEqual(
            text,
            "Voici mon iban : FR76 3000 6000 0112 3456 7890 189 merci de faire le virement")

    def test_a_lost_marker_behind_its_trigger_is_put_back(self):
        """Mesuré sur le RTX le 05/10 : la reformulation est gardée."""
        text, _ = self.reformulate("voici mon iban merci de faire le virement",
                                   "Voici mon IBAN, merci de faire le virement.")
        self.assertEqual(
            text,
            "Voici mon IBAN : FR76 3000 6000 0112 3456 7890 189, merci de faire le virement.")

    def test_a_refused_answer_returns_the_dictation_with_its_snippet(self):
        text, _ = self.reformulate("ecris a mon mail pour le partiel de l'ipsa",
                                   "Write to the email address for the exam.")
        self.assertEqual(text, "Ecris a mon mail : contact@ipsa.fr pour le partiel de l'IPSA")

    def test_a_truncated_answer_returns_the_dictation_with_its_snippet(self):
        token = self.sign_in(EMAIL)

        async def generate(task_type, input_text, instruction, **_):
            return TextInferenceResult(text="Voici", finish_reason="length",
                                       latency_ms=1, usage={}, backend={"adapter": "test"})

        with patch.object(main.ORGANIZATION_AI_ROUTER, "generate", generate):
            response = self.client.post(
                "/api/reformulate", headers=self.auth(token),
                json={"text": "voici mon iban", "style_id": "nova_style_messages",
                      "style_prompt": "Message court."})
        self.assertEqual(response.json()["text"],
                         "Voici mon iban : FR76 3000 6000 0112 3456 7890 189")


    def test_a_stopped_text_engine_returns_the_dictation_with_its_snippet(self):
        """Test 8 du RTX (09/10) : modèle de texte arrêté 2 min 15, les 95
        reformulations envoyées pendant ce temps répondaient 503. Le poste
        collait alors sa transcription brute : « mon iban » sans sa valeur."""
        token = self.sign_in(EMAIL)

        async def generate(task_type, input_text, instruction, **_):
            raise AIRuntimeError(AI_RUNTIME_UNAVAILABLE)

        with patch.object(main.ORGANIZATION_AI_ROUTER, "generate", generate):
            response = self.client.post(
                "/api/reformulate", headers=self.auth(token),
                json={"text": "voici mon iban pour l'ipsa", "style_id": "nova_style_messages",
                      "style_prompt": "Message court."})
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["text"],
                         "Voici mon iban : FR76 3000 6000 0112 3456 7890 189 pour l'IPSA")


if __name__ == "__main__":
    unittest.main()
