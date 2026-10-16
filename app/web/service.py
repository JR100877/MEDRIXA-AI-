"""Transport-agnostic run service: authorization, idempotency, audit, execution.

Idempotency design (ADR-012): the run id is derived from
SHA-256("medrixa.idempotency.v1" | subject | key). ``StateRepository.create``
is atomic, so a key can create at most one run, across retries, concurrent
requests, processes and restarts, without a separate key store. A retry with a
different body is rejected; a retry while the run is unfinished gets 409; a
retry after it finished replays the same view. Agents never run twice.
"""
import hashlib
import re
from collections.abc import Callable
from threading import Lock

from app.bootstrap import create_runner
from app.core.state import State
from app.core.state_manager import ContractViolation, StateConflict
from app.models.contracts import ClinicalCase
from app.persistence.leases import LeaseHeld, LeaseLost, RunLeases
from app.persistence.leasing import DEFAULT_TTL_SECONDS, LeasedExecutor
from app.persistence.manager import PersistentStateManager
from app.persistence.recovery import recover_interrupted_runs
from app.persistence.repository import (CorruptRecord, DuplicateRun, RepositoryError, RunNotFound,
                                        StateRepository, validate_run_id)
from app.web.audit import AuditSink, audit_event
from app.web.auth import Principal
from app.web.contracts import RecoveryView, RunEventsView, RunView
from app.web.errors import ApiError
from app.web.policy import Forbidden, OutOfScope, permissions_of, require

IDEMPOTENCY_KEY = re.compile(r"^[A-Za-z0-9_-]{16,64}$")
_DOMAIN = b"medrixa.idempotency.v1"


def derive_run_id(subject: str, key: str) -> str:
    digest = hashlib.sha256(b"\x00".join((_DOMAIN, subject.encode("utf-8"), key.encode("ascii")))).hexdigest()
    return f"idem-{digest}"


def _storage_error(exc: BaseException) -> ApiError:
    if isinstance(exc, StateConflict):
        return ApiError("CONFLICT")
    if isinstance(exc, CorruptRecord):
        return ApiError("STORE_INTEGRITY_ERROR")
    if isinstance(exc, (RepositoryError, OSError)):
        return ApiError("STORE_UNAVAILABLE")
    return ApiError("INTERNAL_ERROR")


