from __future__ import annotations

import argparse
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from playwright.sync_api import Page, sync_playwright

from tools.bank_statement.accounts import DEFAULT_ACCOUNTS_FILE, load_account
from tools.bank_statement.fubon_agent import ensure_login, run_download
from tools.bank_statement.fubon_supply_purchase_filter import pending_supply_purchases
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
    SUPPLY_PURCHASE_TYPE,
    read_report_values,
    resolve_report_location,
)
from tools.common.config_loader import get_sheets_service
from tools.bank_statement.open_login import current_fubon_page
from tools.invoice_center.chrome_cdp import DEFAULT_CDP_URL, connect_existing_chrome


def _month_display(month: str) -> str:
    """把 YYYYMM 轉成留言用的月份顯示（取月份兩碼，例如 "202608" -> "08"）。"""
    digits = "".join(ch for ch in month if ch.isdigit())
    return digits[-2:] if len(digits) >= 2 else digits


def _choose_supply_purchase_destination(page: Page, item: dict[str, object]) -> None:
    """優先使用與廠商同名的常用轉入帳號，找不到才依報表自行輸入。"""
    supplier = str(item.get("supplier") or "").strip()
    if supplier:
        try:
            choose_saved_destination(page, supplier)
            print(f"已找到同名常用轉入帳號：{supplier}")
            return
        except SavedAccountNotFound:
            print(f"常用轉入帳號找不到同名廠商「{supplier}」，改用報表帳號自行輸入。")
    else:
        print("廠商名稱空白，改用報表帳號自行輸入。")

    choose_manual_destination(page)
    # 點「自行輸入」後，富邦會以 AJAX 建立銀行代碼下拉元件；稍候再填值，
    # 避免剛從常用帳號彈窗退回時抓到尚未完成更新的舊元件。
    page.wait_for_timeout(800)
    fill_manual_destination(page, str(item["bank_code"]), str(item["account_number"]))
    print(f"轉入帳號已填入：{item['bank_code']}／{item['account_number']}")


def fill_supply_purchase(
    page: Page, area: str, source_account: str, month: str, item: dict[str, object]
) -> None:
    page = open_transfer_form(page)
    choose_source_account(page, area, source_account)

    _choose_supply_purchase_destination(page, item)
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

    spreadsheet_id, sheet_title = resolve_report_location(SUPPLY_PURCHASE_TYPE, area)
    service = get_sheets_service()
    account = load_account("fubon", area, accounts_file.expanduser())
    with sync_playwright() as playwright:
        _browser, context = connect_existing_chrome(playwright, cdp_url)
        page = ensure_login(context, account)
        try:
            for item in selected:
                rows_desc = "、".join(str(r) for r in item["rows"])
                print(
                    f"準備第 {rows_desc} 列（{item['supplier']}）／"
                    f"{item['bank_code']}／{item['account_number']}／NT$ {item['amount']}"
                )
                fill_supply_purchase(page, area, account.bank_account, month, item)
                completed_at = wait_user_completed_transfer(
                    page,
                    require_completed_at=True,
                    expected_amount=str(item["amount"]),
                    expected_account=str(item["account_number"]),
                )
                payment_date = datetime.strptime(
                    completed_at[:10], "%Y-%m-%d"
                ).strftime("%Y/%m/%d")
                escaped_title = sheet_title.replace("'", "''")
                try:
                    service.spreadsheets().values().batchUpdate(
                        spreadsheetId=spreadsheet_id,
                        body={
                            "valueInputOption": "USER_ENTERED",
                            "data": [
                                {
                                    "range": f"'{escaped_title}'!Q{row}",
                                    "values": [[payment_date]],
                                }
                                for row in item["rows"]
                            ],
                        },
                    ).execute()
                except Exception as exc:
                    raise RuntimeError(
                        f"第 {rows_desc} 列付款已完成，但 Q 欄日期回填失敗；"
                        "已停止，請人工確認，勿重複付款。"
                    ) from exc
                print(f"已回填第 {rows_desc} 列 Q 欄：{payment_date}")
                page = current_fubon_page(context, page) or page
            print("全部勾選資料均已完成付款並回填 Q 欄。")
            print("開始下載今日富邦明細。")
            today = datetime.now(ZoneInfo("Asia/Taipei")).date()
            page = run_download(context, page, account, today, today)
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
