"""P9 experiment: run the WHOLE existing test suite with option C forced as the default
execution strategy of every app built through create_app, and report what breaks.

    python -m scripts.experiments.p9_suite_under_thread [--out FILE]

Nothing in the product changes: the default is patched only inside this process.
"""
import argparse
import json
import sys
import unittest

import app.web.app as web_app
from app.experimental.execution import ThreadLoopExecution

_original = web_app.create_app
created: list[ThreadLoopExecution] = []

# Tests whose agents wait on an asyncio.Event created in the TEST's loop. Under option C
# the agent runs in another loop, Event.set() never wakes it and the test hangs: this is
# the documented loop-affinity incompatibility (tests/experimental re-verifies the same
# guarantees with thread-safe coordination).
LOOP_BOUND = (
    "tests.web.test_idempotency.IdempotencyConcurrencyTests.test_concurrent_duplicates_execute_once",
    "tests.web.test_idempotency.IdempotencyConcurrencyTests.test_recovery_refused_while_a_run_executes_in_process",
    "tests.security.test_ratelimit.CapacityTests.test_capacity_exhaustion_refuses_without_creating_runs",
    "tests.persistence.test_postgres.PostgresHttpTests.test_concurrent_duplicates_and_restart_replay",
)


def _flatten(suite):
    for item in suite:
        if isinstance(item, unittest.TestSuite):
            yield from _flatten(item)
        else:
            yield item


def create_app_with_threads(settings, *args, **kwargs):
    if kwargs.get("execution") is None:
        execution = ThreadLoopExecution(max_workers=16)
        created.append(execution)
        kwargs["execution"] = execution
    return _original(settings, *args, **kwargs)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--out")
    parser.add_argument("--pattern", default="test*.py")
    parser.add_argument("--start", default="tests")
    parser.add_argument("--exclude-loop-bound", action="store_true",
                        help="exclude the 4 known loop-affinity tests (they hang under option C)")
    args = parser.parse_args(argv)
    setattr(web_app, "create_app", create_app_with_threads)  # noqa: B010 (patch only in this process)
    for module in list(sys.modules.values()):  # modules that imported create_app by name
        if module and getattr(module, "create_app", None) is _original:
            setattr(module, "create_app", create_app_with_threads)  # noqa: B010
    suite = unittest.defaultTestLoader.discover(args.start, pattern=args.pattern, top_level_dir=".")
    for module in list(sys.modules.values()):
        if module and getattr(module, "create_app", None) is _original:
            setattr(module, "create_app", create_app_with_threads)  # noqa: B010
    excluded = []
    kept = unittest.TestSuite()
    for test in _flatten(suite):
        test_id = test.id()
        if test_id.startswith("tests.experimental."):
            continue  # they choose their own strategy explicitly
        if args.exclude_loop_bound and test_id in LOOP_BOUND:
            excluded.append(test_id)
            continue
        kept.addTest(test)
    suite = kept
    result = unittest.TextTestRunner(verbosity=1, stream=sys.stderr).run(suite)
    report = {"tests_run": result.testsRun, "failures": len(result.failures), "errors": len(result.errors),
              "skipped": len(result.skipped), "excluded_loop_bound": excluded,
              "apps_built_with_option_c": len(created),
              "apps_that_executed_runs_in_worker_threads": sum(e.peak_in_flight > 0 for e in created),
              "broken": sorted(test.id() for test, _ in result.failures + result.errors)}
    for execution in created:
        execution.shutdown(wait=False)
    text = json.dumps(report, indent=2)
    if args.out:
        with open(args.out, "w", encoding="utf-8") as handle:
            handle.write(text + "\n")
    print(text)
    return 0


if __name__ == "__main__":
    sys.exit(main())
