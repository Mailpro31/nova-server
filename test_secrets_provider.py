"""Résolution des secrets : ce qu'elle accepte, ce qu'elle refuse, ce qu'elle tait."""

import os
import tempfile
import unittest

import secrets_provider as secrets


class EnvReferenceTests(unittest.TestCase):
    def setUp(self):
        os.environ["NOVA_TEST_SECRET_VALUE"] = "  s3cr3t-value  "

    def tearDown(self):
        os.environ.pop("NOVA_TEST_SECRET_VALUE", None)

    def test_an_env_reference_is_resolved_and_trimmed(self):
        resolved = secrets.resolve("env://NOVA_TEST_SECRET_VALUE")
        self.assertEqual(resolved.value, "s3cr3t-value")
        self.assertEqual(resolved.source, secrets.ENV)
        self.assertEqual(resolved.reference, "NOVA_TEST_SECRET_VALUE")

    def test_a_missing_or_empty_variable_is_an_error_not_an_empty_secret(self):
        os.environ["NOVA_TEST_SECRET_BLANK"] = "   "
        try:
            for reference in ("env://NOVA_TEST_SECRET_ABSENT", "env://NOVA_TEST_SECRET_BLANK"):
                with self.assertRaises(secrets.SecretError, msg=reference) as raised:
                    secrets.resolve(reference)
                self.assertEqual(raised.exception.code, "SECRET_NOT_FOUND")
        finally:
            os.environ.pop("NOVA_TEST_SECRET_BLANK", None)

    def test_a_malformed_variable_name_is_refused(self):
        for name in ("", "1BAD", "with space", "with-dash", "a;b"):
            with self.assertRaises(secrets.SecretError, msg=name):
                secrets.resolve(f"env://{name}")


class FileReferenceTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()

    def tearDown(self):
        self.directory.cleanup()

    def _write(self, content, name="secret"):
        path = os.path.join(self.directory.name, name)
        with open(path, "wb") as handle:
            handle.write(content)
        # Un secret correctement protege : lisible par son seul proprietaire.
        # Sans cela, le masque par defaut (souvent 022) le laisse lisible par
        # tous sous POSIX, et le fournisseur le refuse a juste titre. Le cas
        # d'un secret trop ouvert est teste a part, avec un chmod explicite.
        os.chmod(path, 0o600)
        return path

    def test_a_file_reference_is_resolved(self):
        path = self._write(b"file-secret-value")
        resolved = secrets.resolve("file://" + path.replace("\\", "/"))
        self.assertEqual(resolved.value, "file-secret-value")
        self.assertEqual(resolved.source, secrets.FILE)

    def test_a_trailing_newline_is_stripped(self):
        # `echo secret > file` en ajoute un, et le garder produirait un échec
        # d'authentification impossible à diagnostiquer.
        path = self._write(b"file-secret-value\n")
        self.assertEqual(secrets.resolve_value("file://" + path.replace("\\", "/")),
                         "file-secret-value")
        path = self._write(b"file-secret-value\r\n", name="crlf")
        self.assertEqual(secrets.resolve_value("file://" + path.replace("\\", "/")),
                         "file-secret-value")

    def test_internal_whitespace_is_preserved(self):
        # Seules les fins de ligne sont retirées : un secret peut légitimement
        # contenir des espaces.
        path = self._write(b"a b c\n")
        self.assertEqual(secrets.resolve_value("file://" + path.replace("\\", "/")), "a b c")

    def test_a_relative_path_is_refused(self):
        with self.assertRaises(secrets.SecretError) as raised:
            secrets.resolve("file://relative/secret")
        self.assertEqual(raised.exception.code, "SECRET_PATH_NOT_ABSOLUTE")

    def test_a_missing_file_is_reported_without_leaking_the_path(self):
        absent = os.path.join(self.directory.name, "does-not-exist").replace("\\", "/")
        with self.assertRaises(secrets.SecretError) as raised:
            secrets.resolve("file://" + absent)
        self.assertEqual(raised.exception.code, "SECRET_NOT_FOUND")
        self.assertNotIn(self.directory.name.replace("\\", "/"), str(raised.exception))

    def test_a_directory_is_not_a_secret(self):
        with self.assertRaises(secrets.SecretError):
            secrets.resolve("file://" + self.directory.name.replace("\\", "/"))

    def test_an_oversized_file_is_refused(self):
        path = self._write(b"x" * (secrets.MAX_SECRET_BYTES + 10))
        with self.assertRaises(secrets.SecretError) as raised:
            secrets.resolve("file://" + path.replace("\\", "/"))
        self.assertEqual(raised.exception.code, "SECRET_TOO_LARGE")

    def test_an_empty_file_is_refused(self):
        path = self._write(b"\n")
        with self.assertRaises(secrets.SecretError) as raised:
            secrets.resolve("file://" + path.replace("\\", "/"))
        self.assertEqual(raised.exception.code, "SECRET_EMPTY")

    def test_a_non_utf8_file_is_refused(self):
        path = self._write(b"\xff\xfe\x00binary")
        with self.assertRaises(secrets.SecretError) as raised:
            secrets.resolve("file://" + path.replace("\\", "/"))
        self.assertEqual(raised.exception.code, "SECRET_ENCODING_INVALID")

    @unittest.skipUnless(os.name == "posix", "file modes are meaningful on POSIX only")
    def test_a_world_readable_secret_is_refused(self):
        path = self._write(b"too-open")
        os.chmod(path, 0o644)
        with self.assertRaises(secrets.SecretError) as raised:
            secrets.resolve("file://" + path)
        self.assertEqual(raised.exception.code, "SECRET_PERMISSIONS_TOO_OPEN")

    def test_only_the_basename_is_reported_not_the_production_layout(self):
        path = self._write(b"value", name="microsoft_client_secret")
        resolved = secrets.resolve("file://" + path.replace("\\", "/"))
        self.assertEqual(resolved.reference, "microsoft_client_secret")
        self.assertNotIn("/", resolved.reference)


