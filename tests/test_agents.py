import unittest
from pydantic import ValidationError

from app.agents.clinical.agent import ClinicalAgent
from app.agents.history.agent import HistoryAgent
from app.core.state import State
from app.models.contracts import AgentContext
from tests.helpers import case, message


class AgentTests(unittest.IsolatedAsyncioTestCase):
    async def test_history_present_and_missing(self):
        for available in (True, False):
            state = State(case=case(history=available))
            request = message(state)
            result = await HistoryAgent().execute(AgentContext(case=state.case, message=request))
            self.assertEqual(result.status, "completed" if available else "abstained")
            self.assertEqual(result.message_id, request.message_id)
            self.assertTrue(result.requires_human_review)

    async def test_clinical_only_reports_completeness(self):
        for available in (True, False):
            state = State(case=case(observations=available))
            request = message(state, agent_id=ClinicalAgent.agent_id, step=ClinicalAgent.step)
            result = await ClinicalAgent().execute(AgentContext(case=state.case, message=request))
            self.assertEqual(result.status, "completed" if available else "abstained")
            self.assertEqual({f.code for f in result.findings},
                             {"OBSERVATIONS_PRESENT" if available else "OBSERVATIONS_MISSING", "SYNTHETIC_ONLY"})

    async def test_wrong_agent_rejected(self):
        state = State(case=case())
        with self.assertRaises(ValueError):
            await ClinicalAgent().execute(AgentContext(case=state.case, message=message(state)))
