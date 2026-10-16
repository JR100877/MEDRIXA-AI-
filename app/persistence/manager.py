"""StateManager that persists every transition BEFORE publishing it in memory."""
from threading import RLock

from app.core.state import State
from app.core.state_manager import StateManager
from app.models.contracts import ClinicalCase
from app.persistence.repository import StateRepository


class PersistentStateManager(StateManager):
    """Same transition rules as StateManager; each transition is a CAS write.

    If the repository rejects or fails a write, the exception propagates and the
    in-memory snapshot keeps the last persisted revision. A RevisionConflict
    (another writer advanced the run) is never overwritten.
    """

    def __init__(self, case: ClinicalCase, repository: StateRepository, run_id: str | None = None):
        """``run_id`` (optional, P4b) lets a caller derive a deterministic run id,
        e.g. from an idempotency key; ``create`` still rejects duplicates."""
        if not isinstance(repository, StateRepository):
            raise TypeError("repository must implement StateRepository")
        super().__init__(case)
        if run_id is not None:
            self._state = State(case=self._state.case, run_id=run_id)
        self._repository = repository
        self._state = repository.create(self._state)

    @classmethod
    def resume(cls, repository: StateRepository, run_id: str) -> "PersistentStateManager":
        """Open a stored run. Resuming never re-executes anything by itself."""
        if not isinstance(repository, StateRepository):
            raise TypeError("repository must implement StateRepository")
        manager = cls.__new__(cls)
        manager._lock = RLock()
        manager._repository = repository
        manager._state = repository.load(run_id)
        return manager

    _write_guard = None  # P6: optional callable raising before a write (e.g. lease deadline passed)

    @property
    def run_id(self) -> str:
        return self.snapshot().run_id

    def set_write_guard(self, guard) -> None:
        """P6 (additive): ``guard()`` runs before every persisted transition and may
        raise to refuse the write. Used to stop writing once a lease may have expired."""
        if guard is not None and not callable(guard):
            raise TypeError("guard must be callable")
        with self._lock:
            self._write_guard = guard

    def _publish(self, previous: State, candidate: State) -> None:
        if self._write_guard is not None:
            self._write_guard()
        self._state = self._repository.save(candidate, previous.revision)
