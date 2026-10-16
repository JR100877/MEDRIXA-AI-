"""P7.2: OIDC access-token (JWT) authentication through the ``Authenticator`` port.

Verification is delegated to PyJWT + cryptography (no custom cryptography).
This module only decides WHICH key and WHICH checks:

- Asymmetric algorithms only (RS/PS/ES/EdDSA allow-list); ``none`` and HMAC are
  impossible, which also prevents RSA-public-key-as-HMAC-secret confusion.
- ``kid`` is mandatory; a JWK's own ``alg`` (if declared) must equal the token's;
  ``use`` other than ``sig`` is ignored; ``crit`` headers are rejected.
- Required claims: iss (exact), aud, exp, iat, sub; ``nbf`` honoured; bounded
  leeway; bounded token lifetime (exp - iat).
- Organization: the token's organization claim must equal the configured
  organization. MEDRIXA runs single-organization; multi-tenancy is not enabled.
- Roles: MEDRIXA role names from a configurable claim (dotted paths such as
  ``realm_access.roles`` supported); unknown roles are ignored; no known role
  -> rejected. Cases: list of identifiers, strictly validated.

JWKS handling: fetched over HTTPS (plain HTTP only for loopback, intended for
local test infrastructure), no redirects, bounded size and timeout. Keys are
cached; an unknown ``kid`` triggers a refresh at most once per
``min_refresh_seconds`` (random-kid DoS protection); if the IdP is unreachable,
cached keys keep working for at most ``max_stale_seconds`` past their cache
lifetime. Every failure is the same ``AuthenticationFailed`` (HTTP 401).
"""
import hashlib
import ipaddress
import json
import logging
import time
import urllib.error
import urllib.request
from collections.abc import Callable
from threading import Lock
from typing import Annotated, Any, Literal, cast
from urllib.parse import urlsplit

import jwt
from pydantic import Field, TypeAdapter, ValidationError, field_validator, model_validator

from app.models.contracts import Contract, Identifier
from app.web.auth import MAX_TOKEN_CHARS, AuthenticationFailed, Authenticator, Principal
from app.web.policy import ROLE_PERMISSIONS, Role

_log = logging.getLogger("medrixa.oidc")
Algorithm = Literal["RS256", "RS384", "RS512", "PS256", "PS384", "PS512", "ES256", "ES384", "ES512", "EdDSA"]
_IDENTIFIER = TypeAdapter(Identifier)
_LOOPBACK_NAMES = {"localhost"}
_REQUIRED_CLAIMS = ["exp", "iat", "iss", "aud", "sub"]


def is_loopback(host: str | None) -> bool:
    if not host:
        return False
    if host in _LOOPBACK_NAMES:
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


def check_url(url: str, what: str) -> str:
    parts = urlsplit(url)
    if parts.scheme == "https" and parts.hostname:
        return url
    if parts.scheme == "http" and is_loopback(parts.hostname):
        return url  # local, controlled test infrastructure only
    raise ValueError(f"{what} must be https (http only for loopback)")


class OidcSettings(Contract):
    issuer: Annotated[str, Field(min_length=1, max_length=512)]
    audience: Annotated[str, Field(min_length=1, max_length=256)]
    jwks_url: Annotated[str, Field(min_length=1, max_length=1024)]
    organization_id: Identifier
    algorithms: Annotated[tuple[Algorithm, ...], Field(min_length=1, max_length=10)] = ("RS256", "ES256")
    roles_claim: Annotated[str, Field(min_length=1, max_length=128)] = "medrixa_roles"
    cases_claim: Annotated[str, Field(min_length=1, max_length=128)] = "medrixa_cases"
    organization_claim: Annotated[str, Field(min_length=1, max_length=128)] = "medrixa_org"
    leeway_seconds: Annotated[int, Field(ge=0, le=120)] = 30
    max_token_lifetime_seconds: Annotated[int, Field(ge=60, le=86_400)] = 3_600
    jwks_cache_seconds: Annotated[float, Field(ge=1, le=86_400, allow_inf_nan=False)] = 300.0
    jwks_min_refresh_seconds: Annotated[float, Field(ge=0.1, le=3_600, allow_inf_nan=False)] = 30.0
    jwks_max_stale_seconds: Annotated[float, Field(ge=0, le=86_400, allow_inf_nan=False)] = 3_600.0
    http_timeout_seconds: Annotated[float, Field(gt=0, le=30, allow_inf_nan=False)] = 5.0
    max_jwks_bytes: Annotated[int, Field(ge=1_024, le=1_048_576)] = 262_144

    @field_validator("issuer")
    @classmethod
    def issuer_url(cls, value: str) -> str:
        return check_url(value, "issuer")

    @field_validator("jwks_url")
    @classmethod
    def jwks_https(cls, value: str) -> str:
        return check_url(value, "jwks_url")

    @model_validator(mode="after")
    def coherent(self) -> "OidcSettings":
        if len(set(self.algorithms)) != len(self.algorithms):
            raise ValueError("duplicate algorithms")
        if self.jwks_min_refresh_seconds > self.jwks_cache_seconds:
            raise ValueError("min refresh interval exceeds cache lifetime")
        return self


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):  # noqa: D102
        raise urllib.error.HTTPError(req.full_url, code, "redirects are not followed", headers, fp)


