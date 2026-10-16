import asyncio
from typing import Annotated

from pydantic import Field, ValidationError

from app.core.planner import Plan, Planner
from app.core.router import Router, UnknownRoute
from app.core.state import State
from app.core.state_manager import ContractViolation, StateConflict, StateManager
from app.models.contracts import AgentContext, AgentMessage, AgentResult, Contract, StopCode


class ExecutionLimits(Contract):
    max_steps: Annotated[int, Field(ge=1, le=10)] = 2
    agent_timeout_seconds: Annotated[float, Field(gt=0, le=60, allow_inf_nan=False)] = 1.0


class Orchestrator:
    """Bounded local runner. A manager is single-run and exclusively owned while running.

    asyncio timeouts are cooperative, NOT process isolation or hard deadlines.
    Never register untrusted/blocking agents here.
    """
    def __init__(self, planner: Planner, router: Router, limits: ExecutionLimits | None = None):
        self.planner = planner
        self.router = router
        self.limits = ExecutionLimits.model_validate(limits or ExecutionLimits())

    @staticmethod
    def _review(manager: StateManager, reason: StopCode) -> State:
        state = manager.snapshot()
        if state.status == "awaiting_human_review":
            return state
        return manager.request_review(reason, state.revision)

    async def run(self, manager: StateManager) -> State:
        # Starting a used manager is caller misuse; do not hide or reset its history.
        state = manager.start(0)
        try:
            for step_number in range(self.limits.max_steps + 1):
                try:
                    plan = Plan.model_validate(self.planner.create_plan(state))
                except Exception:
                    return self._review(manager, "INVALID_PLAN")
                if plan.action != "execute":
                    return self._review(manager, plan.reason)
                if step_number == self.limits.max_steps:
                    return self._review(manager, "STEP_LIMIT")
                if any(m.step == plan.step for m in state.messages):
                    return self._review(manager, "INVALID_PLAN")
                try:
                    agent = self.router.resolve(plan.step)
                except UnknownRoute:
                    return self._review(manager, "UNKNOWN_ROUTE")
                request = AgentMessage(case_id=state.case.case_id, run_id=state.run_id,
                                       agent_id=agent.agent_id, step=plan.step)
                state = manager.dispatch(request, state.revision)
                context = AgentContext(case=state.case, message=request, prior_results=state.results)
                try:
                    raw_result = await asyncio.wait_for(agent.execute(context), timeout=self.limits.agent_timeout_seconds)
                except TimeoutError:
                    return self._review(manager, "TIMEOUT")
                except Exception:
                    return self._review(manager, "AGENT_ERROR")
                try:
                    result = AgentResult.model_validate(raw_result)
                    state = manager.accept(result, state.revision)
                except (ValidationError, ContractViolation, TypeError, ValueError):
                    return self._review(manager, "INVALID_RESULT")
            return self._review(manager, "STEP_LIMIT")
        except asyncio.CancelledError:
            self._review(manager, "CANCELLED")
            raise
        except StateConflict:
            # A foreign state mutation must never be silently overwritten.
            raise
        except Exception:
            return self._review(manager, "INTERNAL_ERROR")
