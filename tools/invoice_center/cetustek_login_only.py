from __future__ import annotations

import argparse
from pathlib import Path

from playwright.sync_api import sync_playwright

from tools.invoice_center.cetustek_invoice_paste import process_pending_invoice_payloads
from tools.invoice_center.ei_export_all import (
    configured_areas,
    credentials_for,
    load_accounts,
    login_second,
    login_portal,
    open_second_login,
    portal_values,
)
from tools.invoice_center.chrome_cdp import (
    DEFAULT_CDP_URL,
    connect_existing_chrome,
    find_invoice_pages,
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="登入鯨躍第一層及指定地區 EI 第二層；若有待開立 Payload 則接續貼入")
    parser.add_argument("--area", help="第二層地區，例如：台北、台中；未指定時使用第一個已設定地區")
    parser.add_argument("--accounts", type=Path, help="EI／鯨躍帳密 JSON")
    parser.add_argument("--cdp-url", default=DEFAULT_CDP_URL)
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
    with sync_playwright() as playwright:
        _browser, context = connect_existing_chrome(playwright, args.cdp_url)
        print(f"瀏覽器：已連接現有 Chrome（{args.cdp_url}）")
        portal_page, ei_page = find_invoice_pages(context)
        active_page = None

        if portal_page is not None:
            login_portal(portal_page, accounts)
            active_page = open_second_login(context, portal_page)
            login_second(active_page, credentials)
        elif ei_page is not None:
            active_page = ei_page
            login_second(active_page, credentials)
        else:
            portal_page = context.new_page()
            login_portal(portal_page, accounts)
            active_page = open_second_login(context, portal_page)
            login_second(active_page, credentials)

        print(f"鯨躍第一層及 {credentials.label} EI 第二層登入完成")
        processed = process_pending_invoice_payloads(active_page, credentials.label)
        if processed:
            print(f"已接續處理 {processed} 筆發票 Payload；同區多筆不重複登入。")
        else:
            print("沒有待開立 Payload；停留在目前鯨躍登入狀態。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
