from __future__ import annotations

import argparse
from pathlib import Path

from playwright.sync_api import Page, sync_playwright

from tools.bank_statement.accounts import DEFAULT_ACCOUNTS_FILE, load_account
from tools.bank_statement.fubon_agent import ensure_login
from tools.bank_statement.fubon_supply_purchase_filter import pending_supply_purchases
from tools.bank_statement.fubon_transfer_common import (
    choose_immediate_date,
    choose_manual_destination,
    choose_source_account,
    fill_manual_destination,
    fill_row_inputs,
    open_transfer_form,
    wait_user_completed_transfer,
)
from tools.bank_statement.internal_payment_registry import (
    SUPPLY_PURCHASE_TYPE,
    read_report_values,
)
from tools.bank_statement.open_login import current_fubon_page
from tools.invoice_center.chrome_cdp import DEFAULT_CDP_URL, connect_existing_chrome

# 目前假設：轉入帳號直接用 N/O 欄自行輸入（不先查常用轉入帳號清單），
# 交易日期選立即，給自己／給對方留空，且跟另外兩支一樣不自動按確認。
# 如果實際測試發現應該不一樣，請告知再調整。


def fill_supply_purchase(
    page: Page, area: str, source_account: str, item: dict[str, object]
) -> None:
    page = open_transfer_form(page)
    choose_source_account(page, area, source_account)

    choose_manual_destination(page)
    page.wait_for_timeout(800)

    fill_manual_destination(
        page, str(item["bank_code"]), str(item["account_number"])
    )
    print(f"轉入帳號已填入：{item['bank_code']}／{item['account_number']}")
    page.wait_for_timeout(800)

    fill_row_inputs(page, "轉帳金額", [str(item["amount"])])
    print(f"轉帳金額已填入：{item['amount']}")
    page.wait_for_timeout(800)

    choose_immediate_date(page)
    print("交易日期已選擇：立即。")
    page.wait_for_timeout(800)

    print("欄位已填寫完成，請人工核對後自行按「確認」送出（不會自動送出）。")


def run(area: str, month: str, rows: set[int], accounts_file: Path, cdp_url: str) -> int:
    values = read_report_values(SUPPLY_PURCHASE_TYPE, area)
    candidates = pending_supply_purchases(values, month)
    selected = [item for item in candidates if int(item["sheet_row"]) in rows]
    if not selected:
        raise ValueError("勾選列中沒有符合採購月份、且 N/O 欄皆非空白的資料")

    account = load_account("fubon", area, accounts_file.expanduser())
    with sync_playwright() as playwright:
        _browser, context = connect_existing_chrome(playwright, cdp_url)
        page = ensure_login(context, account)
        try:
            for index, item in enumerate(selected):
                rows_desc = "、".join(str(r) for r in item["rows"])
                print(
                    f"準備第 {rows_desc} 列（{item['supplier']}）／"
                    f"{item['bank_code']}／{item['account_number']}／NT$ {item['amount']}"
                )
                fill_supply_purchase(page, area, account.bank_account, item)
                if index + 1 < len(selected):
                    wait_user_completed_transfer(page)
                    page = current_fubon_page(context, page) or page
            print("全部勾選資料均已準備完成；每一筆都請人工核對並自行按「確認」送出。")
        except Exception:
            # 發生錯誤時保留銀行頁，方便人工確認；不登出、不關閉。
            raise
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="依清潔用品採購報表準備富邦臺幣轉帳")
    parser.add_argument("--area", required=True, choices=("台北", "台中"))
    parser.add_argument("--month", required=True, help="採購月份，例如 2026/08")
    parser.add_argument("--rows", required=True, help="勾選的工作表列號，以逗號分隔")
    parser.add_argument("--accounts-file", type=Path, default=DEFAULT_ACCOUNTS_FILE)
    parser.add_argument("--cdp-url", default=DEFAULT_CDP_URL)
    args = parser.parse_args()
    rows = {int(value) for value in args.rows.split(",") if value.strip().isdigit()}
    if not rows:
        raise ValueError("沒有勾選清潔用品採購資料")
    return run(args.area, args.month, rows, args.accounts_file, args.cdp_url)


if __name__ == "__main__":
    raise SystemExit(main())
