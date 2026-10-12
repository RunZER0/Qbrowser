from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch
import unittest

from qbrowser.store import JobStore


class CrossTaskMemoryTests(unittest.TestCase):
    def setUp(self):
        self.tmp = TemporaryDirectory()
        self.store = JobStore(Path(self.tmp.name) / "qbrowser.db")

    def tearDown(self):
        self.tmp.cleanup()

    def test_owner_curated_notes_are_indexed_deduplicated_and_deletable(self):
        first = self.store.add_long_term_memory(
            "Prefer concise quarterly financial reports with a table first.",
            category="preference", tags=["reports", "finance"])
        duplicate = self.store.add_long_term_memory(
            "Prefer concise quarterly financial reports with a table first.",
            category="preference", tags=["reports", "finance"])
        self.assertEqual(first["id"], duplicate["id"])
        found = self.store.search_long_term_memory("quarterly financial report", limit=5)
        self.assertEqual([first["id"]], [item["id"] for item in found])
        self.assertTrue(self.store.delete_long_term_memory(first["id"]))
        self.assertEqual([], self.store.search_long_term_memory("quarterly financial report"))
        self.assertFalse(self.store.delete_long_term_memory(first["id"]))

    def test_memory_rejects_credentials_and_bad_expiry(self):
        with self.assertRaisesRegex(ValueError, "Credentials"):
            self.store.add_long_term_memory("api_key: sk-example-secret-value")
        with self.assertRaises(ValueError):
            self.store.add_long_term_memory("A useful note", expires_days=0)

    def test_explicit_supersession_resolves_conflicts_without_erasing_history(self):
        old = self.store.add_long_term_memory(
            "Use brief summaries for travel reports", category="preference")
        new = self.store.add_long_term_memory(
            "Use detailed summaries for travel reports", category="preference",
            supersedes_id=old["id"])
        self.assertEqual(new["id"], self.store.list_long_term_memory()[0]["id"])
        self.assertEqual(old["id"], self.store.list_long_term_memory(
            include_superseded=True)[1]["id"])
        self.assertEqual(new["id"], self.store.list_long_term_memory(
            include_superseded=True)[1]["superseded_by"])
        self.assertEqual([new["id"]], [item["id"] for item in
                         self.store.search_long_term_memory("travel reports")])

    def test_expired_notes_are_not_returned(self):
        row = self.store.add_long_term_memory("Use the blue template for invoices",
                                               expires_days=1)
        with self.store._connection() as db:
            db.execute("UPDATE long_term_memory SET expires_at='2000-01-01T00:00:00+00:00' WHERE id=?",
                       (row["id"],))
        self.assertEqual([], self.store.search_long_term_memory("blue template invoices"))

    def test_optional_local_vectors_find_related_notes_without_shared_keywords(self):
        class FakeEmbedder:
            def embed(self, texts):
                return ([1.0, 0.0] if any(word in text.lower() for word in ("bird", "avian"))
                        else [0.0, 1.0] for text in texts)

        with patch.dict("os.environ", {"QBROWSER_MEMORY_EMBEDDING_MODEL": "test-model"}):
            relevant = self.store.add_long_term_memory(
                "Prefer the bird watching site for species guides", category="procedure",
                embedder=FakeEmbedder())
            self.store.add_long_term_memory(
                "Use the red car site to compare vehicle prices", category="note",
                embedder=FakeEmbedder())
            results = self.store.search_long_term_memory("avian field guide", embedder=FakeEmbedder())
        self.assertEqual(relevant["id"], results[0]["id"])
        self.assertEqual("semantic", results[0]["retrieval_method"])
        self.assertNotIn("embedding", results[0])

    def test_memory_reindex_adds_vectors_for_existing_notes(self):
        class FakeEmbedder:
            def embed(self, texts):
                return ([float(index + 1), 1.0] for index, _text in enumerate(texts))

        saved = self.store.add_long_term_memory("Use the green travel planner")
        with patch.dict("os.environ", {"QBROWSER_MEMORY_EMBEDDING_MODEL": "test-model"}):
            result = self.store.reindex_long_term_memory(embedder=FakeEmbedder())
            found = self.store.search_long_term_memory("trip planning", embedder=FakeEmbedder())
        self.assertEqual({"indexed": 1, "model": "test-model"}, result)
        self.assertEqual(saved["id"], found[0]["id"])


if __name__ == "__main__":
    unittest.main()
