"""Optional Playwright adapter for role-based DOM and accessibility-tree actions.

The adapter deliberately exposes a small structured surface instead of arbitrary
JavaScript. A persistent profile is isolated per desktop identity and stays under
QBROWSER_HOME. Pages and downloaded files remain untrusted task data.
"""

from __future__ import annotations

import ipaddress
import base64
import os
import re
import socket
import tempfile
from pathlib import Path
from urllib.parse import urlsplit

from qbrowser.store import home


def _desktop_identity() -> str:
    return (os.getenv("QBROWSER_DESKTOP_ID") or os.getenv("WAYLAND_DISPLAY") or
            os.getenv("DISPLAY") or os.getenv("SESSIONNAME") or "default")


def profile_directory(identity: str | None = None) -> Path:
    import hashlib
    digest = hashlib.sha256((identity or _desktop_identity()).encode("utf-8", "replace")).hexdigest()[:24]
    path = home() / "browser_profiles" / digest
    path.mkdir(mode=0o700, parents=True, exist_ok=True)
    if os.name != "nt":
        path.chmod(0o700)
    return path


def validate_url(url: str, allowed_hosts: set[str] | None = None) -> str:
    """Allow only web URLs; block local/private targets unless explicitly allowlisted."""
    if not isinstance(url, str) or len(url) > 4096:
        raise ValueError("Browser URL must be at most 4096 characters")
    parsed = urlsplit(url.strip())
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise ValueError("Browser navigation requires an http(s) URL")
    if parsed.username or parsed.password:
        raise ValueError("Credentials in browser URLs are not allowed")
    host = parsed.hostname.lower().rstrip(".")
    allow = {item.lower().strip().rstrip(".") for item in (allowed_hosts or set()) if item.strip()}
    local_allowed = host in allow or any(host.endswith("." + item.lstrip("*."))
                                         for item in allow if item.startswith("*."))
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        address = None
    is_local = (host == "localhost" or host.endswith(".localhost") or
                (address is not None and not address.is_global))
    if is_local and not local_allowed:
        raise ValueError("Local or private browser targets require an explicit host allowlist")
    if allow and not any(host == item or (item.startswith("*.") and host.endswith(item[1:]))
                         for item in allow):
        raise ValueError("Browser target is outside QBROWSER_BROWSER_ALLOWED_HOSTS")
    return url.strip()


def validate_resolved_url(url: str, allowed_hosts: set[str] | None = None,
                          resolver=None) -> str:
    """Also reject public hostnames that resolve to a local or private address."""
    target = validate_url(url, allowed_hosts)
    parsed = urlsplit(target)
    host = parsed.hostname.lower().rstrip(".")
    allow = {item.lower().strip().rstrip(".") for item in (allowed_hosts or set())
             if item.strip()}
    explicitly_allowed = (host in allow or any(
        item.startswith("*.") and host.endswith(item[1:]) for item in allow))
    if explicitly_allowed:
        return target
    try:
        literal = ipaddress.ip_address(host)
    except ValueError:
        literal = None
    if literal is not None:
        return target
    lookup = resolver or socket.getaddrinfo
    try:
        answers = lookup(host, parsed.port or (443 if parsed.scheme == "https" else 80),
                         type=socket.SOCK_STREAM)
    except (OSError, ValueError) as exc:
        raise ValueError("Browser target DNS could not be verified") from exc
    addresses = {ipaddress.ip_address(answer[4][0].split("%", 1)[0]) for answer in answers}
    if not addresses or any(not address.is_global for address in addresses):
        raise ValueError("Browser target resolves to a local or private address")
    return target


