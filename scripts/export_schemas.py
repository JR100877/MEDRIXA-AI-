"""Reproduce provisional JSON Schema contracts without a network dependency."""
import json
from pathlib import Path

from app.core.orchestrator import ExecutionLimits
from app.core.planner import Plan
from app.core.state import State
from app.models.contracts import AgentContext, AgentMessage, AgentResult, ClinicalCase
from app.observability.summary import RunSummary


def main():
    target = Path(__file__).resolve().parents[1] / "contracts" / "medrixa.prototype.v1"
    target.mkdir(parents=True, exist_ok=True)
    for model in (ClinicalCase, AgentContext, AgentMessage, AgentResult, State, Plan, ExecutionLimits, RunSummary):
        (target / f"{model.__name__}.schema.json").write_text(
            json.dumps(model.model_json_schema(), indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
