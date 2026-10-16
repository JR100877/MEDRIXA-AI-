"""Helpers for P4b HTTP tests. Synthetic data only; secrets are generated per test."""
import asyncio
import secrets
from pathlib import Path

from fastapi.testclient import TestClient

from app.agents.clinical.agent import ClinicalAgent
from app.agents.history.agent import HistoryAgent
from app.core.orchestrator import Orchestrator
from app.core.planner import Planner
from app.core.router import Router
from app.web.app import create_app
from app.web.audit import MemoryAuditSink
from app.web.auth import issue_token
from app.web.settings import Settings

MARKER = "SECRET_MARKER_P4B"


def case_body(case_id="case-a", history=True, **extra_case):
    case = {"case_id": case_id, "synthetic": True, "source": "synthetic_fixture", "history_available": history,
            "observations": [{"code": "demo-measure", "value": 1.0, "unit": "demo-unit"}], **extra_case}
    return {"case": case}


class CountingHistoryAgent(HistoryAgent):
    """Counts executions; optionally waits on an event to keep a run in flight."""

    def __init__(self, gate: asyncio.Event | None = None, entered: asyncio.Event | None = None):
        self.calls = 0
        self.gate = gate
        self.entered = entered

    async def execute(self, context):
        self.calls += 1
        if self.entered is not None:
            self.entered.set()
        if self.gate is not None:
            await self.gate.wait()
        return await super().execute(context)


def runner_factory_for(history_agent):
    return lambda: Orchestrator(Planner(), Router({"history_analysis": history_agent,
                                                   "clinical_analysis": ClinicalAgent()}))


class Api:
    """App + client + token factory around one generated secret."""

    def __init__(self, repository=None, audit=None, runner_factory=None, environment="test", store_dir=None,
                 secret=None):
        self.secret = secret or secrets.token_urlsafe(48)
        self.audit = audit if audit is not None else MemoryAuditSink()
        self.settings = Settings(environment=environment, auth_secret=self.secret,
                                 store_dir=Path(store_dir) if store_dir else None,
                                 allowed_hosts=("testserver", "127.0.0.1"))
        self.app = create_app(self.settings, repository=repository, audit=self.audit,
                              runner_factory=runner_factory)
        self.client = TestClient(self.app)

    def token(self, subject="user-runner", roles=("case_runner",), cases=("case-a",), ttl=900, now=None):
        return issue_token(self.secret, subject, roles, cases, ttl, now)

    def auth(self, **kwargs):
        return {"Authorization": f"Bearer {self.token(**kwargs)}"}

    def create(self, body=None, key="key-0000000000000001", headers=None, **token_kwargs):
        merged = {**self.auth(**token_kwargs), "Idempotency-Key": key, **(headers or {})}
        return self.client.post("/v1/runs", json=body if body is not None else case_body(), headers=merged)
