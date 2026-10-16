"""P7.2: OIDC/JWT authentication against a real local JWKS endpoint with real keys."""
import base64
import hashlib
import hmac
import json
import time
import unittest
import urllib.error

from cryptography.hazmat.primitives import serialization
from pydantic import ValidationError

from app.web.auth import AuthenticationFailed
from app.web.oidc import JwksCache, OidcJwtAuthenticator, fetch_jwks
from tests.security.oidc_support import AUDIENCE, ISSUER, ORG, FakeClock, JwksServer, SigningKey, oidc_settings


def b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


class OidcTestCase(unittest.TestCase):
    def setUp(self):
        self.rsa = SigningKey("RSA")
        self.ec = SigningKey("EC")
        self.server = JwksServer([self.rsa, self.ec])
        self.addCleanup(self.server.close)
        self.clock = FakeClock()

    def authenticator(self, **overrides):
        settings = oidc_settings(self.server.url, **overrides)
        return OidcJwtAuthenticator(settings, JwksCache(settings, clock=self.clock))

    def ok(self, token, auth=None):
        return (auth or self.authenticator()).authenticate(f"Bearer {token}")

    def refused(self, token, auth=None, header=None):
        with self.assertRaises(AuthenticationFailed) as caught:
            (auth or self.authenticator()).authenticate(header if header is not None else f"Bearer {token}")
        self.assertEqual(str(caught.exception), "")


