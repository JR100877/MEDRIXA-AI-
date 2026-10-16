"""Safe recovery of interrupted runs, including a real process kill and restart."""
import json
import os
import signal
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path

from app.core.state_manager import InvalidTransition
from app.persistence.local_file import ProvisionalLocalFileRepository, _key
from app.persistence.manager import PersistentStateManager
from app.persistence.memory import InMemoryStateRepository
from app.persistence.recovery import RecoveryReport, recover_interrupted_runs
from tests.helpers import case, message, result

PROJECT_ROOT = Path(__file__).resolve().parents[2]


def seed(repo):
    """One run in every lifecycle position. Returns {label: State before recovery}."""
    runs = {}
    runs["created"] = PersistentStateManager(case("syn-created"), repo).snapshot()
    m = PersistentStateManager(case("syn-started"), repo)
    runs["started"] = m.start(0)
    m = PersistentStateManager(case("syn-pending"), repo)
    started = m.start(0)
    runs["pending"] = m.dispatch(message(started), started.revision)
    m = PersistentStateManager(case("syn-accepted"), repo)
    started = m.start(0)
    request = message(started)
    dispatched = m.dispatch(request, started.revision)
    runs["accepted"] = m.accept(result(request), dispatched.revision)
    m = PersistentStateManager(case("syn-terminal"), repo)
    runs["terminal"] = m.request_review("REVIEW_REQUIRED", m.start(0).revision)
    return runs


class RecoveryPolicyTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)
        self.repo = ProvisionalLocalFileRepository(self.root)
        self.runs = seed(self.repo)

    def test_running_runs_move_to_review_with_history_preserved(self):
        report = recover_interrupted_runs(self.repo)
        interrupted = {self.runs[k].run_id for k in ("started", "pending", "accepted")}
        self.assertEqual(set(report.moved_to_review), interrupted)
        self.assertEqual(report.not_started, (self.runs["created"].run_id,))
        self.assertEqual((report.already_terminal, report.corrupt_records, report.agents_reexecuted), (1, 0, 0))
        for label in ("started", "pending", "accepted"):
            before, after = self.runs[label], self.repo.load(self.runs[label].run_id)
            with self.subTest(label=label):
                self.assertEqual((after.status, after.stop_reason, after.pending_message),
                                 ("awaiting_human_review", "INTERRUPTED", None))
                self.assertEqual(after.events[:-1], before.events)
                self.assertEqual((after.events[-1].kind, after.events[-1].reason), ("REVIEW_REQUESTED", "INTERRUPTED"))
                self.assertEqual((after.messages, after.results), (before.messages, before.results))

    def test_created_and_terminal_runs_untouched(self):
        before = {k: self.repo.load(self.runs[k].run_id) for k in ("created", "terminal")}
        recover_interrupted_runs(self.repo)
        for label, state in before.items():
            with self.subTest(label=label):
                self.assertEqual(self.repo.load(state.run_id), state)

    def test_in_flight_result_cannot_be_accepted_after_recovery(self):
        pending = self.runs["pending"]
        recover_interrupted_runs(self.repo)
        resumed = PersistentStateManager.resume(self.repo, pending.run_id)
        late = result(pending.pending_message)
        with self.assertRaises(InvalidTransition):
            resumed.accept(late, resumed.snapshot().revision)
        self.assertEqual(self.repo.load(pending.run_id).results, ())

    def test_recovery_is_idempotent(self):
        recover_interrupted_runs(self.repo)
        files = {p.name: p.read_bytes() for p in self.root.glob("*.json")}
        second = recover_interrupted_runs(self.repo)
        self.assertEqual((second.moved_to_review, second.already_terminal), ((), 4))
        self.assertEqual({p.name: p.read_bytes() for p in self.root.glob("*.json")}, files)

    def test_corrupt_record_reported_never_modified(self):
        path = self.root / f"{_key(self.runs['pending'].run_id)}.json"
        raw = path.read_bytes()[:-10]
        path.write_bytes(raw)
        report = recover_interrupted_runs(self.repo)
        self.assertEqual(report.corrupt_records, 1)
        self.assertNotIn(self.runs["pending"].run_id, report.moved_to_review)
        self.assertEqual(path.read_bytes(), raw)

    def test_concurrent_recoverers_apply_one_transition_per_run(self):
        reports, barrier = [], threading.Barrier(4)

        def recover():
            handle = ProvisionalLocalFileRepository(self.root)
            barrier.wait()
            reports.append(recover_interrupted_runs(handle))
        threads = [threading.Thread(target=recover) for _ in range(4)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(30)
        moved = [run_id for report in reports for run_id in report.moved_to_review]
        self.assertEqual(len(reports), 4)
        self.assertEqual(sorted(moved), sorted(self.runs[k].run_id for k in ("started", "pending", "accepted")))
        for label in ("started", "pending", "accepted"):
            after = self.repo.load(self.runs[label].run_id)
            self.assertEqual(len(after.events), len(self.runs[label].events) + 1)

    def test_in_memory_adapter_same_policy(self):
        repo = InMemoryStateRepository()
        runs = seed(repo)
        report = recover_interrupted_runs(repo)
        self.assertEqual(len(report.moved_to_review), 3)
        self.assertEqual(repo.load(runs["pending"].run_id).stop_reason, "INTERRUPTED")

    def test_report_cannot_claim_reexecution(self):
        with self.assertRaises(ValueError):
            RecoveryReport(agents_reexecuted=1)


@unittest.skipUnless(hasattr(signal, "SIGKILL"), "requires POSIX SIGKILL")
class ProcessRestartTests(unittest.TestCase):
    """Acceptance criterion of docs/NEXT_PACKAGE.md, with real processes."""

    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.store = Path(directory.name) / "runs"

    def python(self, *args, **kwargs):
        return subprocess.run([sys.executable, *args], cwd=PROJECT_ROOT, capture_output=True,
                              text=True, timeout=120, **kwargs)

    def test_complete_and_killed_runs_survive_restart(self):
        done = self.python("-m", "tests.persistence.crash_worker", str(self.store), "complete", "syn-complete-001")
        self.assertEqual(done.returncode, 0, done.stderr)
        complete_id = done.stdout.split()[0]

        signal_file = self.store.parent / "in-flight.signal"
        worker = subprocess.Popen([sys.executable, "-m", "tests.persistence.crash_worker", str(self.store),
                                   "hang", "syn-killed-001", str(signal_file)],
                                  cwd=PROJECT_ROOT, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        try:
            killed_id = worker.stdout.readline().strip()
            deadline = time.monotonic() + 60
            while not signal_file.exists():
                self.assertIsNone(worker.poll(), "worker exited before its agent was in flight")
                self.assertLess(time.monotonic(), deadline, "agent never started")
                time.sleep(0.05)
            os.kill(worker.pid, signal.SIGKILL)
        finally:
            worker.wait(30)
            worker.stdout.close()
            worker.stderr.close()
        self.assertEqual(worker.returncode, -signal.SIGKILL)

        repo = ProvisionalLocalFileRepository(self.store)
        complete_before = repo.load(complete_id)
        killed_before = repo.load(killed_id)
        self.assertEqual((complete_before.status, complete_before.stop_reason, complete_before.revision),
                         ("awaiting_human_review", "REVIEW_REQUIRED", 6))
        self.assertEqual((killed_before.status, killed_before.revision), ("running", 2))
        self.assertIsNotNone(killed_before.pending_message)

        # Restart: recovery runs in a brand-new process through the CLI.
        recovered = self.python("-m", "app", "--store", str(self.store), "--recover")
        self.assertEqual(recovered.returncode, 0, recovered.stderr)
        self.assertEqual(json.loads(recovered.stdout),
                         {"schema_version": "medrixa.prototype.v1", "moved_to_review": 1, "not_started": 0,
                          "already_terminal": 1, "corrupt_records": 0, "agents_reexecuted": 0})
        self.assertNotIn(killed_id, recovered.stdout + recovered.stderr)

        repo = ProvisionalLocalFileRepository(self.store)
        killed_after = repo.load(killed_id)
        self.assertEqual(repo.load(complete_id), complete_before)
        self.assertEqual([e.kind for e in killed_after.events], ["RUN_STARTED", "AGENT_DISPATCHED", "REVIEW_REQUESTED"])
        self.assertEqual((killed_after.stop_reason, killed_after.results), ("INTERRUPTED", ()))
        self.assertEqual(killed_after.events[:2], killed_before.events)

        again = self.python("-m", "app", "--store", str(self.store), "--recover")
        self.assertEqual(json.loads(again.stdout)["moved_to_review"], 0)
        self.assertEqual(json.loads(again.stdout)["already_terminal"], 2)


class PersistenceCliTests(unittest.TestCase):
    def python(self, *args):
        return subprocess.run([sys.executable, "-m", "app", *args], cwd=PROJECT_ROOT,
                              capture_output=True, text=True, timeout=60)

    def test_cli_persists_run_and_rejects_invalid_combinations(self):
        with tempfile.TemporaryDirectory() as directory:
            store = Path(directory) / "runs"
            ran = self.python("--store", str(store), "--case", "data/synthetic_cases/complete.json")
            self.assertEqual(ran.returncode, 0, ran.stderr)
            self.assertEqual(json.loads(ran.stdout)["events"], 6)
            [run_id] = ProvisionalLocalFileRepository(store).list_runs()
            self.assertNotIn(run_id, ran.stdout)
            for args in (("--recover",), ("--recover", "--store", str(store), "--case", "x.json")):
                with self.subTest(args=args):
                    rejected = self.python(*args)
                    self.assertEqual(rejected.returncode, 2)
                    self.assertEqual(rejected.stderr.strip(), '{"error":"INVALID_INPUT"}')


if __name__ == "__main__":
    unittest.main()
