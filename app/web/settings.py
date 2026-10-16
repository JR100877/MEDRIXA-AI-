"""Secure-by-default configuration. Missing or weak settings stop start-up."""
import os
from pathlib import Path
from typing import Annotated, Literal

from pydantic import Field, SecretStr, field_validator, model_validator

from app.models.contracts import Contract

MIN_SECRET_BYTES = 32


class ConfigurationError(RuntimeError):
    """Raised at start-up; messages never include secret values."""


class Settings(Contract):
    environment: Literal["development", "test"]
    auth_secret: SecretStr
    store_dir: Path | None = None
    postgres_dsn: SecretStr | None = None  # P5; treated as a secret (may hold credentials)
    max_body_bytes: Annotated[int, Field(ge=1024, le=1_048_576)] = 1_048_576
    token_max_ttl_seconds: Annotated[int, Field(ge=60, le=28_800)] = 3_600
    clock_skew_seconds: Annotated[int, Field(ge=0, le=300)] = 30
    allowed_hosts: Annotated[tuple[str, ...], Field(min_length=1, max_length=10)] = ("127.0.0.1", "localhost")

    @field_validator("auth_secret")
    @classmethod
    def strong_secret(cls, value: SecretStr) -> SecretStr:
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
    def single_store(self) -> "Settings":
        if self.store_dir is not None and self.postgres_dsn is not None:
            raise ValueError("choose one store: MEDRIXA_STORE_DIR or MEDRIXA_PG_DSN")
        return self

    @property
    def expose_docs(self) -> bool:
        return self.environment == "development"


def settings_from_env(environ: dict | None = None) -> Settings:
    env = os.environ if environ is None else environ
    name = env.get("MEDRIXA_ENV", "")
    if name == "production":
        raise ConfigurationError("production profile is not authorized for MEDRIXA")
    data = {"environment": name, "auth_secret": env.get("MEDRIXA_AUTH_SECRET", "")}
    if env.get("MEDRIXA_STORE_DIR"):
        data["store_dir"] = Path(env["MEDRIXA_STORE_DIR"])
    if env.get("MEDRIXA_PG_DSN"):
        data["postgres_dsn"] = env["MEDRIXA_PG_DSN"]
    if env.get("MEDRIXA_ALLOWED_HOSTS"):
        data["allowed_hosts"] = tuple(h.strip() for h in env["MEDRIXA_ALLOWED_HOSTS"].split(","))
    try:
        return Settings.model_validate(data, strict=False)
    except ValueError:
        # Field names only; never echo values (the secret could be among them).
        raise ConfigurationError("invalid MEDRIXA configuration (MEDRIXA_ENV, MEDRIXA_AUTH_SECRET, "
                                 "MEDRIXA_STORE_DIR, MEDRIXA_PG_DSN, MEDRIXA_ALLOWED_HOSTS)") from None
