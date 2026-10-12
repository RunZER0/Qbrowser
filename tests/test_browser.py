from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch
import unittest
import socket

from qbrowser.browser import (
    PlaywrightSession, profile_directory, validate_resolved_url, validate_url,
)


class FakeLocator:
    def __init__(self, role, name, calls):
        self.role, self.name, self.calls = role, name, calls

    def count(self):
        return 1

    def aria_snapshot(self):
        return "- heading \"Example\"\n- button \"Continue\""

    def inner_text(self, **kwargs):
        return "Example page"

    def click(self, **kwargs):
        self.calls.append(("click", self.role, self.name))

    def fill(self, value, **kwargs):
        self.calls.append(("fill", self.role, self.name, value))

    def press(self, key, **kwargs):
        self.calls.append(("press", self.role, self.name, key))

    def set_input_files(self, path, **kwargs):
        self.calls.append(("upload", self.role, self.name, path))
        self.selected = path

    def input_value(self, **kwargs):
        return "C:\\fakepath\\" + Path(self.selected).name


class FakePage:
    url = "https://example.com/"

    def __init__(self):
        self.calls = []

    def locator(self, selector):
        return FakeLocator("body", selector, self.calls)

    def get_by_role(self, role, name, exact):
        return FakeLocator(role, name, self.calls)

    def get_by_label(self, name, exact):
        return FakeLocator("file", name, self.calls)

    def is_closed(self):
        return False

    def title(self):
        return "Example"


class FakeContext:
    def __init__(self, pages=None):
        self.pages = pages or []

    def new_page(self):
        page = FakePage()
        self.pages.append(page)
        return page


class BrowserAdapterTests(unittest.TestCase):
    def test_only_web_urls_and_explicit_local_host_allowlists_are_accepted(self):
        self.assertEqual("https://example.com", validate_url("https://example.com"))
        self.assertEqual("http://127.0.0.1:8000", validate_url(
            "http://127.0.0.1:8000", {"127.0.0.1"}))
        self.assertEqual("https://docs.example.com", validate_url(
            "https://docs.example.com", {"*.example.com"}))
        for url in ("file:///etc/passwd", "javascript:alert(1)",
                    "http://localhost", "http://192.168.1.1", "https://user:pw@example.com"):
            with self.subTest(url=url), self.assertRaises(ValueError):
                validate_url(url)

    def test_hostname_resolution_blocks_private_destinations_and_supports_explicit_hosts(self):
        def private_resolver(host, port, **kwargs):
            return [(socket.AF_INET, socket.SOCK_STREAM, 0, "", ("10.0.0.8", port))]

        with self.assertRaisesRegex(ValueError, "local or private"):
            validate_resolved_url("https://private.example", resolver=private_resolver)
        self.assertEqual("https://private.example", validate_resolved_url(
            "https://private.example", {"private.example"}, resolver=private_resolver))
        def public_resolver(host, port, **kwargs):
            return [(socket.AF_INET, socket.SOCK_STREAM, 0, "", ("93.184.216.34", port))]
        self.assertEqual("https://public.example", validate_resolved_url(
            "https://public.example", resolver=public_resolver))

    def test_accessible_actions_require_an_exactly_named_role(self):
        session = PlaywrightSession(allowed_hosts={"example.com"})
        session.page = FakePage()
        self.assertIn("Accessible page tree", session.perform("snapshot", {}))
        self.assertIn("click completed", session.perform("click", {
            "role": "button", "name": "Continue"}))
        self.assertIn("fill completed", session.perform("fill", {
            "role": "textbox", "name": "Search", "value": "quarterly report"}))
        self.assertEqual("click", session.page.calls[0][0])
        self.assertEqual("fill", session.page.calls[1][0])
        with self.assertRaisesRegex(ValueError, "accessible role"):
            session.perform("click", {"role": "button; script", "name": "Continue"})

    def test_tabs_are_listed_switched_and_managed_file_uploads_are_labeled(self):
        first = FakePage()
        context = FakeContext([first])
        session = PlaywrightSession(allowed_hosts={"example.com"})
        session.page = first
        session.context = context
        self.assertIn("0: Example", session.perform("tabs", {}))
        session.perform("new_tab", {})
        self.assertEqual(2, len(context.pages))
        session.perform("switch_tab", {"tab_index": 0})
        self.assertIs(first, session.page)
        with TemporaryDirectory() as tmp:
            file_path = Path(tmp) / "report.pdf"
            file_path.write_bytes(b"managed test input")
            output = session.perform("upload", {"role": "button", "name": "Choose report"},
                                     upload_path=file_path)
        self.assertIn("Selected and verified managed file report.pdf", output)

    def test_download_completion_is_validated_and_sent_to_managed_handler(self):
        class Download:
            suggested_filename = "report.txt"

            def save_as(self, path):
                Path(path).write_text("downloaded report", encoding="utf-8")

        class DownloadExpectation:
            value = Download()

            def __enter__(self):
                return self

            def __exit__(self, *_args):
                return False

        page = FakePage()
        page.expect_download = lambda **_kwargs: DownloadExpectation()
        received = []

        def save(filename, path, details):
            received.append((filename, path.read_text(encoding="utf-8"), details["format"]))
            return "Revision 1 recorded"

        session = PlaywrightSession(allowed_hosts={"example.com"}, download_handler=save)
        session.page = page
        result = session.perform("download", {"role": "link", "name": "Download report"})
        self.assertIn("Download completed", result)
        self.assertEqual([("report.txt", "downloaded report", "txt")], received)

    def test_browser_profile_is_isolated_by_desktop_identity(self):
        with TemporaryDirectory() as tmp, patch.dict("os.environ", {"QBROWSER_HOME": tmp}):
            first = profile_directory("session-one")
            second = profile_directory("session-two")
        self.assertNotEqual(first, second)
        self.assertEqual(Path(tmp).resolve(), first.parents[1])


if __name__ == "__main__":
    unittest.main()
