from __future__ import annotations

import argparse
from pathlib import Path

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
)
from tools.bank_statement.open_login import current_fubon_page
from tools.invoice_center.chrome_cdp import DEFAULT_CDP_URL, connect_existing_chrome


def fill_payment_request(
    page: Page, area: str, source_account: str, item: dict[str, object]
) -> None:
    page = open_transfer_form(page)
    choose_source_account(page, area, source_account)

    try:
        choose_saved_destination(page, str(item["name"]))
    except SavedAccountNotFound:
        bank_name = str(item.get("bank_name") or "").strip()
        account_number = str(item.get("account_number") or "").strip()
        if not bank_name or not account_number:
            raise RuntimeError(
                f"常用轉入帳號清單找不到「{item['name']}」，"
                "且 I 欄缺少可解析的銀行名稱或帳號可自行輸入"
            ) from None
        print(f"常用轉入帳號清單找不到「{item['name']}」，改用自行輸入：{bank_name}／{account_number}")
        choose_manual_destination(page)
        fill_manual_destination(page, bank_name, account_number)
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
    with sync_playwright() as playwright:
        _browser, context = connect_existing_chrome(playwright, cdp_url)
        page = ensure_login(context, account)
        try:
            for index, item in enumerate(selected):
                print(f"準備第 {item['sheet_row']} 列：{item['name']}／NT$ {item['amount']}")
                fill_payment_request(page, area, account.bank_account, item)
                if index + 1 < len(selected):
                    wait_user_completed_transfer(page)
                    page = current_fubon_page(context, page) or page
            print("全部勾選資料均已準備完成；每一筆都請人工核對並自行按「確認」送出。")
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
