"""Secure-by-default configuration. Missing or weak settings stop start-up."""
import os
from collections.abc import Mapping
from pathlib import Path
from typing import Annotated, Literal

from pydantic import Field, SecretStr, field_validator, model_validator

from app.models.contracts import Contract
from app.web.oidc import OidcSettings
from app.security.secrets import EnvironmentSecretProvider, SecretError, SecretProvider

MIN_SECRET_BYTES = 32
_OIDC_VARIABLES = {
    "issuer": "MEDRIXA_OIDC_ISSUER", "audience": "MEDRIXA_OIDC_AUDIENCE", "jwks_url": "MEDRIXA_OIDC_JWKS_URL",
    "organization_id": "MEDRIXA_ORGANIZATION_ID", "algorithms": "MEDRIXA_OIDC_ALGORITHMS",
    "roles_claim": "MEDRIXA_OIDC_ROLES_CLAIM", "cases_claim": "MEDRIXA_OIDC_CASES_CLAIM",
    "organization_claim": "MEDRIXA_OIDC_ORGANIZATION_CLAIM", "leeway_seconds": "MEDRIXA_OIDC_LEEWAY_SECONDS",
    "jwks_cache_seconds": "MEDRIXA_OIDC_JWKS_CACHE_SECONDS",
}


class ConfigurationError(RuntimeError):
    """Raised at start-up; messages never include secret values."""


class Settings(Contract):
    environment: Literal["development", "test"]
    auth_mode: Literal["hmac", "oidc"] = "hmac"  # P7.2
    auth_secret: SecretStr | None = None  # required in hmac mode
    oidc: OidcSettings | None = None  # required in oidc mode
    store_dir: Path | None = None
    postgres_dsn: SecretStr | None = None  # P5; treated as a secret (may hold credentials)
    postgres_migrate: bool = True  # P7.4: False for a least-privilege application role
    postgres_pool_size: Annotated[int, Field(ge=0, le=100)] = 4  # P7.6: 0 = connection per operation
    max_body_bytes: Annotated[int, Field(ge=1024, le=1_048_576)] = 1_048_576
    token_max_ttl_seconds: Annotated[int, Field(ge=60, le=28_800)] = 3_600
    clock_skew_seconds: Annotated[int, Field(ge=0, le=300)] = 30
    lease_ttl_seconds: Annotated[float, Field(ge=1, le=3600, allow_inf_nan=False)] = 30.0  # P6
    allowed_hosts: Annotated[tuple[str, ...], Field(min_length=1, max_length=10)] = ("127.0.0.1", "localhost")
    # P7.3: per-process limits (a shared limiter is needed with several replicas).
    rate_limit_ip_per_minute: Annotated[int, Field(ge=1, le=100_000)] = 600
    rate_limit_subject_per_minute: Annotated[int, Field(ge=1, le=100_000)] = 120
    max_active_runs: Annotated[int, Field(ge=1, le=1_000)] = 16

    @field_validator("auth_secret")
    @classmethod
    def strong_secret(cls, value: SecretStr | None) -> SecretStr | None:
        if value is None:
            return value
        raw = value.get_secret_value()
        if len(raw.encode("utf-8")) < MIN_SECRET_BYTES or len(set(raw)) < 8:
            raise ValueError("auth secret too short or low-entropy")
        return value

    @field_validator("allowed_hosts")
    @classmethod
    def no_wildcard_hosts(cls, hosts: tuple[str, ...]) -> tuple[str, ...]:
        if any(not h or "*" in h for h in hosts):
            raise ValueError("wildcard hosts are not allowed")
        return hosts

    @model_validator(mode="after")
    def auth_configured(self) -> "Settings":
        if self.auth_mode == "hmac" and self.auth_secret is None:
            raise ValueError("hmac mode requires MEDRIXA_AUTH_SECRET")
        if self.auth_mode == "oidc" and self.oidc is None:
            raise ValueError("oidc mode requires the MEDRIXA_OIDC_* settings")
        if self.auth_mode == "oidc" and self.auth_secret is not None:
            raise ValueError("MEDRIXA_AUTH_SECRET must not be set in oidc mode")
        return self

    @model_validator(mode="after")
    def single_store(self) -> "Settings":
        if self.store_dir is not None and self.postgres_dsn is not None:
            raise ValueError("choose one store: MEDRIXA_STORE_DIR or MEDRIXA_PG_DSN")
        return self

    @property
    def expose_docs(self) -> bool:
        return self.environment == "development"


