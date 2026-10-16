"""In-process state owner with optimistic revision checks; not durable storage."""
from datetime import datetime, timezone
from threading import RLock

from app.core.state import AuditEvent, State
from app.models.contracts import AgentMessage, AgentResult, ClinicalCase, StopCode


class StateConflict(RuntimeError):
    pass


class InvalidTransition(RuntimeError):
    pass


class ContractViolation(ValueError):
    pass


class StateManager:
    def __init__(self, case: ClinicalCase):
        self._lock = RLock()
        self._state = State(case=ClinicalCase.model_validate(case))

    def snapshot(self) -> State:
        with self._lock:
            return self._state

    def _check(self, expected_revision: int, status: str) -> State:
        if type(expected_revision) is not int or self._state.revision != expected_revision:
            raise StateConflict("stale or invalid revision")
        if self._state.status != status:
            raise InvalidTransition("operation not permitted in current state")
        return self._state

    def _commit(self, kind: str, *, message_id: str | None = None, reason: StopCode | None = None, **changes: object) -> State:
        current = self._state
        event = AuditEvent(sequence=current.revision + 1, case_id=current.case.case_id,
                           run_id=current.run_id, kind=kind, at=datetime.now(timezone.utc),
                           message_id=message_id, reason=reason)
        data = current.model_dump()
        data.update(changes, revision=current.revision + 1, events=(*current.events, event))
        candidate = State.model_validate(data)
        self._publish(current, candidate)
        return candidate

    def _publish(self, previous: State, candidate: State) -> None:
        """Make a validated candidate current. Subclasses may persist first;
        if persistence raises, the in-memory snapshot must stay unchanged."""
        self._state = candidate

    def start(self, expected_revision: int) -> State:
        with self._lock:
            self._check(expected_revision, "created")
            return self._commit("RUN_STARTED", status="running")

    def dispatch(self, message: AgentMessage, expected_revision: int) -> State:
        with self._lock:
            current = self._check(expected_revision, "running")
            message = AgentMessage.model_validate(message)
            if current.pending_message is not None:
                raise InvalidTransition("another request is active")
            if message.case_id != current.case.case_id or message.run_id != current.run_id:
                raise ContractViolation("request scope mismatch")
            if any(m.message_id == message.message_id for m in current.messages):
                raise ContractViolation("duplicate request")
            return self._commit("AGENT_DISPATCHED", message_id=message.message_id,
                                pending_message=message, messages=(*current.messages, message))

    def accept(self, result: AgentResult, expected_revision: int) -> State:
        with self._lock:
            current = self._check(expected_revision, "running")
            result = AgentResult.model_validate(result)
            request = current.pending_message
            if request is None:
                raise InvalidTransition("no active request")
            if any(getattr(result, key) != getattr(request, key)
                   for key in ("message_id", "case_id", "run_id", "agent_id")):
                raise ContractViolation("result correlation mismatch")
            if any(r.result_id == result.result_id for r in current.results):
                raise ContractViolation("duplicate result")
            return self._commit("RESULT_ACCEPTED", message_id=request.message_id,
                                pending_message=None, results=(*current.results, result))

    def request_review(self, reason: StopCode, expected_revision: int) -> State:
        with self._lock:
            self._check(expected_revision, "running")
            return self._commit("REVIEW_REQUESTED", reason=reason,
                                pending_message=None, status="awaiting_human_review", stop_reason=reason)