class RunService:
    def __init__(self, repository: StateRepository, audit: AuditSink,
                 runner_factory: Callable = create_runner, leases: RunLeases | None = None,
                 lease_ttl_seconds: float = DEFAULT_TTL_SECONDS):
        if not isinstance(repository, StateRepository) or not isinstance(audit, AuditSink):
            raise TypeError("repository and audit sink ports are required")
        self.repository = repository
        self.leases = leases
        self._executor = LeasedExecutor(leases, ttl_seconds=lease_ttl_seconds) if leases is not None else None
        self._audit = audit
        self._runner_factory = runner_factory
        self._active_lock = Lock()
        self._active = 0

    # -- audit helpers -----------------------------------------------------------
    def _record(self, **fields) -> None:
        try:
            self._audit.record(audit_event(**fields))
        except Exception:
            raise ApiError("AUDIT_UNAVAILABLE") from None

    def audit_auth_failure(self, request_id: str) -> None:
        self._record(request_id=request_id, action="auth", phase="outcome", outcome="UNAUTHENTICATED",
                     status_code=401)

    # -- operations --------------------------------------------------------------
    async def create_run(self, principal: Principal, case: ClinicalCase, idempotency_key: str,
                         request_id: str) -> tuple[RunView, bool]:
        base = dict(request_id=request_id, action="runs.create", subject=principal.subject, organization=principal.organization, case_id=case.case_id)
        try:
            require(principal, "runs:create", case.case_id)
        except (Forbidden, OutOfScope):
            self._record(**base, phase="outcome", outcome="FORBIDDEN", status_code=403)
            raise ApiError("FORBIDDEN") from None
        if not IDEMPOTENCY_KEY.match(idempotency_key or ""):
            raise ApiError("IDEMPOTENCY_KEY_REQUIRED")
        run_id = derive_run_id(principal.subject, idempotency_key)
        base["run_id"] = run_id
        self._record(**base, phase="attempt", outcome="ATTEMPT")  # before any side effect
        code, status = "CREATED", 201
        try:
            view, replayed = await self._create_or_replay(case, run_id)
            if replayed:
                code = "REPLAYED"
            return view, replayed
        except ApiError as error:
            code, status = error.code, error.status
            raise
        finally:
            self._record(**base, phase="outcome", outcome=code, status_code=status)

    async def _create_or_replay(self, case: ClinicalCase, run_id: str) -> tuple[RunView, bool]:
        try:
            manager = PersistentStateManager(case, self.repository, run_id=run_id)
        except DuplicateRun:
            try:
                existing = self.repository.load(run_id)
            except Exception as exc:
                raise _storage_error(exc) from None
            if existing.case != case:
                raise ApiError("IDEMPOTENCY_KEY_REUSED") from None
            if existing.status != "awaiting_human_review":
                raise ApiError("IDEMPOTENCY_IN_PROGRESS") from None
            return RunView.of(existing), True
        except (ContractViolation, ValueError):
            raise ApiError("INVALID_INPUT") from None
        except Exception as exc:
            raise _storage_error(exc) from None
        with self._active_lock:
            self._active += 1
        try:
            if self._executor is not None:
                final = await self._executor.run(self._runner_factory(), manager)
            else:
                final = await self._runner_factory().run(manager)
        except (LeaseHeld, LeaseLost):
            # Execution stopped without retry; the run is left for recovery and human review.
            raise ApiError("CONFLICT") from None
        except Exception as exc:
            # The store keeps the last persisted revision; recovery or a retry resolves it.
            raise _storage_error(exc) from None
        finally:
            with self._active_lock:
                self._active -= 1
        return RunView.of(final), False

    def _authorized_state(self, principal: Principal, run_id: str, request_id: str, action: str) -> State:
        base = dict(request_id=request_id, action=action, subject=principal.subject, organization=principal.organization)
        if "runs:read" not in permissions_of(principal):
            # Checked before loading: a caller without the permission learns nothing about runs.
            self._record(**base, phase="outcome", outcome="FORBIDDEN", status_code=403)
            raise ApiError("FORBIDDEN")
        try:
            state = self.repository.load(validate_run_id(run_id))
            require(principal, "runs:read", state.case.case_id)
        except (RunNotFound, OutOfScope):
            # Missing and out-of-scope are indistinguishable to the caller.
            self._record(**base, phase="outcome", outcome="NOT_FOUND", status_code=404)
            raise ApiError("NOT_FOUND") from None
        except Exception as exc:
            error = _storage_error(exc)
            self._record(**base, phase="outcome", outcome=error.code, status_code=error.status)
            raise error from None
        self._record(**base, case_id=state.case.case_id, run_id=state.run_id, phase="outcome",
                     outcome="OK", status_code=200)
        return state

    def get_run(self, principal: Principal, run_id: str, request_id: str) -> RunView:
        return RunView.of(self._authorized_state(principal, run_id, request_id, "runs.read"))

    def get_events(self, principal: Principal, run_id: str, request_id: str) -> RunEventsView:
        return RunEventsView.of(self._authorized_state(principal, run_id, request_id, "runs.read_events"))

    def recover(self, principal: Principal, request_id: str) -> RecoveryView:
        base = dict(request_id=request_id, action="runs.recover", subject=principal.subject, organization=principal.organization)
        try:
            require(principal, "runs:recover")
        except (Forbidden, OutOfScope):
            self._record(**base, phase="outcome", outcome="FORBIDDEN", status_code=403)
            raise ApiError("FORBIDDEN") from None
        self._record(**base, phase="attempt", outcome="ATTEMPT")
        code, status = "OK", 200
        try:
            with self._active_lock:
                if self._active:
                    raise ApiError("RUNS_ACTIVE")
                try:
                    report = recover_interrupted_runs(self.repository, self.leases)
                except Exception as exc:
                    raise _storage_error(exc) from None
            return RecoveryView(moved_to_review=len(report.moved_to_review), not_started=len(report.not_started),
                                already_terminal=report.already_terminal, corrupt_records=report.corrupt_records,
                                active_elsewhere=len(report.active_elsewhere))
        except ApiError as error:
            code, status = error.code, error.status
            raise
        finally:
            self._record(**base, phase="outcome", outcome=code, status_code=status)
