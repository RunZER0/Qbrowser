from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest.mock import patch
import sys
import unittest

from qbrowser.audio import transcribe_audio
from qbrowser.document_engine import SUPPORTED, extract_text, inspect_document


class AudioExtractionTests(unittest.TestCase):
    def setUp(self):
        self.tmp = TemporaryDirectory()
        self.audio = Path(self.tmp.name) / "sample.wav"
        self.audio.write_bytes(b"RIFF" + (36).to_bytes(4, "little") + b"WAVEfmt " + b"\x00" * 24)

    def tearDown(self):
        self.tmp.cleanup()

    def test_audio_is_a_supported_input_and_inspection_does_not_upload_it(self):
        self.assertIn(".wav", SUPPORTED)
        info = inspect_document(self.audio)
        self.assertEqual("wav", info["format"])
        self.assertIn("explicit", info["transcription"])
        with patch.dict("os.environ", {}, clear=True):
            with self.assertRaisesRegex(RuntimeError, "transcription is disabled"):
                extract_text(self.audio)

    def test_transcription_uses_only_the_explicitly_configured_openai_compatible_client(self):
        calls = []

        class FakeClient:
            def __init__(self, **kwargs):
                calls.append(("client", kwargs))
                self.audio = SimpleNamespace(transcriptions=self)

            def create(self, **kwargs):
                calls.append(("request", kwargs["model"], kwargs["response_format"], kwargs["file"].name))
                return SimpleNamespace(text="Meeting starts at noon.")

        fake_module = SimpleNamespace(OpenAI=FakeClient)
        with patch.dict("sys.modules", {"openai": fake_module}), patch.dict("os.environ", {
            "QBROWSER_TRANSCRIPTION_BASE_URL": "https://api.example.test/v1",
            "QBROWSER_TRANSCRIPTION_API_KEY": "test-only-secret",
            "QBROWSER_TRANSCRIPTION_MODEL": "whisper-test",
        }, clear=True):
            self.assertEqual("Meeting starts at noon.", transcribe_audio(self.audio))
        self.assertEqual("whisper-test", calls[1][1])
        self.assertEqual("text", calls[1][2])

    def test_transcription_rejects_non_https_remote_endpoint(self):
        with patch.dict("os.environ", {
            "QBROWSER_TRANSCRIPTION_BASE_URL": "http://remote.example/v1",
            "QBROWSER_TRANSCRIPTION_API_KEY": "test",
        }, clear=True):
            with self.assertRaisesRegex(ValueError, "HTTPS or localhost"):
                transcribe_audio(self.audio)

    def test_transcription_rejects_http_hostname_that_only_starts_with_localhost(self):
        with patch.dict("os.environ", {
            "QBROWSER_TRANSCRIPTION_BASE_URL": "http://localhost.attacker.test/v1",
            "QBROWSER_TRANSCRIPTION_API_KEY": "test",
        }, clear=True):
            with self.assertRaisesRegex(ValueError, "HTTPS or localhost"):
                transcribe_audio(self.audio)


if __name__ == "__main__":
    unittest.main()
