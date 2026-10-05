"""Duree maximale d'une dictee, reglable par l'organisation.

Une dictee de quarante minutes occupe le moteur de transcription pendant que
les autres membres attendent. L'organisation fixe donc une duree maximale —
dix minutes par defaut — que le poste applique en prevenant avant la fin, et
que le serveur fait respecter a son tour : un poste ancien ou modifie ne doit
pas pouvoir la contourner.

La dictee elle-meme reste ouverte a tous : cette cle borne une duree, elle ne
ferme jamais la route.
"""

import io
import json
import unittest
import wave
from unittest.mock import patch

from test_main import AdminTestCase  # noqa: F401

import main

main.init_db()


def clear_policies():
    with main.db() as conn:
        conn.execute("DELETE FROM organization_policies")


def wav_of(seconds: float, rate: int = 8000) -> bytes:
    """Un vrai WAV mono 8 bits : l'en-tete porte la duree que le serveur lit."""
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as output:
        output.setnchannels(1)
        output.setsampwidth(1)
        output.setframerate(rate)
        output.writeframes(b"\x80" * int(seconds * rate))
    return buffer.getvalue()


class DictationLimitSchemaTests(unittest.TestCase):
    def test_the_limit_is_a_declared_integer_of_ten_minutes(self):
        setting = main.POLICY_SETTINGS["max_dictation_seconds"]
        self.assertIs(setting.kind, int)
        self.assertEqual(setting.default, 600)
        self.assertEqual(main.DEFAULT_ORGANIZATION_POLICY["max_dictation_seconds"], 600)

    def test_a_value_within_bounds_is_kept(self):
        self.assertEqual(
            main.validate_policy_settings({"max_dictation_seconds": 120}),
            {"max_dictation_seconds": 120},
        )

    def test_the_default_stores_no_override(self):
        self.assertEqual(main.validate_policy_settings({"max_dictation_seconds": 600}), {})

    def test_a_value_outside_bounds_or_of_the_wrong_type_is_refused(self):
        for value in (29, 7201, 0, -1, True, False, "600", 60.5, None):
            with self.subTest(value=value):
                with self.assertRaises(ValueError) as raised:
                    main.validate_policy_settings({"max_dictation_seconds": value})
                self.assertIn("POLICY_VALUE_INVALID:max_dictation_seconds",
                              str(raised.exception))

    def test_a_stored_value_outside_bounds_falls_back_to_the_default(self):
        overrides = main.read_policy_settings(json.dumps({"max_dictation_seconds": 5}))
        self.assertEqual(overrides, {})

    def test_the_limit_governs_no_capability(self):
        """Une duree ne ferme rien : aucune capacite ne depend de cette cle."""
        self.assertIsNone(main.POLICY_SETTINGS["max_dictation_seconds"].capability)


class DictationLimitDeliveryTests(AdminTestCase):
    PATH = "/api/control/organization/policy"

    def setUp(self):
        super().setUp()
        clear_policies()

    def set_limit(self, seconds: int):
        token = self.admin_session()
        revision = self.client.get(self.PATH, headers=self.auth(token)).json()["revision"]
        response = self.client.put(
            self.PATH, headers=self.auth(token),
            json={"expected_revision": revision,
                  "settings": {"max_dictation_seconds": seconds}})
        self.assertEqual(response.status_code, 200, response.text)

    def dictate(self, audio: bytes):
        async def engine(file, user, task_type, max_bytes, **options):
            return {"text": "bonjour"}

        token = self.sign_in("etudiant@example.com")
        with patch.object(main, "transcribe_audio", engine):
            return self.client.post(
                "/api/transcribe", headers=self.auth(token),
                files={"file": ("recording.wav", audio, "audio/wav")})

    def test_the_workstation_learns_the_default_limit(self):
        self.assertEqual(
            main.public_campus_config()["limits"], {"max_dictation_seconds": 600})

    def test_the_workstation_learns_the_limit_the_organization_chose(self):
        self.set_limit(120)
        self.assertEqual(
            main.public_campus_config()["limits"]["max_dictation_seconds"], 120)

    def test_a_signed_in_member_receives_the_limit_of_their_organization(self):
        """Le poste connecte lit `/api/me`, pas `/api/config` : la limite doit
        y figurer, calculee pour l'organisation du membre."""
        self.set_limit(90)
        token = self.sign_in("etudiant@example.com")
        response = self.client.get("/api/me", headers=self.auth(token))
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["limits"], {"max_dictation_seconds": 90})

    def test_a_dictation_within_the_limit_is_transcribed(self):
        self.set_limit(60)
        self.assertEqual(self.dictate(wav_of(30)).status_code, 200)

    def test_a_small_overrun_is_tolerated(self):
        """Le poste arrete l'enregistrement a la limite ; le temps de l'arret
        et l'en-tete ajoutent quelques fractions de seconde."""
        self.set_limit(60)
        self.assertEqual(self.dictate(wav_of(62)).status_code, 200)

    def test_a_dictation_beyond_the_limit_is_refused_before_the_engine(self):
        self.set_limit(60)
        response = self.dictate(wav_of(90))
        self.assertEqual(response.status_code, 413, response.text)
        detail = response.json()["detail"]
        self.assertEqual(detail["code"], "DICTATION_TOO_LONG")
        self.assertEqual(detail["max_seconds"], 60)

    def test_audio_whose_length_cannot_be_read_is_left_to_the_size_bound(self):
        """Refuser ce qu'on ne sait pas mesurer couperait la dictee d'un client
        qui envoie un autre format ; la borne en octets s'applique toujours."""
        self.set_limit(30)
        self.assertEqual(self.dictate(b"RIFF0000WAVE").status_code, 200)

    def test_file_import_is_not_bound_by_the_dictation_limit(self):
        self.set_limit(30)

        async def engine(file, user, task_type, max_bytes, **options):
            return {"text": "bonjour"}

        token = self.sign_in("etudiant@example.com")
        with patch.object(main, "transcribe_audio", engine):
            response = self.client.post(
                "/api/import/transcribe", headers=self.auth(token),
                files={"file": ("meeting.wav", wav_of(90), "audio/wav")})
        self.assertEqual(response.status_code, 200, response.text)


if __name__ == "__main__":
    unittest.main()
