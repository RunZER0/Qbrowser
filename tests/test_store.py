"""Job durability and state transitions, without launching a GUI."""

from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from qbrowser.store import JobStore


class StoreTests(unittest.TestCase):
    def setUp(self):
        self.temp = TemporaryDirectory()
        self.store = JobStore(Path(self.temp.name) / "jobs.db")

    def tearDown(self):
        self.temp.cleanup()

    def test_create_claim_verify_complete(self):
        job = self.store.create("Open a browser and draft a note")
        self.assertEqual(job["status"], "queued")
        selected = self.store.claim()
        self.assertEqual(selected["id"], job["id"])
        self.assertEqual(selected["attempt"], 1)
        self.assertEqual(self.store.claim(), None)
        self.store.transition(job["id"], "verify", allowed={"running"},
                              result="GUI process exit 0")
        done = self.store.transition(job["id"], "completed", allowed={"verify"})
        self.assertEqual(done["status"], "completed")
        self.assertEqual([e["type"] for e in self.store.events(job["id"])],
                         ["created", "started", "status", "status"])

    def test_recovery_requires_explicit_requeue(self):
        job = self.store.create("Create draft")
        self.store.claim()
        self.assertEqual(self.store.recover_interrupted(), 1)
        self.assertEqual(self.store.claim(), None)
        self.assertEqual(self.store.get(job["id"])["status"], "needs_review")
        self.store.transition(job["id"], "queued", allowed={"needs_review"})
        retry = self.store.claim()
        self.assertEqual(retry["attempt"], 2)
        self.assertEqual(self.store.recover_interrupted(), 1)

    def test_concurrent_claims_only_once(self):
        ids = [self.store.create(f"Job {i}")["id"] for i in range(8)]
        with ThreadPoolExecutor(max_workers=8) as pool:
            claimed = list(pool.map(lambda _: self.store.claim(), range(16)))
        actual = [j["id"] for j in claimed if j]
        self.assertCountEqual(actual, ids)
        self.assertEqual(len(actual), len(set(actual)))

    def test_no_invalid_transitions(self):
        job = self.store.create("Take screenshot")
        with self.assertRaises(ValueError):
            self.store.transition(job["id"], "completed", allowed={"verify"})
        with self.assertRaises(ValueError):
            self.store.create(" ")
        self.assertEqual(self.store.get(job["id"])["status"], "queued")

    def test_approval_cannot_change_while_running(self):
        job = self.store.create("Post something")
        self.assertFalse(job["external_approved"])
        self.store.claim()
        with self.assertRaises(ValueError):
            self.store.approve_external(job["id"])
        self.store.recover_interrupted()
        self.assertTrue(self.store.approve_external(job["id"])["external_approved"])

    def test_dynamic_checkpoints_survive_restart(self):
        job = self.store.create("Any unfamiliar task")
        self.store.checkpoint(job["id"], "research", "in_progress", "Searching sources")
        self.store.checkpoint(job["id"], "research", "done", "Five verified sources")
        self.store.checkpoint(job["id"], "draft", "blocked", "Needs clarification")
        reopened = JobStore(self.store.path)
        states = {x["key"]: x["state"] for x in reopened.checkpoints(job["id"])}
        self.assertEqual(states, {"research": "done", "draft": "blocked"})
        self.assertIn("checkpoint", [e["type"] for e in reopened.events(job["id"])])
        with self.assertRaises(ValueError):
            self.store.checkpoint(job["id"], "review", "invalid")
        with self.assertRaises(ValueError):
            self.store.checkpoint(job["id"], "../outside", "done")

    def test_persists_between_instances(self):
        job = self.store.create("Make a document")
        reopened = JobStore(self.store.path)
        self.assertEqual(reopened.get(job["id"])["instruction"], "Make a document")


if __name__ == "__main__":
    unittest.main()
