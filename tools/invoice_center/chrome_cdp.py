from __future__ import annotations

import json
from typing import Iterable
from urllib.request import urlopen

from playwright.sync_api import Browser, BrowserContext, Page, Playwright


DEFAULT_CDP_URL = "http://127.0.0.1:9222"
CHROME_START_COMMAND = (
    'open -na "Google Chrome" --args --remote-debugging-port=9222 '
    '--user-data-dir="$HOME/EI account/chrome_profile"'
)


def connect_existing_chrome(
    playwright: Playwright,
    cdp_url: str = DEFAULT_CDP_URL,
) -> tuple[Browser, BrowserContext]:
    try:
        with urlopen(f"{cdp_url.rstrip('/')}/json/list", timeout=3) as response:
            targets = json.loads(response.read().decode("utf-8"))
    except Exception as exc:
        raise RuntimeError(
            f"無法連接目前 Chrome（{cdp_url}）。\n請先啟動：{CHROME_START_COMMAND}"
        ) from exc
    if not targets:
        raise RuntimeError(
            "Chrome 9222 已開啟，但沒有可控制的分頁。\n"
            f"請關閉目前空的 9222 Chrome 後啟動：{CHROME_START_COMMAND}"
        )
    try:
        browser = playwright.chromium.connect_over_cdp(cdp_url)
    except Exception as exc:
        raise RuntimeError(
            f"無法連接目前 Chrome（{cdp_url}）。\n"
            f"請先啟動：{CHROME_START_COMMAND}"
        ) from exc

    if not browser.contexts:
        raise RuntimeError(
            f"Chrome 已連接但沒有可用 browser context（{cdp_url}）。\n"
            f"請重新啟動：{CHROME_START_COMMAND}"
        )
    return browser, browser.contexts[0]


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
