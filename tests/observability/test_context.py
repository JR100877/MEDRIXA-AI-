"""P8.3: request-id correlation of operational logs (also across the authentication worker thread)."""
import io
import json
import logging
import unittest

from fastapi.testclient import TestClient

from app.observability.context import _OperationalHandler, configure_operational_logging, request_id_var
from app.web.app import create_app
from app.web.audit import MemoryAuditSink
from app.web.settings import Settings
from tests.security.oidc_support import JwksServer, SigningKey, oidc_settings


class OperationalLoggingTests(unittest.TestCase):
    def setUp(self):
        self.stream = io.StringIO()
        self.logger = configure_operational_logging(self.stream)
        self.addCleanup(self._remove_handler)

    def _remove_handler(self):
        for handler in list(self.logger.handlers):
            if isinstance(handler, _OperationalHandler):
                self.logger.removeHandler(handler)

    def lines(self):
        return [json.loads(line) for line in self.stream.getvalue().splitlines()]

    def test_json_lines_with_and_without_request_context(self):
        logging.getLogger("medrixa.test").warning("outside any request")
        token = request_id_var.set("req-ctx-00000001")
        try:
            logging.getLogger("medrixa.test").warning("inside a request")
        finally:
            request_id_var.reset(token)
        first, second = self.lines()
        self.assertEqual((first["request_id"], second["request_id"]), ("-", "req-ctx-00000001"))
        self.assertEqual(set(second), {"at", "level", "logger", "message", "request_id"})
        configure_operational_logging(self.stream)  # idempotent: no duplicated handler
        logging.getLogger("medrixa.test").warning("once")
        self.assertEqual(len(self.lines()), 3)

    def test_audit_logger_is_not_mixed_in(self):
        from app.web.audit import configure_audit_logging
        audit_stream = io.StringIO()
        audit = configure_audit_logging(audit_stream)
        self.addCleanup(lambda: [audit.removeHandler(h) for h in list(audit.handlers)
                                 if type(h).__name__ == "_AuditHandler"])
        audit.info('{"action":"x"}')
        self.assertEqual(self.lines(), [])

    def test_oidc_warning_carries_the_request_id_from_the_worker_thread(self):
        server = JwksServer([SigningKey("RSA")])
        self.addCleanup(server.close)
        server.mode = "error"
        settings = Settings(environment="test", auth_mode="oidc", allowed_hosts=("testserver",),
                            oidc=oidc_settings(server.url))
        client = TestClient(create_app(settings, audit=MemoryAuditSink()))
        token = SigningKey("RSA").sign()
        response = client.get("/v1/runs/x", headers={"Authorization": f"Bearer {token}",
                                                     "X-Request-ID": "corr-0000000000001"})
        self.assertEqual(response.status_code, 401)
        warnings = [line for line in self.lines() if line["logger"] == "medrixa.oidc"]
        self.assertEqual([w["request_id"] for w in warnings], ["corr-0000000000001"])
        self.assertNotIn(token, self.stream.getvalue())
        self.assertNotIn(server.url, self.stream.getvalue())
        self.assertIsNone(request_id_var.get())  # reset after the request


if __name__ == "__main__":
    unittest.main()
