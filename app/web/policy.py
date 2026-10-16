"""Authorization policy: explicit roles, least privilege, default deny, case scope.

| Role          | Permissions                | Case scope |
|---------------|----------------------------|------------|
| case_runner   | runs:create, runs:read     | own cases  |
| case_reviewer | runs:read                  | own cases  |
| operator      | runs:recover, metrics:read | none       |

No role can approve a clinical decision: no such permission exists.
"""
from typing import Literal

Role = Literal["case_runner", "case_reviewer", "operator"]
Permission = Literal["runs:create", "runs:read", "runs:recover", "metrics:read"]

ROLE_PERMISSIONS: dict[str, frozenset[str]] = {
    "case_runner": frozenset({"runs:create", "runs:read"}),
    "case_reviewer": frozenset({"runs:read"}),
    "operator": frozenset({"runs:recover", "metrics:read"}),  # P8: aggregate metrics, no case data
}
CASE_SCOPED: frozenset[str] = frozenset({"runs:create", "runs:read"})


class Forbidden(Exception):
    """Authenticated, but the permission is not granted."""


class OutOfScope(Exception):
    """Permission granted, but not for this case. Reads map it to 404 so that
    the existence of other cases' runs is not disclosed."""


def permissions_of(principal) -> frozenset[str]:
    granted: set[str] = set()
    for role in principal.roles:
        granted |= ROLE_PERMISSIONS.get(role, frozenset())  # unknown role grants nothing
    return frozenset(granted)


def require(principal, permission: str, case_id: str | None = None) -> None:
    """Default deny. Case-scoped permissions additionally need the case in scope."""
    if permission not in permissions_of(principal):
        raise Forbidden()
    if permission in CASE_SCOPED:
        if case_id is None or case_id not in principal.cases:
            raise OutOfScope()
