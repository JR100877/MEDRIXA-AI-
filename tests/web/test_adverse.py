"""Adverse scenarios: failing storage/audit, corruption, internal errors, CLI safety."""
import os
import secrets
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from app.persistence.local_file import _key
from app.persistence.memory import InMemoryStateRepository
from app.persistence.repository import StateRepository
from app.web.audit import AuditSink, MemoryAuditSink
from app.web.auth import HmacTokenAuthenticator
from tests.web.support import MARKER, Api

PROJECT_ROOT = Path(__file__).resolve().parents[2]


class FailingSaves(StateRepository):
    adapter_name = "test-failing-saves"

    def __init__(self, error):
        self.inner, self.error = InMemoryStateRepository(), error

    def create(self, state):
        return self.inner.create(state)

    def load(self, run_id):
        return self.inner.load(run_id)

    def scan(self):
        return self.inner.scan()

    def save(self, state, expected_revision):
        raise self.error


class FailingAudit(AuditSink):
    def record(self, event):
        raise OSError(f"disk full {MARKER}")


class ExplodingRunner:
    def __init__(self, *_):
        pass

    async def run(self, manager):
        raise RuntimeError(f"unexpected {MARKER} /etc/passwd")


class AdverseTests(unittest.TestCase):
    def assert_code(self, response, status, code):
        self.assertEqual((response.status_code, response.json()["error"]["code"]), (status, code), response.text)
        self.assertNotIn(MARKER, response.text)
        self.assertNotIn("Traceback", response.text)

    def test_storage_failure_is_503_without_details(self):
        repo = FailingSaves(OSError(f"io error {MARKER} /var/lib/x"))
        api = Api(repository=repo)
        self.assert_code(api.create(), 503, "STORE_UNAVAILABLE")
        [run_id] = repo.list_runs()
        self.assertEqual(repo.load(run_id).revision, 0)  # last persisted revision kept
        retry = api.create()
        self.assert_code(retry, 409, "IDEMPOTENCY_IN_PROGRESS")  # never re-executed blindly

    def test_audit_failure_refuses_operation_before_side_effects(self):
        repo = InMemoryStateRepository()
        api = Api(repository=repo, audit=FailingAudit())
        self.assert_code(api.create(), 503, "AUDIT_UNAVAILABLE")
        self.assertEqual(repo.list_runs(), ())
        self.assert_code(api.client.post("/v1/admin/recovery",
                                         headers=api.auth(roles=("operator",), cases=())), 503, "AUDIT_UNAVAILABLE")

    def test_corrupt_record_is_500_integrity_error(self):
        with tempfile.TemporaryDirectory() as store:
            api = Api(store_dir=store)
            run_id = api.create().json()["run_id"]
            record = Path(store) / f"{_key(run_id)}.json"
            record.write_bytes(record.read_bytes()[:-20])
            self.assert_code(api.client.get(f"/v1/runs/{run_id}", headers=api.auth()), 500, "STORE_INTEGRITY_ERROR")
            replay = api.create()
            self.assert_code(replay, 500, "STORE_INTEGRITY_ERROR")
            recovered = api.client.post("/v1/admin/recovery", headers=api.auth(roles=("operator",), cases=()))
            self.assertEqual(recovered.json()["corrupt_records"], 1)

    def test_unexpected_exception_is_generic_500(self):
        api = Api(runner_factory=ExplodingRunner)
        response = api.create()
        self.assert_code(response, 500, "INTERNAL_ERROR")
        self.assertNotIn("passwd", response.text)

    def test_agent_failure_is_a_completed_http_call_ending_in_review(self):
        from tests.core.test_engine import FaultAgent, runner
        api = Api(runner_factory=lambda: runner(FaultAgent("error")))
        response = api.create()
        self.assertEqual(response.status_code, 201)
        self.assertEqual((response.json()["status"], response.json()["stop_reason"]),
                         ("awaiting_human_review", "AGENT_ERROR"))

    def test_audit_outcome_recorded_for_failed_create(self):
        audit = MemoryAuditSink()
        api = Api(repository=FailingSaves(OSError("x")), audit=audit)
        api.create()
        self.assertEqual([(e.phase, e.outcome, e.status_code) for e in audit.events],
                         [("attempt", "ATTEMPT", None), ("outcome", "STORE_UNAVAILABLE", 503)])

    def test_unconfigured_audit_logger_fails_closed_configured_one_emits(self):
        import io
        import json
        import logging
        from app.web.audit import LoggingAuditSink, configure_audit_logging
        silent = logging.getLogger(f"medrixa.audit.unconfigured.{secrets.token_hex(4)}")
        silent.propagate = False
        repo = InMemoryStateRepository()
        api = Api(repository=repo, audit=LoggingAuditSink(silent))
        self.assert_code(api.create(), 503, "AUDIT_UNAVAILABLE")
        self.assertEqual(repo.list_runs(), ())
        stream = io.StringIO()
        logger = configure_audit_logging(stream)
        self.addCleanup(lambda: [logger.removeHandler(h) for h in list(logger.handlers)
                                 if getattr(h, "_medrixa_audit", False)])
        api = Api(audit=LoggingAuditSink(logger))
        self.assertEqual(api.create().status_code, 201)
        lines = [json.loads(line) for line in stream.getvalue().splitlines()]
        self.assertEqual([(e["action"], e["phase"], e["outcome"]) for e in lines],
                         [("runs.create", "attempt", "ATTEMPT"), ("runs.create", "outcome", "CREATED")])
        self.assertNotIn("demo-measure", stream.getvalue())

    def test_clock_rollback_cannot_extend_token(self):
        api = Api()
        authenticator = HmacTokenAuthenticator(api.secret, 3600, 30, clock=lambda: 10_000_000_000)
        from app.web.auth import AuthenticationFailed
        with self.assertRaises(AuthenticationFailed):
            authenticator.authenticate(f"Bearer {api.token(ttl=900)}")


