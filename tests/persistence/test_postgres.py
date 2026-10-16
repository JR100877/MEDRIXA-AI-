"""P5: PostgreSQL adapter against a real ephemeral PostgreSQL (skipped, never faked, if unavailable)."""
import asyncio
import multiprocessing
import unittest

from app.persistence.postgres import PostgresStateRepository, StoreUnavailable
from app.persistence.recovery import recover_interrupted_runs
from app.persistence.repository import CorruptRecord, RevisionConflict, RunNotFound
from tests.persistence.pg_support import fresh_database_dsn
from tests.persistence.support import state_chain
from tests.persistence.test_recovery import seed
from tests.persistence.test_repository_contract import RepositoryContract


def _contend(dsn, payload, barrier, results):
    from app.core.state import State
    repo = PostgresStateRepository(dsn, migrate=False)
    candidate = State.model_validate_json(payload)
    barrier.wait()
    try:
        repo.save(candidate, 0)
        results.put("ok")
    except RevisionConflict:
        results.put("conflict")


class PostgresRepositoryContractTests(RepositoryContract, unittest.TestCase):
    """The unchanged P4 contract suite, now against PostgreSQL."""

    def make_repo(self):
        return PostgresStateRepository(fresh_database_dsn())

    def peer(self, repo):
        return PostgresStateRepository(repo._dsn)  # independent handle; migration re-run is idempotent


class PostgresSpecificTests(unittest.TestCase):
    def setUp(self):
        self.dsn = fresh_database_dsn()
        self.repo = PostgresStateRepository(self.dsn)
        self.chain, _ = state_chain()
        self.repo.create(self.chain[0])
        for previous, current in zip(self.chain[:2], self.chain[1:3], strict=True):
            self.repo.save(current, previous.revision)
        self.run_id = self.chain[0].run_id
        import psycopg
        self.sql = lambda statement, params=(): self._sql(psycopg, statement, params)

    def _sql(self, psycopg, statement, params):
        with psycopg.connect(self.dsn, autocommit=True) as connection:
            return connection.execute(statement, params).rowcount

    def test_migration_is_idempotent_and_versioned(self):
        self.assertEqual(self.repo.migrate(), 1)
        self.assertEqual(PostgresStateRepository(self.dsn).migrate(), 1)
        self.assertEqual(self.repo.load(self.run_id), self.chain[2])

    def test_event_table_is_append_only_in_the_database(self):
        import psycopg
        for statement in ("UPDATE medrixa_run_events SET kind = 'RUN_STARTED'",
                          "DELETE FROM medrixa_run_events", "TRUNCATE medrixa_run_events"):
            with self.subTest(statement=statement.split()[0]), self.assertRaises(psycopg.Error):
                self.sql(statement)
        self.assertEqual(self.repo.load(self.run_id), self.chain[2])

    def test_tampering_is_detected_on_read(self):
        tamperings = {
            "snapshot_text": "UPDATE medrixa_runs SET snapshot = replace(snapshot, 'synthetic-case-1', 'synthetic-case-X')",
            "status_column": "UPDATE medrixa_runs SET status = 'awaiting_human_review'",
            "revision_column": "UPDATE medrixa_runs SET revision = 1",
            "checksum": "UPDATE medrixa_runs SET snapshot_sha256 = repeat('0', 64)",
        }
        for name, statement in tamperings.items():
            with self.subTest(name):
                original = self.repo._run(lambda c: c.execute(
                    "SELECT revision, status, snapshot, snapshot_sha256 FROM medrixa_runs").fetchone())
                self.sql(statement)
                with self.assertRaises(CorruptRecord):
                    self.repo.load(self.run_id)
                with self.assertRaises(CorruptRecord):
                    self.repo.save(self.chain[3], 2)
                self.sql("UPDATE medrixa_runs SET revision = %s, status = %s, snapshot = %s, snapshot_sha256 = %s",
                         original)
                self.assertEqual(self.repo.load(self.run_id), self.chain[2])

    def test_event_rows_disagreeing_with_snapshot_are_detected(self):
        self.sql("ALTER TABLE medrixa_run_events DISABLE TRIGGER USER")  # deliberate tampering by the owner
        self.sql("DELETE FROM medrixa_run_events WHERE sequence = 2")
        self.sql("ALTER TABLE medrixa_run_events ENABLE TRIGGER USER")
        with self.assertRaises(CorruptRecord):
            self.repo.load(self.run_id)
        [entry] = self.repo.scan()
        self.assertFalse(entry.readable)
        self.assertEqual(recover_interrupted_runs(self.repo).corrupt_records, 1)

    def test_unavailable_database_never_leaks_dsn(self):
        bad_dsn = "host=/nonexistent/medrixa-socket dbname=nope user=nobody password=SECRET_MARKER_PG"
        with self.assertRaises(StoreUnavailable) as caught:
            PostgresStateRepository(bad_dsn, connect_timeout=1)
        self.assertNotIn("SECRET_MARKER_PG", str(caught.exception) + repr(caught.exception))
        self.assertIsNone(caught.exception.__cause__)
        self.assertNotIn("dbname", repr(self.repo))

    def test_hostile_run_ids_are_rejected_before_sql(self):
        for run_id in ("x' OR '1'='1", "'; DROP TABLE medrixa_runs; --", "a" * 81):
            with self.subTest(run_id=run_id[:20]), self.assertRaises(RunNotFound):
                self.repo.load(run_id)
        self.assertEqual(self.repo.load(self.run_id), self.chain[2])

    def test_multiprocess_writers_single_winner(self):
        chain, _ = state_chain("synthetic-case-mp")
        self.repo.create(chain[0])
        ctx = multiprocessing.get_context("spawn")
        workers = 4
        barrier, results = ctx.Barrier(workers), ctx.Queue()
        processes = [ctx.Process(target=_contend, args=(self.dsn, chain[1].model_dump_json(), barrier, results))
                     for _ in range(workers)]
        for process in processes:
            process.start()
        for process in processes:
            process.join(60)
        self.assertEqual(sorted(results.get(timeout=5) for _ in range(workers)), ["conflict"] * 3 + ["ok"])
        self.assertEqual(self.repo.load(chain[0].run_id), chain[1])


