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


def summarize(state: State) -> RunSummary:
    """Payload-free counters. No case identifiers, observations or findings."""
    state = State.model_validate(state)
    return RunSummary(status=state.status, stop_reason=state.stop_reason,
                      dispatched=len(state.messages),
                      completed=sum(r.status == "completed" for r in state.results),
                      abstained=sum(r.status == "abstained" for r in state.results),
                      failed=sum(r.status == "failed" for r in state.results), events=len(state.events))
