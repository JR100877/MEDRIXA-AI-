"""P9 EXPERIMENT: where does the process CPU of one PostgreSQL-backed run go?

    python -m scripts.experiments.p9_profile_run [--requests N] [--out FILE]

Sequential runs (concurrency 1, inline P8 strategy, pool 4) against an ephemeral
local PostgreSQL. Reports SQL statements and transactions per run (counted by
wrapping psycopg Cursor.execute / Connection.commit in-process) and a
cProfile breakdown of process CPU by component. THIS host only.
"""
import argparse
import asyncio
import cProfile
import json
import platform
import pstats
import secrets
import sys
import time
from collections import Counter

import httpx
import psycopg
from pydantic import SecretStr

from app.web.app import create_app
from app.web.audit import MemoryAuditSink
from app.web.auth import issue_token
from app.web.settings import Settings

GROUPS = (("psycopg", "psycopg"), ("pydantic", "pydantic"), ("app/persistence", "medrixa persistence"),
          ("app/core", "medrixa core"), ("app/agents", "medrixa agents"), ("app/web", "medrixa web"),
          ("starlette", "starlette/fastapi"), ("fastapi", "starlette/fastapi"), ("httpx", "httpx client"),
          ("asyncio", "asyncio"), ("json", "json"), ("hashlib", "hashing"), ("hmac", "hashing"))


def instrument(counter: Counter):
    original_execute = psycopg.Cursor.execute

    def execute(self, query, *args, **kwargs):
        text = query if isinstance(query, str) else type(query).__name__
        counter["statements"] += 1
        label = " ".join(text.split()[:2]).upper()[:40] or "<empty query: psycopg-pool connection check>"
        counter["statement:" + label] += 1
        return original_execute(self, query, *args, **kwargs)

    psycopg.Cursor.execute = execute  # type: ignore[method-assign]
    original_commit = psycopg.Connection.commit

    def commit(self):
        counter["commits"] += 1
        return original_commit(self)

    psycopg.Connection.commit = commit  # type: ignore[method-assign]


async def drive(app, token, total):
    body = {"case": {"case_id": "case-load", "synthetic": True, "source": "synthetic_fixture",
                     "history_available": True, "observations": [{"code": "demo-measure", "value": 1.0,
                                                                  "unit": "demo-unit"}]}}
    prefix = secrets.token_hex(4)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://testserver") as http:
        for i in range(total):
            response = await http.post("/v1/runs", json=body, headers={
                "Authorization": f"Bearer {token}", "Idempotency-Key": f"prof-{prefix}-{i:010d}"})
            assert response.status_code == 201, response.text


def main(argv=None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--requests", type=int, default=200)
    parser.add_argument("--out")
    args = parser.parse_args(argv)
    from tests.persistence.pg_support import fresh_database_dsn
    secret = secrets.token_urlsafe(48)
    settings = Settings(environment="test", auth_secret=SecretStr(secret), allowed_hosts=("testserver",),
                        rate_limit_ip_per_minute=100_000, rate_limit_subject_per_minute=100_000,
                        postgres_dsn=SecretStr(fresh_database_dsn()), postgres_pool_size=4)
    app = create_app(settings, audit=MemoryAuditSink())
    token = issue_token(secret, "load-user", ["case_runner"], ["case-load"])
    asyncio.run(drive(app, token, 20))  # warm-up (pool, imports, prepared statements)
    counter: Counter = Counter()
    instrument(counter)
    profiler = cProfile.Profile()
    cpu, wall = time.process_time(), time.perf_counter()
    profiler.enable()
    asyncio.run(drive(app, token, args.requests))
    profiler.disable()
    cpu, wall = time.process_time() - cpu, time.perf_counter() - wall
    app.state.service.repository.close()
    stats = pstats.Stats(profiler)
    total_tt = sum(row[2] for row in stats.stats.values())  # type: ignore[attr-defined]
    by_group: Counter = Counter()
    for (filename, _, _), row in stats.stats.items():  # type: ignore[attr-defined]
        label = next((name for key, name in GROUPS if key in filename), "other")
        by_group[label] += row[2]  # own time (tottime)
    n = args.requests
    report = {"experiment": "P9 per-run CPU and SQL profile (inline, c=1, PostgreSQL pool 4)",
              "python": platform.python_version(), "requests": n,
              "wall_ms_per_run": round(wall * 1000 / n, 2), "cpu_ms_per_run": round(cpu * 1000 / n, 2),
              "cpu_share_of_wall": round(cpu / wall, 2),
              "sql_statements_per_run": round(counter["statements"] / n, 1),
              "commits_per_run": round(counter["commits"] / n, 1),
              "statement_kinds_per_run": {k.split(":", 1)[1]: round(v / n, 1) for k, v in counter.most_common()
                                          if k.startswith("statement:")},
              "profiled_own_time_share": {k: round(v / total_tt, 3) for k, v in by_group.most_common()},
              "note": "cProfile inflates Python-level overhead; shares are indicative, not absolute"}
    text = json.dumps(report, indent=2)
    if args.out:
        with open(args.out, "w", encoding="utf-8") as handle:
            handle.write(text + "\n")
    print(text)
    return 0


if __name__ == "__main__":
    sys.exit(main())
