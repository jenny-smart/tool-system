from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

from playwright.sync_api import Page, sync_playwright

from tools.invoice_center.chrome_cdp import (
    DEFAULT_CDP_URL,
    connect_existing_chrome,
    find_existing_page,
)

from tools.newebpay.download_reports import (
    DEFAULT_ACCOUNTS_FILE,
    AreaAccount,
    LOGIN_URL,
    fill_enterprise_login,
    load_accounts,
)


def wait_for_login(page: Page, account: AreaAccount, timeout: float = 300.0) -> None:
    messages: list[str] = []

    def handle_dialog(dialog: object) -> None:
        message = str(getattr(dialog, "message", "")).strip()
        if message:
            messages.append(message)
            print(f"\n[{account.area}] 藍新訊息：{message}", file=sys.stderr)
        getattr(dialog, "accept")()

    page.on("dialog", handle_dialog)
    if "newebpay.com" in page.url and "login_center/single_login" not in page.url:
        print(f"[{account.area}] 沿用目前藍新登入狀態。")
        return
    page.goto(LOGIN_URL, wait_until="domcontentloaded")
    fill_enterprise_login(page, account)
    print(f"[{account.area}] 藍新帳密已預填，請輸入驗證碼並按『企業會員登入』。")

    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        login_field = page.locator(".MoComUbn")
        on_login_page = "login_center/single_login" in page.url
        field_visible = bool(login_field.count() and login_field.first.is_visible())
        if not on_login_page and not field_visible:
            print(f"[{account.area}] 藍新登入完成。")
            return
        if field_visible and not login_field.first.input_value().strip():
            fill_enterprise_login(page, account)
            print(f"[{account.area}] 帳密已重新預填，請輸入新的驗證碼。")
        page.wait_for_timeout(500)
    detail = f"；最後訊息：{messages[-1]}" if messages else ""
    raise RuntimeError(f"等待藍新登入逾時，目前頁面：{page.url}{detail}")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="只登入藍新，不查詢或下載資料")
    parser.add_argument("--area", required=True, help="登入地區，例如：台北、台中")
    parser.add_argument("--accounts-file", type=Path, default=DEFAULT_ACCOUNTS_FILE)
    parser.add_argument("--cdp-url", default=DEFAULT_CDP_URL)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    account = load_accounts(args.accounts_file.expanduser(), args.area)[0]
    with sync_playwright() as playwright:
        _browser, context = connect_existing_chrome(playwright, args.cdp_url)
        page = find_existing_page(context, ("newebpay.com",)) or context.new_page()
        wait_for_login(page, account)
        print(f"藍新 {account.area} 登入完成；瀏覽器保留供後續下載。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
