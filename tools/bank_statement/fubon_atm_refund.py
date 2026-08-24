from __future__ import annotations

import argparse
from pathlib import Path

from playwright.sync_api import Page, sync_playwright

from tools.bank_statement.accounts import DEFAULT_ACCOUNTS_FILE, load_account
from tools.bank_statement.fubon_agent import ensure_login
from tools.bank_statement.fubon_refund_filter import pending_atm_refunds
from tools.bank_statement.fubon_transfer_common import (
    choose_immediate_date,
    choose_manual_destination,
    choose_source_account,
    click_text,
    fill_manual_destination,
    fill_row_inputs,
    open_transfer_form,
    wait_confirmation,
    wait_user_completed_transfer,
)
from tools.bank_statement.open_login import current_fubon_page
from tools.invoice_center.chrome_cdp import DEFAULT_CDP_URL, connect_existing_chrome
from tools.memo_system.change_order import get_worksheet


def fill_refund(
    page: Page, area: str, source_account: str, item: dict[str, object]
) -> None:
    page = open_transfer_form(page)
    choose_source_account(page, area, source_account)

    choose_manual_destination(page)
    print("轉入帳號已切換為自行輸入。")
    page.wait_for_timeout(800)

    fill_manual_destination(
        page, str(item["bank_code"]), str(item["account_number"])
    )
    print(f"轉入帳號已填入：P={item['bank_code']}／Q={item['account_number']}")
    page.wait_for_timeout(800)

    fill_row_inputs(page, "轉帳金額", [str(item["amount"])])
    print(f"轉帳金額已填入：{item['amount']}")
    page.wait_for_timeout(800)

    choose_immediate_date(page)
    print("交易日期已選擇：立即。")
    page.wait_for_timeout(800)

    fill_row_inputs(page, "給自己", [f"清潔{item['customer']}退"])
    fill_row_inputs(page, "給對方", ["檸檬家事"])
    print("留言欄位已填寫完成。")
    page.wait_for_timeout(800)

    click_text(page, "確認", exact=True)
    print("已點擊確認，等待進入確認資料頁。")
    wait_confirmation(page)


def run(area: str, rows: set[int], accounts_file: Path, cdp_url: str) -> int:
    worksheet = get_worksheet(area)
    candidates = pending_atm_refunds(worksheet.get_all_values())
    selected = [item for item in candidates if int(item["sheet_row"]) in rows]
    if not selected:
        raise ValueError("勾選列中沒有符合 B=待退款、R=ATM 且 P/Q/T 完整的資料")

    zero_amount = [item for item in selected if item["amount"] == "0"]
    if zero_amount:
        rows_desc = "、".join(
            f"第{item['sheet_row']}列（{item['customer']}）" for item in zero_amount
        )
        print(f"⚠️ 轉帳金額（T欄）為 0，已略過：{rows_desc}")
    selected = [item for item in selected if item["amount"] != "0"]
    if not selected:
        raise ValueError("勾選列的轉帳金額（T欄）皆為 0，請先確認金額後再執行")

    account = load_account("fubon", area, accounts_file.expanduser())
    with sync_playwright() as playwright:
        _browser, context = connect_existing_chrome(playwright, cdp_url)
        page = ensure_login(context, account)
        try:
            for item in selected:
                print(
                    f"準備第 {item['sheet_row']} 列：{item['customer']}／"
                    f"P={item['bank_code']}／Q={item['account_number']}／NT$ {item['amount']}"
                )
                fill_refund(page, area, account.bank_account, item)
                print("已進入富邦確認資料頁。")
                # 每一筆都要等人工完成最終交易並驗證成功後才回寫，最後一筆也不例外。
                wait_user_completed_transfer(page)
                worksheet.update_cell(int(item["sheet_row"]), 2, "已退款")
                print(f"已回寫第 {item['sheet_row']} 列狀態：已退款")
                page = current_fubon_page(context, page) or page
            print("全部勾選資料均已完成退款並回寫 B 欄。")
        except Exception:
            # 發生錯誤時保留銀行頁，方便人工確認；不登出、不關閉。
            raise
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="依清潔異動工作表準備富邦 ATM 退款")
    parser.add_argument("--area", required=True, choices=("台北", "台中"))
    parser.add_argument("--rows", required=True, help="勾選的工作表列號，以逗號分隔")
    parser.add_argument("--accounts-file", type=Path, default=DEFAULT_ACCOUNTS_FILE)
    parser.add_argument("--cdp-url", default=DEFAULT_CDP_URL)
    args = parser.parse_args()
    rows = {int(value) for value in args.rows.split(",") if value.strip().isdigit()}
    if not rows:
        raise ValueError("沒有勾選 ATM 退款資料")
    return run(args.area, rows, args.accounts_file, args.cdp_url)


if __name__ == "__main__":
    raise SystemExit(main())