class ValidationTests(OidcTestCase):
    def test_valid_rsa_pss_and_ec_tokens(self):
        auth = self.authenticator()
        for token in (self.rsa.sign(), self.rsa.sign(alg="PS256"), self.ec.sign()):
            principal = self.ok(token, auth)
            self.assertEqual((principal.subject, principal.roles, principal.cases, principal.organization),
                             ("user-oidc-1", frozenset({"case_runner"}), frozenset({"case-a"}), ORG))
        self.assertEqual(self.server.hits, 1)  # one fetch, then cache

    def test_claim_failures(self):
        now = int(time.time())
        cases = {
            "wrong_issuer": dict(iss="https://evil.example/realms/medrixa"),
            "issuer_prefix_trick": dict(iss=ISSUER + "/extra"),
            "wrong_audience": dict(aud="another-api"),
            "expired": dict(iat=now - 7200, exp=now - 3600),
            "not_yet_valid": dict(nbf=now + 3600),
            "issued_in_future": dict(iat=now + 3600, exp=now + 4000),
            "lifetime_too_long": dict(iat=now, exp=now + 7200),
            "missing_exp": dict(exp=None),
            "missing_iat": dict(iat=None),
            "missing_sub": dict(sub=None),
            "missing_aud": dict(aud=None),
            "missing_iss": dict(iss=None),
            "float_times": dict(iat=float(now), exp=float(now + 60)),
            "other_organization": dict(medrixa_org="org-other"),
            "missing_organization": dict(medrixa_org=None),
            "no_medrixa_role": dict(medrixa_roles=["admin", "superuser"]),
            "roles_not_list": dict(medrixa_roles={"case_runner": True}),
            "invalid_case": dict(medrixa_cases=["../etc"]),
            "duplicate_case": dict(medrixa_cases=["case-a", "case-a"]),
            "too_many_cases": dict(medrixa_cases=[f"c{i}" for i in range(101)]),
            "empty_sub": dict(sub=""),
            "numeric_sub": dict(sub=12345),
        }
        auth = self.authenticator()
        for name, overrides in cases.items():
            with self.subTest(name):
                self.refused(self.rsa.sign(**overrides), auth)

    def test_header_and_algorithm_attacks(self):
        auth = self.authenticator()
        good = self.rsa.sign()
        head, body, sig = good.split(".")
        payload = json.loads(base64.urlsafe_b64decode(body + "=="))
        payload["medrixa_roles"] = ["operator"]
        forged_body = b64(json.dumps(payload).encode())
        none_token = b64(json.dumps({"alg": "none", "kid": self.rsa.kid}).encode()) + "." + body + "."
        pem = self.rsa.private.public_key().public_bytes(serialization.Encoding.PEM,
                                                         serialization.PublicFormat.SubjectPublicKeyInfo)
        attacker = SigningKey("RSA", kid=self.rsa.kid)  # same kid, different key
        # Classic key-confusion attack, crafted by hand (PyJWT refuses to produce it):
        # HS256 "signed" with the RSA public key PEM as the HMAC secret, valid claims.
        hs_head = b64(json.dumps({"alg": "HS256", "kid": self.rsa.kid, "typ": "JWT"}).encode())
        signing_input = f"{hs_head}.{body}".encode()
        hs_confusion = f"{hs_head}.{body}.{b64(hmac.new(pem, signing_input, hashlib.sha256).digest())}"
        tokens = {
            "tampered_payload": f"{head}.{forged_body}.{sig}",
            "tampered_signature": f"{head}.{body}.{sig[:10]}{'B' if sig[10] == 'A' else 'A'}{sig[11:]}",
            "alg_none": none_token,
            "hs256_with_public_key": hs_confusion,
            "alg_not_allowed": self.rsa.sign(alg="RS384"),
            "unknown_kid": self.rsa.sign(kid="does-not-exist"),
            "missing_kid": self.rsa.sign(kid=False),
            "attacker_key_same_kid": attacker.sign(),
            "ec_kid_with_rsa_alg": self.ec.sign(alg="ES256", kid=self.rsa.kid),
            "rsa_kid_with_ec_header": self.rsa.sign(alg="RS256", kid=self.ec.kid),
            "crit_header": self.rsa.sign(headers={"crit": ["exp"]}),
        }
        for name, token in tokens.items():
            with self.subTest(name):
                self.refused(token, auth)
        for header in (None, "", f"Basic {good}", f"bearer {good}", f"Bearer {good} x", "Bearer " + "a" * 9000,
                       "Bearer not.a.jwt", "Bearer onlyone"):
            with self.subTest(header=(header or "")[:20]):
                self.refused(None, auth, header=header or "")

    def test_declared_jwk_alg_must_match(self):
        pinned = SigningKey("RSA", alg="RS256", declare_alg=True)
        self.server.keys.append(pinned)
        auth = self.authenticator()
        self.ok(pinned.sign(), auth)
        self.refused(pinned.sign(alg="PS256"), auth)  # same RSA key, other alg than the JWK declares

    def test_subject_mapping_and_nested_roles(self):
        auth = self.authenticator(roles_claim="realm_access.roles")
        principal = self.ok(self.rsa.sign(sub="auth0|65f0c1", medrixa_roles=None,
                                          claims={"realm_access": {"roles": ["offline_access", "case_reviewer"]}}),
                            auth)
        self.assertRegex(principal.subject, r"^sub-[0-9a-f]{40}$")
        again = self.ok(self.rsa.sign(sub="auth0|65f0c1", medrixa_roles=None,
                                      claims={"realm_access": {"roles": ["case_reviewer"]}}), auth)
        self.assertEqual(principal.subject, again.subject)
        self.assertEqual(principal.roles, frozenset({"case_reviewer"}))
        spaced = self.ok(self.rsa.sign(medrixa_roles="case_runner case_reviewer"))
        self.assertEqual(spaced.roles, frozenset({"case_runner", "case_reviewer"}))


