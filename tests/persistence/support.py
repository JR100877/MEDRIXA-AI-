"""Shared helpers for P4 persistence tests (synthetic data only)."""
import json

from app.core.state import State
from app.core.state_manager import StateManager
from tests.helpers import case, message, result


def state_chain(case_id="synthetic-case-1"):
    """Successive valid snapshots of one run: created, started, dispatched, accepted."""
    manager = StateManager(case(case_id))
    chain = [manager.snapshot(), manager.start(0)]
    request = message(chain[-1])
    chain.append(manager.dispatch(request, chain[-1].revision))
    chain.append(manager.accept(result(request), chain[-1].revision))
    return chain, request


def rebuild(state, mutate):
    """Return a revalidated State after applying ``mutate`` to its JSON form."""
    data = state.model_dump(mode="json")
    mutate(data)
    return State.model_validate_json(json.dumps(data))
