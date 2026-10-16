from app.agents.base import BaseAgent
from app.models.contracts import AgentContext, AgentResult, Finding


class ClinicalAgent(BaseAgent):
    """Historical role name retained; this agent ONLY checks synthetic data presence."""
    agent_id = "clinical-data-simulator"
    step = "clinical_analysis"

    async def execute(self, context: AgentContext) -> AgentResult:
        context = AgentContext.model_validate(context)
        request = context.message
        if request.agent_id != self.agent_id or request.step != self.step:
            raise ValueError("wrong agent or step")
        available = bool(context.case.observations)
        return AgentResult(message_id=request.message_id, case_id=request.case_id,
                           run_id=request.run_id, agent_id=self.agent_id,
                           status="completed" if available else "abstained",
                           findings=(Finding(code="OBSERVATIONS_PRESENT" if available else "OBSERVATIONS_MISSING",
                                             rule_id="observation-presence-v1"),
                                     Finding(code="SYNTHETIC_ONLY", rule_id="synthetic-scope-v1")))