def fetch_jwks(url: str, timeout: float, max_bytes: int) -> dict[str, Any]:
    """GET the JWKS document. Raises on any transport, size or format problem."""
    url = check_url(url, "jwks_url")
    handlers: list[urllib.request.BaseHandler] = [_NoRedirect()]
    if is_loopback(urlsplit(url).hostname):
        handlers.append(urllib.request.ProxyHandler({}))  # never route loopback through a proxy
    opener = urllib.request.build_opener(*handlers)
    request = urllib.request.Request(  # noqa: S310 - scheme restricted to https/loopback http by check_url
        url, headers={"Accept": "application/json", "User-Agent": "medrixa-oidc"})
    with opener.open(request, timeout=timeout) as response:  # noqa: S310 - scheme checked by check_url
        if response.status != 200:
            raise ValueError("unexpected status")
        raw = response.read(max_bytes + 1)
    if len(raw) > max_bytes:
        raise ValueError("JWKS too large")
    document = json.loads(raw.decode("utf-8"))
    if not isinstance(document, dict) or not isinstance(document.get("keys"), list):
        raise ValueError("not a JWK set")
    return document


class JwksCache:
    """Signing keys by ``kid``, refreshed lazily with bounded refetching."""

    def __init__(self, settings: OidcSettings, fetch: Callable[[], dict[str, Any]] | None = None,
                 clock: Callable[[], float] = time.monotonic):
        self._settings = settings
        self._fetch = fetch or (lambda: fetch_jwks(settings.jwks_url, settings.http_timeout_seconds,
                                                   settings.max_jwks_bytes))
        self._clock = clock
        self._lock = Lock()
        self._keys: dict[str, dict[str, Any]] = {}
        self._fetched_at: float | None = None
        self._last_attempt: float | None = None
        self.fetch_count = 0  # observable for tests and diagnostics

    @staticmethod
    def _signing_keys(document: dict[str, Any]) -> dict[str, dict[str, Any]]:
        keys: dict[str, dict[str, Any]] = {}
        for item in document.get("keys", []):
            if not isinstance(item, dict) or item.get("use", "sig") != "sig":
                continue
            kid = item.get("kid")
            if isinstance(kid, str) and 0 < len(kid) <= 256 and kid not in keys:
                keys[kid] = item
        if not keys:
            raise ValueError("no signing keys with kid")
        return keys

    def _refresh_if_due(self, now: float) -> None:
        """Fetch the JWKS unless one was attempted within ``min_refresh_seconds``. Caller holds the lock."""
        if self._last_attempt is not None and now - self._last_attempt < self._settings.jwks_min_refresh_seconds:
            return
        self._last_attempt = now
        self.fetch_count += 1
        try:
            self._keys = self._signing_keys(self._fetch())
            self._fetched_at = now
        except Exception:
            # Constant message: never log URLs with credentials or response bodies.
            _log.warning("JWKS refresh failed; using cached keys if still allowed")

    def _usable(self, now: float) -> bool:
        cfg = self._settings
        return self._fetched_at is not None and now - self._fetched_at < cfg.jwks_cache_seconds + cfg.jwks_max_stale_seconds

    def _fresh(self, now: float) -> bool:
        return self._fetched_at is not None and now - self._fetched_at < self._settings.jwks_cache_seconds

    def ready(self) -> bool:
        """Usable signing keys exist (refreshing at most once per min_refresh interval)."""
        now = self._clock()
        with self._lock:
            if not self._fresh(now):
                self._refresh_if_due(now)
            return bool(self._keys) and self._usable(now)

    def raw_key(self, kid: str) -> dict[str, Any]:
        now = self._clock()
        with self._lock:
            if kid in self._keys and self._fresh(now):
                return self._keys[kid]
            self._refresh_if_due(now)
            if self._usable(now) and kid in self._keys:
                return self._keys[kid]
        raise AuthenticationFailed()


