"""Synthetic visual provider contract checks."""
import unittest
from qbrowser.native import ModelConfig
from qbrowser.smoke import probe, probe_desktop

class FakeModel:
    def __init__(self, coordinates=(160,100)):
        self.coordinates=coordinates
    def ask(self, messages):
        assert messages[-1]["content"][1]["image_url"]["url"].startswith("data:image/png;base64,")
        x,y=self.coordinates
        return {"calls":[{"name":"computer_action",
                          "args":{"action":"click","x":x,"y":y,
                                  "impact":"local","purpose":"smoke test"}}]}

class TestSmoke(unittest.TestCase):
    def setUp(self):
        self.config=ModelConfig("zai","glm-5.3-flash","unused",
                                "https://example.test",5,100,1280)

    def test_valid_target(self):
        result=probe(model=FakeModel(),config=self.config)
        self.assertEqual(result["status"],"passed")
        self.assertFalse(result["gui_executed"])

    def test_invalid_target_is_rejected(self):
        with self.assertRaisesRegex(RuntimeError,"highlighted"):
            probe(model=FakeModel((0,0)),config=self.config)

    def test_desktop_smoke_reports_missing_display_without_traceback(self):
        from unittest.mock import patch
        with patch("qbrowser.native.Desktop", side_effect=KeyError("DISPLAY")):
            result = probe_desktop()
        self.assertEqual(result["status"], "blocked")
        self.assertEqual(result["error"], "No graphical display is available")

if __name__=="__main__":
    unittest.main()
