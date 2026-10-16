"""P6: run leases — liveness of executors, so recovery never interrupts a live run.

Safety does not depend on leases: CAS and terminal immutability (P4) already
make any late write of a superseded executor fail. Leases add *liveness*:
recovery skips ``running`` runs whose lease is still live.

Contract (tests/persistence/test_leases.py, identical for every adapter):
- ``acquire(run_id, owner, ttl)``: succeeds if there is no live lease; a new
  lease gets ``epoch = previous + 1``. A live lease (any owner) -> ``LeaseHeld``.
- ``renew(lease, ttl)``: only the current holder (owner + epoch) of a lease that
  has NOT expired. Otherwise ``LeaseLost`` — an expired lease is never revived,
  because recovery may already have acted on it.
- ``release(lease)``: expires the lease if it is still the current, live one; a
  stale release never affects a newer holder.
- ``live(run_id)``: the current unexpired lease, or ``None``.

Clocks: the in-memory and file adapters use the local wall clock (single host);
PostgreSQL uses the database clock. Executors additionally keep a local
monotonic deadline (see leasing.py) so they stop writing before expiry.
"""
import json
import os
import time
from abc import ABC, abstractmethod
from datetime import datetime, timedelta, timezone
from pathlib import Path
from threading import Lock
from typing import Annotated
from uuid import uuid4

from pydantic import Field, TypeAdapter, ValidationError

from app.models.contracts import Contract, Identifier
from app.persistence.local_file import _exclusive, _key

MIN_TTL_SECONDS, MAX_TTL_SECONDS = 0.2, 3600.0
_IDENTIFIER = TypeAdapter(Identifier)


class LeaseHeld(RuntimeError):
    """Another (or the same) owner holds a live lease."""


class LeaseLost(RuntimeError):
    """The caller is no longer the live holder; it must stop writing."""


class Lease(Contract):
    run_id: Identifier
    owner: Identifier
    epoch: Annotated[int, Field(ge=1)]
    expires_at: datetime


def check_ttl(ttl_seconds: object) -> float:
    if type(ttl_seconds) not in (int, float):  # bool and numeric strings rejected
        raise ValueError("lease ttl out of range")
    ttl = float(ttl_seconds)  # type: ignore[arg-type]  # narrowed to int|float above
    if not MIN_TTL_SECONDS <= ttl <= MAX_TTL_SECONDS:  # also rejects NaN
        raise ValueError("lease ttl out of range")
    return ttl


def check_ids(run_id: object, owner: object) -> tuple[str, str]:
    try:
        return _IDENTIFIER.validate_python(run_id), _IDENTIFIER.validate_python(owner)
    except ValidationError:
        raise ValueError("invalid run id or owner") from None


def new_owner_id() -> str:
    return f"exec-{uuid4().hex}"


class RunLeases(ABC):
    adapter_name: str

    @abstractmethod
    def acquire(self, run_id: str, owner: str, ttl_seconds: float) -> Lease: ...

    @abstractmethod
    def renew(self, lease: Lease, ttl_seconds: float) -> Lease: ...

    @abstractmethod
    def release(self, lease: Lease) -> None: ...

    @abstractmethod
    def live(self, run_id: str) -> Lease | None: ...


def _now(clock) -> datetime:
    return datetime.fromtimestamp(clock(), timezone.utc)


class InMemoryRunLeases(RunLeases):
    """PROVISIONAL, single process."""
    adapter_name = "provisional-in-memory"

    def __init__(self, clock=time.time):
        self._clock = clock
        self._lock = Lock()
        self._leases: dict[str, Lease] = {}

    def acquire(self, run_id, owner, ttl_seconds):
        run_id, owner = check_ids(run_id, owner)
        ttl = check_ttl(ttl_seconds)
        with self._lock:
            now = _now(self._clock)
            current = self._leases.get(run_id)
            if current is not None and current.expires_at > now:
                raise LeaseHeld("lease is held")
            lease = Lease(run_id=run_id, owner=owner, epoch=(current.epoch + 1) if current else 1,
                          expires_at=now + timedelta(seconds=ttl))
            self._leases[run_id] = lease
            return lease

    def renew(self, lease, ttl_seconds):
        lease = Lease.model_validate(lease)
        ttl = check_ttl(ttl_seconds)
        with self._lock:
            now = _now(self._clock)
            current = self._leases.get(lease.run_id)
            if (current is None or (current.owner, current.epoch) != (lease.owner, lease.epoch)
                    or current.expires_at <= now):
                raise LeaseLost("lease lost")
            renewed = Lease(run_id=lease.run_id, owner=lease.owner, epoch=lease.epoch,
                            expires_at=now + timedelta(seconds=ttl))
            self._leases[lease.run_id] = renewed
            return renewed

    def release(self, lease):
        lease = Lease.model_validate(lease)
        with self._lock:
            now = _now(self._clock)
            current = self._leases.get(lease.run_id)
            if current and (current.owner, current.epoch) == (lease.owner, lease.epoch) and current.expires_at > now:
                self._leases[lease.run_id] = Lease(run_id=current.run_id, owner=current.owner,
                                                   epoch=current.epoch, expires_at=now)

    def live(self, run_id):
        run_id = _IDENTIFIER.validate_python(run_id)
        with self._lock:
            current = self._leases.get(run_id)
            return current if current is not None and current.expires_at > _now(self._clock) else None


