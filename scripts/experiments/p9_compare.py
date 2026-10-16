"""P9 EXPERIMENT: inline execution (P8 baseline) vs option C (thread + own loop per run).

    python -m scripts.experiments.p9_compare [--pg] [--requests N] [--repeats R] [--out FILE]

Same harness as scripts/load_test.py (in-process ASGI, synthetic agents, rate
limits raised) plus, per scenario:
- event-loop lag of the HTTP loop (a 5 ms ticker; lag = how late it wakes up),
- latency of /v1/health probes issued while the load runs (what other clients feel),
- peak threads, peak RSS, CPU seconds per request.
Strategies run interleaved and repeated; the median repeat (by throughput) is
reported with all repeats kept. Results describe THIS host only.
"""
import argparse
import asyncio
import json
import os
import platform
import secrets
import sys
import threading
import time
from collections import Counter

import httpx
from pydantic import SecretStr

from app.experimental.execution import ThreadLoopExecution
from app.web.app import create_app
from app.web.audit import MemoryAuditSink
from app.web.auth import issue_token
from app.web.settings import Settings

PAGE = os.sysconf("SC_PAGE_SIZE")


def rss_mb() -> float:
    with open("/proc/self/statm", encoding="ascii") as handle:
        return int(handle.read().split()[1]) * PAGE / 2**20


def pct(values: list[float], p: float) -> float:
    ordered = sorted(values)
    return round(ordered[min(len(ordered) - 1, int(round(p / 100 * (len(ordered) - 1))))], 2) if ordered else 0.0


async def scenario(store: str, settings_kwargs: dict, strategy: str, concurrency: int, total: int) -> dict:
    secret = secrets.token_urlsafe(48)
    settings = Settings(environment="test", auth_secret=SecretStr(secret), allowed_hosts=("testserver",),
                        rate_limit_ip_per_minute=100_000, rate_limit_subject_per_minute=100_000,
                        **settings_kwargs)
    execution = ThreadLoopExecution(settings.max_active_runs) if strategy == "thread" else None
    app = create_app(settings, audit=MemoryAuditSink(), execution=execution)
    token = issue_token(secret, "load-user", ["case_runner"], ["case-load"])
    body = {"case": {"case_id": "case-load", "synthetic": True, "source": "synthetic_fixture",
                     "history_available": True, "observations": [{"code": "demo-measure", "value": 1.0,
                                                                  "unit": "demo-unit"}]}}
    latencies: list[float] = []
    probes: list[float] = []
    lags: list[float] = []
    statuses: Counter = Counter()
    peak = {"threads": threading.active_count(), "rss_mb": rss_mb()}
    queue: asyncio.Queue = asyncio.Queue()
    for i in range(total):
        queue.put_nowait(i)
    prefix = secrets.token_hex(4)
    done = asyncio.Event()
    try:
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://testserver") as http:
            async def worker():
                while True:
                    try:
                        i = queue.get_nowait()
                    except asyncio.QueueEmpty:
                        return
                    started = time.perf_counter()
                    response = await http.post("/v1/runs", json=body, headers={
                        "Authorization": f"Bearer {token}", "Idempotency-Key": f"p9-{prefix}-{i:010d}"})
                    latencies.append((time.perf_counter() - started) * 1000)
                    statuses[response.status_code] += 1

            async def ticker():
                while not done.is_set():
                    started = time.perf_counter()
                    await asyncio.sleep(0.005)
                    lags.append(max(0.0, (time.perf_counter() - started - 0.005) * 1000))
                    peak["threads"] = max(peak["threads"], threading.active_count())
                    peak["rss_mb"] = max(peak["rss_mb"], rss_mb())

            async def prober():
                while not done.is_set():
                    started = time.perf_counter()
                    await http.get("/v1/health")
                    probes.append((time.perf_counter() - started) * 1000)
                    await asyncio.sleep(0.02)

            side = [asyncio.create_task(ticker()), asyncio.create_task(prober())]
            cpu, wall = time.process_time(), time.perf_counter()
            await asyncio.gather(*(worker() for _ in range(concurrency)))
            wall, cpu = time.perf_counter() - wall, time.process_time() - cpu
            done.set()
            await asyncio.gather(*side)
    finally:
        if execution is not None:
            execution.shutdown(wait=True)
        for component in (app.state.service.repository, getattr(app.state.service, "leases", None)):
            close = getattr(component, "close", None)
            if callable(close):
                close()
    return {"store": store, "strategy": strategy, "concurrency": concurrency,
            "max_active_runs": settings.max_active_runs, "requests": total,
            "throughput_rps": round(total / wall, 1), "status_counts": dict(sorted(statuses.items())),
            "latency_ms": {"p50": pct(latencies, 50), "p95": pct(latencies, 95), "p99": pct(latencies, 99),
                           "max": round(max(latencies), 2)},
            "loop_lag_ms": {"p50": pct(lags, 50), "p99": pct(lags, 99), "max": round(max(lags or [0.0]), 2)},
            "health_probe_ms": {"n": len(probes), "p50": pct(probes, 50), "p99": pct(probes, 99),
                                "max": round(max(probes or [0.0]), 2)},
            "cpu_ms_per_request": round(cpu * 1000 / total, 2),
            "peak_threads": peak["threads"], "peak_rss_mb": round(peak["rss_mb"], 1),
            "worker_peak_in_flight": execution.peak_in_flight if execution else None}


