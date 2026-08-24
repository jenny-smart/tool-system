from __future__ import annotations

import argparse
from pathlib import Path

from playwright.sync_api import sync_playwright

from tools.invoice_center.cetustek_invoice_paste import (
    INVOICE_CREATE_URL,
    _is_invoice_create_page,
    process_pending_invoice_payloads,
)
from tools.invoice_center.ei_export_all import (
    EI_HOME_URL,
    EI_LOGIN_URL,
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
    parser = argparse.ArgumentParser(description="登入鯨躍並將第一筆待開立發票 Payload 匯入發票開立頁")
    parser.add_argument("--area", help="第二層地區，例如：台北、台中；未指定時使用第一個已設定地區")
    parser.add_argument("--accounts", type=Path, help="EI／鯨躍帳密 JSON")
    parser.add_argument("--cdp-url", default=DEFAULT_CDP_URL)
    return parser.parse_args()


def _ei_logged_in(page) -> bool:
    messages: list[str] = []

    def handle_dialog(dialog) -> None:
        messages.append(str(dialog.message or "").strip())
        dialog.accept()

    try:
        page.remove_all_listeners("dialog")
        page.on("dialog", handle_dialog)
        page.goto(INVOICE_CREATE_URL, wait_until="domcontentloaded", timeout=15000)
        return not any("未授權" in message for message in messages) and _is_invoice_create_page(page)
    except Exception:
        return False
    finally:
        try:
            page.remove_listener("dialog", handle_dialog)
        except Exception:
            pass

def _clean_ei_page(context, page):
    clean = context.new_page()
    clean.goto(EI_HOME_URL, wait_until="domcontentloaded")
    try:
        if _ei_logged_in(clean):
            print("[鯨躍] 已建立乾淨 EI 分頁，不沿用登入 Dialog handler")
            return clean
    except Exception:
        pass
    try:
        clean.close()
    except Exception:
        pass
    return page


def main() -> int:
    args = parse_args()
    accounts = load_accounts(args.accounts)
    area = args.area or configured_areas(accounts)[0]
    credentials = credentials_for(area, accounts)
    company_id, _member_id, portal_password = portal_values(accounts)
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
        used_login_handler = False

        if ei_page is not None and _ei_logged_in(ei_page):
            active_page = ei_page
            print(f"[{credentials.label}] 發現既有 EI 分頁，沿用目前登入狀態")
        elif ei_page is not None:
            active_page = ei_page
            active_page.goto(EI_LOGIN_URL, wait_until="domcontentloaded")
            login_second(active_page, credentials)
            used_login_handler = True
        elif portal_page is not None:
            login_portal(portal_page, accounts)
            active_page = open_second_login(context, portal_page)
            login_second(active_page, credentials)
            used_login_handler = True
        else:
            portal_page = context.new_page()
            login_portal(portal_page, accounts)
            active_page = open_second_login(context, portal_page)
            login_second(active_page, credentials)
            used_login_handler = True

        print(f"鯨躍第一層及 {credentials.label} EI 第二層登入完成")

        if used_login_handler:
            active_page = _clean_ei_page(context, active_page)

        processed = process_pending_invoice_payloads(active_page, credentials.label)
        if processed:
            print(f"[{credentials.label}] 發票已儲存，號碼與開立時間已回填清潔異動表")
        else:
            print(f"[{credentials.label}] 沒有待匯入 Payload；保留目前鯨躍頁面")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