class JwksTests(OidcTestCase):
    def test_key_rotation(self):
        auth = self.authenticator(jwks_cache_seconds=60, jwks_min_refresh_seconds=5)
        self.ok(self.rsa.sign(), auth)
        new_key = SigningKey("RSA")
        self.server.keys.append(new_key)              # IdP publishes the next key
        self.refused(new_key.sign(), auth)           # unknown kid, refresh rate-limited (< 5 s)
        self.clock.now += 6
        self.ok(new_key.sign(), auth)                # refetched: new key accepted
        self.server.keys = [new_key]                  # IdP retires the old key
        self.ok(self.rsa.sign(), auth)               # still cached until cache lifetime ends
        self.clock.now += 61
        self.ok(new_key.sign(), auth)                # triggers refresh
        self.refused(self.rsa.sign(), auth)          # retired key no longer accepted
        self.assertEqual(self.server.hits, 3)

    def test_random_kids_cannot_amplify_fetches(self):
        auth = self.authenticator(jwks_min_refresh_seconds=30)
        self.ok(self.rsa.sign(), auth)
        for i in range(200):
            self.refused(self.rsa.sign(kid=f"random-{i}"), auth)
        self.assertEqual(self.server.hits, 1)
        self.clock.now += 31
        for i in range(200):
            self.refused(self.rsa.sign(kid=f"random-b-{i}"), auth)
        self.assertEqual(self.server.hits, 2)

    def test_idp_outage_uses_cache_within_staleness_bound(self):
        auth = self.authenticator(jwks_cache_seconds=60, jwks_min_refresh_seconds=10, jwks_max_stale_seconds=120)
        self.ok(self.rsa.sign(), auth)
        self.server.mode = "error"
        self.clock.now += 61                          # cache expired, refresh fails
        self.ok(self.rsa.sign(), auth)               # stale keys tolerated
        self.clock.now += 120                         # beyond cache + max_stale
        self.refused(self.rsa.sign(), auth)
        self.server.mode = "ok"
        self.clock.now += 11
        self.ok(self.rsa.sign(), auth)               # recovers when the IdP is back

    def test_no_stale_tolerance_fails_closed(self):
        auth = self.authenticator(jwks_cache_seconds=60, jwks_min_refresh_seconds=10, jwks_max_stale_seconds=0)
        self.ok(self.rsa.sign(), auth)
        self.server.mode = "error"
        self.clock.now += 61
        self.refused(self.rsa.sign(), auth)

    def test_unusable_jwks_documents_fail_closed(self):
        for mode in ("error", "redirect", "oversized", "invalid"):
            with self.subTest(mode=mode):
                self.server.mode = mode
                self.refused(self.rsa.sign())
        self.server.mode = "ok"
        self.server.keys = []
        self.refused(self.rsa.sign())

    def test_fetch_refuses_redirects_size_and_non_loopback_http(self):
        self.server.mode = "redirect"
        with self.assertRaises(urllib.error.HTTPError) as caught:
            fetch_jwks(self.server.url, 2, 262_144)
        self.assertEqual(caught.exception.code, 302)  # refused, not followed
        self.server.mode = "oversized"
        with self.assertRaises(ValueError):
            fetch_jwks(self.server.url, 2, 262_144)
        with self.assertRaises(ValueError):
            fetch_jwks("http://idp.example.com/jwks", 2, 262_144)
        with self.assertRaises(ValueError):
            fetch_jwks("file:///etc/passwd", 2, 262_144)


class SettingsTests(unittest.TestCase):
    def test_url_and_algorithm_policy(self):
        bad = [dict(jwks_url="http://idp.example.com/jwks"), dict(jwks_url="file:///etc/jwks"),
               dict(issuer="http://idp.example.com"), dict(algorithms=("HS256",)), dict(algorithms=("none",)),
               dict(algorithms=()), dict(algorithms=("RS256", "RS256")), dict(organization_id="../x"),
               dict(jwks_min_refresh_seconds=600, jwks_cache_seconds=60), dict(leeway_seconds=3600)]
        for overrides in bad:
            with self.subTest(overrides=overrides), self.assertRaises(ValidationError):
                oidc_settings("https://idp.example.com/jwks", **overrides)
        oidc_settings("https://idp.example.com/jwks")
        oidc_settings("http://[::1]:8080/jwks")

    def test_environment_configuration(self):
        from app.web.settings import ConfigurationError, settings_from_env
        env = {"MEDRIXA_ENV": "test", "MEDRIXA_AUTH_MODE": "oidc", "MEDRIXA_OIDC_ISSUER": ISSUER,
               "MEDRIXA_OIDC_AUDIENCE": AUDIENCE, "MEDRIXA_OIDC_JWKS_URL": "https://idp.test.invalid/jwks",
               "MEDRIXA_ORGANIZATION_ID": ORG, "MEDRIXA_OIDC_ALGORITHMS": "RS256, ES256"}
        settings = settings_from_env(env)
        self.assertEqual((settings.auth_mode, settings.oidc.algorithms, settings.auth_secret),
                         ("oidc", ("RS256", "ES256"), None))
        for broken in ({"MEDRIXA_ORGANIZATION_ID": ""}, {"MEDRIXA_OIDC_JWKS_URL": ""},
                       {"MEDRIXA_AUTH_SECRET": "x" * 40 + "abcdefgh"}, {"MEDRIXA_OIDC_ALGORITHMS": "HS256"},
                       {"MEDRIXA_AUTH_MODE": "magic"}):
            with self.subTest(broken=list(broken)), self.assertRaises(ConfigurationError):
                settings_from_env({**env, **broken})


