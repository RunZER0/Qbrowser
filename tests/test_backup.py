"""Operational behavior tests."""
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from qbrowser.store import JobStore
from qbrowser.backup import backup_database

class TestBackup(unittest.TestCase):
    def test_backup_preserves_jobs(self):
        with TemporaryDirectory() as tmp:
            db = JobStore(Path(tmp) / "jobs.db")
            job = db.create("Write a report")
            target = Path(tmp) / "backup.db"
            backup_database(db, target)
            self.assertEqual(JobStore(target).get(job["id"])["instruction"], "Write a report")
            with self.assertRaises(FileExistsError):
                backup_database(db, target)

if __name__ == "__main__":
    unittest.main()
