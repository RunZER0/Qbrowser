from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch
import unittest

from qbrowser.sandbox import sandbox_command


class SandboxCommandTests(unittest.TestCase):
    def test_sandbox_is_off_by_default(self):
        command = ["agent_s", "--task", "Inspect the page"]
        self.assertEqual(command, sandbox_command(command, "/unused", platform="linux"))

    def test_bwrap_is_explicit_and_mounts_only_the_task_workspace_writable(self):
        with TemporaryDirectory() as tmp, patch.dict("os.environ", {"QBROWSER_HOME": tmp}):
            task_dir = Path(tmp) / "workspaces" / "job-1"
            task_dir.mkdir(parents=True)
            command = sandbox_command(["agent_s", "--task", "safe"], task_dir,
                                     mode="bwrap", platform="linux", bwrap_path="/usr/bin/bwrap")
        self.assertEqual("/usr/bin/bwrap", command[0])
        self.assertIn("--unshare-all", command)
        self.assertIn("--share-net", command)
        self.assertIn("--ro-bind", command)
        mount = command.index(str(task_dir.resolve()))
        self.assertEqual("--bind", command[mount - 1])
        self.assertEqual(str(task_dir.resolve()), command[mount + 1])
        self.assertEqual(["agent_s", "--task", "safe"], command[-3:])

    def test_requested_sandbox_fails_closed_when_unavailable_or_unsupported(self):
        with TemporaryDirectory() as tmp, patch.dict("os.environ", {"QBROWSER_HOME": tmp}):
            task_dir = Path(tmp) / "workspaces" / "job-1"
            task_dir.mkdir(parents=True)
            with self.assertRaisesRegex(RuntimeError, "only on Linux"):
                sandbox_command(["agent_s"], task_dir, mode="bwrap", platform="win32",
                                bwrap_path="/usr/bin/bwrap")
            with patch("qbrowser.sandbox.shutil.which", return_value=None):
                with self.assertRaisesRegex(RuntimeError, "unavailable"):
                    sandbox_command(["agent_s"], task_dir, mode="bwrap", platform="linux")
            with self.assertRaisesRegex(ValueError, "within QBROWSER_HOME"):
                outside = Path(tmp) / "outside"
                outside.mkdir()
                sandbox_command(["agent_s"], outside, mode="bwrap", platform="linux",
                                bwrap_path="/usr/bin/bwrap")


if __name__ == "__main__":
    unittest.main()
