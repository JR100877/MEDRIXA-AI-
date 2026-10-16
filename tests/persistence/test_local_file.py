"""Durability, corruption and cross-process behaviour of the PROVISIONAL file adapter."""
import hashlib
import json
import multiprocessing
import os
import stat
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from app.persistence import local_file
from app.persistence.local_file import ProvisionalLocalFileRepository, _canonical, _key
from app.persistence.manager import PersistentStateManager
from app.persistence.repository import CorruptRecord, RevisionConflict
from tests.helpers import case
from tests.persistence.support import state_chain


def _contend(root, payload, barrier, results):
    """Process worker: one CAS attempt at revision 0 -> 1."""
    from app.core.state import State
    repo = ProvisionalLocalFileRepository(root)
    candidate = State.model_validate_json(payload)
    barrier.wait()
    try:
        repo.save(candidate, 0)
        results.put("ok")
    except RevisionConflict:
        results.put("conflict")


class LocalFileTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)
        self.repo = ProvisionalLocalFileRepository(self.root)
        self.chain, _ = state_chain()
        self.repo.create(self.chain[0])
        self.repo.save(self.chain[1], 0)
        self.run_id = self.chain[0].run_id
        self.path = self.root / f"{_key(self.run_id)}.json"

    def envelope(self):
        return json.loads(self.path.read_bytes())

    def write_raw(self, data: bytes):
        self.path.write_bytes(data)
        return data

    def assert_corrupt_and_untouched(self, raw):
        with self.assertRaises(CorruptRecord):
            ProvisionalLocalFileRepository(self.root).load(self.run_id)
        with self.assertRaises(CorruptRecord):
            self.repo.save(self.chain[2], 1)
        self.assertEqual(self.path.read_bytes(), raw)
        [entry] = self.repo.scan()
        self.assertFalse(entry.readable)
        self.assertEqual(self.repo.list_runs(), ())

    def test_new_process_handle_reads_identical_state(self):
        self.assertEqual(ProvisionalLocalFileRepository(self.root).load(self.run_id), self.chain[1])
        envelope = self.envelope()
        self.assertEqual((envelope["format"], envelope["adapter"]),
                         ("medrixa.local-run-record.v1", "provisional-local-file"))

    def test_truncated_record(self):
        self.assert_corrupt_and_untouched(self.write_raw(self.path.read_bytes()[:-40]))

    def test_checksum_mismatch(self):
        envelope = self.envelope()
        envelope["state"]["case"]["history_available"] = False
        self.assert_corrupt_and_untouched(self.write_raw(_canonical(envelope)))

    def test_valid_checksum_but_inconsistent_state(self):
        envelope = self.envelope()
        envelope["state"]["revision"] = 5  # revision no longer matches the event log
        envelope["state_sha256"] = hashlib.sha256(_canonical(envelope["state"])).hexdigest()
        self.assert_corrupt_and_untouched(self.write_raw(_canonical(envelope)))

    def test_envelope_disagrees_with_state(self):
        envelope = self.envelope()
        envelope["status"] = "awaiting_human_review"
        self.assert_corrupt_and_untouched(self.write_raw(_canonical(envelope)))

    def test_unknown_format_extra_keys_and_bad_encoding(self):
        variants = []
        envelope = self.envelope()
        variants.append(_canonical({**envelope, "format": "medrixa.local-run-record.v0"}))
        variants.append(_canonical({**envelope, "unexpected": 1}))
        variants.append(b"\xff\xfe not utf-8")
        variants.append(b"[]")
        for raw in variants:
            with self.subTest(raw=raw[:30]):
                self.assert_corrupt_and_untouched(self.write_raw(raw))

    def test_record_copied_under_another_run_rejected(self):
        other, _ = state_chain("synthetic-case-2")
        self.repo.create(other[0])
        other_path = self.root / f"{_key(other[0].run_id)}.json"
        other_path.write_bytes(self.path.read_bytes())  # swap content between runs
        with self.assertRaises(CorruptRecord):
            self.repo.load(other[0].run_id)
        self.assertEqual(self.repo.load(self.run_id), self.chain[1])

    def test_oversized_record(self):
        raw = self.write_raw(b" " * (local_file.MAX_RECORD_BYTES + 1))
        self.assert_corrupt_and_untouched(raw)

    def test_failed_atomic_replace_keeps_previous_record(self):
        before = self.path.read_bytes()
        with mock.patch.object(local_file.os, "replace", side_effect=OSError("simulated crash")):
            with self.assertRaises(OSError):
                self.repo.save(self.chain[2], 1)
        self.assertEqual(self.path.read_bytes(), before)
        self.assertEqual(self.repo.load(self.run_id), self.chain[1])
        self.assertEqual([p.name for p in self.root.iterdir() if p.name.endswith(".tmp")], [])

    def test_leftover_temp_and_foreign_files_ignored(self):
        (self.root / f".{_key(self.run_id)}.deadbeef.tmp").write_bytes(b"{partial")
        (self.root / "notes.json").write_text("{}")
        self.assertEqual(self.repo.list_runs(), (self.run_id,))
        self.assertEqual(self.repo.load(self.run_id), self.chain[1])

    @unittest.skipUnless(os.name == "posix", "POSIX permission bits")
    def test_record_permissions_owner_only(self):
        mode = stat.S_IMODE(self.path.stat().st_mode)
        self.assertEqual(mode & 0o077, 0)

    def test_persistent_manager_rejects_non_repository(self):
        with self.assertRaises(TypeError):
            PersistentStateManager(case(), object())

    def test_multiprocess_writers_single_winner(self):
        chain, _ = state_chain("synthetic-case-mp")
        self.repo.create(chain[0])
        ctx = multiprocessing.get_context("spawn")
        workers = 4
        barrier, results = ctx.Barrier(workers), ctx.Queue()
        processes = [ctx.Process(target=_contend, args=(str(self.root), chain[1].model_dump_json(), barrier, results))
                     for _ in range(workers)]
        for process in processes:
            process.start()
        for process in processes:
            process.join(60)
        outcomes = sorted(results.get(timeout=5) for _ in range(workers))
        self.assertEqual(outcomes, ["conflict"] * (workers - 1) + ["ok"])
        self.assertEqual(self.repo.load(chain[0].run_id), chain[1])


if __name__ == "__main__":
    unittest.main()
