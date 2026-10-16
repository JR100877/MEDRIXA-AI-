from app.agents.base import BaseAgent
from app.models.contracts import AgentContext, AgentResult, Finding


class HistoryAgent(BaseAgent):
    agent_id = "history-simulator"
    step = "history_analysis"

    async def execute(self, context: AgentContext) -> AgentResult:
        context = AgentContext.model_validate(context)
        request = context.message
        if request.agent_id != self.agent_id or request.step != self.step:
            raise ValueError("wrong agent or step")
        available = context.case.history_available
        return AgentResult(message_id=request.message_id, case_id=request.case_id,
                           run_id=request.run_id, agent_id=self.agent_id,
                           status="completed" if available else "abstained",
                           findings=(Finding(code="HISTORY_PRESENT" if available else "HISTORY_MISSING",
                                             rule_id="history-presence-v1"),))
