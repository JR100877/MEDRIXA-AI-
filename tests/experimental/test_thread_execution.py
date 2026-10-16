"""P9 EXPERIMENT: option C (thread + own event loop per run) versus inline execution (P8).

Characterisation tests: they record how each strategy behaves, including where
option C DIFFERS (agent loop affinity, cancellation, capacity accounting).
Coordination with agents uses threading primitives, which are safe across loops.
"""
import asyncio
import io
import json
import logging
import secrets
import threading
import time
import unittest

import httpx
from pydantic import SecretStr

from app.agents.clinical.agent import ClinicalAgent
from app.agents.history.agent import HistoryAgent
from app.core.orchestrator import ExecutionLimits, Orchestrator
from app.core.planner import Planner
from app.core.router import Router
from app.experimental.execution import ThreadLoopExecution
from app.persistence.leases import InMemoryRunLeases
from app.web.app import create_app
from app.web.audit import MemoryAuditSink
from app.web.auth import issue_token
from app.web.settings import Settings
from tests.web.support import case_body


class ThreadGateAgent(HistoryAgent):
    """Blocks on a threading.Event without blocking its own event loop."""

    def __init__(self, gate: threading.Event | None = None, block_seconds: float = 0.0):
        self.gate, self.block_seconds = gate, block_seconds
        self.entered = threading.Event()
        self.calls = 0
        self.threads: set[str] = set()

    async def execute(self, context):
        self.calls += 1
        self.threads.add(threading.current_thread().name)
        self.entered.set()
        if self.gate is not None:
            await asyncio.to_thread(self.gate.wait, 10)
        if self.block_seconds:
            time.sleep(self.block_seconds)  # simulates synchronous I/O (e.g. psycopg) in the run
        logging.getLogger("medrixa.experiment").warning("agent step")
        return await super().execute(context)


class LoopEventAgent(HistoryAgent):
    """Coordinates through an asyncio.Event created in the CALLER's loop (as P4b tests do)."""

    def __init__(self, event: asyncio.Event):
        self.event = event

    async def execute(self, context):
        await self.event.wait()
        return await super().execute(context)


def factory(agent, timeout=10.0):
    return lambda: Orchestrator(Planner(), Router({"history_analysis": agent, "clinical_analysis": ClinicalAgent()}),
                                ExecutionLimits(agent_timeout_seconds=timeout))


class Harness:
    def __init__(self, strategy: str, agent, timeout=10.0, max_active_runs=16, leases=None, lease_ttl=30.0,
                 secret=None, **settings_kwargs):
        self.secret = secret or secrets.token_urlsafe(48)
        self.execution = ThreadLoopExecution(max_active_runs) if strategy == "thread" else None
        settings = Settings(environment="test", auth_secret=SecretStr(self.secret), allowed_hosts=("testserver",),
                            max_active_runs=max_active_runs, lease_ttl_seconds=lease_ttl,
                            rate_limit_ip_per_minute=100_000, rate_limit_subject_per_minute=100_000,
                            **settings_kwargs)
        self.app = create_app(settings, audit=MemoryAuditSink(), runner_factory=factory(agent, timeout),
                              execution=self.execution, leases=leases)
        self.service = self.app.state.service
        self.auth = {"Authorization": f"Bearer {issue_token(self.secret, 'u1', ['case_runner'], ['case-a'])}"}

    def pool(self) -> ThreadLoopExecution:
        assert self.execution is not None, "option C harness expected"
        return self.execution

    def client(self):
        return httpx.AsyncClient(transport=httpx.ASGITransport(app=self.app), base_url="http://testserver")

    def post(self, http, key, request_id=None):
        headers = {**self.auth, "Idempotency-Key": key}
        if request_id:
            headers["X-Request-ID"] = request_id
        return http.post("/v1/runs", json=case_body(), headers=headers)

    def close(self):
        if self.execution is not None:
            self.execution.shutdown(wait=True)
        for component in (self.service.repository, self.service.leases):
            close = getattr(component, "close", None)
            if callable(close):
                close()


