"""Artifact registry tests with output paths, symlink defenses and hashing."""

import os
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch
import unittest

from qbrowser.artifacts import workspace, index_outputs, artifacts
from qbrowser.store import JobStore


class ArtifactTests(unittest.TestCase):
    def setUp(self):
        self.temp = TemporaryDirectory()
        self.patched = patch.dict(os.environ, {"QBROWSER_HOME": self.temp.name})
        self.patched.start()
        self.store = JobStore(Path(self.temp.name) / "jobs.db")

    def tearDown(self):
        self.patched.stop()
        self.temp.cleanup()

    def test_discovered_files_have_reproducible_hashes(self):
        job = self.store.create("Write a document")
        root = workspace(job["id"])
        (root / "draft.docx").write_bytes(b"demo")
        items = index_outputs(self.store, job["id"])
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0]["name"], "draft.docx")
        self.assertEqual(items[0]["bytes"], 4)
        self.assertEqual(artifacts(self.store, job["id"])[0]["sha256"], items[0]["sha256"])
        self.assertEqual(index_outputs(self.store, job["id"])[0]["sha256"],
                         items[0]["sha256"])

    def test_job_id_path_validation(self):
        with self.assertRaises(ValueError):
            workspace("../")
        with self.assertRaises(KeyError):
            index_outputs(self.store, "aaaaaaaaaaaa")

    def test_symlinks_are_not_indexed(self):
        job = self.store.create("Write a file")
        root = workspace(job["id"])
        outside = Path(self.temp.name) / "private.txt"
        outside.write_text("secret")
        try:
            (root / "linked").symlink_to(outside)
        except (OSError, NotImplementedError):
            self.skipTest("Symlink unavailable on this platform")
        self.assertEqual(index_outputs(self.store, job["id"]), [])


if __name__ == "__main__":
    unittest.main()
