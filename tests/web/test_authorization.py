"""Authentication, per-case authorization, cross-access protection and access audit."""
import time
import unittest

from tests.web.support import MARKER, Api, case_body


class AuthorizationTests(unittest.TestCase):
    def setUp(self):
        self.api = Api()
        self.client = self.api.client
        self.run_a = self.api.create(case_body("case-a"), key="key-a-000000000000001").json()["run_id"]
        self.run_b = self.api.create(case_body("case-b"), key="key-b-000000000000001", subject="user-b",
                                     cases=("case-b",)).json()["run_id"]

    def code(self, response):
        return response.status_code, response.json()["error"]["code"] if response.status_code >= 400 else None

    def test_every_protected_route_requires_authentication(self):
        routes = [("post", "/v1/runs"), ("get", f"/v1/runs/{self.run_a}"),
                  ("get", f"/v1/runs/{self.run_a}/events"), ("post", "/v1/admin/recovery")]
        expired = self.api.token(now=int(time.time()) - 7200, ttl=900)
        for method, path in routes:
            for headers in ({}, {"Authorization": "Bearer nope"}, {"Authorization": f"Bearer {expired}"}):
                with self.subTest(path=path, headers=list(headers)):
                    response = getattr(self.client, method)(path, headers=headers)
                    self.assertEqual(self.code(response), (401, "UNAUTHENTICATED"))
                    self.assertEqual(response.headers["www-authenticate"], 'Bearer realm="medrixa"')

    def test_unauthenticated_request_is_rejected_before_body_or_key_checks(self):
        response = self.client.post("/v1/runs", content=b"{" + MARKER.encode(), headers={"Content-Type": "text/x"})
        self.assertEqual(self.code(response), (401, "UNAUTHENTICATED"))

    def test_create_requires_role_and_case_scope(self):
        reviewer = self.api.create(case_body("case-a"), key="key-r-000000000000001", roles=("case_reviewer",))
        operator = self.api.create(case_body("case-a"), key="key-o-000000000000001", roles=("operator",),
                                   cases=())
        other_case = self.api.create(case_body("case-z"), key="key-z-000000000000001")
        for response in (reviewer, operator, other_case):
            self.assertEqual(self.code(response), (403, "FORBIDDEN"))
        self.assertEqual(len(self.api.app.state.service.repository.list_runs()), 2)

    def test_cross_case_read_is_indistinguishable_from_missing(self):
        runner_a = self.api.auth(subject="user-a", cases=("case-a",))
        for suffix in ("", "/events"):
            foreign = self.client.get(f"/v1/runs/{self.run_b}{suffix}", headers=runner_a)
            missing = self.client.get(f"/v1/runs/idem-{'0' * 64}{suffix}", headers=runner_a)
            invalid = self.client.get(f"/v1/runs/..%2F..%2Fetc{suffix}", headers=runner_a)
            too_long = self.client.get(f"/v1/runs/{'x' * 200}{suffix}", headers=runner_a)
            for response in (foreign, missing, invalid, too_long):
                with self.subTest(suffix=suffix, path=str(response.url)[-30:]):
                    self.assertEqual(self.code(response), (404, "NOT_FOUND"))
                    body = response.json()
                    body["error"].pop("request_id")
                    self.assertEqual(body, {"api_version": "medrixa.http.v1", "error": {
                        "code": "NOT_FOUND", "message": "The resource was not found."}})

    def test_reviewer_reads_only_own_cases(self):
        reviewer = self.api.auth(subject="rev-1", roles=("case_reviewer",), cases=("case-a",))
        self.assertEqual(self.client.get(f"/v1/runs/{self.run_a}", headers=reviewer).status_code, 200)
        self.assertEqual(self.code(self.client.get(f"/v1/runs/{self.run_b}", headers=reviewer)), (404, "NOT_FOUND"))

    def test_operator_recovers_but_cannot_read_runs(self):
        operator = self.api.auth(subject="ops-1", roles=("operator",), cases=())
        self.assertEqual(self.code(self.client.get(f"/v1/runs/{self.run_a}", headers=operator)),
                         (403, "FORBIDDEN"))
        recovered = self.client.post("/v1/admin/recovery", headers=operator)
        self.assertEqual(recovered.status_code, 200)
        self.assertEqual(recovered.json(), {"api_version": "medrixa.http.v1", "moved_to_review": 0, "not_started": 0,
                                            "already_terminal": 2, "corrupt_records": 0, "agents_reexecuted": 0})
        runner = self.api.auth()
        self.assertEqual(self.code(self.client.post("/v1/admin/recovery", headers=runner)), (403, "FORBIDDEN"))

    def test_access_audit_records_identity_without_sensitive_data(self):
        self.client.get(f"/v1/runs/{self.run_b}", headers=self.api.auth(subject="user-a"))
        self.client.get(f"/v1/runs/{self.run_a}", headers={"Authorization": "Bearer forged"})
        events = self.api.audit.events
        creates = [e for e in events if e.action == "runs.create"]
        self.assertEqual([(e.phase, e.outcome) for e in creates],
                         [("attempt", "ATTEMPT"), ("outcome", "CREATED")] * 2)
        self.assertTrue(all(e.subject and e.case_id and e.run_id and e.request_id for e in creates))
        denied = [e for e in events if e.action == "runs.read"][-1]
        self.assertEqual((denied.subject, denied.outcome, denied.status_code, denied.case_id),
                         ("user-a", "NOT_FOUND", 404, None))
        auth_failure = [e for e in events if e.action == "auth"][-1]
        self.assertEqual((auth_failure.subject, auth_failure.outcome), (None, "UNAUTHENTICATED"))
        dumped = "".join(e.model_dump_json() for e in events)
        for forbidden in ("demo-measure", "demo-unit", "Bearer", "mdx1", self.api.secret, "observations"):
            self.assertNotIn(forbidden, dumped)


if __name__ == "__main__":
    unittest.main()
