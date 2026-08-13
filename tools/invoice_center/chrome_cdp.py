from __future__ import annotations

import json
import subprocess
import time
from pathlib import Path
from typing import Iterable
from urllib.request import urlopen

from playwright.sync_api import Browser, BrowserContext, Page, Playwright


DEFAULT_CDP_URL = "http://127.0.0.1:9222"
CHROME_START_COMMAND = (
    'open -na "Google Chrome" --args --remote-debugging-port=9222 '
    '--user-data-dir="$HOME/EI account/chrome_profile"'
)


def _read_targets(cdp_url: str) -> list[dict]:
    with urlopen(f"{cdp_url.rstrip('/')}/json/list", timeout=3) as response:
        return json.loads(response.read().decode("utf-8"))


def _start_agent_chrome(cdp_url: str) -> None:
    if cdp_url.rstrip("/") != DEFAULT_CDP_URL:
        return
    profile = Path.home() / "EI account" / "chrome_profile"
    profile.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        ["open", "-na", "Google Chrome", "--args", "--remote-debugging-port=9222", f"--user-data-dir={profile}"],
        check=False,
    )


def connect_existing_chrome(
    playwright: Playwright,
    cdp_url: str = DEFAULT_CDP_URL,
) -> tuple[Browser, BrowserContext]:
    last_error: Exception | None = None
    started = False
    for _ in range(30):
        try:
            if not _read_targets(cdp_url):
                raise RuntimeError("Chrome 沒有可用分頁")
            browser = playwright.chromium.connect_over_cdp(cdp_url, timeout=5000)
            if browser.contexts:
                return browser, browser.contexts[0]
            last_error = RuntimeError("Chrome 沒有可用 browser context")
        except Exception as exc:
            last_error = exc
            if not started:
                _start_agent_chrome(cdp_url)
                started = True
            time.sleep(0.5)

    raise RuntimeError(
        f"無法連接目前 Chrome（{cdp_url}）。\n請先啟動：{CHROME_START_COMMAND}"
    ) from last_error


def find_existing_page(context: BrowserContext, url_parts: Iterable[str]) -> Page | None:
    for url_part in url_parts:
        for page in context.pages:
            if url_part in page.url:
                return page
    return None


def find_invoice_pages(context: BrowserContext) -> tuple[Page | None, Page | None]:
    portal_page = find_existing_page(context, ("cetustek.com.tw",))
    ei_page = find_existing_page(context, ("ei.com.tw/InvoiceRent",))
    return portal_page, ei_page
