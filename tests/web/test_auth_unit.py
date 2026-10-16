"""Unit tests: token verification, settings safety, authorization policy."""
import base64
import hashlib
import hmac
import json
import secrets
import time
import unittest

from pydantic import ValidationError

from app.web.auth import AuthenticationFailed, HmacTokenAuthenticator, Principal, issue_token
from app.web.policy import Forbidden, OutOfScope, permissions_of, require
from app.web.settings import ConfigurationError, Settings, settings_from_env

SECRET = secrets.token_urlsafe(48)


def b64(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode()


def forge(claims: dict, secret: str = SECRET) -> str:
    """Correctly signed token with arbitrary claims (to test claim checks, not signatures)."""
    body = b64(json.dumps(claims).encode())
    signing_input = f"mdx1.{body}"
    signature = b64(hmac.new(secret.encode(), signing_input.encode(), hashlib.sha256).digest())
    return f"{signing_input}.{signature}"


def valid_claims(now, **overrides):
    claims = {"iss": "medrixa-local-dev", "aud": "medrixa-api-v1", "sub": "user-1", "roles": ["case_runner"],
              "cases": ["case-a"], "iat": now, "exp": now + 600}
    claims.update(overrides)
    return claims


class TokenTests(unittest.TestCase):
    def setUp(self):
        self.now = int(time.time())
        self.auth = HmacTokenAuthenticator(SECRET, max_ttl_seconds=3600, clock_skew_seconds=30,
                                           clock=lambda: self.now)

    def check(self, header):
        return self.auth.authenticate(header)

    def test_valid_token_yields_principal(self):
        principal = self.check(f"Bearer {issue_token(SECRET, 'user-1', ['case_runner'], ['case-a'], now=self.now)}")
        self.assertEqual(principal, Principal(subject="user-1", roles=frozenset({"case_runner"}),
                                              cases=frozenset({"case-a"})))

    def test_signature_and_format_failures_are_uniform(self):
        good = issue_token(SECRET, "user-1", ["case_runner"], ["case-a"], now=self.now)
        prefix, body, signature = good.split(".")
        # Always different from the real signature (a fixed suffix could coincide, ~1/1024).
        tampered = signature[:10] + ("B" if signature[10] == "A" else "A") + signature[11:]
        elevated = b64(json.dumps(valid_claims(self.now, roles=["operator"])).encode())
        headers = [None, "", good, f"Basic {good}", f"bearer {good}", f"Bearer {good} extra",
                   f"Bearer {prefix}.{body}.{tampered}", f"Bearer {prefix}.{elevated}.{signature}",
                   f"Bearer mdx0.{body}.{signature}", f"Bearer {body}.{signature}",
                   f"Bearer {issue_token('another-secret-' + 'x' * 40, 'user-1', ['case_runner'], now=self.now)}",
                   f"Bearer mdx1.!!!.{signature}", "Bearer " + "A" * 5000]
        for header in headers:
            with self.subTest(header=(header or "")[:40]), self.assertRaises(AuthenticationFailed) as caught:
                self.check(header)
            self.assertEqual(str(caught.exception), "")

    def test_claim_checks(self):
        cases = {
            "expired": valid_claims(self.now - 3600, exp=self.now - 31),
            "issued_in_future": valid_claims(self.now + 120),
            "ttl_above_max": valid_claims(self.now, exp=self.now + 7200),
            "exp_before_iat": valid_claims(self.now, exp=self.now - 1),
            "wrong_audience": valid_claims(self.now, aud="other-api"),
            "wrong_issuer": valid_claims(self.now, iss="someone-else"),
            "unknown_role": valid_claims(self.now, roles=["admin"]),
            "no_roles": valid_claims(self.now, roles=[]),
            "duplicate_case": valid_claims(self.now, cases=["case-a", "case-a"]),
            "extra_claim": {**valid_claims(self.now), "scope": "all"},
            "bad_subject": valid_claims(self.now, sub="../root"),
            "string_times": valid_claims(self.now, iat=str(self.now)),
        }
        for name, claims in cases.items():
            with self.subTest(name), self.assertRaises(AuthenticationFailed):
                self.check(f"Bearer {forge(claims)}")

    def test_clock_skew_tolerated_within_bound(self):
        self.assertEqual(self.check(f"Bearer {forge(valid_claims(self.now + 20))}").subject, "user-1")
        self.assertEqual(self.check(f"Bearer {forge(valid_claims(self.now - 600, exp=self.now - 10))}").subject,
                         "user-1")


class SettingsTests(unittest.TestCase):
    def test_production_and_unknown_environments_refused(self):
        for name in ("production", "prod", "", "staging"):
            with self.subTest(name=name), self.assertRaises(ConfigurationError):
                settings_from_env({"MEDRIXA_ENV": name, "MEDRIXA_AUTH_SECRET": SECRET})

    def test_weak_or_missing_secret_refused_without_echo(self):
        for secret in ("", "short", "a" * 64, "ab" * 32):
            with self.subTest(length=len(secret)), self.assertRaises(ConfigurationError) as caught:
                settings_from_env({"MEDRIXA_ENV": "test", "MEDRIXA_AUTH_SECRET": secret})
            if secret:
                self.assertNotIn(secret, str(caught.exception))

    def test_secret_hidden_in_repr_and_dump(self):
        settings = settings_from_env({"MEDRIXA_ENV": "test", "MEDRIXA_AUTH_SECRET": SECRET})
        self.assertNotIn(SECRET, repr(settings) + settings.model_dump_json())

    def test_wildcard_hosts_refused_and_docs_only_in_development(self):
        with self.assertRaises(ValidationError):
            Settings(environment="test", auth_secret=SECRET, allowed_hosts=("*",))
        self.assertFalse(Settings(environment="test", auth_secret=SECRET).expose_docs)
        self.assertTrue(Settings(environment="development", auth_secret=SECRET).expose_docs)


class PolicyTests(unittest.TestCase):
    def principal(self, *roles, cases=("case-a",)):
        return Principal(subject="u", roles=frozenset(roles), cases=frozenset(cases))

    def test_role_matrix_is_least_privilege(self):
        self.assertEqual(permissions_of(self.principal("case_runner")), {"runs:create", "runs:read"})
        self.assertEqual(permissions_of(self.principal("case_reviewer")), {"runs:read"})
        self.assertEqual(permissions_of(self.principal("operator")), {"runs:recover"})

    def test_default_deny_and_case_scope(self):
        runner, reviewer, operator = (self.principal(r) for r in ("case_runner", "case_reviewer", "operator"))
        require(runner, "runs:create", "case-a")
        with self.assertRaises(OutOfScope):
            require(runner, "runs:create", "case-b")
        with self.assertRaises(OutOfScope):
            require(runner, "runs:read", None)
        for principal, permission in ((reviewer, "runs:create"), (operator, "runs:read"),
                                      (runner, "runs:recover"), (runner, "runs:approve"), (runner, "")):
            with self.subTest(permission=permission), self.assertRaises(Forbidden):
                require(principal, permission, "case-a")
        require(operator, "runs:recover")


if __name__ == "__main__":
    unittest.main()
