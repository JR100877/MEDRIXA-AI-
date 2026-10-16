import inspect
from collections.abc import Mapping
from types import MappingProxyType

from pydantic import TypeAdapter

from app.agents.base import BaseAgent
from app.models.contracts import Identifier, Step


class UnknownRoute(LookupError):
    pass


class Router:
    """Explicit trusted registry. Resolution never executes the selected agent."""
    def __init__(self, routes: Mapping[str, BaseAgent]):
        copied = dict(routes)
        agent_ids = []
        for step, agent in copied.items():
            TypeAdapter(Step).validate_python(step)
            if not isinstance(agent, BaseAgent) or agent.step != step or not inspect.iscoroutinefunction(agent.execute):
                raise ValueError("agent must implement the declared async contract")
            TypeAdapter(Identifier).validate_python(agent.agent_id)
            agent_ids.append(agent.agent_id)
        if len(set(agent_ids)) != len(agent_ids):
            raise ValueError("agent identifiers must be unique")
        self._routes = MappingProxyType(copied)

    def resolve(self, step: Step) -> BaseAgent:
        try:
            return self._routes[step]
        except KeyError:
            raise UnknownRoute("unregistered step") from None
