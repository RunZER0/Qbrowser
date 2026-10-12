"""Tests for GUI command safety and subprocess integration without Agent-S installed."""

from pathlib import Path
from io import BytesIO
from tempfile import TemporaryDirectory
from unittest.mock import patch
import os
import unittest

from qbrowser.runner import _copy_limited_output, agent_command, desktop_lock, execute, prune_logs
from qbrowser.store import JobStore


class RunnerTests(unittest.TestCase):
    def setUp(self):
        self.tmp = TemporaryDirectory()
        self.env = patch.dict(os.environ, {
            "QBROWSER_HOME": self.tmp.name,
            "AGENT_S_PATH": "/unused/agent_s",
            "AGENT_S_PROVIDER": "openai",
            "AGENT_S_MODEL": "unit-test-model",
            "OPENAI_API_KEY": "private",
            "AGENT_S_GROUND_URL": "http://localhost:8080",
            "AGENT_S_GROUND_MODEL": "ui-tars-1.5-7b",
            "AGENT_S_TIMEOUT_SECONDS": "30",
        })
        self.env.start()
        self.store = JobStore(Path(self.tmp.name) / "test.db")

    def tearDown(self):
        self.env.stop()
        self.tmp.cleanup()

    def test_command_is_not_a_shell_and_guards_external_actions(self):
        job = self.store.create("Open a website")
        cmd = agent_command(job)
        self.assertIsInstance(cmd, list)
        instruction = cmd[cmd.index("--task") + 1]
        self.assertIn("Do not make purchases", instruction)
        self.assertNotIn("private", cmd)
        self.assertEqual(cmd[0], "/unused/agent_s")

    def test_approval_changes_prompt(self):
        job = self.store.create("Upload my report", external_approved=True)
        cmd = agent_command(job)
        self.assertIn("approved external actions", cmd[cmd.index("--task") + 1])

    def test_missing_grounding_is_reported(self):
        with patch.dict(os.environ, {"AGENT_S_GROUND_URL": ""}):
            with self.assertRaisesRegex(RuntimeError, "GROUND_URL"):
                agent_command(self.store.create("Do task"))

    def test_missing_cli_moves_to_review_without_retry(self):
        job = self.store.create("Do task")
        claimed = self.store.claim()
        with patch.dict(os.environ, {"AGENT_S_PATH": "/path/does/not/exist"}):
            done = execute(claimed, self.store)
        self.assertEqual(done["status"], "needs_review")
        self.assertIsNone(self.store.claim())

    def test_child_output_is_capped_and_drained(self):
        log = BytesIO()
        _copy_limited_output(BytesIO(b"x" * 10000), log, 2048)
        self.assertLessEqual(len(log.getvalue()), 2048)
        self.assertIn(b"output omitted", log.getvalue())

    def test_log_retention_only_removes_old_regular_log_files(self):
        root = Path(self.tmp.name) / "logs"
        root.mkdir()
        old_log = root / "old.log"
        old_log.write_text("old", encoding="utf-8")
        fresh_log = root / "fresh.log"
        fresh_log.write_text("fresh", encoding="utf-8")
        unrelated = root / "old.txt"
        unrelated.write_text("keep", encoding="utf-8")
        import time
        old_stamp = time.time() - 2 * 86400
        os.utime(old_log, (old_stamp, old_stamp))
        os.utime(unrelated, (old_stamp, old_stamp))
        self.assertEqual(prune_logs(root, retention_days=1), 1)
        self.assertFalse(old_log.exists())
        self.assertTrue(fresh_log.exists())
        self.assertTrue(unrelated.exists())

    def test_isolated_desktop_identities_can_run_separate_workers(self):
        first = desktop_lock("display-one")
        second = desktop_lock("display-two")
        first.__enter__()
        try:
            second.__enter__()
            second.__exit__(None, None, None)
            with self.assertRaisesRegex(RuntimeError, "Another Qbrowser"):
                with desktop_lock("display-one"):
                    pass
        finally:
            first.__exit__(None, None, None)


if __name__ == "__main__":
    unittest.main()