class LocalFileRunLeases(RunLeases):
    """PROVISIONAL, one host: lease files under ``root``, exclusive OS lock per run,
    atomic replace. Wall-clock based: hosts sharing the directory are not supported."""
    adapter_name = "provisional-local-file"

    def __init__(self, root: str | os.PathLike, clock=time.time):
        self.root = Path(root)
        self._locks = self.root / "locks"
        self.root.mkdir(parents=True, exist_ok=True, mode=0o700)
        self._locks.mkdir(exist_ok=True, mode=0o700)
        self._clock = clock

    def _path(self, run_id: str) -> Path:
        return self.root / f"{_key(run_id)}.lease.json"

    def _lock(self, run_id: str):
        return _exclusive(self._locks / f"{_key(run_id)}.lease.lock")

    def _read(self, run_id: str) -> Lease | None:
        try:
            raw = self._path(run_id).read_bytes()
        except FileNotFoundError:
            return None
        try:
            lease = Lease.model_validate_json(raw)
        except ValidationError:
            # A corrupt lease never grants liveness; it is replaced on the next acquire.
            return None
        return lease if lease.run_id == run_id else None

    def _write(self, lease: Lease) -> None:
        target = self._path(lease.run_id)
        tmp = self.root / f".{target.name}.{uuid4().hex}.tmp"
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        try:
            with os.fdopen(fd, "wb") as handle:
                handle.write(lease.model_dump_json().encode("utf-8"))
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(tmp, target)
        except BaseException:
            try:
                tmp.unlink()
            except OSError:
                pass
            raise

    def acquire(self, run_id, owner, ttl_seconds):
        run_id, owner = check_ids(run_id, owner)
        ttl = check_ttl(ttl_seconds)
        with self._lock(run_id):
            now = _now(self._clock)
            current = self._read(run_id)
            if current is not None and current.expires_at > now:
                raise LeaseHeld("lease is held")
            epoch = (current.epoch + 1) if current else self._last_epoch(run_id) + 1
            lease = Lease(run_id=run_id, owner=owner, epoch=epoch, expires_at=now + timedelta(seconds=ttl))
            self._write(lease)
            return lease

    def _last_epoch(self, run_id: str) -> int:
        """Epoch of an unreadable record, so a corrupt file can never reset epochs to 1."""
        try:
            return int(json.loads(self._path(run_id).read_bytes()).get("epoch", 0))
        except (OSError, ValueError, TypeError, AttributeError):
            return 0

    def renew(self, lease, ttl_seconds):
        lease = Lease.model_validate(lease)
        ttl = check_ttl(ttl_seconds)
        with self._lock(lease.run_id):
            now = _now(self._clock)
            current = self._read(lease.run_id)
            if (current is None or (current.owner, current.epoch) != (lease.owner, lease.epoch)
                    or current.expires_at <= now):
                raise LeaseLost("lease lost")
            renewed = Lease(run_id=lease.run_id, owner=lease.owner, epoch=lease.epoch,
                            expires_at=now + timedelta(seconds=ttl))
            self._write(renewed)
            return renewed

    def release(self, lease):
        lease = Lease.model_validate(lease)
        with self._lock(lease.run_id):
            now = _now(self._clock)
            current = self._read(lease.run_id)
            if current and (current.owner, current.epoch) == (lease.owner, lease.epoch) and current.expires_at > now:
                self._write(Lease(run_id=current.run_id, owner=current.owner, epoch=current.epoch, expires_at=now))

    def live(self, run_id):
        run_id = _IDENTIFIER.validate_python(run_id)
        current = self._read(run_id)
        return current if current is not None and current.expires_at > _now(self._clock) else None
