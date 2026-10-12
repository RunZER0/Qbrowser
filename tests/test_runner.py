"""Tests for GUI command safety and subprocess integration without Agent-S installed."""

from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch
import os
import unittest

from qbrowser.runner import agent_command, execute
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
        self.assertNotIn("private", str(cmd))
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


if __name__ == "__main__":
    unittest.main()
