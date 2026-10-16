"""P7.5: measure latencies that justify operational defaults (lease TTL, connection pooling).

    python -m scripts.measure_latency [--pg-dsn-env VAR] [--iterations N] [--out FILE]

Measures, per available adapter: lease acquire/renew/release, repository
create/load/save, a full persisted run (6 transitions), and for PostgreSQL the
cost of opening a connection. Synthetic data only; results describe THIS host
and are not a capacity benchmark.
"""
import argparse
import asyncio
import json
import os
import platform
import statistics
import sys
import tempfile
import time
import uuid
from collections.abc import Callable

from app.bootstrap import create_runner
from app.persistence.leases import InMemoryRunLeases, LocalFileRunLeases
from app.persistence.local_file import ProvisionalLocalFileRepository
from app.persistence.manager import PersistentStateManager
from app.persistence.memory import InMemoryStateRepository
from app.models.contracts import ClinicalCase, Observation


def summary(samples_s: list[float]) -> dict:
    ms = sorted(s * 1000 for s in samples_s)

    def pct(p: float) -> float:
        return round(ms[min(len(ms) - 1, int(round(p / 100 * (len(ms) - 1))))], 3)
    return {"n": len(ms), "p50_ms": pct(50), "p95_ms": pct(95), "p99_ms": pct(99), "max_ms": round(ms[-1], 3),
            "mean_ms": round(statistics.fmean(ms), 3)}


def timed(fn: Callable[[], object], iterations: int) -> dict:
    samples = []
    for _ in range(iterations):
        start = time.perf_counter()
        fn()
        samples.append(time.perf_counter() - start)
    return summary(samples)


def case(i: int) -> ClinicalCase:
    return ClinicalCase(case_id=f"bench-{i}", synthetic=True, source="synthetic_fixture", history_available=True,
                        observations=(Observation(code="demo-measure", value=1.0, unit="demo-unit"),))


def measure_leases(leases, iterations: int) -> dict:
    counter = iter(range(10**9))
    prefix = uuid.uuid4().hex[:8]  # unique per measurement: earlier leases may still be live
    held = []

    def acquire():
        held.append(leases.acquire(f"lease-{prefix}-{next(counter)}", "bench-owner", 30))
    result = {"acquire": timed(acquire, iterations)}
    target = held[0]

    def renew():
        nonlocal target
        target = leases.renew(target, 30)
    result["renew"] = timed(renew, iterations)
    releasable = iter(held[1:])
    result["release"] = timed(lambda: leases.release(next(releasable)), min(iterations, len(held) - 1))
    return result


def measure_repository(repo, iterations: int) -> dict:
    created = []
    counter = iter(range(10**9))

    def create():
        created.append(PersistentStateManager(case(next(counter)), repo))
    result = {"create": timed(create, iterations)}
    ids = iter(m.run_id for m in created)
    result["load"] = timed(lambda: repo.load(next(ids)), iterations)
    managers = iter(created)
    result["save_one_transition"] = timed(lambda: (lambda m: m.start(0))(next(managers)), iterations)

    def full_run():
        asyncio.run(create_runner().run(PersistentStateManager(case(next(counter)), repo)))
    result["full_run_6_transitions"] = timed(full_run, max(10, iterations // 5))
    return result


def main(argv=None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--pg-dsn-env", help="environment variable holding a TEST PostgreSQL DSN")
    parser.add_argument("--iterations", type=int, default=200)
    parser.add_argument("--pg-pool-size", type=int, default=0, help="also measure a pooled repository (P7.6)")
    parser.add_argument("--out")
    args = parser.parse_args(argv)
    n = args.iterations
    report: dict = {"python": platform.python_version(), "platform": platform.platform(), "iterations": n,
                    "note": "single host, synthetic data; not a capacity benchmark", "adapters": {}}
    report["adapters"]["in-memory"] = {"leases": measure_leases(InMemoryRunLeases(), n),
                                       "repository": measure_repository(InMemoryStateRepository(), n)}
    with tempfile.TemporaryDirectory() as directory:
        report["adapters"]["local-file"] = {
            "leases": measure_leases(LocalFileRunLeases(os.path.join(directory, "leases")), n),
            "repository": measure_repository(ProvisionalLocalFileRepository(os.path.join(directory, "runs")), n)}
    if args.pg_dsn_env:
        import psycopg
        from app.persistence.postgres import PostgresRunLeases, PostgresStateRepository
        dsn = os.environ[args.pg_dsn_env]
        repo, leases = PostgresStateRepository(dsn), PostgresRunLeases(dsn)

        def connect_only():
            psycopg.connect(dsn).close()

        def connect_and_trivial_query():
            with psycopg.connect(dsn) as connection:
                connection.execute("SELECT 1").fetchone()
        with psycopg.connect(dsn) as persistent:
            def query_on_open_connection():
                persistent.execute("SELECT 1").fetchone()
            on_open = timed(query_on_open_connection, n)
        report["adapters"]["postgresql"] = {
            "server_version": repo._run(lambda c: c.execute("SHOW server_version").fetchone()[0]),
            "transport": "unix socket" if "host=/" in dsn else "tcp",
            "connection": {"connect_and_close": timed(connect_only, n),
                           "connect_and_select_1": timed(connect_and_trivial_query, n),
                           "select_1_on_open_connection": on_open},
            "leases": measure_leases(leases, n), "repository": measure_repository(repo, n)}
        if args.pg_pool_size:
            pooled = PostgresStateRepository(dsn, pool_size=args.pg_pool_size)
            try:
                report["adapters"]["postgresql-pooled"] = {
                    "pool_size": args.pg_pool_size,
                    "leases": measure_leases(PostgresRunLeases(dsn, repository=pooled), n),
                    "repository": measure_repository(pooled, n)}
            finally:
                pooled.close()
    text = json.dumps(report, indent=2)
    if args.out:
        with open(args.out, "w", encoding="utf-8") as handle:
            handle.write(text + "\n")
    print(text)
    return 0


if __name__ == "__main__":
    sys.exit(main())
