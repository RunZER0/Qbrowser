"""Operational behavior tests."""
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from qbrowser.store import JobStore
from qbrowser.backup import backup_database, backup_full, restore_full
from unittest.mock import patch
import zipfile

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

    def test_full_backup_restores_database_and_private_files(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp) / "source"
            root.mkdir()
            db = JobStore(root / "qbrowser.db")
            job = db.create("Write a report")
            (root / "workspaces" / job["id"]).mkdir(parents=True)
            (root / "workspaces" / job["id"] / "report.txt").write_text("private output")
            (root / "vault" / job["id"]).mkdir(parents=True)
            (root / "vault" / job["id"] / "snapshot").write_bytes(b"private input")
            (root / ".env").write_text("ZAI_API_KEY=must-not-be-backed-up")
            target = Path(tmp) / "backup.zip"
            with patch("qbrowser.backup.home", return_value=root):
                backup_full(db, target)
                restored = restore_full(target, Path(tmp) / "restored")
            self.assertEqual(JobStore(restored / "qbrowser.db").get(job["id"])["instruction"],
                             "Write a report")
            self.assertEqual((restored / "workspaces" / job["id"] / "report.txt").read_text(),
                             "private output")
            self.assertEqual((restored / "vault" / job["id"] / "snapshot").read_bytes(),
                             b"private input")
            self.assertFalse((restored / ".env").exists())
            with self.assertRaises(FileExistsError):
                backup_full(db, target)

    def test_full_restore_rejects_tampered_content(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp) / "source"
            root.mkdir()
            db = JobStore(root / "qbrowser.db")
            target = Path(tmp) / "backup.zip"
            with patch("qbrowser.backup.home", return_value=root):
                backup_full(db, target)
            tampered = Path(tmp) / "tampered.zip"
            with zipfile.ZipFile(target) as original, zipfile.ZipFile(tampered, "w") as altered:
                for name in original.namelist():
                    data = original.read(name)
                    if name == "qbrowser.db":
                        data = bytes([data[0] ^ 1]) + data[1:]
                    altered.writestr(name, data)
            with self.assertRaisesRegex(ValueError, "hash mismatch"):
                restore_full(tampered, Path(tmp) / "bad-restore")

if __name__ == "__main__":
    unittest.main()
