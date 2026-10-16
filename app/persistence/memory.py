"""PROVISIONAL in-memory adapter for tests. Not durable: lost on process exit."""
from threading import Lock

from app.core.state import State
from app.persistence.repository import (DuplicateRun, RunIndexEntry, RunNotFound, StateRepository,
                                        check_new_run, check_successor, validate_run_id)


class InMemoryStateRepository(StateRepository):
    adapter_name = "provisional-in-memory"

    def __init__(self):
        self._lock = Lock()
        self._records: dict[str, State] = {}

    def create(self, state: State) -> State:
        state = check_new_run(state)
        with self._lock:
            if state.run_id in self._records:
                raise DuplicateRun("run already exists")
            self._records[state.run_id] = state
        return state

    def load(self, run_id: str) -> State:
        run_id = validate_run_id(run_id)
        with self._lock:
            try:
                return self._records[run_id]
            except KeyError:
                raise RunNotFound("run not found") from None

    def save(self, state: State, expected_revision: int) -> State:
        state = State.model_validate(state)
        with self._lock:
            stored = self._records.get(state.run_id)
            if stored is None:
                raise RunNotFound("run not found")
            candidate = check_successor(stored, state, expected_revision)
            self._records[candidate.run_id] = candidate
        return candidate

    def scan(self) -> tuple[RunIndexEntry, ...]:
        with self._lock:
            records = tuple(self._records.values())
        return tuple(RunIndexEntry(key=s.run_id, run_id=s.run_id, case_id=s.case.case_id,
                                   revision=s.revision, status=s.status, readable=True)
                     for s in sorted(records, key=lambda s: s.run_id))
