"""P6: run leases — contract for every adapter, executor heartbeat/guard, recovery, SIGKILL."""
import asyncio
import json
import os
import signal
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path

from app.agents.clinical.agent import ClinicalAgent
from app.agents.history.agent import HistoryAgent
from app.core.orchestrator import ExecutionLimits, Orchestrator
from app.core.planner import Planner
from app.core.router import Router
from app.persistence.leases import (InMemoryRunLeases, LeaseHeld, LeaseLost, LocalFileRunLeases, RunLeases,
                                    new_owner_id)
from app.persistence.leasing import LeasedExecutor
from app.persistence.local_file import ProvisionalLocalFileRepository
from app.persistence.manager import PersistentStateManager
from app.persistence.memory import InMemoryStateRepository
from app.persistence.recovery import recover_interrupted_runs
from tests.helpers import case, message

PROJECT_ROOT = Path(__file__).resolve().parents[2]
TTL = 0.6  # short real-time TTL; expiry is observed by waiting, never faked


class LeaseContract:
    """Mixin: subclasses provide make_leases() and peer(leases)."""

    def make_leases(self) -> RunLeases:
        raise NotImplementedError

    def peer(self, leases) -> RunLeases:
        raise NotImplementedError

    def test_acquire_is_exclusive_while_live(self):
        leases = self.make_leases()
        lease = leases.acquire("run-1", "owner-a", 30)
        self.assertEqual((lease.run_id, lease.owner, lease.epoch), ("run-1", "owner-a", 1))
        self.assertEqual(self.peer(leases).live("run-1"), lease)
        for owner in ("owner-b", "owner-a"):
            with self.subTest(owner=owner), self.assertRaises(LeaseHeld):
                self.peer(leases).acquire("run-1", owner, 30)
        self.assertIsNone(leases.live("run-2"))

    def test_renew_only_by_live_holder(self):
        leases = self.make_leases()
        lease = leases.acquire("run-1", "owner-a", 30)
        renewed = leases.renew(lease, 60)
        self.assertGreater(renewed.expires_at, lease.expires_at)
        self.assertEqual((renewed.owner, renewed.epoch), ("owner-a", 1))
        for stale in (lease.model_copy(update={"owner": "owner-b"}), lease.model_copy(update={"epoch": 2})):
            with self.subTest(stale=(stale.owner, stale.epoch)), self.assertRaises(LeaseLost):
                self.peer(leases).renew(stale, 30)

    def test_expired_lease_is_never_revived_and_can_be_taken(self):
        leases = self.make_leases()
        old = leases.acquire("run-1", "owner-a", TTL)
        time.sleep(TTL + 0.3)
        self.assertIsNone(leases.live("run-1"))
        with self.assertRaises(LeaseLost):
            leases.renew(old, 30)  # recovery may already have acted on the expired lease
        new = self.peer(leases).acquire("run-1", "owner-b", 30)
        self.assertEqual((new.owner, new.epoch), ("owner-b", 2))
        with self.assertRaises(LeaseLost):
            leases.renew(old, 30)

    def test_release_frees_and_stale_release_is_harmless(self):
        leases = self.make_leases()
        first = leases.acquire("run-1", "owner-a", 30)
        leases.release(first)
        self.assertIsNone(leases.live("run-1"))
        second = self.peer(leases).acquire("run-1", "owner-b", 30)
        self.assertEqual(second.epoch, 2)
        leases.release(first)  # stale holder: must not affect owner-b
        self.assertEqual(leases.live("run-1"), second)

    def test_concurrent_acquire_single_winner(self):
        leases = self.make_leases()
        barrier, outcomes = threading.Barrier(8), []

        def contend(i):
            handle = self.peer(leases)
            barrier.wait()
            try:
                handle.acquire("run-1", f"owner-{i}", 30)
                outcomes.append("ok")
            except LeaseHeld:
                outcomes.append("held")
        threads = [threading.Thread(target=contend, args=(i,)) for i in range(8)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(30)
        self.assertEqual(sorted(outcomes), ["held"] * 7 + ["ok"])

    def test_invalid_arguments_rejected(self):
        leases = self.make_leases()
        for run_id, owner, ttl in (("run-1", "o", 0), ("run-1", "o", -1), ("run-1", "o", 4000), ("run-1", "o", True),
                                   ("../x", "o", 30), ("run-1", "", 30), ("run-1", "o", float("nan"))):
            with self.subTest(run_id=run_id, owner=owner, ttl=ttl), self.assertRaises(ValueError):
                leases.acquire(run_id, owner, ttl)


class InMemoryLeaseTests(LeaseContract, unittest.TestCase):
    def make_leases(self):
        return InMemoryRunLeases()

    def peer(self, leases):
        return leases


class LocalFileLeaseTests(LeaseContract, unittest.TestCase):
    def make_leases(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        return LocalFileRunLeases(directory.name)

    def peer(self, leases):
        return LocalFileRunLeases(leases.root)

    def test_corrupt_lease_file_grants_no_liveness_and_never_resets_epoch(self):
        leases = self.make_leases()
        lease = leases.acquire("run-1", "owner-a", 30)
        path = next(p for p in leases.root.iterdir() if p.name.endswith(".lease.json"))
        path.write_text(json.dumps({"epoch": lease.epoch, "garbage": True}))
        self.assertIsNone(leases.live("run-1"))
        self.assertEqual(leases.acquire("run-1", "owner-b", 30).epoch, 2)


class PostgresLeaseTests(LeaseContract, unittest.TestCase):
    def make_leases(self):
        from app.persistence.postgres import PostgresRunLeases
        from tests.persistence.pg_support import fresh_database_dsn
        return PostgresRunLeases(fresh_database_dsn())

    def peer(self, leases):
        from app.persistence.postgres import PostgresRunLeases
        return PostgresRunLeases(leases._db._dsn)

    def test_migration_v2_is_additive(self):
        from app.persistence.postgres import PostgresStateRepository
        leases = self.make_leases()
        self.assertEqual(leases.migrate(), 2)
        self.assertEqual(PostgresStateRepository(leases._db._dsn).migrate(), 2)  # v1 re-run keeps v2


class CountingAgent(HistoryAgent):
    def __init__(self, delay=0.0):
        self.calls, self.delay = 0, delay

    async def execute(self, context):
        self.calls += 1
        await asyncio.sleep(self.delay)
        return await super().execute(context)


def runner_with(agent, timeout=60.0):
    return Orchestrator(Planner(), Router({"history_analysis": agent, "clinical_analysis": ClinicalAgent()}),
                        ExecutionLimits(agent_timeout_seconds=timeout))


class LeasedExecutionTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.repo, self.leases = InMemoryStateRepository(), InMemoryRunLeases()

    async def test_lease_held_during_run_and_released_after(self):
        manager = PersistentStateManager(case(), self.repo)
        agent = CountingAgent(delay=0.3)
        task = asyncio.create_task(LeasedExecutor(self.leases, ttl_seconds=TTL, heartbeat_seconds=0.1)
                                   .run(runner_with(agent), manager))
        await asyncio.sleep(0.15)
        self.assertIsNotNone(self.leases.live(manager.run_id))
        final = await task
        self.assertEqual((final.stop_reason, agent.calls), ("REVIEW_REQUIRED", 1))
        self.assertIsNone(self.leases.live(manager.run_id))

    async def test_heartbeat_keeps_a_run_longer_than_ttl_alive_and_recovery_skips_it(self):
        manager = PersistentStateManager(case(), self.repo)
        agent = CountingAgent(delay=TTL * 3)
        task = asyncio.create_task(LeasedExecutor(self.leases, ttl_seconds=TTL, heartbeat_seconds=TTL / 4)
                                   .run(runner_with(agent), manager))
        await asyncio.sleep(TTL * 2)  # well past the first expiry
        report = recover_interrupted_runs(self.repo, self.leases)
        self.assertEqual((report.active_elsewhere, report.moved_to_review), ((manager.run_id,), ()))
        final = await task
        self.assertEqual((final.status, final.stop_reason, agent.calls), ("awaiting_human_review",
                                                                          "REVIEW_REQUIRED", 1))

    async def test_lost_lease_stops_execution_without_retry(self):
        manager = PersistentStateManager(case(), self.repo)
        agent = CountingAgent(delay=5)
        executor = LeasedExecutor(self.leases, ttl_seconds=TTL, heartbeat_seconds=0.1)
        task = asyncio.create_task(executor.run(runner_with(agent), manager))
        await asyncio.sleep(0.2)
        lease = self.leases.live(manager.run_id)
        self.leases.release(lease)                       # ownership lost (e.g. operator action)
        self.leases.acquire(manager.run_id, "intruder", 30)
        with self.assertRaises(LeaseLost):
            await asyncio.wait_for(task, 5)
        self.assertEqual(agent.calls, 1)
        stored = self.repo.load(manager.run_id)
        self.assertEqual(stored.results, ())              # the in-flight agent's result was never accepted

    async def test_write_guard_blocks_writes_after_local_deadline(self):
        class StalledLeases(InMemoryRunLeases):
            def renew(self, lease, ttl_seconds):
                time.sleep(TTL)                          # renewals stall past the local deadline
                raise LeaseLost("stalled")
        leases = StalledLeases()
        manager = PersistentStateManager(case(), self.repo)
        agent = CountingAgent(delay=TTL)                 # returns after the deadline
        executor = LeasedExecutor(leases, ttl_seconds=TTL, heartbeat_seconds=TTL * 0.9)
        with self.assertRaises(LeaseLost):
            await executor.run(runner_with(agent), manager)
        stored = self.repo.load(manager.run_id)
        self.assertEqual((stored.status, stored.results), ("running", ()))  # nothing written after deadline

    async def test_second_executor_cannot_take_a_live_run(self):
        manager = PersistentStateManager(case(), self.repo)
        self.leases.acquire(manager.run_id, "someone-alive", 30)
        agent = CountingAgent()
        with self.assertRaises(LeaseHeld):
            await LeasedExecutor(self.leases).run(runner_with(agent), manager)
        self.assertEqual((agent.calls, self.repo.load(manager.run_id).revision), (0, 0))

    def test_executor_rejects_bad_configuration(self):
        for kwargs in ({"ttl_seconds": 0}, {"ttl_seconds": 1, "heartbeat_seconds": 1},
                       {"ttl_seconds": 1, "heartbeat_seconds": 0}):
            with self.subTest(kwargs=kwargs), self.assertRaises(ValueError):
                LeasedExecutor(self.leases, **kwargs)
        with self.assertRaises(TypeError):
            LeasedExecutor(object())


class LeaseAwareRecoveryTests(unittest.TestCase):
    def test_live_expired_and_missing_leases(self):
        repo, leases = InMemoryStateRepository(), InMemoryRunLeases()
        runs = {}
        for label in ("live", "expired", "unleased"):
            m = PersistentStateManager(case(f"syn-{label}"), repo)
            started = m.start(0)
            runs[label] = m.dispatch(message(started), started.revision)
        leases.acquire(runs["live"].run_id, "alive", 30)
        leases.acquire(runs["expired"].run_id, "dead", TTL)
        time.sleep(TTL + 0.2)
        report = recover_interrupted_runs(repo, leases)
        self.assertEqual(report.active_elsewhere, (runs["live"].run_id,))
        self.assertEqual(set(report.moved_to_review), {runs["expired"].run_id, runs["unleased"].run_id})
        self.assertEqual(repo.load(runs["live"].run_id).status, "running")
        self.assertEqual(recover_interrupted_runs(repo).moved_to_review, (runs["live"].run_id,))  # P4 mode intact


@unittest.skipUnless(hasattr(signal, "SIGKILL"), "requires POSIX SIGKILL")
class LeasedProcessRestartTests(unittest.TestCase):
    """A killed executor's run is protected while its lease lives, recovered after it expires."""

    def kill_worker(self, store_arg, env, signal_file):
        worker = subprocess.Popen([sys.executable, "-m", "tests.persistence.crash_worker", store_arg, "hang",
                                   "syn-leased-kill-001", str(signal_file)],
                                  cwd=PROJECT_ROOT, env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        try:
            run_id = worker.stdout.readline().strip()
            deadline = time.monotonic() + 60
            while not signal_file.exists():
                self.assertIsNone(worker.poll(), "worker exited early")
                self.assertLess(time.monotonic(), deadline)
                time.sleep(0.05)
            time.sleep(0.5)  # at least one heartbeat while alive
            os.kill(worker.pid, signal.SIGKILL)
            killed_at = time.monotonic()
        finally:
            worker.wait(30)
            worker.stdout.close()
            worker.stderr.close()
        self.assertEqual(worker.returncode, -signal.SIGKILL)
        return run_id, killed_at

    def assert_protected_then_recovered(self, repo, leases, run_id, killed_at, ttl):
        early = recover_interrupted_runs(repo, leases)
        self.assertLess(time.monotonic() - killed_at, ttl)  # still inside the lease window
        self.assertEqual((early.active_elsewhere, early.moved_to_review), ((run_id,), ()))
        time.sleep(max(0.0, ttl - (time.monotonic() - killed_at)) + 0.5)
        late = recover_interrupted_runs(repo, leases)
        self.assertEqual((late.moved_to_review, late.active_elsewhere), ((run_id,), ()))
        after = repo.load(run_id)
        self.assertEqual((after.stop_reason, after.results), ("INTERRUPTED", ()))

    def test_file_store(self):
        ttl = 3.0
        with tempfile.TemporaryDirectory() as tmp:
            store = Path(tmp) / "runs"
            env = {**os.environ, "MEDRIXA_CRASH_LEASE_TTL": str(ttl)}
            run_id, killed_at = self.kill_worker(str(store), env, Path(tmp) / "signal")
            self.assert_protected_then_recovered(ProvisionalLocalFileRepository(store),
                                                 LocalFileRunLeases(store / "leases"), run_id, killed_at, ttl)

    def test_postgres(self):
        from app.persistence.postgres import PostgresRunLeases, PostgresStateRepository
        from tests.persistence.pg_support import fresh_database_dsn
        ttl = 3.0
        dsn = fresh_database_dsn()
        repo, leases = PostgresStateRepository(dsn), PostgresRunLeases(dsn)
        with tempfile.TemporaryDirectory() as tmp:
            env = {**os.environ, "MEDRIXA_CRASH_PG_DSN": dsn, "MEDRIXA_CRASH_LEASE_TTL": str(ttl)}
            run_id, killed_at = self.kill_worker("pgdsn-env:MEDRIXA_CRASH_PG_DSN", env, Path(tmp) / "signal")
        self.assert_protected_then_recovered(repo, leases, run_id, killed_at, ttl)


class LeaseInterfacesTests(unittest.TestCase):
    def test_cli_recovery_reports_active_elsewhere_only_when_present(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = Path(tmp) / "runs"
            repo, leases = ProvisionalLocalFileRepository(store), LocalFileRunLeases(store / "leases")
            m = PersistentStateManager(case("syn-cli-live"), repo)
            m.dispatch(message(m.start(0)), 1)
            leases.acquire(m.run_id, new_owner_id(), 60)
            out = subprocess.run([sys.executable, "-m", "app", "--store", str(store), "--recover"], cwd=PROJECT_ROOT,
                                 capture_output=True, text=True, timeout=60)
            self.assertEqual(json.loads(out.stdout)["active_elsewhere"], 1)
            self.assertEqual(repo.load(m.run_id).status, "running")
            ran = subprocess.run([sys.executable, "-m", "app", "--store", str(store)], cwd=PROJECT_ROOT,
                                 capture_output=True, text=True, timeout=60)
            self.assertEqual(ran.returncode, 0, ran.stderr)
            self.assertEqual([r for r in repo.list_runs() if leases.live(r)], [m.run_id])  # CLI run released

    def test_lease_ttl_setting_bounds(self):
        from app.web.settings import ConfigurationError, settings_from_env
        base = {"MEDRIXA_ENV": "test", "MEDRIXA_AUTH_SECRET": "lease-setting-test-secret-0123456789abcdefg"}
        self.assertEqual(settings_from_env(base).lease_ttl_seconds, 30.0)
        self.assertEqual(settings_from_env({**base, "MEDRIXA_LEASE_TTL_SECONDS": "12.5"}).lease_ttl_seconds, 12.5)
        for bad in ("0", "0.5", "7200", "nan", "abc"):
            with self.subTest(bad=bad), self.assertRaises(ConfigurationError):
                settings_from_env({**base, "MEDRIXA_LEASE_TTL_SECONDS": bad})

    def test_http_recovery_reports_active_elsewhere(self):
        from tests.web.support import Api
        repo, leases = InMemoryStateRepository(), InMemoryRunLeases()
        api = Api(repository=repo)
        api.app.state.service.leases = leases
        m = PersistentStateManager(case("case-a"), repo)
        m.dispatch(message(m.start(0)), 1)
        leases.acquire(m.run_id, "alive-elsewhere", 60)
        response = api.client.post("/v1/admin/recovery", headers=api.auth(roles=("operator",), cases=()))
        self.assertEqual((response.status_code, response.json()["active_elsewhere"],
                          response.json()["moved_to_review"]), (200, 1, 0))


if __name__ == "__main__":
    unittest.main()