class PostgresRecoveryTests(unittest.TestCase):
    def test_recovery_policy_on_postgres(self):
        repo = PostgresStateRepository(fresh_database_dsn())
        runs = seed(repo)
        report = recover_interrupted_runs(repo)
        self.assertEqual(set(report.moved_to_review), {runs[k].run_id for k in ("started", "pending", "accepted")})
        self.assertEqual((report.not_started, report.already_terminal), ((runs["created"].run_id,), 1))
        pending = repo.load(runs["pending"].run_id)
        self.assertEqual((pending.stop_reason, pending.results), ("INTERRUPTED", ()))
        self.assertEqual(recover_interrupted_runs(repo).moved_to_review, ())


@unittest.skipUnless(hasattr(__import__("signal"), "SIGKILL"), "requires POSIX SIGKILL")
class PostgresProcessRestartTests(unittest.TestCase):
    """A run killed with SIGKILL while its agent is in flight survives in PostgreSQL and is recovered."""

    def test_killed_run_recovered_from_postgres(self):
        import os
        import signal
        import subprocess
        import sys
        import tempfile
        import time
        from pathlib import Path
        dsn = fresh_database_dsn()
        PostgresStateRepository(dsn)  # migrate once
        root = Path(__file__).resolve().parents[2]
        env = {**os.environ, "MEDRIXA_CRASH_PG_DSN": dsn}
        with tempfile.TemporaryDirectory() as tmp:
            signal_file = Path(tmp) / "in-flight.signal"
            worker = subprocess.Popen([sys.executable, "-m", "tests.persistence.crash_worker",
                                       "pgdsn-env:MEDRIXA_CRASH_PG_DSN", "hang", "syn-pg-killed-001", str(signal_file)],
                                      cwd=root, env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
            try:
                run_id = worker.stdout.readline().strip()
                deadline = time.monotonic() + 60
                while not signal_file.exists():
                    self.assertIsNone(worker.poll(), worker.stderr.read() if worker.poll() is not None else "")
                    self.assertLess(time.monotonic(), deadline)
                    time.sleep(0.05)
                os.kill(worker.pid, signal.SIGKILL)
            finally:
                worker.wait(30)
                worker.stdout.close()
                worker.stderr.close()
        self.assertEqual(worker.returncode, -signal.SIGKILL)
        repo = PostgresStateRepository(dsn, migrate=False)
        before = repo.load(run_id)
        self.assertEqual((before.status, before.revision, before.pending_message is not None), ("running", 2, True))
        self.assertEqual(recover_interrupted_runs(repo).moved_to_review, (run_id,))
        after = repo.load(run_id)
        self.assertEqual([e.kind for e in after.events], ["RUN_STARTED", "AGENT_DISPATCHED", "REVIEW_REQUESTED"])
        self.assertEqual((after.stop_reason, after.results, after.events[:2]), ("INTERRUPTED", (), before.events))


class PostgresHttpTests(unittest.IsolatedAsyncioTestCase):
    """P4b idempotency guarantees with PostgreSQL as the store."""

    async def test_concurrent_duplicates_and_restart_replay(self):
        import httpx
        from tests.web.support import Api, CountingHistoryAgent, case_body, runner_factory_for
        dsn = fresh_database_dsn()
        gate, entered = asyncio.Event(), asyncio.Event()
        agent = CountingHistoryAgent(gate=gate, entered=entered)
        api = Api(repository=PostgresStateRepository(dsn), runner_factory=runner_factory_for(agent))
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=api.app), base_url="http://testserver") as http:
            def post():
                return http.post("/v1/runs", json=case_body(),
                                 headers={**api.auth(), "Idempotency-Key": "pg-retry-key-000001"})
            first = asyncio.create_task(post())
            await asyncio.wait_for(entered.wait(), 10)
            duplicates = await asyncio.gather(*(post() for _ in range(5)))
            gate.set()
            winner = await first
        self.assertEqual(winner.status_code, 201)
        self.assertEqual({r.json()["error"]["code"] for r in duplicates}, {"IDEMPOTENCY_IN_PROGRESS"})
        restarted_agent = CountingHistoryAgent()
        restarted = Api(repository=PostgresStateRepository(dsn), secret=api.secret,
                        runner_factory=runner_factory_for(restarted_agent))
        replay = restarted.create(key="pg-retry-key-000001")
        self.assertEqual((replay.status_code, replay.headers["idempotent-replayed"], replay.json()),
                         (201, "true", winner.json()))
        self.assertEqual((agent.calls, restarted_agent.calls), (1, 0))


