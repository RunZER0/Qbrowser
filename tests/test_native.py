"""Offline contract tests for the screenshot/action ReAct loop.

No model credentials, network or visible desktop are necessary.
"""
import os
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import MagicMock, patch
import unittest

from qbrowser.native import (
    Desktop, ModelConfig, compact, run_native, screen_message,
)
from qbrowser.store import JobStore


class FakeDesktop:
    def __init__(self):
        self.actions = []

    def observe(self):
        return "data:image/jpeg;base64,ZHVtbXk=", (1280, 720)

    def perform(self, args):
        self.actions.append(args)
        return "Action performed"


class FakeModel:
    def __init__(self, *calls):
        self.calls = list(calls)
        self.histories = []

    def ask(self, history):
        self.histories.append(history)
        if not self.calls:
            raise AssertionError("Model unexpectedly called again")
        name, args = self.calls.pop(0)
        return {
            "text": "", "calls": [{"id": "call-" + str(len(self.histories)),
                                   "name": name, "args": args}],
            "assistant": {"role": "assistant", "content": None,
                          "tool_calls": [{"id": "call-" + str(len(self.histories)),
                            "type": "function", "function": {
                                "name": name, "arguments": "{}"}}]},
        }


class NativeTests(unittest.TestCase):
    def setUp(self):
        self.tmp = TemporaryDirectory()
        self.store = JobStore(Path(self.tmp.name) / "state.db")
        self.config = ModelConfig("zai", "glm-5.3-flash", "fake-key",
                                  "https://example.org/v4", 12, 120, 1280)

    def tearDown(self):
        self.tmp.cleanup()

    def claim(self, instruction="Open an app"):
        self.store.create(instruction)
        return self.store.claim()

    def test_action_progress_then_finish(self):
        job = self.claim()
        model = FakeModel(
            ("record_progress", {"key": "navigate", "state": "in_progress"}),
            ("computer_action", {"action": "click", "x": 5, "y": 6}),
            ("record_progress", {"key": "navigate", "state": "done"}),
            ("finish_task", {"result": "Opened app", "evidence": "Visible on-screen"}),
        )
        desktop = FakeDesktop()
        out = run_native(job, self.store, model=model, desktop=desktop, config=self.config)
        self.assertEqual(out["status"], "verify")
        self.assertEqual(desktop.actions[0]["action"], "click")
        self.assertEqual(self.store.checkpoints(job["id"])[0]["state"], "in_progress")
        self.assertIn("Opened app", out["result"])
        self.assertGreaterEqual(len(self.store.events(job["id"])), 4)
        # Screenshots appear only in user turns; models receive the latest screenshot.
        self.assertTrue(any(isinstance(x["content"], list)
                            for x in model.histories[0] if x["role"] == "user"))

    def test_request_assistance_is_blocked(self):
        job = self.claim()
        model = FakeModel(("request_assistance", {"reason": "Login required"}))
        out = run_native(job, self.store, model=model,
                         desktop=FakeDesktop(), config=self.config)
        self.assertEqual(out["status"], "needs_review")
        self.assertIn("Login", out["last_error"])
        self.assertIsNone(self.store.claim())

    def test_bounded_steps_require_review(self):
        job = self.claim()
        model = FakeModel(*[("computer_action", {"action": "screenshot"}) for _ in range(2)])
        config = ModelConfig("deepseek", "deepseek-flash", "fake", "https://example.org", 2, 120, 1280)
        out = run_native(job, self.store, model=model,
                         desktop=FakeDesktop(), config=config)
        self.assertEqual(out["status"], "needs_review")
        self.assertIn("exhausted", out["last_error"])

    def test_desktop_rejects_coordinates_out_of_bounds(self):
        d = object.__new__(Desktop)
        d.last_actual = (1920, 1080)
        d.last_scaled = (1280, 720)
        self.assertEqual(d.position({"x": 640, "y": 360}), (960, 540))
        with self.assertRaises(ValueError):
            d.position({"x": 1280, "y": 0})
        with self.assertRaises(ValueError):
            d.position({"x": "0", "y": 0})

    def test_compact_preserves_initial_instructions(self):
        history = [{"role": "system", "content": "Rules"}, {"role": "user", "content": "Task"}]
        for n in range(12):
            history.append({"role": "assistant", "content": f"action {n}"})
            history.append({"role": "user", "content": f"image {n}"})
        recent = compact(history, 8)
        self.assertEqual(recent[0]["content"], "Rules")
        self.assertEqual(recent[1]["content"], "Task")
        self.assertLessEqual(len(recent), 11)

    def test_provider_defaults_and_missing_keys(self):
        with patch.dict(os.environ, {"QBROWSER_PROVIDER": "zai", "ZAI_API_KEY": "unit",
                                     "QBROWSER_MODEL": "", "QBROWSER_BASE_URL": ""}):
            config = ModelConfig.from_env()
            self.assertEqual(config.model, "glm-5.3-flash")
        with patch.dict(os.environ, {"QBROWSER_PROVIDER": "deepseek",
                                     "DEEPSEEK_API_KEY": "unit", "QBROWSER_MODEL": "",
                                     "QBROWSER_BASE_URL": ""}):
            config = ModelConfig.from_env()
            self.assertEqual(config.model, "deepseek-flash")
        with patch.dict(os.environ, {"QBROWSER_PROVIDER": "deepseek",
                                     "DEEPSEEK_API_KEY": "", "QBROWSER_MODEL": "",
                                     "QBROWSER_BASE_URL": ""}):
            with self.assertRaisesRegex(ValueError, "DEEPSEEK_API_KEY"):
                ModelConfig.from_env()

    def test_invalid_backend_and_non_https_remote_rejected(self):
        with patch.dict(os.environ, {"QBROWSER_PROVIDER": "custom",
                         "QBROWSER_MODEL": "vision", "QBROWSER_BASE_URL": "http://other.example",
                         "QBROWSER_MODEL_API_KEY": "dummy"}):
            with self.assertRaisesRegex(ValueError, "HTTPS"):
                ModelConfig.from_env()

    def test_screen_image_message_shape(self):
        msg = screen_message("data:image/jpeg;base64,AA==", (1280, 720))
        self.assertEqual(msg["content"][1]["type"], "image_url")
        self.assertIn("1280", msg["content"][0]["text"])


if __name__ == "__main__":
    unittest.main()