class LiteralAndSchemeTests(unittest.TestCase):
    def test_a_literal_value_still_works_for_existing_deployments(self):
        resolved = secrets.resolve("plain-inline-secret")
        self.assertEqual(resolved.value, "plain-inline-secret")
        self.assertEqual(resolved.source, secrets.LITERAL)

    def test_a_literal_can_be_refused_where_a_reference_is_required(self):
        with self.assertRaises(secrets.SecretError) as raised:
            secrets.resolve("plain-inline-secret", allow_literal=False)
        self.assertEqual(raised.exception.code, "SECRET_LITERAL_REFUSED")

    def test_an_unknown_scheme_is_refused_not_taken_for_a_literal(self):
        # Sans cela, une faute de frappe dans `env://` deviendrait un secret
        # littéral valant « env:/NOVA_... », et l'authentification échouerait
        # sans que rien ne signale la cause.
        for reference in ("vault://path/to/secret", "https://example/secret", "aws://x"):
            with self.assertRaises(secrets.SecretError, msg=reference) as raised:
                secrets.resolve(reference)
            self.assertEqual(raised.exception.code, "SECRET_SCHEME_UNSUPPORTED")

    def test_absence_is_not_an_error(self):
        for reference in (None, "", "   "):
            self.assertIsNone(secrets.resolve(reference))


class LeakageTests(unittest.TestCase):
    def setUp(self):
        os.environ["NOVA_TEST_LEAK_SECRET"] = "SECRET_TOKEN_MUST_NOT_APPEAR"

    def tearDown(self):
        os.environ.pop("NOVA_TEST_LEAK_SECRET", None)

    def test_the_value_never_appears_in_repr_or_str(self):
        resolved = secrets.resolve("env://NOVA_TEST_LEAK_SECRET")
        self.assertNotIn("SECRET_TOKEN_MUST_NOT_APPEAR", repr(resolved))
        self.assertNotIn("SECRET_TOKEN_MUST_NOT_APPEAR", str(resolved))
        self.assertNotIn("SECRET_TOKEN_MUST_NOT_APPEAR", f"{resolved}")
        # La valeur reste évidemment accessible quand on la demande.
        self.assertEqual(resolved.value, "SECRET_TOKEN_MUST_NOT_APPEAR")

    def test_describe_reports_provenance_without_the_value(self):
        described = secrets.describe("env://NOVA_TEST_LEAK_SECRET")
        self.assertEqual(described["configured"], True)
        self.assertEqual(described["source"], secrets.ENV)
        self.assertEqual(described["reference"], "NOVA_TEST_LEAK_SECRET")
        self.assertNotIn("SECRET_TOKEN_MUST_NOT_APPEAR", str(described))

    def test_describe_reports_a_broken_reference_as_a_code(self):
        described = secrets.describe("env://NOVA_TEST_ABSENT_SECRET")
        self.assertEqual(described["error"], "SECRET_NOT_FOUND")

    def test_describe_of_nothing_says_not_configured(self):
        self.assertEqual(secrets.describe(None)["configured"], False)


if __name__ == "__main__":
    unittest.main()