class WebCliTests(unittest.TestCase):
    def run_cli(self, *args, **env):
        environment = {k: v for k, v in os.environ.items() if not k.startswith("MEDRIXA_")}
        environment.update(env)
        return subprocess.run([sys.executable, "-m", "app.web", *args], cwd=PROJECT_ROOT, env=environment,
                              capture_output=True, text=True, timeout=60)

    def test_issue_token_verifies_and_secret_never_printed(self):
        secret = secrets.token_urlsafe(48)
        result = self.run_cli("issue-token", "--sub", "dev-1", "--role", "case_reviewer", "--case", "case-a",
                              MEDRIXA_ENV="development", MEDRIXA_AUTH_SECRET=secret)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertNotIn(secret, result.stdout + result.stderr)
        principal = HmacTokenAuthenticator(secret, 3600, 30).authenticate(f"Bearer {result.stdout.strip()}")
        self.assertEqual((principal.subject, principal.roles), ("dev-1", frozenset({"case_reviewer"})))

    def test_refusals(self):
        secret = secrets.token_urlsafe(48)
        cases = [({"MEDRIXA_ENV": "production", "MEDRIXA_AUTH_SECRET": secret}, ("issue-token", "--sub", "a",
                                                                               "--role", "operator")),
                 ({"MEDRIXA_ENV": "test"}, ("serve",)),
                 ({"MEDRIXA_ENV": "test", "MEDRIXA_AUTH_SECRET": secret},
                  ("issue-token", "--sub", "a", "--role", "operator", "--ttl", "999999")),
                 ({"MEDRIXA_ENV": "test", "MEDRIXA_AUTH_SECRET": secret},
                  ("issue-token", "--sub", "../x", "--role", "operator"))]
        for env, args in cases:
            with self.subTest(args=args[:1], env=env.get("MEDRIXA_ENV")):
                result = self.run_cli(*args, **env)
                self.assertEqual(result.returncode, 2)
                self.assertNotIn(secret, result.stdout + result.stderr)


if __name__ == "__main__":
    unittest.main()
