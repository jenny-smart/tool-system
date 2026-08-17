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


def _month_display(month: str) -> str:
    """把 YYYYMM 轉成留言用的月份顯示（取月份兩碼，例如 "202608" -> "08"）。"""
    digits = "".join(ch for ch in month if ch.isdigit())
    return digits[-2:] if len(digits) >= 2 else digits


def fill_supply_purchase(
    page: Page, area: str, source_account: str, month: str, item: dict[str, object]
) -> None:
    page = open_transfer_form(page)
    choose_source_account(page, area, source_account)

    # N／O 欄（匯款銀行、帳號）本來就是這份報表的必填欄位，一律有值，
    # 不需要也不要再去查常用轉入帳號清單：那套「點開彈出視窗、比對卡片
    # 文字」的互動本身比較不穩定（多次實機測試出現彈出視窗沒關閉、銀行
    # 代碼撞號誤點到不相干帳號等問題），既然報表已經明確指定銀行代碼與
    # 帳號，直接自行輸入更可靠、也不會有選錯常用帳號的風險。
    choose_manual_destination(page)
    fill_manual_destination(page, str(item["bank_code"]), str(item["account_number"]))
    print(f"轉入帳號已填入：{item['bank_code']}／{item['account_number']}")
    page.wait_for_timeout(800)

    fill_row_inputs(page, "轉帳金額", [str(item["amount"])])
    print(f"轉帳金額已填入：{item['amount']}")
    page.wait_for_timeout(800)

    choose_immediate_date(page)
    print("交易日期已選擇：立即。")
    page.wait_for_timeout(800)

    month_display = _month_display(month)
    fill_row_inputs(page, "給自己", [f"{month_display}月清潔用品"])
    fill_row_inputs(page, "給對方", [f"{month_display}月檸檬家事"])
    print("留言欄位已填寫完成。")
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
                fill_supply_purchase(page, area, account.bank_account, month, item)
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
