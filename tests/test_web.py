"""Local API contract, including authentication and failure states."""

import asyncio
import json
import os
from tempfile import TemporaryDirectory
from unittest.mock import patch
import unittest

from httpx import ASGITransport, AsyncClient
from qbrowser.web import app, store as get_store


class ASGITestClient:
    """Use HTTPX's async ASGI transport without TestClient's blocking portal."""

    def __init__(self, application):
        self.application = application

    def request(self, method, path, **kwargs):
        async def send():
            async with AsyncClient(transport=ASGITransport(app=self.application),
                                   base_url="http://test") as client:
                return await client.request(method, path, **kwargs)
        return asyncio.run(send())

    def get(self, path, **kwargs):
        return self.request("GET", path, **kwargs)

    def post(self, path, **kwargs):
        return self.request("POST", path, **kwargs)

    def put(self, path, **kwargs):
        return self.request("PUT", path, **kwargs)

    def delete(self, path, **kwargs):
        return self.request("DELETE", path, **kwargs)

    def close(self):
        pass


class APITests(unittest.TestCase):
    def setUp(self):
        self.tmp = TemporaryDirectory()
        self.env = patch.dict(os.environ, {
            "QBROWSER_HOME": self.tmp.name, "QBROWSER_API_TOKEN": "testing",
        })
        self.env.start()
        self.client = ASGITestClient(app)
        self.headers = {"Authorization": "Bearer testing"}

    def tearDown(self):
        self.client.close()
        self.env.stop()
        self.tmp.cleanup()

    def test_requires_token(self):
        self.assertEqual(self.client.get("/api/jobs").status_code, 401)
        self.assertEqual(self.client.get("/api/jobs", headers=self.headers).status_code, 200)

    def test_health_and_installable_dashboard_resources(self):
        self.assertEqual(self.client.get("/health/live").json(), {"live": True})
        ready = self.client.get("/api/health/ready", headers=self.headers)
        self.assertTrue(ready.json()["ready"])
        self.assertEqual(ready.json()["schema_version"], 8)
        manifest = self.client.get("/manifest.webmanifest")
        self.assertEqual(manifest.status_code, 200)
        self.assertEqual(manifest.json()["short_name"], "Qbrowser")
        self.assertIn("serviceWorker.register", self.client.get("/").text)
        self.assertEqual(self.client.get("/service-worker.js").status_code, 200)
        metrics = self.client.get("/api/metrics", headers=self.headers)
        self.assertEqual(metrics.status_code, 200)
        self.assertEqual(metrics.json()["jobs"], 0)
        self.assertEqual(metrics.json()["jobs_by_status"], {})
        self.assertEqual(metrics.json()["average_execution_ms"], 0)

    def test_schedules_and_external_wake_are_authenticated(self):
        self.assertEqual(self.client.get("/api/schedules").status_code, 401)
        created = self.client.post("/api/schedules", headers=self.headers, json={
            "instruction": "Check status", "interval_seconds": 60,
            "first_run_seconds": 0})
        self.assertEqual(created.status_code, 201, created.text)
        self.assertTrue(created.json()["enabled"])
        self.assertEqual(self.client.delete("/api/schedules/"+created.json()["id"],
                                            headers=self.headers).status_code, 200)
        job = self.client.post("/api/jobs", headers=self.headers,
            json={"instruction": "Wait for a report"}).json()
        repository = get_store()
        repository.claim()
        repository.wait_for(job["id"], 60, "Waiting")
        woken = self.client.post(f"/api/jobs/{job['id']}/wake", headers=self.headers,
                                 json={"reason": "Report arrived"})
        self.assertEqual(woken.json()["status"], "queued")

    def test_live_screenshot_requires_token_and_returns_no_store_image(self):
        class Screen:
            def observe(self):
                return "data:image/jpeg;base64,/9j/2Q==", (1, 1)

        with patch("qbrowser.native.Desktop", return_value=Screen()):
            response = self.client.get("/api/desktop/screenshot", headers=self.headers)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.headers["cache-control"], "no-store")
        self.assertEqual(response.content, b"\xff\xd8\xff\xd9")

    def test_create_pause_and_requeue(self):
        response = self.client.post("/api/jobs", json={"instruction": "Make a file"},
                                    headers=self.headers)
        self.assertEqual(response.status_code, 201, response.text)
        ident = response.json()["id"]
        status = self.client.post(f"/api/jobs/{ident}/pause", headers=self.headers)
        self.assertEqual(status.json()["status"], "paused")
        retry = self.client.post(f"/api/jobs/{ident}/requeue", headers=self.headers)
        self.assertEqual(retry.json()["status"], "queued")
        self.assertEqual(self.client.post(f"/api/jobs/{ident}/verified",
                                         headers=self.headers).status_code, 409)

    def test_persist_checkpoints_via_http(self):
        job = self.client.post("/api/jobs", json={"instruction": "Carry out an unknown task"},
                               headers=self.headers).json()
        path = f"/api/jobs/{job['id']}/checkpoints"
        r = self.client.put(path, json={"key": "source_review", "state": "done",
                                         "detail": "Checked references"},
                            headers=self.headers)
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual(self.client.get(path, headers=self.headers).json()[0]["state"], "done")

    def test_plan_and_conversation_endpoints(self):
        job = self.client.post("/api/jobs", json={"instruction": "Prepare a short report"},
                               headers=self.headers).json()
        plan = self.client.get(f"/api/jobs/{job['id']}/plan", headers=self.headers)
        self.assertEqual(plan.json()["items"], [])
        reply = self.client.post(f"/api/jobs/{job['id']}/messages",
                                 json={"content": "Use the short format."}, headers=self.headers)
        self.assertEqual(reply.status_code, 201)
        self.assertEqual(self.client.get(f"/api/jobs/{job['id']}/messages",
                                         headers=self.headers).json()[0]["content"],
                         "Use the short format.")
        self.assertIsNone(self.client.get(f"/api/jobs/{job['id']}/assessment",
                                          headers=self.headers).json())
        self.client.post(f"/api/jobs/{job['id']}/cancel", headers=self.headers)

    def test_answer_resumes_waiting_user_job(self):
        job = self.client.post("/api/jobs", json={"instruction": "Prepare a report"},
                               headers=self.headers).json()
        store = get_store()
        store.claim()
        store.ask_user(job["id"], "Which date should appear?")
        response = self.client.post(f"/api/jobs/{job['id']}/messages",
                                    json={"content": "Use 12 October."}, headers=self.headers)
        self.assertEqual(response.status_code, 201)
        self.assertEqual(self.client.get(f"/api/jobs/{job['id']}",
                                         headers=self.headers).json()["status"], "queued")

    def test_select_model_and_stop_via_api(self):
        r = self.client.post("/api/jobs", json={"instruction": "Use DeepSeek",
                "engine": "native", "provider": "deepseek", "model": "deepseek-flash"},
                headers=self.headers)
        self.assertEqual(r.status_code, 201, r.text)
        job = r.json()
        self.assertEqual(job["provider"], "deepseek")
        r = self.client.post(f"/api/jobs/{job['id']}/stop", headers=self.headers)
        self.assertEqual(r.json()["status"], "cancelled")

    def test_one_time_approval_tokens_are_not_returned_by_api(self):
        job = self.client.post("/api/jobs", json={"instruction": "Send a document"},
                               headers=self.headers).json()
        repository = get_store()
        repository.claim()
        token = json.dumps({"intent": "c" * 64, "screen": "d" * 64}, sort_keys=True)
        repository.require_approval(job["id"], "Approve sending the document", token)
        response = self.client.post(f"/api/jobs/{job['id']}/approve-external",
                                    headers=self.headers)
        self.assertEqual(response.status_code, 200, response.text)
        self.assertFalse(response.json()["external_approved"])
        self.assertNotIn("approved_action_approval", response.json())
        self.assertNotIn("approved_action_approval",
                         self.client.get("/api/jobs", headers=self.headers).json()[0])

    def test_unsafe_state_rejected(self):
        response = self.client.post("/api/jobs", json={"instruction": "Process documents"},
                                    headers=self.headers)
        ident = response.json()["id"]
        self.assertEqual(self.client.post(f"/api/jobs/{ident}/verified",
                                         headers=self.headers).status_code, 409)


if __name__ == "__main__":
    unittest.main()
