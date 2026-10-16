"""P6: execute a run while holding a lease, with heartbeat and a write guard.

- The lease is acquired before the first transition; ``LeaseHeld`` means another
  executor is alive and nothing is executed.
- A heartbeat renews the lease every ``heartbeat_seconds`` (default ttl/3).
- A write guard refuses every transition once the LOCAL monotonic deadline
  (acquire/renew start + ttl - margin) has passed, so an executor whose
  renewals stall stops writing before its lease can expire elsewhere.
- If a renewal fails (lease lost or store unavailable), the run task is
  cancelled and ``LeaseLost`` is raised. Agents are never retried; the run is
  left for recovery and human review.
"""
import asyncio
import contextlib
import logging
import time

from app.core.state import State
from app.persistence.leases import LeaseLost, RunLeases, check_ttl, new_owner_id
from app.persistence.manager import PersistentStateManager

DEFAULT_TTL_SECONDS = 30.0
_log = logging.getLogger("medrixa.leasing")


class LeasedExecutor:
    def __init__(self, leases: RunLeases, owner: str | None = None, ttl_seconds: float = DEFAULT_TTL_SECONDS,
                 heartbeat_seconds: float | None = None):
        if not isinstance(leases, RunLeases):
            raise TypeError("leases must implement RunLeases")
        self.leases = leases
        self.owner = owner or new_owner_id()
        self.ttl = check_ttl(ttl_seconds)
        self.heartbeat = heartbeat_seconds if heartbeat_seconds is not None else self.ttl / 3
        if not 0 < self.heartbeat < self.ttl:
            raise ValueError("heartbeat must be shorter than the lease ttl")
        self.margin = min(self.ttl * 0.2, 5.0)

    async def run(self, runner, manager: PersistentStateManager) -> State:
        started = time.monotonic()
        lease = self.leases.acquire(manager.run_id, self.owner, self.ttl)  # LeaseHeld propagates
        deadline = started + self.ttl - self.margin

        def guard():
            if time.monotonic() >= deadline:
                raise LeaseLost("local lease deadline passed")
        manager.set_write_guard(guard)
        task = asyncio.ensure_future(runner.run(manager))
        try:
            while True:
                done, _ = await asyncio.wait({task}, timeout=self.heartbeat)
                if done:
                    return task.result()
                renew_started = time.monotonic()
                try:
                    lease = self.leases.renew(lease, self.ttl)
                    deadline = renew_started + self.ttl - self.margin
                except Exception:  # lease lost or store failure: liveness can no longer be proven
                    task.cancel()
                    # The cancelled run's own outcome (CancelledError, a conflict or a guard refusal)
                    # is superseded by LeaseLost; it is awaited only so nothing keeps running.
                    with contextlib.suppress(asyncio.CancelledError, Exception):
                        await task
                    raise LeaseLost("lease lost during execution") from None
        finally:
            if not task.done():
                task.cancel()
            manager.set_write_guard(None)
            try:
                self.leases.release(lease)
            except Exception:
                # Not fatal: an unreleased lease simply expires. Constant message, no details.
                _log.warning("lease release failed; it will expire at its TTL")
