import asyncio
import json
import subprocess
import sys
import unittest
from pathlib import Path

from pydantic import ValidationError

from app.api.contracts import MAX_INPUT_BYTES, InputRejected, parse_case
from app.bootstrap import synthetic_example
from app.core.orchestrator import ExecutionLimits
from app.core.planner import Plan, Planner
from app.core.state import State
from app.core.state_manager import StateManager
from app.models.contracts import ClinicalCase
from app.observability.summary import summarize
from tests.core.test_engine import runner
from app.agents.history.agent import HistoryAgent
from tests.helpers import case, message, result

ROOT = Path(__file__).resolve().parents[1]


class BoundaryTests(unittest.TestCase):
    def test_explicit_synthetic_marker_required(self):
        for value in (False, 1, 1.0, "true", None):
            with self.subTest(value=value), self.assertRaises(ValidationError):
                ClinicalCase(synthetic=value, source="synthetic_fixture", history_available=True)
        with self.assertRaises(ValidationError):
            ClinicalCase(source="synthetic_fixture", history_available=True)
        with self.assertRaises(ValidationError):
            ClinicalCase(synthetic=True, history_available=True)

    def test_review_flag_rejects_integer(self):
        with self.assertRaises(ValidationError):
            result(message(State(case=case())), requires_human_review=1)

    def test_json_valid_case(self):
        expected = synthetic_example()
        self.assertEqual(parse_case(expected.model_dump_json().encode()), expected)

    def test_json_duplicate_unknown_invalid_and_deep(self):
        valid = synthetic_example().model_dump_json()
        samples = [b"", b"\xff", b"{", b"[]", b"null", b"[" * 2000 + b"]" * 2000,
                   (valid[:-1] + ',"synthetic":false}').encode(),
                   (valid[:-1] + ',"unexpected":"SECRET_MARKER"}').encode(),
                   valid.replace('1.0', 'NaN').encode(),
                   valid.replace('true', '1', 1).encode()]
        for payload in samples:
            with self.subTest(payload_length=len(payload)), self.assertRaises(InputRejected) as caught:
                parse_case(payload)
            self.assertEqual(str(caught.exception), "INVALID_INPUT")

    def test_oversized_payload_rejected(self):
        with self.assertRaises(InputRejected):
            parse_case(b" " * (MAX_INPUT_BYTES + 1))

    def test_event_history_tampering_rejected(self):
        manager = StateManager(case())
        state = manager.start(0)
        raw = state.model_dump()
        raw["events"] = ()
        raw["revision"] = 0
        with self.assertRaises(ValidationError):
            State.model_validate(raw)
        req = message(state)
        state = manager.dispatch(req, 1)
        raw = state.model_dump()
        raw["events"][1]["message_id"] = "other"
        with self.assertRaises(ValidationError):
            State.model_validate(raw)

    def test_cli_valid_complete_and_incomplete(self):
        for fixture, reason in (("complete", "REVIEW_REQUIRED"), ("incomplete", "MISSING_DATA")):
            completed = subprocess.run([sys.executable, "-m", "app", "--case", f"data/synthetic_cases/{fixture}.json"],
                                       cwd=ROOT, capture_output=True, text=True, timeout=10)
            self.assertEqual(completed.returncode, 0, completed.stderr)
            payload = json.loads(completed.stdout)
            self.assertEqual(payload["stop_reason"], reason)
            self.assertTrue(payload["human_review_required"])
            self.assertNotIn("case_id", payload)

    def test_cli_missing_file_sanitizes_path(self):
        completed = subprocess.run([sys.executable, "-m", "app", "--case", "SECRET_MARKER_NO_FILE"],
                                   cwd=ROOT, capture_output=True, text=True, timeout=10)
        self.assertEqual(completed.returncode, 2)
        self.assertEqual(completed.stderr.strip(), '{"error":"INVALID_INPUT"}')
        self.assertFalse(completed.stdout)

    def test_execution_limits_reject_nonfinite_and_invalid_values(self):
        for values in ({"max_steps": 0}, {"max_steps": True}, {"max_steps": 11},
                       {"agent_timeout_seconds": 0.0}, {"agent_timeout_seconds": float("nan")},
                       {"agent_timeout_seconds": float("inf")}):
            with self.subTest(values=values), self.assertRaises(ValidationError):
                ExecutionLimits(**values)


class HardeningTests(unittest.IsolatedAsyncioTestCase):
    async def test_summary_does_not_contain_payload(self):
        state = await runner().run(StateManager(case("SECRET_MARKER")))
        summary = summarize(state)
        self.assertNotIn("SECRET_MARKER", summary.model_dump_json())
        self.assertEqual(summary.completed, 2)
        self.assertEqual(summary.events, 6)

    async def test_late_result_from_cancellation_suppression_rejected(self):
        class LateAgent(HistoryAgent):
            async def execute(self, context):
                try:
                    await asyncio.sleep(10)
                except asyncio.CancelledError:
                    return await super().execute(context)
        state = await runner(history=LateAgent(), limits=ExecutionLimits(agent_timeout_seconds=0.01)).run(StateManager(case()))
        self.assertEqual(state.stop_reason, "TIMEOUT")
        self.assertFalse(state.results)

    async def test_out_of_order_planner_is_rejected(self):
        class SkipHistory(Planner):
            def create_plan(self, state):
                return Plan(action="execute", step="clinical_analysis")
        state = await runner(planner=SkipHistory()).run(StateManager(case()))
        self.assertEqual(state.stop_reason, "INVALID_PLAN")
        self.assertFalse(state.messages)

    async def test_snapshot_round_trip_after_execution(self):
        state = await runner().run(StateManager(case()))
        self.assertEqual(State.model_validate_json(state.model_dump_json()), state)
