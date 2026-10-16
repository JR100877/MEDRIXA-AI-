import unittest
from concurrent.futures import ThreadPoolExecutor

from pydantic import ValidationError

from app.core.state import State
from app.core.state_manager import ContractViolation, InvalidTransition, StateConflict, StateManager
from app.models.contracts import AgentContext, AgentResult, ClinicalCase, Observation
from tests.helpers import case, message, result


class ContractTests(unittest.TestCase):
    def test_real_data_marker_rejected(self):
        with self.assertRaises(ValidationError):
            ClinicalCase(history_available=True, synthetic=False)

    def test_unknown_fields_and_coercion_rejected(self):
        for values in ({"history_available": "yes"}, {"history_available": True, "patient_name": "synthetic"}):
            with self.subTest(values=values), self.assertRaises(ValidationError):
                ClinicalCase(**values)

    def test_nonfinite_observation_rejected(self):
        for value in (float("nan"), float("inf"), float("-inf")):
            with self.subTest(value=value), self.assertRaises(ValidationError):
                Observation(code="x", value=value, unit="u")

    def test_duplicate_observation_rejected(self):
        obs = Observation(code="x", value=1.0, unit="u")
        with self.assertRaises(ValidationError):
            ClinicalCase(history_available=True, observations=(obs, obs))

    def test_snapshot_and_nested_case_frozen(self):
        state = State(case=case())
        with self.assertRaises(ValidationError):
            state.revision = 99
        with self.assertRaises(ValidationError):
            state.case.history_available = False

    def test_json_round_trip(self):
        state = State(case=case())
        self.assertEqual(State.model_validate_json(state.model_dump_json()), state)

    def test_context_rejects_other_case_and_run(self):
        state = State(case=case())
        request = message(state)
        for values in ({"case": case("other"), "message": request},
                       {"case": state.case, "message": request, "prior_results": (result(request, run_id="other"),)}):
            with self.subTest(values=values), self.assertRaises(ValidationError):
                AgentContext(**values)

    def test_human_review_cannot_be_disabled(self):
        request = message(State(case=case()))
        with self.assertRaises(ValidationError):
            result(request, requires_human_review=False)

    def test_constructed_invalid_instance_revalidated(self):
        invalid = ClinicalCase.model_construct(history_available=True, synthetic=False)
        with self.assertRaises(ValidationError):
            StateManager(invalid)


class StateManagerTests(unittest.TestCase):
    def setUp(self):
        self.manager = StateManager(case())

    def active_request(self):
        state = self.manager.start(0)
        request = message(state)
        self.manager.dispatch(request, state.revision)
        return request

    def test_complete_flow_and_audit(self):
        request = self.active_request()
        self.manager.accept(result(request), 2)
        final = self.manager.request_review("REVIEW_REQUIRED", 3)
        self.assertEqual(final.status, "awaiting_human_review")
        self.assertEqual(final.revision, 4)
        self.assertEqual([e.kind for e in final.events], ["RUN_STARTED", "AGENT_DISPATCHED", "RESULT_ACCEPTED", "REVIEW_REQUESTED"])
        self.assertIsNone(final.pending_message)

    def test_stale_revision_does_not_mutate(self):
        self.manager.start(0)
        before = self.manager.snapshot()
        with self.assertRaises(StateConflict):
            self.manager.request_review("REVIEW_REQUIRED", 0)
        self.assertEqual(self.manager.snapshot(), before)

    def test_dispatch_before_start_rejected(self):
        with self.assertRaises(InvalidTransition):
            self.manager.dispatch(message(self.manager.snapshot()), 0)

    def test_terminal_state_cannot_restart(self):
        self.manager.start(0)
        self.manager.request_review("REVIEW_REQUIRED", 1)
        with self.assertRaises(InvalidTransition):
            self.manager.start(2)

    def test_correlations_and_atomic_rejection(self):
        request = self.active_request()
        for key in ("case_id", "run_id", "message_id", "agent_id"):
            before = self.manager.snapshot()
            with self.subTest(key=key), self.assertRaises(ContractViolation):
                self.manager.accept(result(request, **{key: "other"}), 2)
            self.assertEqual(self.manager.snapshot(), before)

    def test_duplicate_result_and_pending_dispatch_rejected(self):
        request = self.active_request()
        with self.assertRaises(InvalidTransition):
            self.manager.dispatch(message(self.manager.snapshot()), 2)
        accepted = result(request)
        self.manager.accept(accepted, 2)
        with self.assertRaises(InvalidTransition):
            self.manager.accept(accepted, 3)
        with self.assertRaises(ContractViolation):
            self.manager.dispatch(request, 3)

    def test_concurrent_revision_has_one_winner(self):
        self.manager.start(0)
        def update(_):
            try:
                self.manager.request_review("REVIEW_REQUIRED", 1)
                return "ok"
            except StateConflict:
                return "conflict"
        with ThreadPoolExecutor(max_workers=2) as pool:
            self.assertCountEqual(list(pool.map(update, range(2))), ["ok", "conflict"])

    def test_case_isolation(self):
        other = StateManager(case("synthetic-case-2"))
        state = self.manager.start(0)
        with self.assertRaises(ContractViolation):
            self.manager.dispatch(message(other.snapshot()), state.revision)
        self.assertEqual(other.snapshot().revision, 0)


if __name__ == "__main__":
    unittest.main()
