"""La licence signée (4.1) : créée sur le PC de l'éditeur, vérifiée ici."""

import unittest
from unittest import mock

import licence

DAY = 86400
NOW = 1_800_000_000.0


def keys() -> tuple[str, str]:
    return licence.new_key_pair()


def issued(private_pem: str, **fields) -> str:
    return licence.issue(private_pem, **{
        "organization": "IPSA", "tier": "151-250", "kind": "subscription",
        "starts": "2026-09-01", "ends": "2027-08-31", **fields})


class LicenceTests(unittest.TestCase):
    def setUp(self):
        self.private_pem, self.public_pem = keys()

    def state(self, token: str, now: float) -> dict:
        return licence.licence_state(token, now, public_pem=self.public_pem)

    def test_no_licence_changes_nothing(self):
        state = self.state("", NOW)
        self.assertEqual(state["status"], "none")
        self.assertFalse(state["degraded"])

    def test_a_valid_licence_is_read(self):
        state = self.state(issued(self.private_pem), licence.day_stamp("2026-10-01"))
        self.assertEqual(state["status"], "valid")
        self.assertEqual((state["organization"], state["tier"], state["kind"]),
                         ("IPSA", "151-250", "subscription"))
        self.assertEqual(state["ends"], "2027-08-31")
        self.assertFalse(state["degraded"])

    def test_a_forged_licence_is_refused(self):
        other_private, _ = keys()
        forged = issued(other_private, tier="251-400")
        self.assertEqual(self.state(forged, NOW)["status"], "invalid")
        # Une licence modifiée à la main n'est plus signée.
        token = issued(self.private_pem)
        head, body, signature = token.split(".")
        self.assertEqual(self.state(f"{head}.{body[:-2]}xx.{signature}", NOW)["status"],
                         "invalid")
        self.assertEqual(self.state("pas une licence", NOW)["status"], "invalid")

    def test_warning_then_grace_then_degraded(self):
        token = issued(self.private_pem)
        ends = licence.day_stamp("2027-08-31")
        self.assertEqual(self.state(token, ends - 10 * DAY)["status"], "warning")
        grace = self.state(token, ends + 10 * DAY)
        self.assertEqual(grace["status"], "grace")
        self.assertFalse(grace["degraded"])
        expired = self.state(token, ends + (licence.GRACE_DAYS + 2) * DAY)
        self.assertEqual(expired["status"], "expired")
        self.assertTrue(expired["degraded"])

    def test_an_invalid_licence_does_not_degrade(self):
        # Une faute de copie de la DSI ne coupe rien : la console la signale.
        self.assertFalse(self.state("pas une licence", NOW)["degraded"])

    def test_the_embedded_key_is_used_by_default(self):
        token = issued(self.private_pem)
        with mock.patch.object(licence, "PUBLIC_KEY_PEM", self.public_pem):
            self.assertEqual(
                licence.licence_state(token, licence.day_stamp("2026-10-01"))["status"],
                "valid")

    def test_the_online_console_key_is_accepted_too(self):
        online_private, online_public = licence.new_key_pair()
        stranger_private, _ = licence.new_key_pair()
        at = licence.day_stamp("2026-10-01")
        with mock.patch.object(licence, "PUBLIC_KEY_PEM", self.public_pem),                 mock.patch.object(licence, "ONLINE_PUBLIC_KEY_PEM", online_public):
            self.assertEqual(licence.licence_state(issued(online_private), at)["status"], "valid")
            self.assertEqual(licence.licence_state(issued(self.private_pem), at)["status"], "valid")
            self.assertEqual(licence.licence_state(issued(stranger_private), at)["status"], "invalid")


class DegradedModeTests(unittest.TestCase):
    """Licence échue depuis plus que le délai de grâce : la bulle et les
    commandes s'arrêtent, jamais la dictée."""

    def test_only_extras_stop(self):
        from fastapi import HTTPException

        import main

        with mock.patch.object(main, "licence_degraded", return_value=True):
            with self.assertRaises(HTTPException) as refused:
                main.refuse_when_licence_degraded(("commands", "selectionBubble"), "voice_commands")
            self.assertEqual(refused.exception.detail["code"], "LICENCE_EXPIRED")
            main.refuse_when_licence_degraded("dictionary", "organization_vocabulary")


if __name__ == "__main__":
    unittest.main()
