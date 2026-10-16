"""StateRepository port: durable storage of validated run snapshots.

Contract (every adapter must satisfy tests/persistence/test_repository_contract.py):

- ``create(state)`` stores a NEW run at revision 0, status ``created``.
  An existing run_id raises ``DuplicateRun``; nothing is overwritten.
- ``load(run_id)`` returns a fully revalidated ``State``. Missing runs raise
  ``RunNotFound``; unreadable or inconsistent records raise ``CorruptRecord``.
- ``save(state, expected_revision)`` is a compare-and-swap. It succeeds only if
  the stored revision equals ``expected_revision`` and ``state`` is exactly one
  transition ahead, extending (never rewriting) the stored history. A stale
  revision raises ``RevisionConflict``; a rewrite, cross-case or cross-run
  write raises ``IntegrityViolation``. Snapshot and events are one record, so
  they are written atomically together.
- ``scan()`` lists stored records without raising on corrupt ones.

No adapter may retry, merge or "repair" records silently.
"""
from abc import ABC, abstractmethod
from typing import Literal

from pydantic import TypeAdapter

from app.core.state import State
from app.core.state_manager import ContractViolation, StateConflict
from app.models.contracts import Contract, Identifier

_IDENTIFIER = TypeAdapter(Identifier)


class RepositoryError(RuntimeError):
    """Base class. Messages never contain case payloads."""


class RunNotFound(RepositoryError, LookupError):
    pass


class DuplicateRun(RepositoryError):
    pass


class CorruptRecord(RepositoryError):
    pass


class RevisionConflict(StateConflict):
    """Stale expected revision. Subclasses StateConflict so the Orchestrator
    re-raises it instead of converting it into a review transition."""


class IntegrityViolation(ContractViolation):
    """Attempt to rewrite history, change case/run, or write an invalid successor."""


class RunIndexEntry(Contract):
    key: str
    run_id: Identifier | None = None
    case_id: Identifier | None = None
    revision: int | None = None
    status: Literal["created", "running", "awaiting_human_review"] | None = None
    readable: bool


def validate_run_id(run_id: object) -> str:
    try:
        return _IDENTIFIER.validate_python(run_id)
    except ValueError:
        raise RunNotFound("invalid run identifier") from None


def check_new_run(state: State) -> State:
    state = State.model_validate(state)
    if state.revision != 0 or state.status != "created" or state.events:
        raise IntegrityViolation("only a new run can be created")
    return state


def check_successor(stored: State, candidate: State, expected_revision: int) -> State:
    """Validate that ``candidate`` is the single next transition of ``stored``."""
    if type(expected_revision) is not int or stored.revision != expected_revision:
        raise RevisionConflict("stale or invalid revision")
    candidate = State.model_validate(candidate)
    if candidate.run_id != stored.run_id or candidate.case != stored.case:
        raise IntegrityViolation("run or case mismatch")
    if stored.status == "awaiting_human_review":
        raise IntegrityViolation("terminal run is immutable")
    if candidate.revision != stored.revision + 1:
        raise IntegrityViolation("exactly one transition per save")
    n_events, n_messages, n_results = len(stored.events), len(stored.messages), len(stored.results)
    if (candidate.events[:n_events] != stored.events
            or candidate.messages[:n_messages] != stored.messages
            or candidate.results[:n_results] != stored.results):
        raise IntegrityViolation("history rewrite rejected")
    return candidate


class StateRepository(ABC):
    """Port. Implementations: InMemoryStateRepository and
    ProvisionalLocalFileRepository (both provisional). PostgreSQL: pending."""

    adapter_name: str

    @abstractmethod
    def create(self, state: State) -> State: ...

    @abstractmethod
    def load(self, run_id: str) -> State: ...

    @abstractmethod
    def save(self, state: State, expected_revision: int) -> State: ...

    @abstractmethod
    def scan(self) -> tuple[RunIndexEntry, ...]: ...

    def ping(self) -> None:  # noqa: B027 - optional hook; non-abstract on purpose (no breaking change)
        """P8 readiness probe: raise if the store cannot serve requests. Default: nothing to check."""
        return None

    def list_runs(self, case_id: str | None = None) -> tuple[str, ...]:
        """Readable run ids, optionally restricted to one case."""
        return tuple(sorted(e.run_id for e in self.scan()
                            if e.readable and e.run_id is not None and (case_id is None or e.case_id == case_id)))
