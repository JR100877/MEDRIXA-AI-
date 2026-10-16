import json

from app.models.contracts import ClinicalCase

MAX_INPUT_BYTES = 1_048_576


class InputRejected(ValueError):
    """Safe public error; never include caller payload in its message."""


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise InputRejected("INVALID_INPUT")
        result[key] = value
    return result


def _reject_constant(_value):
    raise InputRejected("INVALID_INPUT")


def parse_case(payload: bytes) -> ClinicalCase:
    """Strict JSON boundary, bounded size, duplicate keys and extras rejected."""
    if type(payload) is not bytes or not payload or len(payload) > MAX_INPUT_BYTES:
        raise InputRejected("INVALID_INPUT")
    try:
        decoded = payload.decode("utf-8")
        # JSON validation below allows JSON arrays to represent immutable tuples.
        # This first pass rejects duplicate keys and nonstandard numeric constants.
        json.loads(decoded, object_pairs_hook=_unique_object, parse_constant=_reject_constant)
        return ClinicalCase.model_validate_json(decoded)
    except (ValueError, TypeError, RecursionError):
        raise InputRejected("INVALID_INPUT") from None
