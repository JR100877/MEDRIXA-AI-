"""HTTP contract: shapes, status codes, uniform errors, input limits, headers, OpenAPI."""
import json
import unittest
from pathlib import Path

from scripts import export_openapi
from tests.web.support import MARKER, Api, case_body

ERROR_KEYS = {"api_version", "error"}
SECURITY_HEADERS = {"cache-control": "no-store", "x-content-type-options": "nosniff", "x-frame-options": "DENY",
                    "content-security-policy": "default-src 'none'; frame-ancestors 'none'"}


class HttpContractTests(unittest.TestCase):
    def setUp(self):
        self.api = Api()
        self.client = self.api.client

    def assert_error(self, response, status, code):
        self.assertEqual(response.status_code, status, response.text)
        body = response.json()
        self.assertEqual(set(body), ERROR_KEYS)
        self.assertEqual(set(body["error"]), {"code", "message", "request_id"})
        self.assertEqual(body["error"]["code"], code)
        self.assertEqual(body["error"]["request_id"], response.headers["x-request-id"])
        self.assertNotIn(MARKER, response.text)
        return body

    def test_create_returns_versioned_view_without_payload(self):
        response = self.api.create(case_body(observations=[{"code": "obs-x", "value": 98765.4321, "unit": "u"}]))
        self.assertEqual(response.status_code, 201, response.text)
        view = response.json()
        self.assertEqual(set(view), {"api_version", "run_id", "case_id", "status", "stop_reason", "revision",
                                     "dispatched", "completed", "abstained", "failed", "human_review_required"})
        self.assertEqual((view["api_version"], view["status"], view["stop_reason"], view["human_review_required"]),
                         ("medrixa.http.v1", "awaiting_human_review", "REVIEW_REQUIRED", True))
        self.assertNotIn("98765", response.text)
        self.assertNotIn("obs-x", response.text)
        self.assertEqual(response.headers["location"], f"/v1/runs/{view['run_id']}")
        self.assertEqual(response.headers["idempotent-replayed"], "false")

    def test_read_run_and_events(self):
        run_id = self.api.create().json()["run_id"]
        view = self.client.get(f"/v1/runs/{run_id}", headers=self.api.auth())
        events = self.client.get(f"/v1/runs/{run_id}/events", headers=self.api.auth())
        self.assertEqual((view.status_code, events.status_code), (200, 200))
        kinds = [e["kind"] for e in events.json()["events"]]
        self.assertEqual(kinds, ["RUN_STARTED", "AGENT_DISPATCHED", "RESULT_ACCEPTED", "AGENT_DISPATCHED",
                                 "RESULT_ACCEPTED", "REVIEW_REQUESTED"])
        self.assertEqual(set(events.json()["events"][0]), {"sequence", "kind", "at", "reason"})

    def test_input_rejections_never_echo_input(self):
        auth = {**self.api.auth(), "Idempotency-Key": "key-0000000000000009"}
        valid = json.dumps(case_body())
        payloads = {
            "not_json": b"{" + MARKER.encode(),
            "duplicate_key": valid[:-1].encode() + b',"case":{}}',
            "extra_field": json.dumps({**case_body(), "note": MARKER}).encode(),
            "extra_case_field": json.dumps(case_body(patient_name=MARKER)).encode(),
            "real_data_marker": json.dumps(case_body(synthetic=False)).encode(),
            "missing_case_id": json.dumps({"case": {k: v for k, v in case_body()["case"].items()
                                                    if k != "case_id"}}).encode(),
            "nan": valid.replace("1.0", "NaN").encode(),
            "bool_as_int": valid.replace('"history_available": true', '"history_available": 1').encode(),
            "array_root": b"[]",
            "empty": b"",
            "not_utf8": b"\xff\xfe",
        }
        for name, payload in payloads.items():
            with self.subTest(name):
                response = self.client.post("/v1/runs", content=payload,
                                            headers={**auth, "Content-Type": "application/json"})
                self.assert_error(response, 400, "INVALID_INPUT")
        self.assertEqual(self.api.app.state.service.repository.list_runs(), ())

    def test_media_type_and_size_limits(self):
        auth = {**self.api.auth(), "Idempotency-Key": "key-0000000000000010"}
        self.assert_error(self.client.post("/v1/runs", content=json.dumps(case_body()),
                                           headers={**auth, "Content-Type": "text/plain"}),
                          415, "UNSUPPORTED_MEDIA_TYPE")
        big = json.dumps(case_body(case_id="case-a")).encode() + b" " * 1_048_577
        self.assert_error(self.client.post("/v1/runs", content=big,
                                           headers={**auth, "Content-Type": "application/json"}),
                          413, "PAYLOAD_TOO_LARGE")

        def chunks():  # no Content-Length: the streamed body is capped while reading
            yield json.dumps(case_body()).encode()
            for _ in range(20):
                yield b" " * 65_536
        self.assert_error(self.client.post("/v1/runs", content=chunks(),
                                           headers={**auth, "Content-Type": "application/json"}),
                          413, "PAYLOAD_TOO_LARGE")
        self.assert_error(self.client.post("/v1/runs", content=b"{}",
                                           headers={**auth, "Content-Type": "application/json",
                                                    "Content-Length": "-1"}), 413, "PAYLOAD_TOO_LARGE")

    def test_unknown_route_method_and_host(self):
        self.assert_error(self.client.get("/v1/unknown", headers=self.api.auth()), 404, "NOT_FOUND")
        self.assert_error(self.client.delete("/v1/runs/x", headers=self.api.auth()), 405, "METHOD_NOT_ALLOWED")
        self.assert_error(self.client.get("/v1/health", headers={"Host": "evil.example"}), 400, "INVALID_INPUT")

    def test_security_headers_and_request_id(self):
        for response in (self.client.get("/v1/health"), self.client.get("/v1/runs/x"), self.api.create()):
            for name, value in SECURITY_HEADERS.items():
                self.assertEqual(response.headers.get(name), value, name)
        echoed = self.client.get("/v1/health", headers={"X-Request-ID": "client-req-12345678"})
        self.assertEqual(echoed.headers["x-request-id"], "client-req-12345678")
        replaced = self.client.get("/v1/health", headers={"X-Request-ID": "bad id\r\nX-Injected: 1"})
        self.assertNotIn("Injected", replaced.headers["x-request-id"])
        self.assertRegex(replaced.headers["x-request-id"], r"^[0-9a-f]{32}$")

    def test_docs_disabled_in_test_enabled_in_development(self):
        self.assertEqual(self.client.get("/docs").status_code, 404)
        self.assertEqual(self.client.get("/openapi.json").status_code, 404)
        dev = Api(environment="development")
        self.assertEqual(dev.client.get("/openapi.json").status_code, 200)
        self.assertEqual(dev.client.get("/docs").status_code, 200)

    def test_published_openapi_matches_application(self):
        published = json.loads(Path(export_openapi.TARGET).read_text(encoding="utf-8"))
        self.assertEqual(published, json.loads(json.dumps(export_openapi.build(), sort_keys=True)))
        self.assertEqual(published["info"]["version"], "medrixa.http.v1")
        for path, operations in published["paths"].items():
            for operation in operations.values():
                if path != "/v1/health":
                    self.assertEqual(operation["security"], [{"bearerAuth": []}], path)
                self.assertNotIn("HTTPValidationError", json.dumps(operation))

    def test_framework_validation_errors_use_envelope_without_echo(self):
        # Guard for future typed parameters: FastAPI's default 422 body echoes the input.
        @self.api.app.get("/v1/_probe/{number}")
        async def probe(number: int):
            return {"number": number}
        self.assert_error(self.client.get(f"/v1/_probe/{MARKER}"), 400, "INVALID_INPUT")

    def test_health_needs_no_credentials_and_reveals_nothing(self):
        response = self.client.get("/v1/health")
        self.assertEqual(response.json(), {"api_version": "medrixa.http.v1", "status": "ok"})


if __name__ == "__main__":
    unittest.main()