async def wait_thread_event(event: threading.Event, timeout=10.0):
    assert await asyncio.to_thread(event.wait, timeout), "timed out"


class SemanticsPreservedTests(unittest.IsolatedAsyncioTestCase):
    """The P4b/P7 guarantees, re-verified under option C with thread-safe coordination."""

    async def test_runs_execute_in_worker_threads_and_complete(self):
        agent = ThreadGateAgent()
        h = Harness("thread", agent)
        self.addCleanup(h.close)
        async with h.client() as http:
            response = await h.post(http, "exp-key-00000000001")
        self.assertEqual((response.status_code, response.json()["stop_reason"]), (201, "REVIEW_REQUIRED"))
        self.assertTrue(all(name.startswith("medrixa-run") for name in agent.threads), agent.threads)

    async def test_concurrent_duplicates_execute_once(self):
        gate = threading.Event()
        agent = ThreadGateAgent(gate)
        h = Harness("thread", agent)
        self.addCleanup(h.close)
        async with h.client() as http:
            first = asyncio.create_task(h.post(http, "exp-dup-key-0000001"))
            await wait_thread_event(agent.entered)
            duplicates = await asyncio.gather(*(h.post(http, "exp-dup-key-0000001") for _ in range(9)))
            gate.set()
            winner = await first
        self.assertEqual(winner.status_code, 201)
        self.assertEqual({r.json()["error"]["code"] for r in duplicates}, {"IDEMPOTENCY_IN_PROGRESS"})
        self.assertEqual(agent.calls, 1)

    async def test_postgres_duplicates_execute_once_and_replay_after_restart(self):
        from tests.persistence.pg_support import fresh_database_dsn
        dsn = fresh_database_dsn()  # skipped (never passed) when PostgreSQL is unavailable
        gate = threading.Event()
        agent = ThreadGateAgent(gate)
        h = Harness("thread", agent, postgres_dsn=dsn, postgres_pool_size=4)
        self.addCleanup(h.close)
        async with h.client() as http:
            first = asyncio.create_task(h.post(http, "exp-pg-key-00000001"))
            await wait_thread_event(agent.entered)
            duplicates = await asyncio.gather(*(h.post(http, "exp-pg-key-00000001") for _ in range(5)))
            gate.set()
            winner = await first
        self.assertEqual(winner.status_code, 201)
        self.assertEqual({r.json()["error"]["code"] for r in duplicates}, {"IDEMPOTENCY_IN_PROGRESS"})
        restarted_agent = ThreadGateAgent()
        restarted = Harness("thread", restarted_agent, secret=h.secret, postgres_dsn=dsn, postgres_pool_size=4)
        self.addCleanup(restarted.close)
        async with restarted.client() as http:
            replay = await restarted.post(http, "exp-pg-key-00000001")
        self.assertEqual((replay.status_code, replay.headers["idempotent-replayed"], replay.json()),
                         (201, "true", winner.json()))
        self.assertEqual((agent.calls, restarted_agent.calls), (1, 0))

    async def test_capacity_and_recovery_guard_while_a_run_is_active(self):
        gate = threading.Event()
        agent = ThreadGateAgent(gate)
        h = Harness("thread", agent, max_active_runs=1)
        self.addCleanup(h.close)
        operator = {"Authorization": f"Bearer {issue_token(h.secret, 'ops', ['operator'], [])}"}
        async with h.client() as http:
            first = asyncio.create_task(h.post(http, "exp-cap-key-0000001"))
            await wait_thread_event(agent.entered)
            refused = await h.post(http, "exp-cap-key-0000002")
            recovery = await http.post("/v1/admin/recovery", headers=operator)
            gate.set()
            self.assertEqual((await first).status_code, 201)
        self.assertEqual((refused.status_code, refused.json()["error"]["code"]), (503, "CAPACITY_EXCEEDED"))
        self.assertEqual((recovery.status_code, recovery.json()["error"]["code"]), (409, "RUNS_ACTIVE"))
        self.assertEqual(len(h.service.repository.list_runs()), 1)

    async def test_request_id_reaches_worker_thread_logs(self):
        from app.observability.context import _OperationalHandler, configure_operational_logging
        stream = io.StringIO()
        logger = configure_operational_logging(stream)
        def detach():
            for handler in list(logger.handlers):
                if isinstance(handler, _OperationalHandler):
                    logger.removeHandler(handler)
        self.addCleanup(detach)
        h = Harness("thread", ThreadGateAgent())
        self.addCleanup(h.close)
        async with h.client() as http:
            await h.post(http, "exp-ctx-key-0000001", request_id="exp-req-000000000001")
        lines = [json.loads(x) for x in stream.getvalue().splitlines() if '"medrixa.experiment"' in x]
        self.assertEqual([x["request_id"] for x in lines], ["exp-req-000000000001"])


