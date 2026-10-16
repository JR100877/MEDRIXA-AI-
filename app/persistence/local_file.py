"""PROVISIONAL local-file adapter for development and restart tests.

NOT PostgreSQL and NOT a production store. PostgreSQL remains the intended
target (see docs/PERSISTENCE.md); nothing here claims it is implemented.

Guarantees provided (and tested) on a single host / local filesystem:
- one JSON record per run holding snapshot + events, so both change together;
- write to a temporary file, fsync, atomic ``os.replace``, then fsync the
  directory: a crash leaves either the previous or the new complete record;
- compare-and-swap under an exclusive per-run OS file lock (``fcntl.flock`` on
  POSIX; ``msvcrt.locking`` on Windows, which is untested);
- SHA-256 over the canonical snapshot detects accidental corruption. It is NOT
  a signature: anyone able to write the directory can forge a record.

Not provided: encryption at rest, network filesystems, multi-host locking,
retention policy, or tamper-proof audit. Use only synthetic data.
"""
import hashlib
import json
import os
import re
from contextlib import contextmanager
from pathlib import Path
from uuid import uuid4

try:  # POSIX
    import fcntl
except ImportError:  # pragma: no cover - Windows path is untested
    fcntl = None
    import msvcrt

from pydantic import ValidationError

from app.core.state import State
from app.persistence.repository import (CorruptRecord, DuplicateRun, RunIndexEntry, RunNotFound, StateRepository,
                                        check_new_run, check_successor, validate_run_id)

RECORD_FORMAT = "medrixa.local-run-record.v1"
ADAPTER_NAME = "provisional-local-file"
MAX_RECORD_BYTES = 4 * 1024 * 1024
_RECORD_NAME = re.compile(r"^[0-9a-f]{64}\.json$")
_ENVELOPE_KEYS = {"format", "adapter", "run_id", "case_id", "revision", "status", "state_sha256", "state"}


def _canonical(obj: object) -> bytes:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False,
                      allow_nan=False).encode("utf-8")


def _key(run_id: str) -> str:
    # Hashed names: no path traversal and no characters invalid on any OS.
    return hashlib.sha256(run_id.encode("utf-8")).hexdigest()


@contextmanager
def _exclusive(lock_path: Path):
    fd = os.open(lock_path, os.O_RDWR | os.O_CREAT, 0o600)
    try:
        if fcntl is not None:
            fcntl.flock(fd, fcntl.LOCK_EX)
        else:  # pragma: no cover
            msvcrt.locking(fd, msvcrt.LK_LOCK, 1)
        try:
            yield
        finally:
            if fcntl is not None:
                fcntl.flock(fd, fcntl.LOCK_UN)
            else:  # pragma: no cover
                os.lseek(fd, 0, os.SEEK_SET)
                msvcrt.locking(fd, msvcrt.LK_UNLCK, 1)
    finally:
        os.close(fd)


class ProvisionalLocalFileRepository(StateRepository):
    adapter_name = ADAPTER_NAME

    def __init__(self, root: str | os.PathLike):
        self.root = Path(root)
        self._locks = self.root / "locks"
        self.root.mkdir(parents=True, exist_ok=True, mode=0o700)
        self._locks.mkdir(exist_ok=True, mode=0o700)

    # -- paths -------------------------------------------------------------
    def _record_path(self, key: str) -> Path:
        return self.root / f"{key}.json"

    def _lock_path(self, key: str) -> Path:
        return self._locks / f"{key}.lock"

    # -- low level I/O -------------------------------------------------------
    def _fsync_dir(self) -> None:
        try:
            fd = os.open(self.root, os.O_RDONLY)
        except OSError:  # pragma: no cover - platforms without directory fds
            return
        try:
            os.fsync(fd)
        except OSError:  # pragma: no cover
            pass
        finally:
            os.close(fd)

    def _write(self, key: str, state: State) -> None:
        state_obj = state.model_dump(mode="json")
        envelope = {"format": RECORD_FORMAT, "adapter": ADAPTER_NAME, "run_id": state.run_id,
                    "case_id": state.case.case_id, "revision": state.revision, "status": state.status,
                    "state_sha256": hashlib.sha256(_canonical(state_obj)).hexdigest(), "state": state_obj}
        payload = _canonical(envelope)
        tmp = self.root / f".{key}.{uuid4().hex}.tmp"
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        try:
            try:
                view = memoryview(payload)
                while view:
                    view = view[os.write(fd, view):]
                os.fsync(fd)
            finally:
                os.close(fd)
            os.replace(tmp, self._record_path(key))
        except BaseException:
            try:
                tmp.unlink()
            except OSError:
                pass
            raise
        self._fsync_dir()

    def _read(self, path: Path, expected_key: str | None) -> State:
        """Return the validated State or raise CorruptRecord (FileNotFoundError passes through)."""
        with path.open("rb") as handle:
            raw = handle.read(MAX_RECORD_BYTES + 1)
        try:
            if len(raw) > MAX_RECORD_BYTES:
                raise ValueError("oversized")
            envelope = json.loads(raw.decode("utf-8"))
            if type(envelope) is not dict or set(envelope) != _ENVELOPE_KEYS:
                raise ValueError("envelope")
            if envelope["format"] != RECORD_FORMAT or envelope["adapter"] != ADAPTER_NAME:
                raise ValueError("format")
            canonical_state = _canonical(envelope["state"])
            if hashlib.sha256(canonical_state).hexdigest() != envelope["state_sha256"]:
                raise ValueError("checksum")
            state = State.model_validate_json(canonical_state)
            if (state.run_id != envelope["run_id"] or state.case.case_id != envelope["case_id"]
                    or state.revision != envelope["revision"] or state.status != envelope["status"]):
                raise ValueError("envelope/state mismatch")
            if _key(state.run_id) != (expected_key or path.name[:-5]):
                raise ValueError("record stored under another run")
            return state
        except (ValueError, TypeError, RecursionError, ValidationError, UnicodeDecodeError):
            raise CorruptRecord("unreadable run record") from None

    # -- port ----------------------------------------------------------------
    def create(self, state: State) -> State:
        state = check_new_run(state)
        key = _key(state.run_id)
        with _exclusive(self._lock_path(key)):
            if self._record_path(key).exists():
                raise DuplicateRun("run already exists")
            self._write(key, state)
        return state

    def load(self, run_id: str) -> State:
        key = _key(validate_run_id(run_id))
        try:
            return self._read(self._record_path(key), key)
        except FileNotFoundError:
            raise RunNotFound("run not found") from None

    def save(self, state: State, expected_revision: int) -> State:
        state = State.model_validate(state)
        key = _key(state.run_id)
        with _exclusive(self._lock_path(key)):
            try:
                stored = self._read(self._record_path(key), key)
            except FileNotFoundError:
                raise RunNotFound("run not found") from None
            candidate = check_successor(stored, state, expected_revision)
            self._write(key, candidate)
        return candidate

    def scan(self) -> tuple[RunIndexEntry, ...]:
        entries = []
        for path in sorted(self.root.iterdir()):
            if not _RECORD_NAME.match(path.name):
                continue  # temporary files, lock directory, foreign files
            key = path.name[:-5]
            try:
                state = self._read(path, key)
            except (CorruptRecord, OSError):
                entries.append(RunIndexEntry(key=key, readable=False))
                continue
            entries.append(RunIndexEntry(key=key, run_id=state.run_id, case_id=state.case.case_id,
                                         revision=state.revision, status=state.status, readable=True))
        return tuple(entries)
