"""P8.3: request correlation for operational logs.

The HTTP middleware stores the request id in a context variable; a logging
filter copies it onto every record of the ``medrixa`` logger tree, so warnings
from OIDC key refresh, leasing or metrics can be traced to the request that
caused them. ``configure_operational_logging`` emits JSON lines (stderr by
default) with constant messages only — no payloads, tokens or identifiers
other than the server-validated request id. The audit logger
(``medrixa.audit``) keeps its own handler and does not propagate here.
"""
import json
import logging
from contextvars import ContextVar
from datetime import datetime, timezone

request_id_var: ContextVar[str | None] = ContextVar("medrixa_request_id", default=None)


class RequestContextFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        record.request_id = request_id_var.get() or "-"
        return True


class JsonLineFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        return json.dumps({"at": datetime.fromtimestamp(record.created, timezone.utc).isoformat(),
                           "level": record.levelname, "logger": record.name, "message": record.getMessage(),
                           "request_id": getattr(record, "request_id", "-")},
                          separators=(",", ":"), sort_keys=True)


class _OperationalHandler(logging.StreamHandler):
    """Marker type so repeated configuration never duplicates lines."""


def configure_operational_logging(stream=None, level: int = logging.INFO) -> logging.Logger:
    logger = logging.getLogger("medrixa")
    if not any(isinstance(h, _OperationalHandler) for h in logger.handlers):
        handler = _OperationalHandler(stream)
        handler.setFormatter(JsonLineFormatter())
        handler.addFilter(RequestContextFilter())
        logger.addHandler(handler)
    logger.setLevel(level)
    logger.propagate = False
    return logger
