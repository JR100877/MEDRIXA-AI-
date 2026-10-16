"""Subprocess used by the restart tests. Not collected by unittest (no test_ prefix).

    python -m tests.persistence.crash_worker STORE complete CASE_ID
    python -m tests.persistence.crash_worker STORE hang CASE_ID SIGNAL_FILE

``hang`` keeps an agent in flight forever after its dispatch is persisted, so the
parent can kill the process with SIGKILL (no cleanup handlers run).
"""
import asyncio
import sys
from pathlib import Path

from app.agents.clinical.agent import ClinicalAgent
from app.agents.history.agent import HistoryAgent
from app.core.orchestrator import ExecutionLimits, Orchestrator
from app.core.planner import Planner
from app.core.router import Router
from app.persistence.local_file import ProvisionalLocalFileRepository
from app.persistence.manager import PersistentStateManager
from tests.helpers import case


class HangingHistoryAgent(HistoryAgent):
    def __init__(self, signal_file: Path):
        self.signal_file = signal_file

    async def execute(self, context):
        self.signal_file.write_text("agent-in-flight", encoding="utf-8")
        await asyncio.sleep(3600)
        raise AssertionError("unreachable: the parent kills this process")


def main(argv):
    store, mode, case_id = argv[:3]
    history = HangingHistoryAgent(Path(argv[3])) if mode == "hang" else HistoryAgent()
    runner = Orchestrator(Planner(), Router({"history_analysis": history, "clinical_analysis": ClinicalAgent()}),
                          ExecutionLimits(agent_timeout_seconds=60))
    if store.startswith("pgdsn-env:"):  # P5: DSN passed through an environment variable, never argv
        import os
        from app.persistence.postgres import PostgresStateRepository
        repository = PostgresStateRepository(os.environ[store.split(":", 1)[1]], migrate=False)
    else:
        repository = ProvisionalLocalFileRepository(store)
    manager = PersistentStateManager(case(case_id), repository)
    print(manager.run_id, flush=True)
    final = asyncio.run(runner.run(manager))
    print(final.status, final.stop_reason, flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
