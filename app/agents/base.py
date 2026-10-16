from abc import ABC, abstractmethod

from app.models.contracts import AgentContext, AgentResult, Step


class BaseAgent(ABC):
    """Implementations must be trusted, nonblocking and cancellation-cooperative."""
    agent_id: str
    step: Step

    @abstractmethod
    async def execute(self, context: AgentContext) -> AgentResult:
        """Return a correlated result. Never mutate CORE state."""
        raise NotImplementedError
