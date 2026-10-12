"""Offline contract tests for the screenshot/action ReAct loop.

No model credentials, network or visible desktop are necessary.
"""
import base64
import io
import os
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import MagicMock, patch
import unittest

from PIL import Image

from qbrowser.native import (
    CompatibleModel, Desktop, ModelConfig, compact, run_native, screen_message,
)
from qbrowser.store import JobStore


class FakeDesktop:
    def __init__(self):
        self.actions = []
        image = Image.new("RGB", (2, 2), "white")
        buffer = io.BytesIO()
        image.save(buffer, format="PNG")
        self.image = "data:image/png;base64," + base64.b64encode(buffer.getvalue()).decode("ascii")

    def observe(self):
        return self.image, (1280, 720)

    def perform(self, args):
        self.actions.append(args)
        return "Action performed"


class FakeModel:
    def __init__(self, *calls):
        self.calls = list(calls)
        if not self.calls or self.calls[0][0] != "assess_task":
            self.calls.insert(0, ("assess_task", {
                "feasibility": "ready", "complexity": "low",
                "requirements": ["Complete the requested task"],
                "success_criteria": ["The requested outcome is verified"],
                "ambiguities": [], "assumptions": [],
                "estimated_steps": 1, "estimated_minutes": 1,
            }))
        if (any(name == "computer_action" and args.get("action") not in {"screenshot", "wait"}
                for name, args in self.calls) and
                not any(name == "create_or_revise_plan" for name, _args in self.calls)):
            self.calls[1:1] = [
                ("create_or_revise_plan", {"items": [{
                    "key": "planned-action", "title": "Perform the requested action",
                    "acceptance": "The requested outcome is verified",
                }]}),
                ("update_plan_item", {"key": "planned-action", "state": "in_progress"}),
            ]
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
            ("create_or_revise_plan", {"items": [{"key": "navigate", "title": "Open the editor",
                "acceptance": "The editor is visible and ready for use"}]}),
            ("update_plan_item", {"key": "navigate", "state": "in_progress"}),
            ("record_progress", {"key": "navigate", "state": "in_progress"}),
            ("computer_action", {"action": "click", "x": 5, "y": 6, "impact":"local", "purpose":"Open local editor"}),
            ("update_plan_item", {"key": "navigate", "state": "done",
                                   "evidence": "The editor window is visibly open"}),
            ("record_progress", {"key": "navigate", "state": "done"}),
            ("finish_task", {"result": "Opened app", "evidence": "Visible on-screen"}),
        )
        desktop = FakeDesktop()
        out = run_native(job, self.store, model=model, desktop=desktop, config=self.config)
        self.assertEqual(out["status"], "verify")
        self.assertEqual(desktop.actions[0]["action"], "click")
        self.assertEqual(self.store.checkpoints(job["id"])[0]["state"], "in_progress")
        self.assertIn("Opened app", out["result"])
        self.assertGreaterEqual(len(self.store.events(job["id"])), 8)
        self.assertEqual(self.store.plan(job["id"])["percent"], 100)
        self.assertEqual(self.store.action_history(job["id"])[0]["state"], "completed")
        # Screenshots appear only in user turns; models receive the latest screenshot.
        self.assertTrue(any(isinstance(x["content"], list)
                            for x in model.histories[0] if x["role"] == "user"))

    def test_100_actions_keep_plan_memory_and_journal(self):
        job = self.claim("Complete a long sequence of local tasks")
        calls = [
            ("create_or_revise_plan", {"items": [{"key": "sequence", "title": "Complete sequence",
                "acceptance": "All 100 visible actions are recorded"}]}),
            ("update_plan_item", {"key": "sequence", "state": "in_progress"}),
        ]
        for index in range(100):
            if index and index % 8 == 0:
                calls.append(("update_working_memory", {
                    "summary": f"Completed {index} GUI actions; continue with action {index + 1}.",
                    "findings": [f"Action {index} was completed"],
                    "decisions": ["Continue the remaining sequence"], "open_questions": []}))
            if index and index % 12 == 0:
                calls.append(("update_plan_item", {"key": "sequence", "state": "in_progress"}))
            calls.append(("computer_action", {"action": "click", "x": index, "y": index,
                "impact": "local", "purpose": f"Perform planned action {index + 1}"}))
        calls.extend([
            ("update_plan_item", {"key": "sequence", "state": "done",
                                   "evidence": "The full 100-action sequence is recorded"}),
            ("finish_task", {"result": "Sequence finished", "evidence": "All actions completed"}),
        ])
        model = FakeModel(*calls)
        config = ModelConfig("zai", "glm-5.3-flash", "fake-key", "https://example.org/v4",
                             200, 120, 1280)
        result = run_native(job, self.store, model=model, desktop=FakeDesktop(), config=config)
        self.assertEqual(result["status"], "verify")
        self.assertEqual(result["action_count"], 100)
        self.assertEqual(self.store.plan(job["id"])["percent"], 100)
        self.assertEqual(len(self.store.action_history(job["id"])), 100)
        self.assertIn("96 GUI actions", self.store.working_memory(job["id"])["summary"])
        self.assertLessEqual(len(model.histories[-1]), 20)

    def test_unapproved_external_click_never_reaches_desktop(self):
        job = self.claim()
        model = FakeModel(("computer_action", {"action":"click", "x":10, "y":10,
            "impact":"external", "purpose":"Send document to website"}))
        desktop = FakeDesktop()
        state = run_native(job, self.store, model=model, desktop=desktop, config=self.config)
        self.assertEqual(state["status"], "waiting_approval")
        self.assertEqual(desktop.actions, [])
        self.assertIn("Send document", state["pending_approval"])
        resumed = self.store.approve_external(job["id"])
        self.assertEqual(resumed["status"], "queued")
        self.assertFalse(resumed["external_approved"])
        self.assertTrue(resumed["approved_action_approval"])

    def test_missing_impact_fails_closed(self):
        job = self.claim()
        model = FakeModel(("computer_action", {"action":"click", "x":5, "y":6}))
        desktop = FakeDesktop()
        state = run_native(job, self.store, model=model, desktop=desktop, config=self.config)
        self.assertEqual(state["status"], "needs_review")
        self.assertFalse(desktop.actions)

    def test_destructive_action_requires_manual_handling_even_when_approved(self):
        self.store.create("Delete remote files", external_approved=True)
        job = self.store.claim()
        desktop = FakeDesktop()
        model = FakeModel(("computer_action", {"action": "click", "x":5, "y":6,
            "impact":"destructive","purpose":"Remove remote folder"}))
        result = run_native(job, self.store, model=model, desktop=desktop, config=self.config)
        self.assertEqual(result["status"], "needs_review")
        self.assertEqual(desktop.actions, [])

    def test_token_limit_stops_before_action(self):
        job = self.claim()
        model = FakeModel(("computer_action", {"action": "screenshot"}))
        original = model.ask
        def overbudget(history):
            turn = original(history)
            turn["usage"] = {"input":250000, "output":1}
            return turn
        model.ask = overbudget
        desktop = FakeDesktop()
        result = run_native(job, self.store, model=model, desktop=desktop, config=self.config)
        self.assertEqual(result["status"], "needs_review")
        self.assertEqual(desktop.actions, [])
        self.assertEqual(result["input_tokens"], 250000)

    def test_persisted_wait_request(self):
        job = self.claim()
        model = FakeModel(("wait_for_external", {"seconds":60,
            "reason":"Report is being prepared"}))
        result = run_native(job, self.store, model=model,
            desktop=FakeDesktop(), config=self.config)
        self.assertEqual(result["status"], "waiting")
        self.assertIsNotNone(result["wake_at"])

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

    def test_cost_rates_must_be_paired_and_are_optional(self):
        base = {"QBROWSER_PROVIDER": "custom", "QBROWSER_MODEL": "vision",
                "QBROWSER_BASE_URL": "http://127.0.0.1:8080/v1",
                "QBROWSER_MODEL_API_KEY": "local"}
        with patch.dict(os.environ, base | {
                "QBROWSER_INPUT_COST_PER_MILLION_USD": "2.5",
                "QBROWSER_OUTPUT_COST_PER_MILLION_USD": "7.0"}, clear=True):
            config = ModelConfig.from_env()
            self.assertEqual(config.input_cost_per_million_usd, 2.5)
            self.assertEqual(config.output_cost_per_million_usd, 7.0)
        with patch.dict(os.environ, base | {"QBROWSER_INPUT_COST_PER_MILLION_USD": "2.5"}, clear=True):
            with self.assertRaisesRegex(ValueError, "both token cost rates"):
                ModelConfig.from_env()

    def test_compatible_model_handles_tool_calls_and_deepseek_reasoning(self):
        from types import SimpleNamespace

        call = SimpleNamespace(
            id="call_abc",
            function=SimpleNamespace(name="computer_action",
                                     arguments='{"action":"screenshot"}'),
        )
        msg = SimpleNamespace(content=None, tool_calls=[call],
                              reasoning_content="unexposed reasoning")
        sdk = MagicMock()
        sdk.chat.completions.create.return_value = SimpleNamespace(
            choices=[SimpleNamespace(message=msg)])
        adapter = object.__new__(CompatibleModel)
        adapter.config = ModelConfig("deepseek", "deepseek-flash", "placeholder",
                                     "https://api.deepseek.com", 10, 120, 1280)
        adapter.client = sdk
        history = [{"role": "system", "content": "rules"}, screen_message(
            "data:image/jpeg;base64,AA==", (1280, 720))]
        turn = adapter.ask(history)
        self.assertEqual(turn["calls"][0]["name"], "computer_action")
        self.assertIn("reasoning_content", turn["assistant"])
        sent = sdk.chat.completions.create.call_args.kwargs
        self.assertEqual(sent["messages"][1]["content"][1]["type"], "image_url")
        self.assertIn("computer_action", [tool["function"]["name"] for tool in sent["tools"]])
        self.assertTrue(turn["usage"]["estimated"])
        self.assertGreater(turn["usage"]["input"], 0)

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
