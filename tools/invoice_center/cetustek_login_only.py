from __future__ import annotations

import argparse
from pathlib import Path

from playwright.sync_api import sync_playwright

from tools.invoice_center.ei_export_all import (
    DEFAULT_CHROME_PROFILE,
    configured_areas,
    credentials_for,
    load_accounts,
    login_second,
    login_portal,
    open_second_login,
    portal_values,
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="登入鯨躍第一層及指定地區 EI 第二層，不匯出資料")
    parser.add_argument("--area", help="第二層地區，例如：台北、台中；未指定時使用第一個已設定地區")
    parser.add_argument("--accounts", type=Path, help="EI／鯨躍帳密 JSON")
    parser.add_argument(
        "--chrome-profile",
        type=Path,
        default=DEFAULT_CHROME_PROFILE,
        help=f"Chrome 設定檔（預設：{DEFAULT_CHROME_PROFILE}）",
    )
    parser.add_argument("--slow-mo", type=int, default=100)
    parser.add_argument("--cdp-url", default="http://127.0.0.1:9222")
    parser.add_argument("--require-existing-chrome", action="store_true")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    accounts = load_accounts(args.accounts)
    area = args.area or configured_areas(accounts)[0]
    credentials = credentials_for(area, accounts)
    company_id, member_id, portal_password = portal_values(accounts)
    missing = []
    if not company_id:
        missing.append("portal_company_id")
    if not portal_password:
        missing.append("portal_password")
    if missing:
        print(
            "第一層鯨躍帳密尚未設定，無法自動預填："
            + "、".join(missing)
            + "。這次仍可手動輸入第一層；登入後程式會自動預填第二層。"
        )
    print(f"第二層登入地區：{credentials.label}")
    profile = args.chrome_profile.expanduser()
    profile.mkdir(parents=True, exist_ok=True)
    with sync_playwright() as playwright:
        connected_browser = None
        owns_context = False
        context = None
        try:
            connected_browser = playwright.chromium.connect_over_cdp(args.cdp_url)
            if connected_browser.contexts:
                context = connected_browser.contexts[0]
                print(f"瀏覽器：已連接現有 Chrome（{args.cdp_url}）")
        except Exception:
            connected_browser = None
        if context is None:
            if args.require_existing_chrome:
                raise RuntimeError(
                    f"找不到可連接的 Chrome：{args.cdp_url}；"
                    "請先以 --remote-debugging-port=9222 啟動鯨躍 Chrome"
                )
            context = playwright.chromium.launch_persistent_context(
                user_data_dir=profile,
                channel="chrome",
                headless=False,
                slow_mo=args.slow_mo,
                locale="zh-TW",
            )
            owns_context = True
        page = next(
            (item for item in context.pages if "cetustek.com.tw" in item.url),
            None,
        ) or context.new_page()
        try:
            login_portal(page, accounts)
            second_page = open_second_login(context, page)
            login_second(second_page, credentials)
            print(f"鯨躍第一層及 {credentials.label} EI 第二層登入完成；不會下載資料。")
        finally:
            if owns_context:
                context.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