def _claim(claims: dict[str, Any], path: str) -> Any:
    value: Any = claims
    for part in path.split("."):
        if not isinstance(value, dict) or part not in value:
            return None
        value = value[part]
    return value


class OidcJwtAuthenticator(Authenticator):
    def __init__(self, settings: OidcSettings, jwks: JwksCache | None = None):
        self.settings = OidcSettings.model_validate(settings)
        self.jwks = jwks or JwksCache(self.settings)

    def ready(self) -> bool:
        return self.jwks.ready()

    def authenticate(self, authorization_header: str | None) -> Principal:
        try:
            return self._authenticate(authorization_header)
        except AuthenticationFailed:
            raise
        except (jwt.PyJWTError, ValueError, TypeError, KeyError, ValidationError):
            raise AuthenticationFailed() from None

    def _authenticate(self, header_value: str | None) -> Principal:
        cfg = self.settings
        if not header_value or len(header_value) > MAX_TOKEN_CHARS + 7:
            raise ValueError("missing")
        scheme, _, token = header_value.partition(" ")
        if scheme != "Bearer" or not token or " " in token or token.count(".") != 2:
            raise ValueError("format")
        header = jwt.get_unverified_header(token)
        algorithm = header.get("alg")
        kid = header.get("kid")
        if algorithm not in cfg.algorithms or not isinstance(kid, str) or "crit" in header:
            raise ValueError("header")
        raw = self.jwks.raw_key(kid)
        if "alg" in raw and raw["alg"] != algorithm:
            raise ValueError("key/algorithm mismatch")
        key = jwt.PyJWK(raw, algorithm=algorithm)  # rejects incompatible key types
        claims = jwt.decode(token, key, algorithms=[algorithm], audience=cfg.audience, issuer=cfg.issuer,
                            leeway=cfg.leeway_seconds, options={"require": list(_REQUIRED_CLAIMS)})
        iat, exp = claims["iat"], claims["exp"]
        if type(iat) is not int or type(exp) is not int or not 0 < exp - iat <= cfg.max_token_lifetime_seconds:
            raise ValueError("lifetime")
        if iat > time.time() + cfg.leeway_seconds:
            raise ValueError("issued in the future")
        if _claim(claims, cfg.organization_claim) != cfg.organization_id:
            raise ValueError("organization")
        return Principal(subject=self._subject(claims), roles=self._roles(claims), cases=self._cases(claims),
                         organization=cfg.organization_id)

    def _subject(self, claims: dict[str, Any]) -> str:
        sub = claims["sub"]
        if not isinstance(sub, str) or not sub:
            raise ValueError("sub")
        try:
            return _IDENTIFIER.validate_python(sub)
        except ValidationError:
            # e.g. "auth0|123" or e-mail subjects: stable, opaque, issuer-bound identifier.
            digest = hashlib.sha256(f"{self.settings.issuer}\x00{sub}".encode()).hexdigest()
            return f"sub-{digest[:40]}"

    def _roles(self, claims: dict[str, Any]) -> frozenset[Role]:
        value = _claim(claims, self.settings.roles_claim)
        if isinstance(value, str):
            value = value.split()
        if not isinstance(value, list) or len(value) > 50 or not all(isinstance(v, str) for v in value):
            raise ValueError("roles")
        known = frozenset(cast(Role, v) for v in value if v in ROLE_PERMISSIONS)
        if not known:
            raise ValueError("no MEDRIXA role")
        return known

    def _cases(self, claims: dict[str, Any]) -> frozenset[str]:
        value = _claim(claims, self.settings.cases_claim)
        if value is None:
            return frozenset()
        if not isinstance(value, list) or len(value) > 100:
            raise ValueError("cases")
        cases = [_IDENTIFIER.validate_python(v) for v in value]
        if len(set(cases)) != len(cases):
            raise ValueError("duplicate cases")
        return frozenset(cases)