async def main_async(args) -> dict:
    stores: list[tuple[str, dict]] = [] if args.no_memory else [("memory", {})]
    if args.pg:
        from tests.persistence.pg_support import fresh_database_dsn
        for size in (int(x) for x in args.pg_pool_sizes.split(",")):
            stores.append((f"postgresql-pool{size}", {"postgres_dsn": SecretStr(fresh_database_dsn()),
                                                       "postgres_pool_size": size}))
    plan = []
    for store, kwargs in stores:
        for concurrency, max_active in ((1, 16), (8, 16), (32, 16), (32, 4)):
            plan.append((store, {**kwargs, "max_active_runs": max_active}, concurrency))
    results = []
    for store, kwargs, concurrency in plan:
        runs: dict[str, list[dict]] = {"inline": [], "thread": []}
        for _ in range(args.repeats):
            for strategy in ("inline", "thread"):  # interleaved: same host state for both
                runs[strategy].append(await scenario(store, kwargs, strategy, concurrency, args.requests))
        for strategy, repeats in runs.items():
            ordered = sorted(repeats, key=lambda r: r["throughput_rps"])
            median = dict(ordered[len(ordered) // 2])
            median["throughput_rps_all_repeats"] = [r["throughput_rps"] for r in repeats]
            results.append(median)
            print(f"{store:17} c={concurrency:<2} max={kwargs['max_active_runs']:<2} {strategy:6} "
                  f"{median['throughput_rps']:7.1f} rps p50={median['latency_ms']['p50']:7.2f} "
                  f"p99={median['latency_ms']['p99']:7.2f} lagmax={median['loop_lag_ms']['max']:7.2f} "
                  f"health_p99={median['health_probe_ms']['p99']:7.2f} thr={median['peak_threads']} "
                  f"rss={median['peak_rss_mb']} {median['status_counts']}", file=sys.stderr, flush=True)
    return {"experiment": "P9 option C vs inline (P8 baseline)", "python": platform.python_version(),
            "platform": platform.platform(), "cpus": os.cpu_count(), "requests": args.requests,
            "repeats": args.repeats, "pg_pool_sizes": args.pg_pool_sizes if args.pg else None,
            "note": ("single process, in-process ASGI (no network/TLS), synthetic agents, ephemeral local "
                     "PostgreSQL over a Unix socket; sizing evidence for THIS host only"),
            "results": results}


def main(argv=None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--pg", action="store_true", help="also measure an ephemeral local PostgreSQL")
    parser.add_argument("--pg-pool-sizes", default="4", help="comma-separated pool sizes, e.g. 4,16")
    parser.add_argument("--no-memory", action="store_true")
    parser.add_argument("--requests", type=int, default=400)
    parser.add_argument("--repeats", type=int, default=3)
    parser.add_argument("--out")
    args = parser.parse_args(argv)
    report = asyncio.run(main_async(args))
    text = json.dumps(report, indent=2)
    if args.out:
        with open(args.out, "w", encoding="utf-8") as handle:
            handle.write(text + "\n")
    else:
        print(text)
    return 0


if __name__ == "__main__":
    sys.exit(main())