class DifferencesTests(unittest.IsolatedAsyncioTestCase):
    """Behaviour that CHANGES with option C."""

    async def run_with_loop_event(self, strategy):
        event = asyncio.Event()  # created in the caller's (test/HTTP) loop
        h = Harness(strategy, LoopEventAgent(event), timeout=1.5)
        self.addCleanup(h.close)
        async with h.client() as http:
            task = asyncio.create_task(h.post(http, f"exp-loop-{strategy}-00001"))
            await asyncio.sleep(0.3)
            event.set()
            return (await asyncio.wait_for(task, 10)).json()["stop_reason"]

    async def test_agents_bound_to_the_caller_loop_break_under_option_c(self):
        self.assertEqual(await self.run_with_loop_event("inline"), "REVIEW_REQUIRED")
        # Under C the agent waits in another loop; set() from this loop never wakes it:
        # the run ends only through the cooperative agent timeout.
        self.assertEqual(await self.run_with_loop_event("thread"), "TIMEOUT")

    async def cancel_midway(self, strategy):
        gate = threading.Event()
        agent = ThreadGateAgent(gate)
        h = Harness(strategy, agent)
        self.addCleanup(h.close)
        async with h.client() as http:
            task = asyncio.create_task(h.post(http, f"exp-cancel-{strategy}-1"))
            await wait_thread_event(agent.entered)
            task.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await task
            slots_after_cancel = h.service.active_runs
            gate.set()
            await asyncio.sleep(0.5)
            in_flight = h.execution.in_flight if h.execution else 0
            replay = await h.post(http, f"exp-cancel-{strategy}-1")
        return replay, slots_after_cancel, in_flight

    async def test_client_cancellation_semantics(self):
        replay, slots, _ = await self.cancel_midway("inline")
        # Inline: cancellation propagates into the run, which ends in review as CANCELLED.
        self.assertEqual((replay.status_code, replay.json()["stop_reason"]), (201, "CANCELLED"))
        self.assertEqual(slots, 0)
        replay, slots, in_flight = await self.cancel_midway("thread")
        # Option C: the run is NOT cancelled; it finishes normally and the retry replays it.
        self.assertEqual((replay.status_code, replay.json()["stop_reason"]), (201, "REVIEW_REQUIRED"))
        self.assertEqual(in_flight, 0)

    async def test_capacity_slot_released_before_worker_finishes(self):
        """DEFECT of the naive option C prototype (must be fixed before adoption): the
        reservation is released when the awaiting request is cancelled, while the worker
        thread is still executing, so admission control under-counts in-flight runs."""
        gate = threading.Event()
        agent = ThreadGateAgent(gate)
        h = Harness("thread", agent, max_active_runs=1)
        self.addCleanup(h.close)
        async with h.client() as http:
            task = asyncio.create_task(h.post(http, "exp-slot-key-000001"))
            await wait_thread_event(agent.entered)
            task.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await task
            self.assertEqual((h.service.active_runs, h.pool().in_flight), (0, 1))  # mismatch
            admitted = asyncio.create_task(h.post(http, "exp-slot-key-000002"))
            await asyncio.sleep(0.3)
            self.assertFalse(admitted.done())  # admitted, queued behind the busy worker
            gate.set()
            self.assertEqual((await asyncio.wait_for(admitted, 10)).status_code, 201)


