"""P8: metrics registry, Prometheus exposition, HTTP instrumentation, metrics endpoint and readiness."""
import asyncio
import logging
import re
import secrets
import shutil
import tempfile
import threading
import unittest
from pathlib import Path

from fastapi.testclient import TestClient

from app.observability.metrics import DEFINITIONS, InMemoryMetrics
from app.persistence.leases import InMemoryRunLeases, LeaseLost
from app.persistence.leasing import LeasedExecutor
from app.persistence.manager import PersistentStateManager
from app.persistence.memory import InMemoryStateRepository
from app.persistence.repository import StateRepository
from app.web.app import create_app
from app.web.audit import LoggingAuditSink, MemoryAuditSink
from app.web.auth import issue_token
from app.web.settings import Settings
from tests.helpers import case
from tests.persistence.test_leases import CountingAgent, runner_with
from tests.web.support import case_body

SAMPLE = re.compile(r'^([a-z_]+)(\{([a-z_]+="(?:[^"\\]|\\.)*",?)*\})? (-?[0-9.e+]+|\+Inf)$')


def parse(text):
    """Strict structural parse of the Prometheus 0.0.4 text format produced by MEDRIXA."""
    samples, declared = {}, {}
    for line in text.strip().splitlines():
        if line.startswith("# HELP "):
            continue
        if line.startswith("# TYPE "):
            _, _, name, kind = line.split(" ", 3)
            declared[name] = kind
            continue
        match = SAMPLE.match(line)
        assert match, f"malformed sample line: {line!r}"
        samples[line.rsplit(" ", 1)[0]] = float(line.rsplit(" ", 1)[1])
    return declared, samples


