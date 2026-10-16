"""P7.3: token-bucket limiter, HTTP rate limiting (IP and subject) and execution capacity."""
import asyncio
import secrets
import threading
import unittest

import httpx
from fastapi.testclient import TestClient

from app.security.ratelimit import TokenBucketLimiter
from app.web.app import create_app
from app.web.audit import MemoryAuditSink
from app.web.auth import issue_token
from app.web.settings import Settings, settings_from_env
from tests.security.oidc_support import FakeClock
from tests.web.support import CountingHistoryAgent, case_body, runner_factory_for


class TokenBucketTests(unittest.TestCase):
    def test_burst_then_refill(self):
        clock = FakeClock(0.0)
        limiter = TokenBucketLimiter(per_minute=60, burst=3, clock=clock)
        self.assertEqual([limiter.check("k") for _ in range(3)], [None, None, None])
        self.assertEqual(limiter.check("k"), 1.0)          # 1 token/s -> retry in 1 s
        clock.now += 1.0
        self.assertIsNone(limiter.check("k"))
        self.assertIsNotNone(limiter.check("k"))
        clock.now += 100
        self.assertEqual([limiter.check("k") for _ in range(4)].count(None), 3)  # capped at burst

    def test_keys_are_independent_and_memory_bounded(self):
        limiter = TokenBucketLimiter(per_minute=60, burst=1, max_keys=100, clock=FakeClock())
        self.assertIsNone(limiter.check("a"))
        self.assertIsNotNone(limiter.check("a"))
        self.assertIsNone(limiter.check("b"))
        for i in range(10_000):
            limiter.check(f"flood-{i}")
        self.assertEqual(len(limiter), 100)

    def test_retry_after_reflects_rate(self):
        limiter = TokenBucketLimiter(per_minute=6, burst=1, clock=FakeClock())  # one token every 10 s
        limiter.check("k")
        self.assertEqual(limiter.check("k"), 10)

    def test_thread_safety_exact_budget(self):
        limiter = TokenBucketLimiter(per_minute=1, burst=50, clock=FakeClock())
        allowed, barrier = [], threading.Barrier(16)

        def hammer():
            barrier.wait()
            for _ in range(20):
                if limiter.check("shared") is None:
                    allowed.append(1)
        threads = [threading.Thread(target=hammer) for _ in range(16)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        self.assertEqual(len(allowed), 50)

    def test_invalid_configuration(self):
        for kwargs in ({"per_minute": 0}, {"per_minute": -5}, {"per_minute": 1.5}, {"per_minute": True},
                       {"per_minute": 60, "burst": 0}, {"per_minute": 60, "max_keys": 0}):
            with self.subTest(kwargs=kwargs), self.assertRaises(ValueError):
                TokenBucketLimiter(**kwargs)


class HttpRateLimitTests(unittest.TestCase):
    def make(self, **limits):
        self.secret = secrets.token_urlsafe(48)
        settings = Settings(environment="test", auth_secret=self.secret, allowed_hosts=("testserver",), **limits)
        self.audit = MemoryAuditSink()
        return TestClient(create_app(settings, audit=self.audit))

    def auth(self, subject="user-1"):
        return {"Authorization": f"Bearer {issue_token(self.secret, subject, ['case_runner'], ['case-a'])}"}

    def assert_limited(self, response):
        self.assertEqual(response.status_code, 429)
        body = response.json()
        self.assertEqual(body["error"]["code"], "RATE_LIMITED")
        self.assertEqual(body["error"]["request_id"], response.headers["x-request-id"])
        self.assertGreaterEqual(int(response.headers["retry-after"]), 1)
        self.assertEqual(response.headers["cache-control"], "no-store")

    def test_ip_limit_applies_before_authentication(self):
        client = self.make(rate_limit_ip_per_minute=8)  # burst 2
        statuses = [client.get("/v1/runs/x", headers={"Authorization": "Bearer guess"}).status_code
                    for _ in range(4)]
        self.assertEqual(statuses[:2], [401, 401])
        self.assert_limited(client.get("/v1/runs/x", headers={"Authorization": "Bearer guess"}))
        self.assert_limited(client.get("/v1/health"))  # same client address
        auth_failures = [e for e in self.audit.events if e.action == "auth"]
        self.assertEqual(len(auth_failures), 2)  # throttled requests never reach the authenticator

    def test_subject_limit_is_per_identity(self):
        client = self.make(rate_limit_subject_per_minute=8, rate_limit_ip_per_minute=10_000)
        for _ in range(2):
            self.assertEqual(client.get("/v1/runs/missing", headers=self.auth("user-1")).status_code, 404)
        self.assert_limited(client.get("/v1/runs/missing", headers=self.auth("user-1")))
        self.assertEqual(client.get("/v1/runs/missing", headers=self.auth("user-2")).status_code, 404)

    def test_disallowed_host_is_refused_without_consuming_budget(self):
        client = self.make(rate_limit_ip_per_minute=4)  # burst 1
        for _ in range(5):
            self.assertEqual(client.get("/v1/health", headers={"Host": "evil.example"}).status_code, 400)
        self.assertEqual(client.get("/v1/health").status_code, 200)

    def test_environment_configuration(self):
        base = {"MEDRIXA_ENV": "test", "MEDRIXA_AUTH_SECRET": "ratelimit-env-secret-0123456789-abcdefgh"}
        settings = settings_from_env({**base, "MEDRIXA_RATE_LIMIT_IP_PER_MINUTE": "100",
                                      "MEDRIXA_RATE_LIMIT_SUBJECT_PER_MINUTE": "50", "MEDRIXA_MAX_ACTIVE_RUNS": "4"})
        self.assertEqual((settings.rate_limit_ip_per_minute, settings.rate_limit_subject_per_minute,
                          settings.max_active_runs), (100, 50, 4))
        from app.web.settings import ConfigurationError
        for variable, value in (("MEDRIXA_RATE_LIMIT_IP_PER_MINUTE", "0"), ("MEDRIXA_MAX_ACTIVE_RUNS", "0"),
                                ("MEDRIXA_RATE_LIMIT_SUBJECT_PER_MINUTE", "lots")):
            with self.subTest(variable=variable), self.assertRaises(ConfigurationError):
                settings_from_env({**base, variable: value})


class CapacityTests(unittest.IsolatedAsyncioTestCase):
    async def test_capacity_exhaustion_refuses_without_creating_runs(self):
        secret = secrets.token_urlsafe(48)
        gate, entered = asyncio.Event(), asyncio.Event()
        agent = CountingHistoryAgent(gate=gate, entered=entered)
        settings = Settings(environment="test", auth_secret=secret, allowed_hosts=("testserver",), max_active_runs=1)
        app = create_app(settings, audit=MemoryAuditSink(), runner_factory=runner_factory_for(agent))
        auth = {"Authorization": f"Bearer {issue_token(secret, 'user-1', ['case_runner'], ['case-a'])}"}
        repo = app.state.service.repository
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://testserver") as http:
            def post(key):
                return http.post("/v1/runs", json=case_body(), headers={**auth, "Idempotency-Key": key})
            first = asyncio.create_task(post("capacity-key-000000001"))
            await asyncio.wait_for(entered.wait(), 10)
            refused = await post("capacity-key-000000002")
            self.assertEqual((refused.status_code, refused.json()["error"]["code"]), (503, "CAPACITY_EXCEEDED"))
            self.assertEqual(refused.headers["retry-after"], "1")
            self.assertEqual(len(repo.list_runs()), 1)          # nothing stored for the refused request
            gate.set()
            self.assertEqual((await first).status_code, 201)
            again = await post("capacity-key-000000002")       # capacity released
            self.assertEqual(again.status_code, 201)
        self.assertEqual(agent.calls, 2)


if __name__ == "__main__":
    unittest.main()