class EventLoopResponsivenessTests(unittest.IsolatedAsyncioTestCase):
    """The point of option C: blocking I/O inside a run no longer stalls other requests."""

    async def health_latency_during_blocking_run(self, strategy):
        agent = ThreadGateAgent(block_seconds=0.8)
        h = Harness(strategy, agent)
        self.addCleanup(h.close)
        async with h.client() as http:
            worst = 0.0
            run = asyncio.create_task(h.post(http, f"exp-block-{strategy}-01"))
            # Probe /v1/health continuously while the run executes; record the worst probe
            # cycle (request + 10 ms pause), i.e. how long the loop could not serve anyone.
            while not run.done():
                started = time.perf_counter()
                await http.get("/v1/health")
                await asyncio.sleep(0.01)
                worst = max(worst, time.perf_counter() - started)
            self.assertEqual((await run).status_code, 201)
            self.assertTrue(agent.entered.is_set())
        return worst

    async def test_health_is_stalled_inline_but_not_with_option_c(self):
        inline = await self.health_latency_during_blocking_run("inline")
        threaded = await self.health_latency_during_blocking_run("thread")
        self.assertGreater(inline, 0.5)
        self.assertLess(threaded, 0.2)

    async def test_lease_heartbeat_survives_a_stalled_http_loop_only_with_option_c(self):
        outcomes = {}
        for strategy in ("inline", "thread"):
            gate = threading.Event()
            agent = ThreadGateAgent(gate)
            h = Harness(strategy, agent, leases=InMemoryRunLeases(), lease_ttl=1.0)
            self.addCleanup(h.close)
            async with h.client() as http:
                run = asyncio.create_task(h.post(http, f"exp-hb-{strategy}-0001"))
                await wait_thread_event(agent.entered)
                time.sleep(1.5)       # the HTTP loop is stalled (e.g. sync I/O elsewhere)
                gate.set()
                response = await asyncio.wait_for(run, 15)
            outcomes[strategy] = response.json().get("stop_reason") or response.json()["error"]["code"]
        # Inline: the stalled loop could not renew the 1 s lease -> execution stops (409 CONFLICT).
        # Option C: renewals run in the worker loop -> the run completes.
        self.assertEqual(outcomes, {"inline": "CONFLICT", "thread": "REVIEW_REQUIRED"})


class ResourceTests(unittest.IsolatedAsyncioTestCase):
    async def test_threads_are_bounded_and_shutdown_waits_for_in_flight_runs(self):
        gate = threading.Event()
        agent = ThreadGateAgent(gate)
        h = Harness("thread", agent, max_active_runs=4)
        async with h.client() as http:
            tasks = [asyncio.create_task(h.post(http, f"exp-res-key-{i:07d}")) for i in range(32)]
            await asyncio.sleep(0.5)
            workers = [t for t in threading.enumerate() if t.name.startswith("medrixa-run")]
            self.assertLessEqual(len(workers), 4)
            self.assertLessEqual(h.pool().peak_in_flight, 4)
            stopper = threading.Timer(0.5, gate.set)
            stopper.start()
            started = time.perf_counter()
            await asyncio.to_thread(h.pool().shutdown, True)  # waits for the gated runs
            waited = time.perf_counter() - started
            results = await asyncio.gather(*tasks)
        self.assertGreaterEqual(waited, 0.4)
        statuses = sorted({r.status_code for r in results})
        self.assertEqual(statuses, [201, 503])  # capacity sheds the excess; admitted runs finish


if __name__ == "__main__":
    unittest.main()
