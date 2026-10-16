"""StateRepository contract, executed against every provisional adapter."""
import asyncio
import tempfile
import threading
import unittest

from app.agents.clinical.agent import ClinicalAgent
from app.agents.history.agent import HistoryAgent
from app.core.orchestrator import Orchestrator
from app.core.planner import Planner
from app.core.router import Router
from app.core.state import State
from app.core.state_manager import InvalidTransition, StateManager
from app.persistence.local_file import ProvisionalLocalFileRepository
from app.persistence.manager import PersistentStateManager
from app.persistence.memory import InMemoryStateRepository
from app.persistence.repository import (DuplicateRun, IntegrityViolation, RevisionConflict, RunNotFound,
                                        StateRepository)
from tests.helpers import case, message, result
from tests.persistence.support import rebuild, state_chain


def runner(history=None):
    return Orchestrator(Planner(), Router({"history_analysis": history or HistoryAgent(),
                                           "clinical_analysis": ClinicalAgent()}))


class RepositoryContract:
    """Mixin: subclasses provide make_repo() and peer() (a second handle to the same store)."""

    def make_repo(self) -> StateRepository:
        raise NotImplementedError

    def peer(self, repo):
        raise NotImplementedError

    def stored(self, repo, chain, upto):
        repo.create(chain[0])
        for previous, current in zip(chain[:upto], chain[1:upto + 1]):
            repo.save(current, previous.revision)
        return chain[upto]

    # -- create / load ---------------------------------------------------------
    def test_create_and_load_round_trip(self):
        repo = self.make_repo()
        chain, _ = state_chain()
        repo.create(chain[0])
        self.assertEqual(self.peer(repo).load(chain[0].run_id), chain[0])

    def test_duplicate_run_rejected_original_kept(self):
        repo = self.make_repo()
        chain, _ = state_chain()
        self.stored(repo, chain, 1)
        fresh = State(case=chain[0].case, run_id=chain[0].run_id)
        with self.assertRaises(DuplicateRun):
            repo.create(fresh)
        self.assertEqual(repo.load(chain[0].run_id), chain[1])

    def test_create_requires_new_run(self):
        repo = self.make_repo()
        chain, _ = state_chain()
        with self.assertRaises(IntegrityViolation):
            repo.create(chain[1])
        with self.assertRaises(RunNotFound):
            repo.load(chain[1].run_id)

    def test_missing_and_invalid_run_ids(self):
        repo = self.make_repo()
        for run_id in ("absent-run", "../escape", "", "a/b", None):
            with self.subTest(run_id=run_id), self.assertRaises(RunNotFound):
                repo.load(run_id)
        chain, _ = state_chain()
        with self.assertRaises(RunNotFound):
            repo.save(chain[1], 0)

    # -- revision control ------------------------------------------------------
    def test_stale_revision_rejected_without_change(self):
        repo = self.make_repo()
        chain, _ = state_chain()
        self.stored(repo, chain, 2)
        for expected in (0, 1, 3, True, "2"):
            with self.subTest(expected=expected), self.assertRaises(RevisionConflict):
                repo.save(chain[3], expected)
        self.assertEqual(repo.load(chain[0].run_id), chain[2])

    def test_exactly_one_transition_per_save(self):
        repo = self.make_repo()
        chain, _ = state_chain()
        self.stored(repo, chain, 1)
        with self.assertRaises(IntegrityViolation):
            repo.save(chain[3], 1)
        with self.assertRaises(IntegrityViolation):
            repo.save(chain[1], 1)
        self.assertEqual(repo.load(chain[0].run_id), chain[1])

    def test_history_rewrite_rejected(self):
        repo = self.make_repo()
        chain, _ = state_chain()
        self.stored(repo, chain, 1)

        def shift_start_time(data):
            data["events"][0]["at"] = "2000-01-01T00:00:00Z"
        tampered = rebuild(chain[2], shift_start_time)
        with self.assertRaises(IntegrityViolation):
            repo.save(tampered, 1)
        self.assertEqual(repo.load(chain[0].run_id), chain[1])

    def test_cross_case_write_rejected(self):
        repo = self.make_repo()
        chain, _ = state_chain("synthetic-case-A")
        self.stored(repo, chain, 1)
        other = StateManager(case("synthetic-case-B"))
        started = other.start(0)

        def adopt_run_id(data):
            data["run_id"] = chain[0].run_id
            for item in (*data["events"], *data["messages"], data["pending_message"]):
                item["run_id"] = chain[0].run_id
        foreign = rebuild(other.dispatch(message(started), 1), adopt_run_id)
        with self.assertRaises(IntegrityViolation):
            repo.save(foreign, 1)
        self.assertEqual(repo.load(chain[0].run_id).case.case_id, "synthetic-case-A")

    def test_terminal_run_is_immutable(self):
        repo = self.make_repo()
        manager = PersistentStateManager(case(), repo)
        state = manager.request_review("REVIEW_REQUIRED", manager.start(0).revision)
        self.assertEqual(state.status, "awaiting_human_review")
        with self.assertRaises(InvalidTransition):
            PersistentStateManager.resume(self.peer(repo), state.run_id).start(state.revision)
        self.assertEqual(repo.load(state.run_id), state)

    # -- concurrency and isolation ---------------------------------------------
    def test_concurrent_writers_single_winner(self):
        repo = self.make_repo()
        chain, _ = state_chain()
        repo.create(chain[0])
        writers = 8
        barrier = threading.Barrier(writers)
        outcomes = []

        def write():
            handle = self.peer(repo)
            barrier.wait()
            try:
                handle.save(chain[1], 0)
                outcomes.append("ok")
            except RevisionConflict:
                outcomes.append("conflict")
        threads = [threading.Thread(target=write) for _ in range(writers)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(10)
        self.assertEqual(sorted(outcomes), ["conflict"] * (writers - 1) + ["ok"])
        self.assertEqual(repo.load(chain[0].run_id), chain[1])

    def test_case_isolation_under_concurrent_runs(self):
        repo = self.make_repo()

        async def run_all():
            managers = [PersistentStateManager(case(f"synthetic-case-{i}"), repo) for i in range(6)]
            return await asyncio.gather(*(runner().run(m) for m in managers))
        finals = asyncio.run(run_all())
        reader = self.peer(repo)
        self.assertEqual(len(reader.list_runs()), 6)
        for final in finals:
            self.assertEqual(reader.list_runs(final.case.case_id), (final.run_id,))
            loaded = reader.load(final.run_id)
            self.assertEqual(loaded, final)
            self.assertTrue(all(x.case_id == final.case.case_id for x in (*loaded.messages, *loaded.results)))

    # -- persistent manager ----------------------------------------------------
    def test_every_transition_persisted_before_publish(self):
        repo = self.make_repo()
        seen = []

        class Watching(HistoryAgent):
            async def execute(inner, context):
                seen.append(repo.load(context.message.run_id))
                return await super().execute(context)
        final = asyncio.run(runner(Watching()).run(PersistentStateManager(case(), repo)))
        self.assertEqual(seen[0].revision, 2)
        self.assertEqual(seen[0].pending_message.agent_id, "history-simulator")
        self.assertEqual(self.peer(repo).load(final.run_id), final)
        self.assertEqual((final.status, final.stop_reason, final.revision),
                         ("awaiting_human_review", "REVIEW_REQUIRED", 6))

    def test_result_never_accepted_twice_across_resume(self):
        repo = self.make_repo()
        manager = PersistentStateManager(case(), repo)
        started = manager.start(0)
        request = message(started)
        dispatched = manager.dispatch(request, started.revision)
        first = result(request)
        accepted = manager.accept(first, dispatched.revision)
        resumed = PersistentStateManager.resume(self.peer(repo), accepted.run_id)
        for candidate in (first, result(request)):
            with self.subTest(result_id=candidate.result_id), self.assertRaises(InvalidTransition):
                resumed.accept(candidate, accepted.revision)
        self.assertEqual(len(repo.load(accepted.run_id).results), 1)

    def test_failed_write_keeps_memory_and_store(self):
        inner = self.make_repo()

        class FailingSaves(StateRepository):
            adapter_name = "test-failing"
            create, load, scan = inner.create, inner.load, inner.scan

            def save(self, state, expected_revision):
                raise OSError("simulated storage failure")
        manager = PersistentStateManager(case(), FailingSaves())
        with self.assertRaises(OSError):
            manager.start(0)
        self.assertEqual(manager.snapshot().status, "created")
        self.assertEqual(inner.load(manager.run_id).revision, 0)

    def test_foreign_writer_conflict_is_not_overwritten(self):
        repo = self.make_repo()
        peer = self.peer(repo)

        class InterferingAgent(HistoryAgent):
            async def execute(inner, context):
                other = PersistentStateManager.resume(peer, context.message.run_id)
                other.request_review("INTERRUPTED", other.snapshot().revision)
                return await super().execute(context)
        manager = PersistentStateManager(case(), repo)
        with self.assertRaises(RevisionConflict):
            asyncio.run(runner(InterferingAgent()).run(manager))
        stored = repo.load(manager.run_id)
        self.assertEqual((stored.status, stored.stop_reason, len(stored.results)),
                         ("awaiting_human_review", "INTERRUPTED", 0))
        self.assertEqual(manager.snapshot().revision, 2)


class InMemoryRepositoryContractTests(RepositoryContract, unittest.TestCase):
    def make_repo(self):
        return InMemoryStateRepository()

    def peer(self, repo):
        return repo  # a single process-local store


class LocalFileRepositoryContractTests(RepositoryContract, unittest.TestCase):
    def make_repo(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        return ProvisionalLocalFileRepository(directory.name)

    def peer(self, repo):
        return ProvisionalLocalFileRepository(repo.root)  # independent handle, own file locks


if __name__ == "__main__":
    unittest.main()