class RegistryTests(unittest.TestCase):
    def test_counter_gauge_histogram_semantics(self):
        m = InMemoryMetrics()
        m.increment("medrixa_runs_total", {"outcome": "CREATED"})
        m.increment("medrixa_runs_total", {"outcome": "CREATED"}, 2)
        m.set_gauge("medrixa_active_runs", 3)
        m.set_gauge("medrixa_active_runs", 1)
        for value in (0.001, 0.02, 0.3, 7.0, 50.0):
            m.observe("medrixa_http_request_duration_seconds", value, {"method": "GET", "route": "/v1/health"})
        self.assertEqual(m.value("medrixa_runs_total", {"outcome": "CREATED"}), 3)
        self.assertEqual(m.value("medrixa_active_runs"), 1)
        declared, samples = parse(m.render_prometheus())
        self.assertEqual(declared, {name: d.kind for name, d in DEFINITIONS.items()})
        base = 'medrixa_http_request_duration_seconds_bucket{method="GET",route="/v1/health",le='
        self.assertEqual(samples[base + '"0.005"}'], 1)
        self.assertEqual(samples[base + '"0.5"}'], 3)
        self.assertEqual(samples[base + '"10"}'], 4)
        self.assertEqual(samples[base + '"+Inf"}'], 5)
        self.assertAlmostEqual(samples['medrixa_http_request_duration_seconds_sum{method="GET",route="/v1/health"}'],
                               57.321)
        buckets = [v for k, v in samples.items() if k.startswith(base)]
        self.assertEqual(buckets, sorted(buckets))  # cumulative

    def test_invalid_updates_are_dropped_never_raised(self):
        m = InMemoryMetrics()
        with self.assertLogs("medrixa.metrics", level="WARNING"):
            m.increment("medrixa_runs_total", {"wrong": "x"})
            m.increment("medrixa_runs_total")                          # missing label
            m.increment("unknown_metric")
            m.increment("medrixa_runs_total", {"outcome": "x"}, -1)    # counters only grow
            m.observe("medrixa_runs_total", 1.0, {"outcome": "x"})     # wrong kind
            m.set_gauge("medrixa_active_runs", float("nan"))
        self.assertEqual(m.value("medrixa_runs_total", {"outcome": "x"}), 0)

    def test_label_values_are_escaped(self):
        m = InMemoryMetrics()
        m.increment("medrixa_http_requests_total", {"method": "GET", "route": 'a"b\\c\nd', "status": "200"})
        line = [x for x in m.render_prometheus().splitlines() if x.startswith("medrixa_http_requests_total{")][0]
        self.assertIn('route="a\\"b\\\\c\\nd"', line)
        parse(m.render_prometheus())

    def test_series_are_bounded(self):
        m = InMemoryMetrics(max_series=10)
        for i in range(1000):
            m.increment("medrixa_runs_total", {"outcome": f"o{i}"})
        self.assertEqual(m.render_prometheus().count("medrixa_runs_total{"), 10)
        self.assertEqual(m.dropped, 990)

    def test_thread_safety(self):
        m = InMemoryMetrics()

        def work():
            for _ in range(1000):
                m.increment("medrixa_auth_failures_total")
        threads = [threading.Thread(target=work) for _ in range(16)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        self.assertEqual(m.value("medrixa_auth_failures_total"), 16_000)

    def test_failing_collector_does_not_break_rendering(self):
        m = InMemoryMetrics()
        m.add_collector(lambda _: 1 / 0)
        with self.assertLogs("medrixa.metrics", level="WARNING"):
            text = m.render_prometheus()
        self.assertIn("# TYPE medrixa_runs_total counter", text)


class HttpMetricsTests(unittest.TestCase):
    def setUp(self):
        self.build()

    def build(self, **limits):
        self.secret = secrets.token_urlsafe(48)
        self.metrics = InMemoryMetrics()
        settings = Settings(environment="test", auth_secret=self.secret, allowed_hosts=("testserver",), **limits)
        self.app = create_app(settings, audit=MemoryAuditSink(), metrics=self.metrics)
        self.client = TestClient(self.app)

    def auth(self, roles=("case_runner",), cases=("case-a",), subject="user-1"):
        return {"Authorization": f"Bearer {issue_token(self.secret, subject, list(roles), list(cases))}"}

    def scrape(self):
        response = self.client.get("/v1/metrics", headers=self.auth(("operator",), (), "ops-1"))
        self.assertEqual(response.status_code, 200, response.text)
        return response

    def test_requests_counted_by_route_template_without_identifiers(self):
        created = self.client.post("/v1/runs", json=case_body(),
                                   headers={**self.auth(), "Idempotency-Key": "metrics-key-0000001"})
        run_id = created.json()["run_id"]
        self.client.post("/v1/runs", json=case_body(), headers={**self.auth(), "Idempotency-Key": "metrics-key-0000001"})
        self.client.get(f"/v1/runs/{run_id}", headers=self.auth())
        self.client.get("/v1/runs/does-not-exist", headers=self.auth())
        self.client.get("/v1/nowhere")
        self.client.get("/v1/runs/x", headers={"Authorization": "Bearer forged"})
        text = self.scrape().text
        _, samples = parse(text)
        self.assertEqual(samples['medrixa_http_requests_total{method="POST",route="/v1/runs",status="201"}'], 2)
        self.assertEqual(samples['medrixa_http_requests_total{method="GET",route="/v1/runs/{run_id}",status="200"}'], 1)
        self.assertEqual(samples['medrixa_http_requests_total{method="GET",route="/v1/runs/{run_id}",status="404"}'], 1)
        self.assertEqual(samples['medrixa_http_requests_total{method="GET",route="unrouted",status="404"}'], 1)
        self.assertEqual(samples['medrixa_runs_total{outcome="CREATED"}'], 1)
        self.assertEqual(samples['medrixa_runs_total{outcome="REPLAYED"}'], 1)
        self.assertEqual(samples['medrixa_run_stop_reasons_total{reason="REVIEW_REQUIRED"}'], 1)
        self.assertEqual(samples["medrixa_auth_failures_total"], 1)
        self.assertEqual(samples["medrixa_active_runs"], 0)
        for secret_value in (run_id, "user-1", "case-a", "does-not-exist", "demo-measure", self.secret):
            self.assertNotIn(secret_value, text)

    def test_rate_limit_counters(self):
        self.build(rate_limit_subject_per_minute=8)  # burst of 2
        for _ in range(4):
            self.client.get("/v1/runs/x", headers=self.auth())
        _, samples = parse(self.scrape().text)
        self.assertGreaterEqual(samples['medrixa_rate_limited_total{scope="identity"}'], 1)

    def test_metrics_endpoint_is_operator_only(self):
        self.assertEqual(self.client.get("/v1/metrics").status_code, 401)
        for roles in (("case_runner",), ("case_reviewer",)):
            with self.subTest(roles=roles):
                response = self.client.get("/v1/metrics", headers=self.auth(roles))
                self.assertEqual((response.status_code, response.json()["error"]["code"]), (403, "FORBIDDEN"))
        response = self.scrape()
        self.assertEqual(response.headers["content-type"], "text/plain; version=0.0.4; charset=utf-8")
        self.assertEqual(response.headers["cache-control"], "no-store")


class ReadinessTests(unittest.TestCase):
    def make(self, **kwargs):
        secret = secrets.token_urlsafe(48)
        settings = kwargs.pop("settings", None) or Settings(environment="test", auth_secret=secret,
                                                            allowed_hosts=("testserver",))
        app = create_app(settings, **{"audit": MemoryAuditSink(), **kwargs})
        return TestClient(app)

    def assert_not_ready(self, client):
        response = client.get("/v1/ready")
        self.assertEqual((response.status_code, response.json()["error"]["code"]), (503, "NOT_READY"))
        self.assertEqual(response.headers["retry-after"], "5")
        self.assertEqual(response.json()["error"]["message"], "The service is not ready.")

    def test_ready_with_memory_store(self):
        response = self.make().get("/v1/ready")
        self.assertEqual((response.status_code, response.json()), (200, {"api_version": "medrixa.http.v1",
                                                                          "status": "ready"}))

    def test_unconfigured_audit_logger_is_not_ready(self):
        silent = logging.getLogger(f"medrixa.audit.silent.{secrets.token_hex(4)}")
        silent.propagate = False
        self.assert_not_ready(self.make(audit=LoggingAuditSink(silent)))

    def test_failing_store_is_not_ready_without_details(self):
        class Broken(InMemoryStateRepository):
            def ping(self):
                raise OSError("disk /var/lib/secret-path failed")
        client = self.make(repository=Broken())
        response = client.get("/v1/ready")
        self.assertNotIn("secret-path", response.text)
        self.assert_not_ready(client)

    def test_file_store_directory_removed(self):
        directory = tempfile.mkdtemp()
        settings = Settings(environment="test", auth_secret=secrets.token_urlsafe(48),
                            allowed_hosts=("testserver",), store_dir=Path(directory) / "runs")
        client = self.make(settings=settings)
        self.assertEqual(client.get("/v1/ready").status_code, 200)
        shutil.rmtree(directory)
        self.assert_not_ready(client)

    def test_oidc_jwks_unavailable_is_not_ready(self):
        from tests.security.oidc_support import JwksServer, SigningKey, oidc_settings
        server = JwksServer([SigningKey("RSA")])
        self.addCleanup(server.close)
        server.mode = "error"
        settings = Settings(environment="test", auth_mode="oidc", allowed_hosts=("testserver",),
                            oidc=oidc_settings(server.url, jwks_min_refresh_seconds=0.1, jwks_cache_seconds=1))
        client = self.make(settings=settings)
        self.assert_not_ready(client)
        server.mode = "ok"
        import time
        time.sleep(0.15)
        self.assertEqual(client.get("/v1/ready").status_code, 200)

    def test_postgres_readiness_and_pool_gauges(self):
        import psycopg
        from tests.persistence.pg_support import fresh_database_dsn
        dsn = fresh_database_dsn()
        secret = secrets.token_urlsafe(48)
        metrics = InMemoryMetrics()
        settings = Settings(environment="test", auth_secret=secret, allowed_hosts=("testserver",), postgres_dsn=dsn,
                            postgres_pool_size=2)
        app = create_app(settings, audit=MemoryAuditSink(), metrics=metrics)
        with TestClient(app) as client:
            self.assertEqual(client.get("/v1/ready").status_code, 200)
            operator = {"Authorization": f"Bearer {issue_token(secret, 'ops', ['operator'], [])}"}
            _, samples = parse(client.get("/v1/metrics", headers=operator).text)
            self.assertGreaterEqual(samples['medrixa_db_pool_connections{state="open"}'], 1)
            self.assertIn('medrixa_db_pool_connections{state="waiting"}', samples)
        dropped = fresh_database_dsn()
        repo_settings = Settings(environment="test", auth_secret=secret, allowed_hosts=("testserver",),
                                 postgres_dsn=dropped, postgres_pool_size=0)
        client = self.make(settings=repo_settings)
        self.assertEqual(client.get("/v1/ready").status_code, 200)
        admin = dropped.replace(dropped.split("dbname=")[1].split()[0], "postgres")
        with psycopg.connect(admin, autocommit=True) as connection:
            connection.execute(f'DROP DATABASE "{dropped.split("dbname=")[1].split()[0]}" WITH (FORCE)')
        self.assert_not_ready(client)


class LeaseAndRecoveryMetricsTests(unittest.IsolatedAsyncioTestCase):
    async def test_lease_renewals_and_losses_are_counted(self):
        metrics, repo, leases = InMemoryMetrics(), InMemoryStateRepository(), InMemoryRunLeases()
        executor = LeasedExecutor(leases, ttl_seconds=0.6, heartbeat_seconds=0.1, metrics=metrics)
        await executor.run(runner_with(CountingAgent(delay=0.35)), PersistentStateManager(case(), repo))
        self.assertGreaterEqual(metrics.value("medrixa_lease_renewals_total"), 2)
        manager = PersistentStateManager(case("case-b"), repo)
        task = asyncio.create_task(executor.run(runner_with(CountingAgent(delay=5)), manager))
        await asyncio.sleep(0.2)
        leases.release(leases.live(manager.run_id))
        leases.acquire(manager.run_id, "intruder", 30)
        with self.assertRaises(LeaseLost):
            await asyncio.wait_for(task, 5)
        self.assertEqual(metrics.value("medrixa_lease_lost_total"), 1)

    def test_recovery_results_are_counted(self):
        from tests.helpers import message
        repo, metrics = InMemoryStateRepository(), InMemoryMetrics()
        secret = secrets.token_urlsafe(48)
        app = create_app(Settings(environment="test", auth_secret=secret, allowed_hosts=("testserver",)),
                         repository=repo, audit=MemoryAuditSink(), metrics=metrics)
        m = PersistentStateManager(case(), repo)
        m.dispatch(message(m.start(0)), 1)
        PersistentStateManager(case("c2"), repo)
        operator = {"Authorization": f"Bearer {issue_token(secret, 'ops', ['operator'], [])}"}
        self.assertEqual(TestClient(app).post("/v1/admin/recovery", headers=operator).status_code, 200)
        self.assertEqual(metrics.value("medrixa_recovery_runs_total", {"result": "moved_to_review"}), 1)
        self.assertEqual(metrics.value("medrixa_recovery_runs_total", {"result": "not_started"}), 1)


class StoreContractTests(unittest.TestCase):
    def test_ping_is_optional_for_third_party_adapters(self):
        class Minimal(StateRepository):
            adapter_name = "minimal"

            def create(self, state):
                return state

            def load(self, run_id):
                raise LookupError

            def save(self, state, expected_revision):
                return state

            def scan(self):
                return ()
        self.assertIsNone(Minimal().ping())


if __name__ == "__main__":
    unittest.main()
