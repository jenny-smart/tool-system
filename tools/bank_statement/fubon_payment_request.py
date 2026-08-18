from __future__ import annotations

import argparse
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from playwright.sync_api import Page, sync_playwright

from tools.bank_statement.accounts import DEFAULT_ACCOUNTS_FILE, load_account
from tools.bank_statement.fubon_agent import ensure_login
from tools.bank_statement.fubon_payment_request_filter import pending_payment_requests
from tools.bank_statement.fubon_transfer_common import (
    SavedAccountNotFound,
    choose_immediate_date,
    choose_manual_destination,
    choose_saved_destination,
    choose_source_account,
    fill_manual_destination,
    fill_row_inputs,
    open_transfer_form,
    wait_user_completed_transfer,
)
from tools.bank_statement.internal_payment_registry import (
    PAYMENT_REQUEST_TYPE,
    read_report_values,
    resolve_report_location,
)
from tools.bank_statement.open_login import current_fubon_page
from tools.common.config_loader import get_sheets_service
from tools.invoice_center.chrome_cdp import DEFAULT_CDP_URL, connect_existing_chrome


def fill_payment_request(
    page: Page, area: str, source_account: str, item: dict[str, object]
) -> None:
    page = open_transfer_form(page)
    choose_source_account(page, area, source_account)

    bank_name = str(item.get("bank_name") or "").strip()
    account_number = str(item.get("account_number") or "").strip()
    if bank_name and account_number:
        # I 欄（收款帳號）已經有可解析的銀行代碼＋帳號時，直接自行輸入，
        # 不要再去查常用轉入帳號清單。常用帳號清單那套「點開彈出視窗、
        # 比對卡片文字」的互動本身就比較不穩定（多次實機測試出現彈出視窗
        # 沒關閉、銀行代碼撞號誤點到不相干帳號等問題），I 欄既然已經明確
        # 指定要匯去哪個帳號，用它自行輸入既更可靠、也不會有選錯常用帳號
        # 的風險。只有 I 欄缺資料、真的沒有其他依據時，才退回查常用帳號。
        print(f"I 欄已提供收款帳號，直接自行輸入：{bank_name}／{account_number}")
        choose_manual_destination(page)
        # 跟 fubon_atm_refund.py 一樣：點「自行輸入」後銀行代碼下拉框是另外
        # AJAX 產生的元件，比底層 select 晚出現；choose_manual_destination
        # 只確認 select 已可用就返回，這裡少了這個緩衝會讓 fill_manual_destination
        # 太早去找還沒渲染出來的下拉框，出現「轉入帳號找不到可點開的銀行代碼
        # 下拉框」。
        page.wait_for_timeout(800)
        fill_manual_destination(page, bank_name, account_number)
    else:
        try:
            choose_saved_destination(page, str(item["name"]))
        except SavedAccountNotFound:
            raise RuntimeError(
                f"常用轉入帳號清單找不到「{item['name']}」，"
                "且 I 欄缺少可解析的銀行名稱或帳號可自行輸入"
            ) from None
    page.wait_for_timeout(800)

    fill_row_inputs(page, "轉帳金額", [str(item["amount"])])
    print(f"轉帳金額已填入：{item['amount']}")
    page.wait_for_timeout(800)

    choose_immediate_date(page)
    print("交易日期已選擇：立即。")
    page.wait_for_timeout(800)

    print("欄位已填寫完成，請人工核對後自行按「確認」送出（不會自動送出）。")


def run(area: str, rows: set[int], accounts_file: Path, cdp_url: str) -> int:
    values = read_report_values(PAYMENT_REQUEST_TYPE, area)
    candidates = pending_payment_requests(values)
    selected = [item for item in candidates if int(item["sheet_row"]) in rows]
    if not selected:
        raise ValueError("勾選列中沒有符合 A=待付款 且 F/G 欄完整的資料")

    account = load_account("fubon", area, accounts_file.expanduser())
    spreadsheet_id, sheet_title = resolve_report_location(PAYMENT_REQUEST_TYPE, area)
    service = get_sheets_service()
    with sync_playwright() as playwright:
        _browser, context = connect_existing_chrome(playwright, cdp_url)
        page = ensure_login(context, account)
        try:
            for item in selected:
                print(f"準備第 {item['sheet_row']} 列：{item['name']}／NT$ {item['amount']}")
                fill_payment_request(page, area, account.bank_account, item)
                # 每一筆都要等人工按「確認」真的送出後才回填，最後一筆也不
                # 例外——不然腳本沒等最後一筆完成就結束，A/B 欄就不會回填。
                wait_user_completed_transfer(page)
                payment_date = datetime.now(ZoneInfo("Asia/Taipei")).strftime("%Y/%m/%d")
                service.spreadsheets().values().update(
                    spreadsheetId=spreadsheet_id,
                    range=f"'{sheet_title}'!A{item['sheet_row']}:B{item['sheet_row']}",
                    valueInputOption="USER_ENTERED",
                    body={"values": [["已付款", payment_date]]},
                ).execute()
                print(f"已回填第 {item['sheet_row']} 列：已付款／{payment_date}")
                page = current_fubon_page(context, page) or page
            print("全部勾選資料均已完成付款並回填 A/B 欄。")
        except Exception:
            # 發生錯誤時保留銀行頁，方便人工確認；不登出、不關閉。
            raise
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="依請款報表準備富邦臺幣轉帳（請款記錄）")
    parser.add_argument("--area", required=True, choices=("台北", "台中"))
    parser.add_argument("--rows", required=True, help="勾選的工作表列號，以逗號分隔")
    parser.add_argument("--accounts-file", type=Path, default=DEFAULT_ACCOUNTS_FILE)
    parser.add_argument("--cdp-url", default=DEFAULT_CDP_URL)
    args = parser.parse_args()
    rows = {int(value) for value in args.rows.split(",") if value.strip().isdigit()}
    if not rows:
        raise ValueError("沒有勾選請款記錄資料")
    return run(args.area, rows, args.accounts_file, args.cdp_url)


if __name__ == "__main__":
    raise SystemExit(main())
