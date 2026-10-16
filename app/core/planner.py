from typing import Literal

from pydantic import model_validator

from app.core.state import State
from app.models.contracts import Contract, Step, StopCode


class Plan(Contract):
    action: Literal["execute", "collect_case_information", "requires_human_review"]
    step: Step | None = None
    reason: StopCode | None = None

    @model_validator(mode="after")
    def valid_action(self) -> "Plan":
        if self.action == "execute" and (self.step is None or self.reason is not None):
            raise ValueError("execution needs a step and no stop reason")
        if self.action != "execute" and (self.step is not None or self.reason is None):
            raise ValueError("review needs a reason and no executable step")
        return self


class Planner:
    """Pure next-step policy. It never calls agents or modifies state."""
    def create_plan(self, state: State) -> Plan:
        state = State.model_validate(state)
        if state.status != "running" or state.pending_message is not None:
            return Plan(action="requires_human_review", reason="INVALID_PLAN")
        if any(r.status == "failed" for r in state.results):
            return Plan(action="requires_human_review", reason="AGENT_ERROR")
        if any(r.status == "abstained" for r in state.results):
            return Plan(action="collect_case_information", reason="MISSING_DATA")
        if not state.case.history_available:
            return Plan(action="collect_case_information", reason="MISSING_DATA")
        completed_ids = {r.message_id for r in state.results if r.status == "completed"}
        completed_steps = {m.step for m in state.messages if m.message_id in completed_ids}
        for step in ("history_analysis", "clinical_analysis"):
            if step not in completed_steps:
                return Plan(action="execute", step=step)
        return Plan(action="requires_human_review", reason="REVIEW_REQUIRED")
