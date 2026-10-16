"""Authentication port and a PROVISIONAL local HMAC token adapter.

The API depends only on ``Authenticator`` and ``Principal``. The bundled
``HmacTokenAuthenticator`` is for local development and tests: a shared-secret
signed token, verified in constant time, with issuer, audience, expiry and
bounded lifetime. It is NOT an identity provider, has no revocation list and
no key rotation; production needs OIDC/an IdP (pending, see docs/HTTP_API.md).

Token: ``mdx1.<base64url(claims JSON)>.<base64url(HMAC-SHA256(secret, "mdx1.<claims>"))>``
"""
import base64
import binascii
import hashlib
import hmac
import json
import re
import time
from abc import ABC, abstractmethod
from typing import Annotated, Final, Literal

from pydantic import Field, ValidationError, model_validator

from app.models.contracts import Contract, Identifier
from app.web.policy import Role

TOKEN_PREFIX = "mdx1"  # noqa: S105 - format marker, not a credential
ISSUER: Final = "medrixa-local-dev"
AUDIENCE: Final = "medrixa-api-v1"
MAX_TOKEN_CHARS = 4096
_B64URL = re.compile(r"^[A-Za-z0-9_-]+$")


class AuthenticationFailed(Exception):
    """Uniform failure; callers must not learn which check failed."""


class Principal(Contract):
    subject: Identifier
    roles: Annotated[frozenset[Role], Field(min_length=1, max_length=3)]
    cases: Annotated[frozenset[Identifier], Field(max_length=100)] = frozenset()
    organization: Identifier | None = None  # P7.2: set by OIDC; None for local HMAC tokens


class TokenClaims(Contract):
    iss: Literal["medrixa-local-dev"]
    aud: Literal["medrixa-api-v1"]
    sub: Identifier
    roles: Annotated[tuple[Role, ...], Field(min_length=1, max_length=3)]
    cases: Annotated[tuple[Identifier, ...], Field(max_length=100)] = ()
    iat: Annotated[int, Field(ge=0)]
    exp: Annotated[int, Field(ge=0)]

    @model_validator(mode="after")
    def coherent(self) -> "TokenClaims":
        if len(set(self.roles)) != len(self.roles) or len(set(self.cases)) != len(self.cases):
            raise ValueError("duplicate roles or cases")
        if self.exp <= self.iat:
            raise ValueError("expiry before issue time")
        return self


class Authenticator(ABC):
    @abstractmethod
    def authenticate(self, authorization_header: str | None) -> Principal:
        """Return a verified Principal or raise AuthenticationFailed."""

    def ready(self) -> bool:
        """P8 readiness: can tokens be verified right now? Default: yes (local keys)."""
        return True


def _b64encode(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


def _b64decode(text: str) -> bytes:
    if not _B64URL.match(text):
        raise ValueError("not base64url")
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


def _sign(secret: bytes, signing_input: str) -> str:
    return _b64encode(hmac.new(secret, signing_input.encode("ascii"), hashlib.sha256).digest())


def issue_token(secret: str, subject: str, roles, cases=(), ttl_seconds: int = 900, now: int | None = None) -> str:
    """Issue a local development/test token. Never use for real identities."""
    issued = int(time.time()) if now is None else now
    claims = TokenClaims(iss=ISSUER, aud=AUDIENCE, sub=subject, roles=tuple(roles), cases=tuple(cases),
                         iat=issued, exp=issued + ttl_seconds)
    body = _b64encode(json.dumps(claims.model_dump(mode="json", exclude={"schema_version"}),
                                 separators=(",", ":"), sort_keys=True).encode("utf-8"))
    signing_input = f"{TOKEN_PREFIX}.{body}"
    return f"{signing_input}.{_sign(secret.encode('utf-8'), signing_input)}"


class HmacTokenAuthenticator(Authenticator):
    def __init__(self, secret: str, max_ttl_seconds: int, clock_skew_seconds: int, clock=time.time):
        self._secret = secret.encode("utf-8")
        self._max_ttl = max_ttl_seconds
        self._skew = clock_skew_seconds
        self._clock = clock

    def authenticate(self, authorization_header: str | None) -> Principal:
        try:
            if not authorization_header or len(authorization_header) > MAX_TOKEN_CHARS + 7:
                raise ValueError("missing")
            scheme, _, token = authorization_header.partition(" ")
            if scheme != "Bearer" or not token or " " in token:
                raise ValueError("scheme")
            parts = token.split(".")
            if len(parts) != 3 or parts[0] != TOKEN_PREFIX:
                raise ValueError("format")
            signing_input = f"{parts[0]}.{parts[1]}"
            if not hmac.compare_digest(_sign(self._secret, signing_input), parts[2]):
                raise ValueError("signature")
            claims = TokenClaims.model_validate_json(_b64decode(parts[1]))
            now = int(self._clock())
            if (claims.exp - claims.iat > self._max_ttl or claims.iat > now + self._skew
                    or claims.exp <= now - self._skew):
                raise ValueError("time")
            return Principal(subject=claims.sub, roles=frozenset(claims.roles), cases=frozenset(claims.cases))
        except (ValueError, ValidationError, binascii.Error, UnicodeError, TypeError):
            raise AuthenticationFailed() from None
