"""Versioned HTTP contracts (medrixa.http.v1). Responses never echo observations."""
from datetime import datetime
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.core.state import State
from app.models.contracts import ClinicalCase, Identifier, StopCode

ApiVersion = Literal["medrixa.http.v1"]
RunStatus = Literal["created", "running", "awaiting_human_review"]
ErrorCode = Literal[
    "INVALID_INPUT", "UNAUTHENTICATED", "FORBIDDEN", "NOT_FOUND", "METHOD_NOT_ALLOWED",
    "PAYLOAD_TOO_LARGE", "UNSUPPORTED_MEDIA_TYPE", "IDEMPOTENCY_KEY_REQUIRED", "IDEMPOTENCY_KEY_REUSED",
    "IDEMPOTENCY_IN_PROGRESS", "CONFLICT", "RUNS_ACTIVE", "STORE_UNAVAILABLE", "STORE_INTEGRITY_ERROR",
    "AUDIT_UNAVAILABLE", "INTERNAL_ERROR",
]


class HttpContract(BaseModel):
    """Same strictness as domain contracts; versioned by ``api_version``."""
    model_config = ConfigDict(strict=True, extra="forbid", frozen=True, revalidate_instances="always")


class CreateRunRequest(HttpContract):
    """POST /v1/runs body. ``case.case_id`` is mandatory over HTTP (no server default),
    so a retried request is byte-for-byte comparable for idempotency."""
    case: ClinicalCase

    @model_validator(mode="after")
    def explicit_case_id(self) -> "CreateRunRequest":
        # An "after" check keeps strict JSON-mode parsing (a "before" hook would
        # switch nested validation to Python mode, where JSON arrays are rejected).
        if "case_id" not in self.case.model_fields_set:
            raise ValueError("case.case_id is required")
        return self


class RunView(HttpContract):
    api_version: ApiVersion = "medrixa.http.v1"
    run_id: Identifier
    case_id: Identifier
    status: RunStatus
    stop_reason: StopCode | None
    revision: Annotated[int, Field(ge=0)]
    dispatched: Annotated[int, Field(ge=0)]
    completed: Annotated[int, Field(ge=0)]
    abstained: Annotated[int, Field(ge=0)]
    failed: Annotated[int, Field(ge=0)]
    human_review_required: Literal[True] = True

    @classmethod
    def of(cls, state: State) -> "RunView":
        state = State.model_validate(state)
        return cls(run_id=state.run_id, case_id=state.case.case_id, status=state.status,
                   stop_reason=state.stop_reason, revision=state.revision, dispatched=len(state.messages),
                   completed=sum(r.status == "completed" for r in state.results),
                   abstained=sum(r.status == "abstained" for r in state.results),
                   failed=sum(r.status == "failed" for r in state.results))


class RunEventView(HttpContract):
    sequence: Annotated[int, Field(ge=1)]
    kind: Literal["RUN_STARTED", "AGENT_DISPATCHED", "RESULT_ACCEPTED", "REVIEW_REQUESTED"]
    at: datetime
    reason: StopCode | None = None


class RunEventsView(HttpContract):
    api_version: ApiVersion = "medrixa.http.v1"
    run_id: Identifier
    events: tuple[RunEventView, ...]

    @classmethod
    def of(cls, state: State) -> "RunEventsView":
        return cls(run_id=state.run_id, events=tuple(
            RunEventView(sequence=e.sequence, kind=e.kind, at=e.at, reason=e.reason) for e in state.events))


class RecoveryView(HttpContract):
    api_version: ApiVersion = "medrixa.http.v1"
    moved_to_review: Annotated[int, Field(ge=0)]
    not_started: Annotated[int, Field(ge=0)]
    already_terminal: Annotated[int, Field(ge=0)]
    corrupt_records: Annotated[int, Field(ge=0)]
    agents_reexecuted: Literal[0] = 0


class HealthView(HttpContract):
    api_version: ApiVersion = "medrixa.http.v1"
    status: Literal["ok"] = "ok"


class ErrorBody(HttpContract):
    code: ErrorCode
    message: Annotated[str, Field(max_length=200)]
    request_id: Identifier


class ErrorResponse(HttpContract):
    api_version: ApiVersion = "medrixa.http.v1"
    error: ErrorBody
