"""Access audit for sensitive HTTP operations (who, what, outcome).

Separate from the run's structural events. Records opaque identifiers and
codes only: never tokens, request bodies, observations or exception text.
Sensitive mutations are audited BEFORE their side effects; if that write
fails the operation is refused (fail closed).
"""
import json
import logging
from abc import ABC, abstractmethod
from datetime import datetime, timezone
from threading import Lock
from typing import Literal

from app.models.contracts import Contract, Identifier

Action = Literal["runs.create", "runs.read", "runs.read_events", "runs.recover", "auth"]


class AccessAuditEvent(Contract):
    at: datetime
    request_id: Identifier
    action: Action
    phase: Literal["attempt", "outcome"]
    outcome: str
    subject: Identifier | None = None
    organization: Identifier | None = None  # P7.2
    case_id: Identifier | None = None
    run_id: Identifier | None = None
    status_code: int | None = None


class AuditSink(ABC):
    @abstractmethod
    def record(self, event: AccessAuditEvent) -> None: ...

    def ready(self) -> bool:
        """P8 readiness: would a record be accepted? Default: yes."""
        return True


class MemoryAuditSink(AuditSink):
    """Test sink."""

    def __init__(self):
        self._lock = Lock()
        self.events: list[AccessAuditEvent] = []

    def record(self, event: AccessAuditEvent) -> None:
        with self._lock:
            self.events.append(AccessAuditEvent.model_validate(event))


class LoggingAuditSink(AuditSink):
    """Structured JSON lines on the ``medrixa.audit`` logger (destination is the
    deployer's logging configuration; retention/shipping pending)."""

    def __init__(self, logger: logging.Logger | None = None):
        self._logger = logger or logging.getLogger("medrixa.audit")

    def ready(self) -> bool:
        return self._logger.isEnabledFor(logging.INFO) and self._logger.hasHandlers()

    def record(self, event: AccessAuditEvent) -> None:
        # Fail closed: an unconfigured logger would drop INFO records silently.
        if not (self._logger.isEnabledFor(logging.INFO) and self._logger.hasHandlers()):
            raise RuntimeError("audit logger is not configured")
        self._logger.info(json.dumps(event.model_dump(mode="json", exclude={"schema_version"}),
                                     separators=(",", ":"), sort_keys=True))


class _AuditHandler(logging.StreamHandler):
    """Marker type so repeated configuration never duplicates audit lines."""


def configure_audit_logging(stream=None) -> logging.Logger:
    """Dedicated JSON-lines handler (stderr by default); no propagation to root."""
    logger = logging.getLogger("medrixa.audit")
    if not any(isinstance(h, _AuditHandler) for h in logger.handlers):
        handler = _AuditHandler(stream)
        handler.setFormatter(logging.Formatter("%(message)s"))
        logger.addHandler(handler)
    logger.setLevel(logging.INFO)
    logger.propagate = False
    return logger


def audit_event(**fields) -> AccessAuditEvent:
    return AccessAuditEvent(at=datetime.now(timezone.utc), **fields)
