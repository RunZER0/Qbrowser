"""Encrypted screenshot evidence tests without a real desktop."""

import base64
import os
from io import BytesIO
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase
from unittest.mock import patch

from PIL import Image

from qbrowser.evidence import new_evidence_key, prune_expired, read_screenshot, save_screenshot
from qbrowser.store import JobStore


class EvidenceTests(TestCase):
    def setUp(self):
        self.temp = TemporaryDirectory()
        self.env = patch.dict(os.environ, {
            "QBROWSER_HOME": self.temp.name,
            "QBROWSER_CAPTURE_EVIDENCE": "1",
            "QBROWSER_EVIDENCE_KEY": new_evidence_key(),
        })
        self.env.start()
        self.store = JobStore(Path(self.temp.name) / "jobs.db")
        self.job = self.store.create("Inspect the browser")

    def tearDown(self):
        self.env.stop()
        self.temp.cleanup()

    @staticmethod
    def screenshot_uri():
        image = Image.new("RGB", (96, 54), color=(20, 80, 160))
        buffer = BytesIO()
        image.save(buffer, format="JPEG")
        return "data:image/jpeg;base64," + base64.b64encode(buffer.getvalue()).decode("ascii"), buffer.getvalue()

    def test_screenshot_is_encrypted_and_round_trips(self):
        uri, raw = self.screenshot_uri()
        item = save_screenshot(self.store, self.job["id"], uri, "milestone")
        path = Path(self.temp.name) / item["relative_path"]
        self.assertNotEqual(path.read_bytes(), raw)
        self.assertEqual(path.stat().st_mode & 0o777, 0o600)
        self.assertEqual(read_screenshot(self.store, self.job["id"], item["id"]), raw)
        self.assertEqual(len(self.store.evidence(self.job["id"])), 1)

    def test_expired_evidence_is_removed_from_disk_and_index(self):
        uri, _ = self.screenshot_uri()
        item = save_screenshot(self.store, self.job["id"], uri, "final")
        path = Path(self.temp.name) / item["relative_path"]
        with self.store._connection() as db:
            db.execute("UPDATE evidence SET captured_at='2000-01-01T00:00:00+00:00' WHERE id=?",
                       (item["id"],))
        self.assertEqual(prune_expired(self.store, retention_days=1), 1)
        self.assertFalse(path.exists())
        self.assertEqual(self.store.evidence(self.job["id"]), [])

    def test_corrupt_evidence_is_rejected(self):
        uri, _ = self.screenshot_uri()
        item = save_screenshot(self.store, self.job["id"], uri, "error")
        path = Path(self.temp.name) / item["relative_path"]
        path.write_bytes(b"broken")
        with self.assertRaisesRegex(ValueError, "integrity"):
            read_screenshot(self.store, self.job["id"], item["id"])
