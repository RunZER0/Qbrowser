"""Local API contract, including authentication and failure states."""

import os
from tempfile import TemporaryDirectory
from unittest.mock import patch
import unittest

from fastapi.testclient import TestClient
from qbrowser.web import app


class APITests(unittest.TestCase):
    def setUp(self):
        self.tmp = TemporaryDirectory()
        self.env = patch.dict(os.environ, {
            "QBROWSER_HOME": self.tmp.name, "QBROWSER_API_TOKEN": "testing",
        })
        self.env.start()
        self.client = TestClient(app)
        self.headers = {"Authorization": "Bearer testing"}

    def tearDown(self):
        self.client.close()
        self.env.stop()
        self.tmp.cleanup()

    def test_requires_token(self):
        self.assertEqual(self.client.get("/api/jobs").status_code, 401)
        self.assertEqual(self.client.get("/api/jobs", headers=self.headers).status_code, 200)

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

    def test_unsafe_state_rejected(self):
        response = self.client.post("/api/jobs", json={"instruction": "Process documents"},
                                    headers=self.headers)
        ident = response.json()["id"]
        self.assertEqual(self.client.post(f"/api/jobs/{ident}/verified",
                                         headers=self.headers).status_code, 409)


if __name__ == "__main__":
    unittest.main()
