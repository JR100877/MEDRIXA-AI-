"""P7.1: secret resolution through a port; no credentials in code or images.

A secret named ``X`` is resolved from exactly one source:
- ``MEDRIXA_X``: environment variable (development/test convenience), or
- ``MEDRIXA_X_FILE``: path to a mounted secret file (Docker/Kubernetes secrets,
  a secrets-manager agent or CSI driver writing a file). Recommended outside
  local development because environment variables leak more easily
  (process listings, crash dumps, child processes).
Defining both is ambiguous and refused.

Mounted files must be regular files (symlinks are resolved: Kubernetes mounts
use them), at most 64 KiB, UTF-8, and NOT writable by group or others; one
trailing newline is removed. Errors never include secret values.

External managers (Vault, AWS/GCP/Azure secret managers) plug in by
implementing ``SecretProvider``; none is bundled: provider selection is a
pending human decision (docs/SECURITY.md).
"""
import os
import stat
from abc import ABC, abstractmethod
from collections.abc import Mapping
from pathlib import Path

from pydantic import SecretStr

MAX_SECRET_FILE_BYTES = 64 * 1024
_NAME_CHARS = frozenset("ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_")


class SecretError(RuntimeError):
    """Secret missing, ambiguous or unsafe. Messages name the variable, never the value."""


def _check_name(name: str) -> str:
    if not name or not set(name) <= _NAME_CHARS:
        raise ValueError("secret names are upper-case identifiers")
    return name


def read_secret_file(path: str | os.PathLike, label: str = "secret file") -> SecretStr:
    target = Path(path)
    try:
        resolved = target.resolve(strict=True)
        info = resolved.stat()
    except (OSError, RuntimeError):
        raise SecretError(f"{label}: file not readable") from None
    if not stat.S_ISREG(info.st_mode):
        raise SecretError(f"{label}: not a regular file")
    if info.st_mode & (stat.S_IWGRP | stat.S_IWOTH):
        raise SecretError(f"{label}: file is writable by group or others")
    if info.st_size > MAX_SECRET_FILE_BYTES:
        raise SecretError(f"{label}: file too large")
    try:
        with resolved.open("rb") as handle:
            raw = handle.read(MAX_SECRET_FILE_BYTES + 1)
        text = raw.decode("utf-8")
    except (OSError, UnicodeDecodeError):
        raise SecretError(f"{label}: file not readable as UTF-8") from None
    if len(raw) > MAX_SECRET_FILE_BYTES:
        raise SecretError(f"{label}: file too large")
    if text.endswith("\r\n"):
        text = text[:-2]
    elif text.endswith("\n"):
        text = text[:-1]
    if not text:
        raise SecretError(f"{label}: file is empty")
    return SecretStr(text)


class SecretProvider(ABC):
    @abstractmethod
    def get(self, name: str) -> SecretStr | None:
        """Return the secret, ``None`` if not configured, or raise SecretError if unsafe."""


class EnvironmentSecretProvider(SecretProvider):
    """``MEDRIXA_<NAME>`` or ``MEDRIXA_<NAME>_FILE`` (exactly one)."""

    def __init__(self, environ: Mapping[str, str] | None = None, prefix: str = "MEDRIXA_"):
        self._env = os.environ if environ is None else environ
        self._prefix = prefix

    def get(self, name: str) -> SecretStr | None:
        variable = self._prefix + _check_name(name)
        direct = self._env.get(variable)
        file_path = self._env.get(variable + "_FILE")
        if direct and file_path:
            raise SecretError(f"{variable} and {variable}_FILE are both set")
        if file_path:
            return read_secret_file(file_path, f"{variable}_FILE")
        return SecretStr(direct) if direct else None


class StaticSecretProvider(SecretProvider):
    """Explicit in-memory values, for tests and embedding applications."""

    def __init__(self, values: Mapping[str, str]):
        self._values = {_check_name(k): SecretStr(v) for k, v in values.items()}

    def get(self, name: str) -> SecretStr | None:
        return self._values.get(_check_name(name))
