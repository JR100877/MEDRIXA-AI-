"""Local, controlled OIDC test infrastructure: real keys, a real JWKS HTTP server on
127.0.0.1 and a token signer. It is a test fixture, not an identity provider."""
import json
import threading
import time
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import jwt
from cryptography.hazmat.primitives.asymmetric import ec, rsa
from jwt.algorithms import ECAlgorithm, RSAAlgorithm

ISSUER = "https://idp.test.invalid/realms/medrixa"
AUDIENCE = "medrixa-api"
ORG = "org-test-001"


class SigningKey:
    def __init__(self, kind="RSA", kid=None, alg=None, declare_alg=False):
        self.kind = kind
        self.kid = kid or f"{kind.lower()}-{uuid.uuid4().hex[:8]}"
        if kind == "RSA":
            self.private = rsa.generate_private_key(public_exponent=65537, key_size=2048)
            self.alg = alg or "RS256"
            self.jwk = RSAAlgorithm.to_jwk(self.private.public_key(), as_dict=True)
        else:
            self.private = ec.generate_private_key(ec.SECP256R1())
            self.alg = alg or "ES256"
            self.jwk = ECAlgorithm.to_jwk(self.private.public_key(), as_dict=True)
        self.jwk.update({"kid": self.kid, "use": "sig"})
        if declare_alg:
            self.jwk["alg"] = self.alg

    def sign(self, claims=None, alg=None, kid=None, headers=None, **overrides):
        now = int(time.time())
        payload = {"iss": ISSUER, "aud": AUDIENCE, "sub": "user-oidc-1", "iat": now, "exp": now + 600,
                   "medrixa_org": ORG, "medrixa_roles": ["case_runner"], "medrixa_cases": ["case-a"]}
        payload.update(claims or {})
        for key, value in overrides.items():
            if value is None:
                payload.pop(key, None)
            else:
                payload[key] = value
        header = {"kid": self.kid if kid is None else kid, **(headers or {})}
        if header.get("kid") is False:
            del header["kid"]
        return jwt.encode(payload, self.private, algorithm=alg or self.alg, headers=header)


class JwksServer:
    """Real HTTP server for the JWK set; behaviour switchable per test."""

    def __init__(self, keys=()):
        self.keys = list(keys)
        self.hits = 0
        self.mode = "ok"  # ok | error | redirect | oversized | invalid
        server = self

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):  # noqa: N802
                server.hits += 1
                if server.mode == "error":
                    self.send_response(500)
                    self.end_headers()
                    return
                if server.mode == "redirect":
                    self.send_response(302)
                    self.send_header("Location", "http://127.0.0.1:1/elsewhere")
                    self.end_headers()
                    return
                if server.mode == "oversized":
                    body = b'{"keys": [], "pad": "' + b"x" * 400_000 + b'"}'
                elif server.mode == "invalid":
                    body = b"<html>not json</html>"
                else:
                    body = json.dumps({"keys": [k.jwk for k in server.keys]}).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, *args):
                pass

        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.url = f"http://127.0.0.1:{self.httpd.server_address[1]}/jwks.json"
        self.thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)
        self.thread.start()

    def close(self):
        self.httpd.shutdown()
        self.httpd.server_close()


class FakeClock:
    """Controls only the JWKS cache timer (token expiry always uses real time)."""

    def __init__(self, start=1000.0):
        self.now = start

    def __call__(self):
        return self.now


def oidc_settings(url, **overrides):
    from app.web.oidc import OidcSettings
    values = dict(issuer=ISSUER, audience=AUDIENCE, jwks_url=url, organization_id=ORG,
                  algorithms=("RS256", "PS256", "ES256"))
    values.update(overrides)
    return OidcSettings(**values)
