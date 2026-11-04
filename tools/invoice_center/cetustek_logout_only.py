"""只登出鯨躍第二層（清潔異動整合執行：同一區的鯨躍步驟都做完後統一登出）。"""
from __future__ import annotations

import argparse

from playwright.sync_api import sync_playwright

from tools.invoice_center.chrome_cdp import DEFAULT_CDP_URL, connect_existing_chrome, find_invoice_pages
from tools.invoice_center.ei_export_all import logout_second


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="鯨躍第二層登出")
    parser.add_argument("--area", required=True)
    parser.add_argument("--cdp-url", default=DEFAULT_CDP_URL)
    args = parser.parse_args(argv)
    with sync_playwright() as playwright:
        _browser, context = connect_existing_chrome(playwright, args.cdp_url)
        _portal, ei_page = find_invoice_pages(context)
        if ei_page is None or ei_page.is_closed():
            print(f"[{args.area}] 沒有鯨躍第二層分頁，不用登出")
            return 0
        try:
            logout_second(ei_page, args.area)
        except Exception as exc:
            print(f"[{args.area}] ⚠️ 鯨躍第二層自動登出失敗，下次執行前請手動登出：{exc}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
