"""Idempotency keys: duplicates, conflicts, retries, concurrency, restart and interruption."""
import asyncio
import json
import tempfile
import unittest

import httpx

from app.persistence.manager import PersistentStateManager
from app.web.contracts import CreateRunRequest
from app.web.service import derive_run_id
from tests.helpers import message
from tests.web.support import Api, CountingHistoryAgent, case_body, runner_factory_for

KEY = "retry-key-0000000001"


class IdempotencySyncTests(unittest.TestCase):
    def setUp(self):
        self.agent = CountingHistoryAgent()
        self.api = Api(runner_factory=runner_factory_for(self.agent))
        self.repo = self.api.app.state.service.repository

    def test_key_required_and_validated(self):
        for key in (None, "", "short", "x" * 65, "has space key 0001", "key/with/slash/0001", "key.with.dots.0001"):
            headers = {**self.api.auth(), "Content-Type": "application/json"}
            if key is not None:
                headers["Idempotency-Key"] = key
            with self.subTest(key=key):
                response = self.api.client.post("/v1/runs", json=case_body(), headers=headers)
                self.assertEqual((response.status_code, response.json()["error"]["code"]),
                                 (400, "IDEMPOTENCY_KEY_REQUIRED"))
        self.assertEqual((self.repo.list_runs(), self.agent.calls), ((), 0))

    def test_retry_replays_identical_response_without_reexecution(self):
        first = self.api.create(key=KEY)
        second = self.api.create(key=KEY)
        self.assertEqual((first.status_code, second.status_code), (201, 201))
        self.assertEqual((first.headers["idempotent-replayed"], second.headers["idempotent-replayed"]),
                         ("false", "true"))
        self.assertEqual(first.json(), second.json())
        self.assertEqual(self.agent.calls, 1)
        self.assertEqual(len(self.repo.list_runs()), 1)

    def test_same_key_different_body_rejected_original_kept(self):
        original = self.api.create(key=KEY).json()
        for body in (case_body(history=False), case_body(observations=[]),
                     case_body(case_id="case-a", observations=[{"code": "other", "value": 2.0, "unit": "u"}])):
            with self.subTest(body=body):
                response = self.api.create(body, key=KEY)
                self.assertEqual((response.status_code, response.json()["error"]["code"]),
                                 (422, "IDEMPOTENCY_KEY_REUSED"))
        self.assertEqual(self.api.client.get(f"/v1/runs/{original['run_id']}", headers=self.api.auth()).json(),
                         original)
        self.assertEqual(self.agent.calls, 1)

    def test_keys_are_scoped_per_subject(self):
        mine = self.api.create(key=KEY, subject="user-1")
        theirs = self.api.create(key=KEY, subject="user-2")
        self.assertNotEqual(mine.json()["run_id"], theirs.json()["run_id"])
        self.assertEqual((theirs.status_code, theirs.headers["idempotent-replayed"]), (201, "false"))
        self.assertEqual(mine.json()["run_id"], derive_run_id("user-1", KEY))

    def test_idempotency_survives_restart_with_file_store(self):
        with tempfile.TemporaryDirectory() as store:
            before = Api(store_dir=store, secret=self.api.secret, runner_factory=runner_factory_for(self.agent))
            created = before.create(key=KEY)
            after_agent = CountingHistoryAgent()
            after = Api(store_dir=store, secret=self.api.secret, runner_factory=runner_factory_for(after_agent))
            replay = after.create(key=KEY)
            self.assertEqual((replay.status_code, replay.headers["idempotent-replayed"]), (201, "true"))
            self.assertEqual(replay.json(), created.json())
            self.assertEqual(after_agent.calls, 0)

    def test_interrupted_run_conflicts_until_recovered_then_replays(self):
        run_id = derive_run_id("user-runner", KEY)
        case = CreateRunRequest.model_validate_json(json.dumps(case_body())).case
        crashed = PersistentStateManager(case, self.repo, run_id=run_id)
        started = crashed.start(0)
        crashed.dispatch(message(started), started.revision)  # process "dies" with the agent in flight

        in_progress = self.api.create(key=KEY)
        self.assertEqual((in_progress.status_code, in_progress.json()["error"]["code"]),
                         (409, "IDEMPOTENCY_IN_PROGRESS"))
        recovered = self.api.client.post("/v1/admin/recovery",
                                         headers=self.api.auth(subject="ops", roles=("operator",), cases=()))
        self.assertEqual(recovered.json()["moved_to_review"], 1)
        replay = self.api.create(key=KEY)
        self.assertEqual((replay.status_code, replay.headers["idempotent-replayed"]), (201, "true"))
        self.assertEqual((replay.json()["stop_reason"], replay.json()["completed"]), ("INTERRUPTED", 0))
        self.assertEqual(self.agent.calls, 0)


class IdempotencyConcurrencyTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.gate, self.entered = asyncio.Event(), asyncio.Event()
        self.agent = CountingHistoryAgent(gate=self.gate, entered=self.entered)
        self.api = Api(runner_factory=runner_factory_for(self.agent))
        self.http = httpx.AsyncClient(transport=httpx.ASGITransport(app=self.api.app), base_url="http://testserver")
        self.addAsyncCleanup(self.http.aclose)

    def post(self, key, **token_kwargs):
        return self.http.post("/v1/runs", json=case_body(),
                              headers={**self.api.auth(**token_kwargs), "Idempotency-Key": key})

    async def test_concurrent_duplicates_execute_once(self):
        first = asyncio.create_task(self.post(KEY))
        await asyncio.wait_for(self.entered.wait(), 10)          # first request's agent is in flight
        duplicates = await asyncio.gather(*(self.post(KEY) for _ in range(9)))
        self.gate.set()
        winner = await first
        self.assertEqual((winner.status_code, winner.headers["idempotent-replayed"]), (201, "false"))
        self.assertEqual({(r.status_code, r.json()["error"]["code"]) for r in duplicates},
                         {(409, "IDEMPOTENCY_IN_PROGRESS")})
        replay = await self.post(KEY)
        self.assertEqual((replay.status_code, replay.json()), (201, winner.json()))
        self.assertEqual(self.agent.calls, 1)
        self.assertEqual(len(self.api.app.state.service.repository.list_runs()), 1)

    async def test_simultaneous_first_requests_have_a_single_winner(self):
        self.gate.set()
        responses = await asyncio.gather(*(self.post(KEY) for _ in range(10)))
        created = [r for r in responses if r.status_code == 201 and r.headers["idempotent-replayed"] == "false"]
        others = {(r.status_code, r.headers.get("idempotent-replayed") or r.json()["error"]["code"])
                  for r in responses if r not in created}
        self.assertEqual(len(created), 1)
        self.assertTrue(others <= {(409, "IDEMPOTENCY_IN_PROGRESS"), (201, "true")}, others)
        self.assertEqual(self.agent.calls, 1)

    async def test_distinct_keys_run_independently(self):
        self.gate.set()
        responses = await asyncio.gather(*(self.post(f"distinct-key-{i:08d}") for i in range(6)))
        self.assertEqual({r.status_code for r in responses}, {201})
        self.assertEqual(len({r.json()["run_id"] for r in responses}), 6)
        self.assertEqual(self.agent.calls, 6)

    async def test_recovery_refused_while_a_run_executes_in_process(self):
        first = asyncio.create_task(self.post(KEY))
        await asyncio.wait_for(self.entered.wait(), 10)
        refused = await self.http.post("/v1/admin/recovery",
                                       headers=self.api.auth(subject="ops", roles=("operator",), cases=()))
        self.assertEqual((refused.status_code, refused.json()["error"]["code"]), (409, "RUNS_ACTIVE"))
        self.gate.set()
        self.assertEqual((await first).json()["stop_reason"], "REVIEW_REQUIRED")


if __name__ == "__main__":
    unittest.main()