def settings_from_env(environ: Mapping[str, str] | None = None, secrets: SecretProvider | None = None) -> Settings:
    """Non-secret values from ``MEDRIXA_*`` variables; secrets through ``secrets``
    (default: ``MEDRIXA_<NAME>`` or ``MEDRIXA_<NAME>_FILE``, see app/security/secrets.py)."""
    env = os.environ if environ is None else environ
    secrets = secrets or EnvironmentSecretProvider(env)
    name = env.get("MEDRIXA_ENV", "")
    if name == "production":
        raise ConfigurationError("production profile is not authorized for MEDRIXA")
    try:
        auth_secret = secrets.get("AUTH_SECRET")
        postgres_dsn = secrets.get("PG_DSN")
    except SecretError as error:
        raise ConfigurationError(str(error)) from None  # names the variable, never the value
    mode = env.get("MEDRIXA_AUTH_MODE", "hmac")
    data: dict[str, object] = {"environment": name, "auth_mode": mode}
    if auth_secret is not None or mode == "hmac":
        data["auth_secret"] = auth_secret or ""
    if mode == "oidc":
        oidc: dict[str, object] = {}
        for field, variable in _OIDC_VARIABLES.items():
            if env.get(variable):
                value: object = env[variable]
                if field == "algorithms":
                    value = tuple(a.strip() for a in str(value).split(","))
                oidc[field] = value
        data["oidc"] = oidc
    if env.get("MEDRIXA_STORE_DIR"):
        data["store_dir"] = Path(env["MEDRIXA_STORE_DIR"])
    if env.get("MEDRIXA_LEASE_TTL_SECONDS"):
        data["lease_ttl_seconds"] = env["MEDRIXA_LEASE_TTL_SECONDS"]
    if postgres_dsn is not None:
        data["postgres_dsn"] = postgres_dsn
    for field, variable in (("rate_limit_ip_per_minute", "MEDRIXA_RATE_LIMIT_IP_PER_MINUTE"),
                            ("rate_limit_subject_per_minute", "MEDRIXA_RATE_LIMIT_SUBJECT_PER_MINUTE"),
                            ("max_active_runs", "MEDRIXA_MAX_ACTIVE_RUNS"),
                            ("postgres_pool_size", "MEDRIXA_PG_POOL_SIZE")):
        if env.get(variable):
            data[field] = env[variable]
    if env.get("MEDRIXA_PG_MIGRATE"):
        flag = env["MEDRIXA_PG_MIGRATE"].strip().lower()
        if flag not in ("true", "false"):
            raise ConfigurationError("MEDRIXA_PG_MIGRATE must be true or false")
        data["postgres_migrate"] = flag == "true"
    if env.get("MEDRIXA_ALLOWED_HOSTS"):
        data["allowed_hosts"] = tuple(h.strip() for h in env["MEDRIXA_ALLOWED_HOSTS"].split(","))
    try:
        return Settings.model_validate(data, strict=False)
    except ValueError:
        # Field names only; never echo values (the secret could be among them).
        raise ConfigurationError("invalid MEDRIXA configuration (MEDRIXA_ENV, MEDRIXA_AUTH_SECRET[_FILE], "
                                 "MEDRIXA_STORE_DIR, MEDRIXA_PG_DSN[_FILE], MEDRIXA_LEASE_TTL_SECONDS, "
                                 "MEDRIXA_ALLOWED_HOSTS, MEDRIXA_AUTH_MODE, MEDRIXA_OIDC_*, MEDRIXA_RATE_LIMIT_*, "
                                 "MEDRIXA_MAX_ACTIVE_RUNS, MEDRIXA_PG_POOL_SIZE)") from None
