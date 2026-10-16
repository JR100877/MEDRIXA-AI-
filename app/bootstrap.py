from app.agents.clinical.agent import ClinicalAgent
from app.agents.history.agent import HistoryAgent
from app.core.orchestrator import Orchestrator
from app.core.planner import Planner
from app.core.router import Router
from app.models.contracts import ClinicalCase, Observation


def create_runner() -> Orchestrator:
    return Orchestrator(Planner(), Router({
        "history_analysis": HistoryAgent(),
        "clinical_analysis": ClinicalAgent(),
    }))


def synthetic_example() -> ClinicalCase:
    return ClinicalCase(case_id="demo-synthetic-001", synthetic=True, source="synthetic_fixture",
                        history_available=True,
                        observations=(Observation(code="demo-measure", value=1.0, unit="demo-unit"),))
