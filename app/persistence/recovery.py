"""Safe recovery of runs found unfinished after a process restart.

Policy (fail-closed, documented in DECISIONS.md ADR-009):
- ``running`` runs, with or without a pending agent request, move to
  ``awaiting_human_review`` with stop reason ``INTERRUPTED``. The outcome of an
  in-flight agent is unknown: it is NEVER re-executed, retried or assumed
  idempotent, and a late result can no longer be accepted.
- ``created`` runs never dispatched anything; they are left untouched and
  reported, so a person decides whether to start them.
- terminal runs are untouched. Corrupt records are reported, never modified,
  repaired or deleted.

Run this at service start-up while no executor is active. There is no lease or
heartbeat yet: recovering a run that a live process is still executing makes
that process fail with a revision conflict (safe, but disruptive).
"""
from typing import Annotated

from pydantic import Field

from app.core.state_manager import InvalidTransition
from app.models.contracts import Contract, Identifier
from app.persistence.manager import PersistentStateManager
from app.persistence.repository import CorruptRecord, RevisionConflict, RunNotFound, StateRepository


class RecoveryReport(Contract):
    moved_to_review: Annotated[tuple[Identifier, ...], Field(max_length=100_000)] = ()
    not_started: Annotated[tuple[Identifier, ...], Field(max_length=100_000)] = ()
    already_terminal: Annotated[int, Field(ge=0)] = 0
    corrupt_records: Annotated[int, Field(ge=0)] = 0
    agents_reexecuted: Annotated[int, Field(ge=0, le=0)] = 0


def recover_interrupted_runs(repository: StateRepository) -> RecoveryReport:
    moved, not_started = [], []
    terminal = corrupt = 0
    for entry in repository.scan():
        if not entry.readable:
            corrupt += 1
            continue
        try:
            manager = PersistentStateManager.resume(repository, entry.run_id)
        except CorruptRecord:
            corrupt += 1
            continue
        except RunNotFound:
            continue
        state = manager.snapshot()
        if state.status == "created":
            not_started.append(state.run_id)
            continue
        if state.status == "awaiting_human_review":
            terminal += 1
            continue
        try:
            manager.request_review("INTERRUPTED", state.revision)
            moved.append(state.run_id)
        except (RevisionConflict, InvalidTransition):
            # Another recoverer (or the live executor) moved it first; re-read, never overwrite.
            if repository.load(state.run_id).status == "awaiting_human_review":
                terminal += 1
            else:
                raise
    return RecoveryReport(moved_to_review=tuple(moved), not_started=tuple(not_started),
                          already_terminal=terminal, corrupt_records=corrupt)
