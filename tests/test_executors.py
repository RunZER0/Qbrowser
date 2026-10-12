"""Executor discovery is explicit and follows the stable execute(job, store) contract."""

import os
from types import SimpleNamespace
from unittest.mock import patch
import unittest

from qbrowser.executors import AgentSExecutor, NativeExecutor, executor_for


class Plugin:
    def execute(self, job, store):
        return {"status": "verify"}


class ExecutorTests(unittest.TestCase):
    def test_builtin_adapters_are_available(self):
        self.assertIsInstance(executor_for("native"), NativeExecutor)
        self.assertIsInstance(executor_for("agent_s"), AgentSExecutor)

    def test_external_plugins_require_both_enable_flag_and_allowlist(self):
        point = SimpleNamespace(name="sample", load=lambda: Plugin)
        with patch("qbrowser.executors.entry_points", return_value=[point]):
            with patch.dict(os.environ, {"QBROWSER_ENABLE_EXECUTOR_PLUGINS": "0",
                                         "QBROWSER_EXECUTOR_ALLOWLIST": "sample"}):
                with self.assertRaisesRegex(ValueError, "not installed and explicitly allowlisted"):
                    executor_for("sample")
            with patch.dict(os.environ, {"QBROWSER_ENABLE_EXECUTOR_PLUGINS": "1",
                                         "QBROWSER_EXECUTOR_ALLOWLIST": "sample"}):
                self.assertIsInstance(executor_for("sample"), Plugin)

    def test_plugin_contract_is_checked(self):
        point = SimpleNamespace(name="broken", load=lambda: object())
        with patch("qbrowser.executors.entry_points", return_value=[point]), patch.dict(
                os.environ, {"QBROWSER_ENABLE_EXECUTOR_PLUGINS": "1",
                             "QBROWSER_EXECUTOR_ALLOWLIST": "broken"}):
            with self.assertRaisesRegex(TypeError, "execute\(job, store\)"):
                executor_for("broken")


if __name__ == "__main__":
    unittest.main()
