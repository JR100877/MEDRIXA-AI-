"""Explicit error catalogue: one fixed status and public message per code.

Public messages are constant strings: no input, exception text, path, stack
trace or identifier other than the server request id is ever returned.
"""
from app.web.contracts import ErrorBody, ErrorResponse

CATALOGUE: dict[str, tuple[int, str]] = {
    "INVALID_INPUT": (400, "The request is invalid."),
    "IDEMPOTENCY_KEY_REQUIRED": (400, "A valid Idempotency-Key header is required."),
    "UNAUTHENTICATED": (401, "Authentication is required."),
    "FORBIDDEN": (403, "The operation is not permitted."),
    "NOT_FOUND": (404, "The resource was not found."),
    "METHOD_NOT_ALLOWED": (405, "Method not allowed."),
    "CONFLICT": (409, "The resource changed concurrently; reload before retrying."),
    "IDEMPOTENCY_IN_PROGRESS": (409, "A request with this Idempotency-Key is still in progress."),
    "RUNS_ACTIVE": (409, "Runs are executing in this process; recovery is refused."),
    "PAYLOAD_TOO_LARGE": (413, "The request body is too large."),
    "UNSUPPORTED_MEDIA_TYPE": (415, "Content-Type must be application/json."),
    "IDEMPOTENCY_KEY_REUSED": (422, "This Idempotency-Key was used with a different request."),
    "INTERNAL_ERROR": (500, "Internal error."),
    "STORE_INTEGRITY_ERROR": (500, "Stored data failed an integrity check."),
    "AUDIT_UNAVAILABLE": (503, "Audit is unavailable; the operation was not performed."),
    "STORE_UNAVAILABLE": (503, "Storage is unavailable."),
}


class ApiError(Exception):
    def __init__(self, code: str):
        if code not in CATALOGUE:
            code = "INTERNAL_ERROR"
        super().__init__(code)
        self.code = code
        self.status, self.message = CATALOGUE[code]


def error_payload(code: str, request_id: str) -> tuple[int, dict]:
    status, message = CATALOGUE[code]
    body = ErrorResponse(error=ErrorBody(code=code, message=message, request_id=request_id))
    return status, body.model_dump(mode="json")
