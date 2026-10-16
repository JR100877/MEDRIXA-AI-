"""P9 option C (EXPERIMENTAL): execute each run in a worker thread with its own event loop.

The HTTP event loop only awaits a future; all synchronous storage I/O (psycopg,
file locks) then blocks the worker thread instead of the shared loop.

Semantics that DIFFER from inline execution (P8) and are measured/tested in
tests/experimental/:
- Agents run in a different event loop from the caller. Agents must not use
  loop-bound objects created elsewhere (asyncio.Event, Queue, Future, locks).
  MEDRIXA's production agents (HistoryAgent, ClinicalAgent) do not.
- Cancelling the awaiting HTTP request does NOT cancel the run: the thread runs
  to completion, its result is persisted and an idempotent retry replays it.
- Threads cannot be killed: shutdown waits for in-flight runs (bounded only by
  the cooperative agent timeouts).
- contextvars (request_id) are copied into the worker.
"""
import asyncio
import contextvars
import threading
from collections.abc import Awaitable, Callable
from concurrent.futures import ThreadPoolExecutor
from typing import TypeVar

T = TypeVar("T")


class ThreadLoopExecution:
    def __init__(self, max_workers: int):
        if type(max_workers) is not int or max_workers < 1:
            raise ValueError("max_workers must be a positive integer")
        self.max_workers = max_workers
        self._pool = ThreadPoolExecutor(max_workers=max_workers, thread_name_prefix="medrixa-run")
        self._lock = threading.Lock()
        self.in_flight = 0       # runs executing in worker threads right now
        self.peak_in_flight = 0

    def _run_in_thread(self, context: contextvars.Context, work: Callable[[], Awaitable[T]]) -> T:
        with self._lock:
            self.in_flight += 1
            self.peak_in_flight = max(self.peak_in_flight, self.in_flight)
        try:
            # The coroutine is created inside the worker so every loop-bound object
            # it makes belongs to this thread's loop.
            return context.run(lambda: asyncio.run(work()))  # type: ignore[arg-type]
        finally:
            with self._lock:
                self.in_flight -= 1

    async def __call__(self, work: Callable[[], Awaitable[T]]) -> T:
        loop = asyncio.get_running_loop()
        context = contextvars.copy_context()
        return await loop.run_in_executor(self._pool, self._run_in_thread, context, work)

    def shutdown(self, wait: bool = True) -> None:
        self._pool.shutdown(wait=wait)
