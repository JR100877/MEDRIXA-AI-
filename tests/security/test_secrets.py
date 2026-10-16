"""P7.1: secret provider port, mounted secret files and settings integration."""
import os
import secrets
import stat
import tempfile
import unittest
from pathlib import Path

from app.security.secrets import (MAX_SECRET_FILE_BYTES, EnvironmentSecretProvider, SecretError,
                                  StaticSecretProvider, read_secret_file)
from app.web.settings import ConfigurationError, settings_from_env

VALUE = "Mounted-Secret-" + secrets.token_urlsafe(32)


class SecretFileTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.dir = Path(directory.name)

    def write(self, name, data: bytes, mode=0o600):
        path = self.dir / name
        path.write_bytes(data)
        os.chmod(path, mode)
        return path

    def assert_refused(self, path, label="X_FILE"):
        with self.assertRaises(SecretError) as caught:
            read_secret_file(path, label)
        self.assertIn(label, str(caught.exception))
        self.assertNotIn(VALUE, str(caught.exception))

    def test_reads_value_and_strips_one_trailing_newline(self):
        for data, expected in ((VALUE.encode(), VALUE), (VALUE.encode() + b"\n", VALUE),
                               (VALUE.encode() + b"\r\n", VALUE), (VALUE.encode() + b"\n\n", VALUE + "\n")):
            with self.subTest(data=data[-3:]):
                self.assertEqual(read_secret_file(self.write("s", data)).get_secret_value(), expected)

    def test_kubernetes_style_symlink_and_read_only_modes_accepted(self):
        target = self.write("..data_secret", VALUE.encode(), mode=0o444)
        link = self.dir / "secret"
        link.symlink_to(target)
        self.assertEqual(read_secret_file(link).get_secret_value(), VALUE)

    def test_unsafe_or_invalid_files_refused_without_echo(self):
        self.assert_refused(self.write("gw", VALUE.encode(), mode=0o620))
        self.assert_refused(self.write("ow", VALUE.encode(), mode=0o602))
        self.assert_refused(self.write("empty", b""))
        self.assert_refused(self.write("newline-only", b"\n"))
        self.assert_refused(self.write("big", b"x" * (MAX_SECRET_FILE_BYTES + 1)))
        self.assert_refused(self.write("bin", b"\xff\xfe" + VALUE.encode()))
        self.assert_refused(self.dir / "missing")
        self.assert_refused(self.dir)  # a directory
        (self.dir / "dangling").symlink_to(self.dir / "nowhere")
        self.assert_refused(self.dir / "dangling")

    @unittest.skipIf(os.geteuid() == 0, "root bypasses read permissions")
    def test_unreadable_file_refused(self):
        self.assert_refused(self.write("noread", VALUE.encode(), mode=0o000))


class ProviderTests(unittest.TestCase):
    def test_environment_direct_file_and_ambiguity(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "auth"
            path.write_text(VALUE + "\n")
            os.chmod(path, 0o400)
            self.assertEqual(EnvironmentSecretProvider({"MEDRIXA_AUTH_SECRET_FILE": str(path)})
                             .get("AUTH_SECRET").get_secret_value(), VALUE)
            self.assertEqual(EnvironmentSecretProvider({"MEDRIXA_AUTH_SECRET": VALUE})
                             .get("AUTH_SECRET").get_secret_value(), VALUE)
            self.assertIsNone(EnvironmentSecretProvider({}).get("AUTH_SECRET"))
            with self.assertRaises(SecretError) as caught:
                EnvironmentSecretProvider({"MEDRIXA_AUTH_SECRET": VALUE,
                                           "MEDRIXA_AUTH_SECRET_FILE": str(path)}).get("AUTH_SECRET")
            self.assertNotIn(VALUE, str(caught.exception))

    def test_names_are_restricted(self):
        for bad in ("", "auth", "../X", "A-B"):
            with self.subTest(name=bad), self.assertRaises(ValueError):
                EnvironmentSecretProvider({}).get(bad)

    def test_secret_values_never_appear_in_repr(self):
        provider = StaticSecretProvider({"AUTH_SECRET": VALUE})
        value = provider.get("AUTH_SECRET")
        self.assertNotIn(VALUE, repr(value) + str(value) + repr(provider))


class SettingsIntegrationTests(unittest.TestCase):
    def test_settings_read_secrets_from_files(self):
        with tempfile.TemporaryDirectory() as directory:
            auth, dsn = Path(directory) / "auth", Path(directory) / "dsn"
            auth.write_text(VALUE)
            dsn.write_text("host=/nowhere dbname=x password=SECRET_MARKER_DSN_FILE\n")
            for path in (auth, dsn):
                os.chmod(path, 0o400)
            settings = settings_from_env({"MEDRIXA_ENV": "test", "MEDRIXA_AUTH_SECRET_FILE": str(auth),
                                          "MEDRIXA_PG_DSN_FILE": str(dsn)})
            self.assertEqual(settings.auth_secret.get_secret_value(), VALUE)
            self.assertTrue(settings.postgres_dsn.get_secret_value().endswith("SECRET_MARKER_DSN_FILE"))
            self.assertNotIn("SECRET_MARKER_DSN_FILE", repr(settings) + settings.model_dump_json())
            os.chmod(auth, 0o622)
            with self.assertRaises(ConfigurationError) as caught:
                settings_from_env({"MEDRIXA_ENV": "test", "MEDRIXA_AUTH_SECRET_FILE": str(auth)})
            self.assertIn("MEDRIXA_AUTH_SECRET_FILE", str(caught.exception))
            self.assertNotIn(VALUE, str(caught.exception))

    def test_custom_provider_replaces_environment_secrets(self):
        settings = settings_from_env({"MEDRIXA_ENV": "test", "MEDRIXA_AUTH_SECRET": "ignored-" + VALUE},
                                     secrets=StaticSecretProvider({"AUTH_SECRET": VALUE}))
        self.assertEqual(settings.auth_secret.get_secret_value(), VALUE)

    def test_weak_secret_from_file_still_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "weak"
            path.write_text("short")
            os.chmod(path, stat.S_IRUSR)
            with self.assertRaises(ConfigurationError):
                settings_from_env({"MEDRIXA_ENV": "test", "MEDRIXA_AUTH_SECRET_FILE": str(path)})


if __name__ == "__main__":
    unittest.main()
