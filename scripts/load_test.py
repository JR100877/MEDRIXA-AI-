"""P8.4: reproducible local load test of POST /v1/runs (in-process ASGI, no network).

    python -m scripts.load_test [--pg-dsn-env VAR] [--requests N] [--out FILE]

Each scenario creates N synthetic runs with distinct idempotency keys at a given
concurrency and reports throughput, latency percentiles and status counts.
Rate limits are raised so the limiter is not what is measured; the execution
capacity limit is kept as configured per scenario. Results describe THIS host,
one process, synthetic agents: they size defaults, they are not a capacity claim.
"""
import argparse
import asyncio
import json
import os
import platform
import secrets
import statistics
import sys
import time
from collections import Counter

import httpx

from app.web.app import create_app
from app.web.audit import MemoryAuditSink
from app.web.auth import issue_token
from app.web.settings import Settings


def pct(sorted_ms: list[float], p: float) -> float:
    return round(sorted_ms[min(len(sorted_ms) - 1, int(round(p / 100 * (len(sorted_ms) - 1))))], 2)


async def scenario(name: str, settings_kwargs: dict, concurrency: int, total: int) -> dict:
    secret = secrets.token_urlsafe(48)
    settings = Settings(environment="test", auth_secret=secret, allowed_hosts=("testserver",),
                        rate_limit_ip_per_minute=100_000, rate_limit_subject_per_minute=100_000, **settings_kwargs)
    app = create_app(settings, audit=MemoryAuditSink())
    token = issue_token(secret, "load-user", ["case_runner"], ["case-load"])
    body = {"case": {"case_id": "case-load", "synthetic": True, "source": "synthetic_fixture",
                     "history_available": True, "observations": [{"code": "demo-measure", "value": 1.0,
                                                                  "unit": "demo-unit"}]}}
    latencies: list[float] = []
    statuses: Counter = Counter()
    queue: asyncio.Queue = asyncio.Queue()
    for i in range(total):
        queue.put_nowait(i)
    run_prefix = secrets.token_hex(4)
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
                        "Authorization": f"Bearer {token}", "Idempotency-Key": f"load-{run_prefix}-{i:010d}"})
                    latencies.append((time.perf_counter() - started) * 1000)
                    statuses[response.status_code] += 1
            wall = time.perf_counter()
            await asyncio.gather(*(worker() for _ in range(concurrency)))
            wall = time.perf_counter() - wall
    finally:
        close = getattr(app.state.service.repository, "close", None)
        if callable(close):
            close()
    ordered = sorted(latencies)
    return {"scenario": name, "concurrency": concurrency, "requests": total,
            "throughput_rps": round(total / wall, 1), "status_counts": dict(sorted(statuses.items())),
            "latency_ms": {"p50": pct(ordered, 50), "p95": pct(ordered, 95), "p99": pct(ordered, 99),
                           "max": round(ordered[-1], 2), "mean": round(statistics.fmean(ordered), 2)}}


async def main_async(args) -> dict:
    results = []
    stores = [("memory", {})]
    if args.pg_dsn_env:
        stores.append(("postgresql-pool4", {"postgres_dsn": os.environ[args.pg_dsn_env], "postgres_pool_size": 4}))
    for store, kwargs in stores:
        for concurrency in (1, 8, 32):
            results.append(await scenario(f"{store} max_active_runs=16", {**kwargs, "max_active_runs": 16},
                                          concurrency, args.requests))
        results.append(await scenario(f"{store} max_active_runs=4 (saturation)", {**kwargs, "max_active_runs": 4},
                                      32, args.requests))
    return {"python": platform.python_version(), "platform": platform.platform(),
            "note": "single process, in-process ASGI (no network/TLS), synthetic agents; sizing evidence only",
            "results": results}


def main(argv=None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--pg-dsn-env")
    parser.add_argument("--requests", type=int, default=400)
    parser.add_argument("--out")
    args = parser.parse_args(argv)
    report = asyncio.run(main_async(args))
    text = json.dumps(report, indent=2)
    if args.out:
        with open(args.out, "w", encoding="utf-8") as handle:
            handle.write(text + "\n")
    print(text)
    return 0


if __name__ == "__main__":
    sys.exit(main())