class PlaywrightSession:
    """Small synchronous browser session with accessible, structured operations."""

    def __init__(self, *, identity: str | None = None, headless: bool | None = None,
                 allowed_hosts: set[str] | None = None, timeout_ms: int = 15000,
                 download_handler=None):
        if type(timeout_ms) is not int or not 1000 <= timeout_ms <= 120000:
            raise ValueError("Browser timeout must be between 1000 and 120000 ms")
        if allowed_hosts is None:
            allowed_hosts = {x.strip() for x in os.getenv(
                "QBROWSER_BROWSER_ALLOWED_HOSTS", "").split(",") if x.strip()}
        self.allowed_hosts = allowed_hosts
        self.timeout_ms = timeout_ms
        self.identity = identity or _desktop_identity()
        self.headless = (os.getenv("QBROWSER_BROWSER_HEADLESS", "1").lower() in
                         {"1", "true", "yes", "on"}) if headless is None else headless
        self._playwright = None
        self.context = None
        self.page = None
        self.download_handler = download_handler

    def _guard_navigation(self, route):
        try:
            if route.request.is_navigation_request():
                validate_resolved_url(route.request.url, self.allowed_hosts)
        except (ValueError, OSError):
            route.abort("blockedbyclient")
        else:
            route.continue_()

    def open(self):
        if self.page is not None:
            return self
        try:
            from playwright.sync_api import sync_playwright
        except ImportError as exc:
            raise RuntimeError("Install Qbrowser browser support: pip install -e '.[browser]'; then run playwright install chromium") from exc
        self._playwright = sync_playwright().start()
        try:
            self.context = self._playwright.chromium.launch_persistent_context(
                user_data_dir=str(profile_directory(self.identity)), headless=self.headless,
                accept_downloads=True, viewport={"width": 1365, "height": 900},
                args=["--disable-background-networking"],
            )
            self.context.route("**/*", self._guard_navigation)
            self.context.set_default_timeout(self.timeout_ms)
            self.page = self.context.pages[0] if self.context.pages else self.context.new_page()
            return self
        except Exception:
            self.close()
            raise

    def close(self):
        if self.context is not None:
            try:
                self.context.close()
            finally:
                self.context = None
                self.page = None
        if self._playwright is not None:
            try:
                self._playwright.stop()
            finally:
                self._playwright = None

    def snapshot(self, *, max_chars: int = 16000) -> str:
        if type(max_chars) is not int or not 500 <= max_chars <= 50000:
            raise ValueError("DOM snapshot limit must be between 500 and 50000 characters")
        page = self.open().page
        try:
            text = page.locator("body").aria_snapshot()
            kind = "Accessible page tree"
        except Exception:
            text = page.locator("body").inner_text(timeout=self.timeout_ms)
            kind = "Visible page text (accessibility snapshot unavailable)"
        pages = ([item for item in self.context.pages if not item.is_closed()]
                 if self.context is not None else [page])
        tab_summary = []
        for index, item in enumerate(pages):
            try:
                title = item.title()
            except Exception:
                title = ""
            current = " (active)" if item is page else ""
            tab_summary.append(f"{index}: {title[:120]} — {item.url[:300]}{current}")
        return ("Open tabs: " + ("; ".join(tab_summary) or "none") +
                f"\n{kind} at {page.url}:\n{text[:max_chars]}")

    def screenshot_uri(self) -> str:
        """Return the current page image as an in-memory data URI."""
        page = self.open().page
        raw = page.screenshot(type="png", full_page=False, animations="disabled")
        return "data:image/png;base64," + base64.b64encode(raw).decode("ascii")

    def perform(self, action: str, args: dict, *, upload_path: Path | None = None) -> str:
        page = self.open().page
        if action == "snapshot":
            return self.snapshot(max_chars=args.get("max_chars", 16000))
        if action == "tabs":
            return self.snapshot(max_chars=args.get("max_chars", 16000))
        if action == "new_tab":
            self.page = self.context.new_page()
            return "Opened a new browser tab. " + self.snapshot(max_chars=12000)
        if action in {"switch_tab", "close_tab"}:
            index = args.get("tab_index")
            pages = [item for item in self.context.pages if not item.is_closed()]
            if type(index) is not int or not 0 <= index < len(pages):
                raise ValueError("Tab index is outside the current open-tab list")
            target = pages[index]
            if action == "switch_tab":
                self.page = target
            else:
                if len(pages) == 1:
                    raise ValueError("Cannot close the only browser tab")
                target.close()
                self.page = next((item for item in pages if item is not target), None)
            return f"{action.replace('_', ' ').title()} completed. " + self.snapshot(max_chars=12000)
        if action == "navigate":
            target = validate_resolved_url(args.get("url", ""), self.allowed_hosts)
            response = page.goto(target, wait_until="domcontentloaded", timeout=self.timeout_ms)
            status = response.status if response is not None else "no response"
            return f"Navigated to {page.url} (HTTP {status}). {self.snapshot(max_chars=12000)}"
        if action == "back":
            page.go_back(wait_until="domcontentloaded", timeout=self.timeout_ms)
            return f"Returned to {page.url}. {self.snapshot(max_chars=12000)}"
        if action == "scroll":
            amount = args.get("amount")
            if type(amount) is not int or not -30 <= amount <= 30 or amount == 0:
                raise ValueError("Browser scroll amount must be a nonzero integer from -30 to 30")
            page.mouse.wheel(0, amount * 120)
            page.wait_for_timeout(250)
            return "Scrolled and observed the updated page. " + self.snapshot(max_chars=12000)
        role = args.get("role")
        name = args.get("name")
        if not isinstance(role, str) or not re.fullmatch(r"[a-zA-Z][a-zA-Z0-9_-]{0,39}", role):
            raise ValueError("Choose a valid accessible role from the current page tree")
        if not isinstance(name, str) or not name.strip() or len(name) > 500:
            raise ValueError("Choose an accessible name from the current page tree")
        locator = (page.get_by_label(name, exact=True) if action == "upload" else
                   page.get_by_role(role, name=name, exact=True))
        matches = locator.count()
        if matches != 1:
            raise ValueError(f"Expected exactly one {role} named {name!r}; found {matches}")
        if action == "click":
            locator.click(timeout=self.timeout_ms)
        elif action == "download":
            with page.expect_download(timeout=self.timeout_ms) as pending_download:
                locator.click(timeout=self.timeout_ms)
            download = pending_download.value
            if self.download_handler is None:
                raise RuntimeError("No managed artifact handler is configured for downloads")
            from qbrowser.artifact_vault import safe_name, validate_file
            filename = safe_name(download.suggested_filename)
            handle, temp_name = tempfile.mkstemp(prefix="qbrowser-download-",
                                                suffix=Path(filename).suffix.lower())
            os.close(handle)
            temp_path = Path(temp_name)
            try:
                download.save_as(str(temp_path))
                details = validate_file(temp_path, filename)
                result = self.download_handler(filename, temp_path, details)
            finally:
                temp_path.unlink(missing_ok=True)
            return f"Download completed and saved as managed output {filename}. {result}"
        elif action == "fill":
            value = args.get("value")
            if not isinstance(value, str) or len(value) > 4000:
                raise ValueError("Browser text entry must be bounded text")
            locator.fill(value, timeout=self.timeout_ms)
        elif action == "upload":
            if upload_path is None or not upload_path.is_file() or upload_path.is_symlink():
                raise ValueError("Upload must refer to a managed, verified task input")
            locator.set_input_files(str(upload_path), timeout=self.timeout_ms)
            selected = locator.input_value(timeout=self.timeout_ms)
            if Path(selected.replace("\\", "/")).name != upload_path.name:
                raise RuntimeError("Browser file control did not confirm the selected filename")
            return f"Selected and verified managed file {upload_path.name}. {self.snapshot(max_chars=12000)}"
        elif action == "press":
            key = args.get("key")
            if key not in {"Enter", "Tab", "Escape", "ArrowDown", "ArrowUp", "Home", "End"}:
                raise ValueError("Unsupported browser key")
            locator.press(key, timeout=self.timeout_ms)
        else:
            raise ValueError(f"Unsupported browser action: {action}")
        return f"{action} completed for the uniquely matched {role} {name!r}. {self.snapshot(max_chars=12000)}"

    def __enter__(self):
        return self.open()

    def __exit__(self, exc_type, exc, tb):
        self.close()
