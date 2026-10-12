"""Independent outcome-review contracts without provider or desktop access."""

import json
import os
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest.mock import patch
import unittest

from qbrowser.store import JobStore
from qbrowser.verification import _configuration, verify_job


class FakeDesktop:
    def observe(self):
        return "data:image/jpeg;base64,/9j/2Q==", (1280, 720)


class FakeVerifier:
    def __init__(self, result):
        self.result = result
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=self.create))
        self.calls = []

    def create(self, **kwargs):
        self.calls.append(kwargs)
        message = SimpleNamespace(content=json.dumps(self.result))
        usage = SimpleNamespace(prompt_tokens=321, completion_tokens=45)
        return SimpleNamespace(choices=[SimpleNamespace(message=message)], usage=usage)


class VerificationTests(unittest.TestCase):
    def setUp(self):
        self.temp = TemporaryDirectory()
        self.env = patch.dict(os.environ, {
            "QBROWSER_HOME": self.temp.name,
            "QBROWSER_PROVIDER": "custom", "QBROWSER_MODEL": "executor-v1",
            "QBROWSER_VERIFIER_PROVIDER": "custom",
            "QBROWSER_VERIFIER_MODEL": "reviewer-v2",
            "QBROWSER_VERIFIER_BASE_URL": "http://127.0.0.1:9000/v1",
            "QBROWSER_VERIFIER_API_KEY": "test-key",
        })
        self.env.start()
        self.store = JobStore(Path(self.temp.name) / "qbrowser.db")
        self.job = self.store.create("Create a short report", provider="custom", model="executor-v1")
        self.store.claim()
        self.store.assess_task(self.job["id"], feasibility="ready", complexity="low",
            requirements=["Create a report"], success_criteria=["The report is visible on screen"],
            estimated_steps=4, estimated_minutes=2)
        plan = self.store.set_plan(self.job["id"], [{"key": "report", "title": "Create report",
            "acceptance": "The report is visible on screen"}])
        self.store.update_plan_item(self.job["id"], "report", "in_progress")
        self.store.update_plan_item(self.job["id"], "report", "done",
                                   "The report was visible in the output window")
        self.store.transition(self.job["id"], "verify", allowed={"running"}, result="Report created")

    def tearDown(self):
        self.env.stop()
        self.temp.cleanup()

    def test_review_is_separate_read_only_and_requires_operator_confirmation(self):
        client = FakeVerifier({"overall": "pass", "criteria": [{
            "criterion": "The report is visible on screen", "status": "pass",
            "evidence": "The current screenshot shows the report window."}],
            "issues": [], "summary": "The visible report meets the stated criterion."})
        review = verify_job(self.store, self.job["id"], client=client, desktop=FakeDesktop())
        self.assertEqual(review["outcome"], "pass")
        self.assertEqual(review["report"]["criteria_score_percent"], 100)
        self.assertTrue(review["report"]["operator_confirmation_required"])
        self.assertEqual(self.store.get(self.job["id"])["status"], "verify")
        self.assertEqual(review["input_tokens"], 321)
        self.assertFalse(client.calls[0].get("tools"))
        self.assertEqual(client.calls[0]["response_format"], {"type": "json_object"})

    def test_reviewer_must_use_a_different_model(self):
        with patch.dict(os.environ, {"QBROWSER_VERIFIER_MODEL": "executor-v1"}):
            with self.assertRaisesRegex(ValueError, "must differ"):
                verify_job(self.store, self.job["id"], client=FakeVerifier({}), desktop=FakeDesktop())

    def test_same_provider_reuses_execution_key_and_endpoint_defaults(self):
        with patch.dict(os.environ, {
            "QBROWSER_VERIFIER_PROVIDER": "", "QBROWSER_VERIFIER_MODEL": "reviewer-v2",
            "QBROWSER_VERIFIER_BASE_URL": "", "QBROWSER_VERIFIER_API_KEY": "",
            "QBROWSER_PROVIDER": "zai", "QBROWSER_MODEL": "executor-v1",
            "ZAI_API_KEY": "shared-provider-key",
        }):
            self.assertEqual(_configuration({"provider": "zai", "model": "executor-v1"}),
                ("zai", "reviewer-v2", "https://api.z.ai/api/paas/v4/", "shared-provider-key"))

    def test_incomplete_criterion_report_is_rejected(self):
        client = FakeVerifier({"overall": "pass", "criteria": [], "issues": [], "summary": ""})
        with self.assertRaisesRegex(ValueError, "every success criterion"):
            verify_job(self.store, self.job["id"], client=client, desktop=FakeDesktop())


if __name__ == "__main__":
    unittest.main()
