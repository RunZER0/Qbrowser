"""Owner-confirmed delivery tests without network calls."""
import os
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest.mock import patch
import unittest
from qbrowser.artifacts import index_outputs, workspace
from qbrowser.delivery import deliver_verified
from qbrowser.store import JobStore

class TestDelivery(unittest.TestCase):
    def test_verified_once_and_not_retried(self):
        with TemporaryDirectory() as directory:
            with patch.dict(os.environ, {"QBROWSER_HOME": directory,
                                          "QBROWSER_OWNER_WHATSAPP": "+254700000000"}):
                store = JobStore(Path(directory) / "db.sqlite")
                job = store.create("Produce report")
                store.claim()
                store.transition(job["id"], "verify", allowed={"running"})
                store.transition(job["id"], "completed", allowed={"verify"})
                (workspace(job["id"]) / "report.docx").write_bytes(b"test")
                index_outputs(store, job["id"])
                with patch("qbrowser.delivery.shutil.which", return_value="openclaw"):
                    with patch("qbrowser.delivery.subprocess.run",
                               return_value=SimpleNamespace(returncode=0)) as sender:
                        self.assertEqual(len(deliver_verified(store, job["id"],
                                                              confirm=True)["sent"]), 1)
                        self.assertEqual(sender.call_count, 1)
                        with self.assertRaises(ValueError):
                            deliver_verified(store, job["id"], confirm=True)

if __name__ == "__main__":
    unittest.main()
