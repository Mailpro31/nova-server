"""Le vocabulaire guide la transcription, pas seulement la correction.

Jusqu'ici le moteur recevait l'audio sans aucun indice : « IPSA », « Aéro 3 »
ou un nom de famille étaient transcrits comme le modèle les devinait, puis
corrigés après coup quand une règle existait. Whisper accepte un texte
d'amorce (`prompt`) qui oriente l'orthographe des mots rares — le plus
simple des leviers, sans réentraînement.

Deux risques bornés ici : une amorce trop longue (le moteur la tronque), et
l'amorce recopiée à la place d'une dictée vide ou inaudible — défaut connu de
Whisper, qui collerait alors une liste de mots que personne n'a dite.
"""

import unittest
from unittest.mock import patch

from test_main import AdminTestCase  # noqa: F401

import main
from ai_runtime import SpeechInferenceResult

main.init_db()


def clear_vocabulary():
    with main.db() as conn:
        conn.execute("DELETE FROM shared_dictionary")
        conn.execute("DELETE FROM personal_dictionary")


def add_shared(term, replacement=""):
    with main.db() as conn:
        conn.execute(
            "INSERT INTO shared_dictionary(term,replacement) VALUES (?,?)",
            (term, replacement),
        )


def add_personal(email, term, replacement=""):
    with main.db() as conn:
        conn.execute(
            "INSERT INTO personal_dictionary(email,term,replacement,source) "
            "VALUES (?,?,?,'personal')",
            (email, term, replacement),
        )


class TranscriptionHintTests(unittest.TestCase):
    def setUp(self):
        clear_vocabulary()
        self.addCleanup(clear_vocabulary)

    def test_the_hint_spells_terms_the_way_they_must_be_written(self):
        """L'amorce donne la forme voulue : « Aéro 3 », jamais « Aero 3 »."""
        add_shared("ipsa", "IPSA")
        add_personal("eleve@example.com", "Aero 3", "Aéro 3")
        add_personal("eleve@example.com", "Baratto")

        hint = main.transcription_hint("eleve@example.com")

        for term in ("IPSA", "Aéro 3", "Baratto"):
            self.assertIn(term, hint)
        self.assertNotIn("Aero 3", hint)

    def test_another_member_s_vocabulary_is_not_used(self):
        add_personal("autre@example.com", "Zyxwvut")
        self.assertIsNone(main.transcription_hint("eleve@example.com"))

    def test_without_vocabulary_no_hint_is_sent(self):
        self.assertIsNone(main.transcription_hint("eleve@example.com"))

    def test_a_term_known_twice_appears_once(self):
        add_shared("IPSA")
        add_personal("eleve@example.com", "ipsa", "IPSA")
        self.assertEqual(main.transcription_hint("eleve@example.com").count("IPSA"), 1)

    def test_the_hint_stays_within_what_the_engine_reads(self):
        for index in range(300):
            add_personal("eleve@example.com", f"Terme{index:03d}")
        hint = main.transcription_hint("eleve@example.com")
        self.assertLessEqual(len(hint), main.TRANSCRIPTION_HINT_MAX_CHARS)


class PromptEchoTests(unittest.TestCase):
    HINT = "IPSA, Aéro 3, IONIS, Baratto."

    def test_the_hint_copied_back_is_not_a_dictation(self):
        self.assertTrue(main.is_hint_echo("IPSA, Aéro 3, IONIS, Baratto.", self.HINT))
        self.assertTrue(main.is_hint_echo("ipsa aéro 3 ionis", self.HINT))

    def test_a_dictation_using_the_terms_is_kept(self):
        self.assertFalse(
            main.is_hint_echo("Je transmets le dossier à l'IPSA pour Aéro 3.", self.HINT))

    def test_a_single_term_said_alone_is_kept(self):
        """Dire « IPSA » seul est une dictée possible ; on ne l'efface pas."""
        self.assertFalse(main.is_hint_echo("IPSA.", self.HINT))

    def test_without_hint_nothing_is_an_echo(self):
        self.assertFalse(main.is_hint_echo("IPSA, Aéro 3, IONIS", None))


class TranscribeRouteTests(AdminTestCase):
    def setUp(self):
        super().setUp()
        clear_vocabulary()
        self.addCleanup(clear_vocabulary)

    def dictate(self, engine_text):
        seen = {}

        async def engine(task_type, audio, filename, **options):
            seen.update(options)
            return SpeechInferenceResult(
                text=engine_text, language="fr", duration_seconds=1.0,
                latency_ms=1, backend={"adapter": "test"})

        token = self.sign_in("eleve@example.com")
        with patch.object(main.ORGANIZATION_AI_ROUTER, "transcribe", engine):
            response = self.client.post(
                "/api/transcribe", headers=self.auth(token),
                files={"file": ("recording.wav", b"RIFFaudio", "audio/wav")})
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()["text"], seen

    def test_by_default_the_engine_gets_no_hint(self):
        # Banc v2 du RTX (11/10) : l'amorce dégradait les deux Whisper.
        add_shared("ipsa", "IPSA")
        add_personal("eleve@example.com", "Aero 3", "Aéro 3")

        _, seen = self.dictate("bonjour")

        self.assertIsNone(seen.get("prompt"))

    def test_the_member_s_vocabulary_reaches_the_engine_when_enabled(self):
        add_shared("ipsa", "IPSA")
        add_personal("eleve@example.com", "Aero 3", "Aéro 3")

        with patch.object(main, "SPEECH_HINT_ENABLED", True):
            _, seen = self.dictate("bonjour")

        self.assertIn("IPSA", seen.get("prompt") or "")
        self.assertIn("Aéro 3", seen.get("prompt") or "")

    def test_the_engine_gets_no_hint_when_there_is_no_vocabulary(self):
        with patch.object(main, "SPEECH_HINT_ENABLED", True):
            _, seen = self.dictate("bonjour")
        self.assertIsNone(seen.get("prompt"))

    def test_the_vocabulary_is_still_applied_after_transcription(self):
        add_shared("ipsa", "IPSA")
        text, _ = self.dictate("le projet de l'ipsa")
        self.assertIn("IPSA", text)

    @patch.object(main, "SPEECH_HINT_ENABLED", True)
    def test_an_echoed_hint_comes_back_as_an_empty_dictation(self):
        add_shared("ipsa", "IPSA")
        add_personal("eleve@example.com", "Aero 3", "Aéro 3")
        add_personal("eleve@example.com", "IONIS")
        hint = main.transcription_hint("eleve@example.com")

        text, _ = self.dictate(hint)

        self.assertEqual(text, "")


if __name__ == "__main__":
    unittest.main()