class HttpIntegrationTests(OidcTestCase):
    def setUp(self):
        super().setUp()
        from fastapi.testclient import TestClient
        from app.web.app import create_app
        from app.web.audit import MemoryAuditSink
        from app.web.settings import Settings
        self.audit = MemoryAuditSink()
        settings = Settings(environment="test", auth_mode="oidc", oidc=oidc_settings(self.server.url),
                            allowed_hosts=("testserver",))
        self.client = TestClient(create_app(settings, audit=self.audit))

    def post(self, token, case_id="case-a", key="oidc-key-00000000001"):
        body = {"case": {"case_id": case_id, "synthetic": True, "source": "synthetic_fixture",
                         "history_available": True, "observations": []}}
        return self.client.post("/v1/runs", json=body,
                                headers={"Authorization": f"Bearer {token}", "Idempotency-Key": key})

    def test_end_to_end_with_oidc_tokens(self):
        created = self.post(self.rsa.sign())
        self.assertEqual(created.status_code, 201, created.text)
        run_id = created.json()["run_id"]
        reviewer = self.ec.sign(sub="rev-1", medrixa_roles=["case_reviewer"])
        self.assertEqual(self.client.get(f"/v1/runs/{run_id}", headers={"Authorization": f"Bearer {reviewer}"})
                         .status_code, 200)
        outsider = self.ec.sign(sub="rev-2", medrixa_roles=["case_reviewer"], medrixa_cases=["case-z"])
        self.assertEqual(self.client.get(f"/v1/runs/{run_id}", headers={"Authorization": f"Bearer {outsider}"})
                         .json()["error"]["code"], "NOT_FOUND")
        other_org = self.rsa.sign(medrixa_org="org-other")
        refused = self.post(other_org, key="oidc-key-00000000002")
        self.assertEqual((refused.status_code, refused.json()["error"]["code"]), (401, "UNAUTHENTICATED"))
        creates = [e for e in self.audit.events if e.action == "runs.create"]
        self.assertTrue(creates and all(e.organization == ORG for e in creates))
        self.assertEqual([e.organization for e in self.audit.events if e.action == "auth"], [None])

    def test_issue_token_cli_refused_in_oidc_mode(self):
        import os
        import subprocess
        import sys
        from pathlib import Path
        env = {k: v for k, v in os.environ.items() if not k.startswith("MEDRIXA_")}
        env.update({"MEDRIXA_ENV": "test", "MEDRIXA_AUTH_MODE": "oidc", "MEDRIXA_OIDC_ISSUER": ISSUER,
                    "MEDRIXA_OIDC_AUDIENCE": AUDIENCE, "MEDRIXA_OIDC_JWKS_URL": self.server.url,
                    "MEDRIXA_ORGANIZATION_ID": ORG})
        result = subprocess.run([sys.executable, "-m", "app.web", "issue-token", "--sub", "a", "--role", "operator"],
                                cwd=Path(__file__).resolve().parents[2], env=env, capture_output=True, text=True,
                                timeout=60)
        self.assertEqual((result.returncode, result.stderr.strip()), (2, '{"error":"ISSUE_TOKEN_REQUIRES_HMAC_MODE"}'))


if __name__ == "__main__":
    unittest.main()
