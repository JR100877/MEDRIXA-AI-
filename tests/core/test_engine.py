import asyncio
import unittest

from pydantic import ValidationError

from app.agents.base import BaseAgent
from app.agents.clinical.agent import ClinicalAgent
from app.agents.history.agent import HistoryAgent
from app.core.orchestrator import ExecutionLimits, Orchestrator
from app.core.planner import Plan, Planner
from app.core.router import Router, UnknownRoute
from app.core.state_manager import StateConflict, StateManager
from tests.helpers import case, result


def runner(history=None, limits=None, planner=None, routes=None):
    return Orchestrator(planner or Planner(), Router(routes if routes is not None else {
        "history_analysis": history or HistoryAgent(), "clinical_analysis": ClinicalAgent()}), limits)


class FaultAgent(HistoryAgent):
    def __init__(self, mode):
        self.mode = mode
        self.entered = asyncio.Event()

    async def execute(self, context):
        self.entered.set()
        if self.mode == "error":
            raise RuntimeError("SECRET_CASE_TEXT_MUST_NOT_APPEAR")
        if self.mode == "slow":
            await asyncio.sleep(10)
        if self.mode == "dict":
            return {"bad": "SECRET_CASE_TEXT_MUST_NOT_APPEAR"}
        if self.mode in ("case_id", "run_id", "message_id", "agent_id"):
            return result(context.message, **{self.mode: "other"})
        if self.mode == "failed":
            return result(context.message, status="failed")
        return await super().execute(context)


class PlannerRouterTests(unittest.TestCase):
    def test_planner_is_pure_and_history_first(self):
        manager = StateManager(case())
        before = manager.start(0)
        plan = Planner().create_plan(before)
        self.assertEqual(plan.step, "history_analysis")
        self.assertEqual(manager.snapshot(), before)

    def test_missing_history_requests_information(self):
        manager = StateManager(case(history=False))
        plan = Planner().create_plan(manager.start(0))
        self.assertEqual(plan.action, "collect_case_information")
        self.assertEqual(plan.reason, "MISSING_DATA")

    def test_invalid_plan_combinations_rejected(self):
        for values in ({"action": "execute"}, {"action": "requires_human_review", "step": "history_analysis"},
                       {"action": "execute", "step": "history_analysis", "reason": "MISSING_DATA"}):
            with self.subTest(values=values), self.assertRaises(ValidationError):
                Plan(**values)

    def test_router_has_no_execution_and_copies_registry(self):
        agent = HistoryAgent()
        routes = {"history_analysis": agent}
        router = Router(routes)
        routes.clear()
        self.assertIs(router.resolve("history_analysis"), agent)
        with self.assertRaises(UnknownRoute):
            router.resolve("clinical_analysis")

    def test_wrong_step_and_sync_agent_rejected(self):
        class SyncAgent(HistoryAgent):
            def execute(self, context):
                raise AssertionError("must not execute")
        for routes in ({"clinical_analysis": HistoryAgent()}, {"history_analysis": SyncAgent()}):
            with self.subTest(routes=routes), self.assertRaises(ValueError):
                Router(routes)


class OrchestratorTests(unittest.IsolatedAsyncioTestCase):
    async def test_end_to_end_requires_human_review(self):
        manager = StateManager(case())
        final = await runner().run(manager)
        self.assertEqual(final.status, "awaiting_human_review")
        self.assertEqual(final.stop_reason, "REVIEW_REQUIRED")
        self.assertEqual([m.step for m in final.messages], ["history_analysis", "clinical_analysis"])
        self.assertEqual(len(final.results), 2)
        self.assertEqual(len(final.events), 6)

    async def test_missing_history_dispatches_nothing(self):
        final = await runner().run(StateManager(case(history=False)))
        self.assertEqual(final.stop_reason, "MISSING_DATA")
        self.assertFalse(final.messages)

    async def test_missing_observations_abstains(self):
        final = await runner().run(StateManager(case(observations=False)))
        self.assertEqual(final.stop_reason, "MISSING_DATA")
        self.assertEqual(final.results[-1].status, "abstained")

    async def test_unknown_route_fails_closed(self):
        final = await runner(routes={}).run(StateManager(case()))
        self.assertEqual(final.stop_reason, "UNKNOWN_ROUTE")
        self.assertFalse(final.results)

    async def test_agent_exception_is_sanitized(self):
        final = await runner(history=FaultAgent("error")).run(StateManager(case()))
        self.assertEqual(final.stop_reason, "AGENT_ERROR")
        self.assertNotIn("SECRET_CASE", final.model_dump_json())
        self.assertIsNone(final.pending_message)

    async def test_timeout_ends_in_review(self):
        final = await runner(history=FaultAgent("slow"), limits=ExecutionLimits(agent_timeout_seconds=0.01)).run(StateManager(case()))
        self.assertEqual(final.stop_reason, "TIMEOUT")
        self.assertFalse(final.results)

    async def test_invalid_result_and_scope_rejected(self):
        for mode in ("dict", "case_id", "run_id", "message_id", "agent_id"):
            with self.subTest(mode=mode):
                final = await runner(history=FaultAgent(mode)).run(StateManager(case()))
                self.assertEqual(final.stop_reason, "INVALID_RESULT")
                self.assertFalse(final.results)

    async def test_agent_failure_stops_following_agent(self):
        final = await runner(history=FaultAgent("failed")).run(StateManager(case()))
        self.assertEqual(final.stop_reason, "AGENT_ERROR")
        self.assertEqual(len(final.messages), 1)

    async def test_step_limit(self):
        final = await runner(limits=ExecutionLimits(max_steps=1)).run(StateManager(case()))
        self.assertEqual(final.stop_reason, "STEP_LIMIT")
        self.assertEqual(len(final.messages), 1)

    async def test_repeated_plan_does_not_loop(self):
        class RepeatedPlanner(Planner):
            def create_plan(self, state):
                return Plan(action="execute", step="history_analysis")
        final = await runner(planner=RepeatedPlanner()).run(StateManager(case()))
        self.assertEqual(final.stop_reason, "INVALID_PLAN")
        self.assertEqual(len(final.messages), 1)

    async def test_bad_planner_output_and_exception(self):
        class BrokenPlanner(Planner):
            def create_plan(self, state):
                return {"action": "unknown"}
        final = await runner(planner=BrokenPlanner()).run(StateManager(case()))
        self.assertEqual(final.stop_reason, "INVALID_PLAN")

    async def test_cancellation_is_recorded_and_propagated(self):
        agent = FaultAgent("slow")
        manager = StateManager(case())
        task = asyncio.create_task(runner(history=agent).run(manager))
        await agent.entered.wait()
        task.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await task
        self.assertEqual(manager.snapshot().stop_reason, "CANCELLED")

    async def test_independent_concurrent_cases(self):
        shared_runner = runner()
        states = await asyncio.gather(*(shared_runner.run(StateManager(case(f"case-{i}"))) for i in range(8)))
        self.assertEqual(len({s.run_id for s in states}), 8)
        for index, state in enumerate(states):
            self.assertTrue(all(r.case_id == f"case-{index}" for r in state.results))

    async def test_used_manager_never_reset(self):
        manager = StateManager(case())
        final = await runner().run(manager)
        with self.assertRaises(StateConflict):
            await runner().run(manager)
        self.assertEqual(manager.snapshot(), final)
