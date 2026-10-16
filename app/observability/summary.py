from typing import Annotated, Literal

from pydantic import Field

from app.core.state import State
from app.models.contracts import Contract, StopCode


class RunSummary(Contract):
    status: Literal["created", "running", "awaiting_human_review"]
    stop_reason: StopCode | None
    dispatched: Annotated[int, Field(ge=0)]
    completed: Annotated[int, Field(ge=0)]
    abstained: Annotated[int, Field(ge=0)]
    failed: Annotated[int, Field(ge=0)]
    events: Annotated[int, Field(ge=0)]
    human_review_required: Literal[True] = True


class RecoverySummary(Contract):
    """Payload-free recovery counters for the CLI; no run or case identifiers."""
    moved_to_review: Annotated[int, Field(ge=0)]
    not_started: Annotated[int, Field(ge=0)]
    already_terminal: Annotated[int, Field(ge=0)]
    corrupt_records: Annotated[int, Field(ge=0)]
    agents_reexecuted: Literal[0] = 0
    active_elsewhere: Annotated[int, Field(ge=0, description="P6: runs skipped because a live lease exists. "
                                                             "Omitted from output when 0.")] = 0

    def public_json(self, **kwargs) -> str:
        """Serialised form; the additive P6 field is omitted when 0 (v1 output unchanged)."""
        return self.model_dump_json(exclude={"active_elsewhere"} if self.active_elsewhere == 0 else None, **kwargs)


def summarize_recovery(report) -> RecoverySummary:
    return RecoverySummary(moved_to_review=len(report.moved_to_review), not_started=len(report.not_started),
                           already_terminal=report.already_terminal, corrupt_records=report.corrupt_records,
                           active_elsewhere=len(getattr(report, "active_elsewhere", ())))


def summarize(state: State) -> RunSummary:
    """Payload-free counters. No case identifiers, observations or findings."""
    state = State.model_validate(state)
    return RunSummary(status=state.status, stop_reason=state.stop_reason,
                      dispatched=len(state.messages),
                      completed=sum(r.status == "completed" for r in state.results),
                      abstained=sum(r.status == "abstained" for r in state.results),
                      failed=sum(r.status == "failed" for r in state.results), events=len(state.events))