class PostgresConfigurationTests(unittest.TestCase):
    SECRET = "x9Q2-config-test-secret-abcdefghijklmnop-0123"

    def test_dsn_is_secret_and_stores_are_exclusive(self):
        from app.web.settings import ConfigurationError, settings_from_env
        dsn = "host=/nowhere dbname=d password=SECRET_MARKER_DSN"
        settings = settings_from_env({"MEDRIXA_ENV": "test", "MEDRIXA_AUTH_SECRET": self.SECRET, "MEDRIXA_PG_DSN": dsn})
        self.assertNotIn("SECRET_MARKER_DSN", repr(settings) + settings.model_dump_json())
        with self.assertRaises(ConfigurationError) as caught:
            settings_from_env({"MEDRIXA_ENV": "test", "MEDRIXA_AUTH_SECRET": self.SECRET, "MEDRIXA_PG_DSN": dsn,
                               "MEDRIXA_STORE_DIR": "/tmp/runs"})
        self.assertNotIn("SECRET_MARKER_DSN", str(caught.exception))

    def test_app_uses_postgres_when_configured(self):
        from app.web.app import create_app
        from app.web.audit import MemoryAuditSink
        from app.web.settings import Settings
        app = create_app(Settings(environment="test", auth_secret=self.SECRET, postgres_dsn=fresh_database_dsn()),
                         audit=MemoryAuditSink())
        self.assertIsInstance(app.state.service.repository, PostgresStateRepository)


if __name__ == "__main__":
    unittest.main()
