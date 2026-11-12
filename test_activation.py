"""Code d'activation à usage unique : échange, signature, persistance."""

import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

import httpx

import activation
import licence

PRIVATE, PUBLIC = licence.new_key_pair()
OTHER_PRIVATE, _ = licence.new_key_pair()
FIELDS = dict(organization="IPSA", tier="151-250", kind="subscription",
              starts="2026-09-01", ends="2027-08-31")
URL = "https://activation.example"


class FakeService:
    """Le service d'activation, en mémoire : même règles que le Worker."""

    def __init__(self):
        self.codes = {}  # hash -> [licence, expires_at, used]
        self.calls = 0

    def add(self, code, token, expires_at):
        self.codes[licence.code_hash(code)] = [token, expires_at, False]

    def post(self, url, json=None, timeout=None, headers=None):
        self.calls += 1
        request = httpx.Request("POST", url)
        if url.endswith("/codes"):
            self.add_hash = json["code_hash"]
            self.codes[json["code_hash"]] = [json["licence"], json["expires_at"], False]
            self.admin_header = headers["authorization"]
            return httpx.Response(201, json={"ok": True}, request=request)
        entry = self.codes.get(licence.code_hash(json["code"]))
        if not entry or entry[2] or entry[1] <= time.time():
            return httpx.Response(403, json={"error": "invalid_code"}, request=request)
        entry[2] = True
        return httpx.Response(200, json={"licence": entry[0]}, request=request)


class ActivationTests(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.dir.cleanup)
        self.store = Path(self.dir.name) / "licence.txt"
        self.service = FakeService()
        patcher = mock.patch.object(licence, "PUBLIC_KEY_PEM", PUBLIC)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.token = licence.issue(PRIVATE, **FIELDS)

    def resolve(self, code="NOVA-7K2P-91XQ", env_licence=""):
        return activation.resolve_licence(env_licence, code, URL, self.store, post=self.service.post)

    def test_the_code_gives_the_licence_and_is_remembered(self):
        self.service.add("NOVA-7K2P-91XQ", self.token, time.time() + 3600)
        self.assertEqual(self.resolve(), self.token)
        calls = self.service.calls
        # Redémarrage : la licence enregistrée suffit, le code n'est plus redemandé.
        self.assertEqual(self.resolve(), self.token)
        self.assertEqual(self.service.calls, calls)

    def test_a_reused_code_is_refused(self):
        self.service.add("NOVA-7K2P-91XQ", self.token, time.time() + 3600)
        self.assertEqual(self.resolve(), self.token)
        self.store.unlink()  # un autre serveur tente le même code
        self.assertEqual(self.resolve(), "")
        self.assertFalse(self.store.exists())

    def test_a_forged_licence_is_refused(self):
        forged = licence.issue(OTHER_PRIVATE, **FIELDS)
        self.service.add("NOVA-7K2P-91XQ", forged, time.time() + 3600)
        self.assertEqual(self.resolve(), "")
        self.assertFalse(self.store.exists())

    def test_an_expired_code_is_refused(self):
        self.service.add("NOVA-7K2P-91XQ", self.token, time.time() - 5)
        self.assertEqual(self.resolve(), "")

    def test_a_tampered_stored_licence_is_ignored(self):
        self.store.write_text(licence.issue(OTHER_PRIVATE, **FIELDS), encoding="utf-8")
        self.assertEqual(self.resolve(code=""), "")

    def test_nova_licence_stays_first(self):
        self.service.add("NOVA-7K2P-91XQ", self.token, time.time() + 3600)
        self.assertEqual(self.resolve(env_licence="LA.LICENCE.EXISTANTE"), "LA.LICENCE.EXISTANTE")
        self.assertEqual(self.service.calls, 0)

    def test_a_down_service_changes_nothing(self):
        def down(*args, **kwargs):
            raise httpx.ConnectError("réseau coupé")
        self.assertEqual(activation.resolve_licence("", "NOVA-7K2P-91XQ", URL, self.store, post=down), "")

    def test_no_code_no_call(self):
        self.assertEqual(self.resolve(code=""), "")
        self.assertEqual(self.service.calls, 0)


class IssuerTests(unittest.TestCase):
    def test_the_code_is_short_and_hashed_without_dashes_or_case(self):
        code = licence.new_activation_code()
        self.assertRegex(code, r"^NOVA-[2-9A-HJKMNP-Z]{4}-[2-9A-HJKMNP-Z]{4}$")
        self.assertEqual(licence.code_hash(code), licence.code_hash(code.lower().replace("-", " ")))

    def test_the_cli_signs_locally_and_sends_only_hash_and_signed_licence(self):
        service = FakeService()
        key = Path(tempfile.mkdtemp()) / "nova.key"
        key.write_text(PRIVATE, encoding="utf-8")
        argv = ["activation-code", str(key), "--url", URL, *sum(
            ([f"--{k}", v] for k, v in FIELDS.items()), [])]
        env = {"NOVA_ACTIVATION_ADMIN_TOKEN": "jeton-admin"}
        with mock.patch.dict("os.environ", env), \
                mock.patch.object(licence.httpx, "post", service.post), \
                mock.patch("builtins.print") as printed:
            self.assertEqual(licence._cli(argv), 0)
        shown = " ".join(str(call.args[0]) for call in printed.call_args_list)
        code = shown.split("    ")[1].split()[0]
        self.assertEqual(service.admin_header, "Bearer jeton-admin")
        self.assertEqual(service.add_hash, licence.code_hash(code))
        sent_licence, _, _ = service.codes[service.add_hash]
        self.assertNotIn("jeton-admin", shown)
        self.assertNotIn("PRIVATE KEY", sent_licence)
        self.assertEqual(licence.licence_state(sent_licence, public_pem=PUBLIC)["status"], "valid"
                         if time.time() < licence.day_stamp("2027-08-01") else "warning")

    def test_the_cli_refuses_without_the_admin_token(self):
        key = Path(tempfile.mkdtemp()) / "nova.key"
        key.write_text(PRIVATE, encoding="utf-8")
        argv = ["activation-code", str(key), "--url", URL, *sum(
            ([f"--{k}", v] for k, v in FIELDS.items()), [])]
        with mock.patch.dict("os.environ", {"NOVA_ACTIVATION_ADMIN_TOKEN": "",
                                            "NOVA_ACTIVATION_ADMIN_TOKEN_FILE": ""}):
            self.assertEqual(licence._cli(argv), 1)


if __name__ == "__main__":
    unittest.main()
