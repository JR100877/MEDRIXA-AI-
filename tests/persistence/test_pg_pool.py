"""P7.6: pooled PostgreSQL connections — same contract, transaction hygiene, exhaustion, recovery."""
import threading
import time
import unittest

from app.persistence.postgres import PostgresRunLeases, PostgresStateRepository, StoreUnavailable
from app.persistence.repository import RevisionConflict
from tests.persistence.pg_support import fresh_database_dsn
from tests.persistence.support import state_chain
from tests.persistence.test_leases import LeaseContract
from tests.persistence.test_repository_contract import RepositoryContract


class PooledRepositoryContractTests(RepositoryContract, unittest.TestCase):
    def make_repo(self):
        repo = PostgresStateRepository(fresh_database_dsn(), pool_size=2)
        self.addCleanup(repo.close)
        return repo

    def peer(self, repo):
        other = PostgresStateRepository(repo._dsn, pool_size=2)
        self.addCleanup(other.close)
        return other


class PooledLeaseContractTests(LeaseContract, unittest.TestCase):
    def make_leases(self):
        dsn = fresh_database_dsn()
        repo = PostgresStateRepository(dsn, pool_size=2)
        self.addCleanup(repo.close)
        return PostgresRunLeases(dsn, repository=repo)

    def peer(self, leases):
        repo = PostgresStateRepository(leases._db._dsn, migrate=False, verify=False, pool_size=2)
        self.addCleanup(repo.close)
        return PostgresRunLeases(leases._db._dsn, migrate=False, repository=repo)


class PoolBehaviourTests(unittest.TestCase):
    def setUp(self):
        import psycopg
        self.psycopg = psycopg
        self.dsn = fresh_database_dsn()
        self.repo = PostgresStateRepository(self.dsn, pool_size=1, connect_timeout=1)
        self.addCleanup(self.repo.close)
        self.chain, _ = state_chain()
        self.repo.create(self.chain[0])

    def test_failed_transaction_leaves_connection_clean(self):
        with self.assertRaises(RevisionConflict):
            self.repo.save(self.chain[1], 7)       # raised inside the transaction -> rollback
        self.repo.save(self.chain[1], 0)            # same single pooled connection still usable
        self.assertEqual(self.repo.load(self.chain[0].run_id), self.chain[1])

    def test_exhausted_pool_fails_fast_as_store_unavailable(self):
        held, release = threading.Event(), threading.Event()

        def hold():
            with self.repo._pool.connection():
                held.set()
                release.wait(10)
        holder = threading.Thread(target=hold)
        holder.start()
        held.wait(5)
        started = time.monotonic()
        with self.assertRaises(StoreUnavailable) as caught:
            self.repo.load(self.chain[0].run_id)
        self.assertLess(time.monotonic() - started, 3)
        self.assertNotIn("host=", str(caught.exception))
        release.set()
        holder.join(5)
        self.assertEqual(self.repo.load(self.chain[0].run_id), self.chain[0])

    def test_recovers_after_server_terminates_pooled_connections(self):
        self.repo.load(self.chain[0].run_id)
        with self.psycopg.connect(self.dsn, autocommit=True) as admin:
            killed = admin.execute("SELECT count(pg_terminate_backend(pid)) FROM pg_stat_activity "
                                   "WHERE datname = current_database() AND pid <> pg_backend_pid()").fetchone()[0]
        self.assertGreaterEqual(killed, 1)
        self.assertEqual(self.repo.load(self.chain[0].run_id), self.chain[0])  # no failed request

    def test_close_releases_connections(self):
        self.repo.load(self.chain[0].run_id)
        self.repo.close()
        with self.psycopg.connect(self.dsn, autocommit=True) as admin:
            deadline = time.monotonic() + 5
            while True:
                others = admin.execute("SELECT count(*) FROM pg_stat_activity WHERE datname = current_database() "
                                       "AND pid <> pg_backend_pid()").fetchone()[0]
                if others == 0 or time.monotonic() > deadline:
                    break
                time.sleep(0.05)
        self.assertEqual(others, 0)

    def test_failed_start_closes_the_pool(self):
        before = threading.active_count()
        with self.assertRaises(StoreUnavailable):
            PostgresStateRepository("host=/nonexistent/medrixa dbname=x", pool_size=2, connect_timeout=1)
        time.sleep(0.5)
        self.assertLessEqual(threading.active_count(), before)

    def test_invalid_pool_size(self):
        for size in (-1, 101, 1.5, True):
            with self.subTest(size=size), self.assertRaises(ValueError):
                PostgresStateRepository(self.dsn, pool_size=size)

    def test_http_app_shares_and_closes_pool(self):
        from fastapi.testclient import TestClient
        from app.web.app import create_app
        from app.web.audit import MemoryAuditSink
        from app.web.auth import issue_token
        from app.web.settings import Settings
        from tests.web.support import case_body
        secret = "pool-http-test-secret-0123456789-abcdefghij"
        app = create_app(Settings(environment="test", auth_secret=secret, postgres_dsn=self.dsn,
                                  postgres_pool_size=2, allowed_hosts=("testserver",)), audit=MemoryAuditSink())
        service = app.state.service
        self.assertIs(service.leases._db, service.repository)   # one pool for runs and leases
        with TestClient(app) as client:
            headers = {"Authorization": f"Bearer {issue_token(secret, 'u', ['case_runner'], ['case-a'])}",
                       "Idempotency-Key": "pool-key-0000000001"}
            self.assertEqual(client.post("/v1/runs", json=case_body(), headers=headers).status_code, 201)
            self.assertIsNotNone(service.repository._pool)
        self.assertIsNone(service.repository._pool)               # closed by the lifespan handler


if __name__ == "__main__":
    unittest.main()
