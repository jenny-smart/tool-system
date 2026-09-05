from __future__ import annotations

import argparse
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from googleapiclient.errors import HttpError
from playwright.sync_api import Page, sync_playwright

from tools.bank_statement.accounts import DEFAULT_ACCOUNTS_FILE, load_account
from tools.bank_statement.fubon_agent import ensure_login
from tools.bank_statement.fubon_deposit_refund_filter import pending_deposit_refunds
from tools.bank_statement.fubon_transfer_common import (
    choose_immediate_date,
    choose_saved_destination,
    choose_source_account,
    fill_row_inputs,
    open_transfer_form,
    wait_user_completed_transfer,
)
from tools.bank_statement.internal_payment_registry import (
    DEPOSIT_REFUND_TYPE,
    read_destination_name,
    read_report_values,
    resolve_report_location,
)
from tools.bank_statement.open_login import current_fubon_page
from tools.common.config_loader import get_sheets_service
from tools.invoice_center.chrome_cdp import DEFAULT_CDP_URL, connect_existing_chrome


def fill_deposit_refund(
    page: Page,
    area: str,
    source_account: str,
    destination_name: str,
    item: dict[str, object],
) -> None:
    page = open_transfer_form(page)
    choose_source_account(page, area, source_account)

    choose_saved_destination(page, destination_name)
    page.wait_for_timeout(800)

    fill_row_inputs(page, "轉帳金額", [str(item["amount"])])
    print(f"轉帳金額已填入：{item['amount']}")
    page.wait_for_timeout(800)

    choose_immediate_date(page)
    print("交易日期已選擇：立即。")
    page.wait_for_timeout(800)

    fill_row_inputs(page, "給自己", [str(item["memo"])])
    fill_row_inputs(page, "給對方", [str(item["memo"])])
    print("留言欄位已填寫完成。")
    page.wait_for_timeout(800)

    print("欄位已填寫完成，請人工核對後自行按「確認」送出（不會自動送出）。")


def _writeback_range(sheet_title: str, row: int) -> str:
    return f"'{sheet_title}'!S{row}"


def verify_writeback_access(
    service,
    spreadsheet_id: str,
    sheet_title: str,
    selected: list[dict[str, object]],
) -> None:
    """在進銀行前先確認所有待退款列的 S 欄可寫，避免退款後才遇到 403。"""
    for item in selected:
        row = int(item["sheet_row"])
        cell_range = _writeback_range(sheet_title, row)
        formula_res = service.spreadsheets().values().get(
            spreadsheetId=spreadsheet_id,
            range=cell_range,
            valueRenderOption="FORMULA",
        ).execute()
        values = formula_res.get("values", [])
        raw_value = str(values[0][0]).strip() if values and values[0] else ""
        if raw_value:
            raise ValueError(f"第 {row} 列 S 欄已有值，已停止避免重複退款")

        # pending 資料的 S 欄必須為空白；寫回同樣的空白只用來驗證實際寫入權限，
        # 不改變有效資料。若服務帳號只有檢視權限，會在任何銀行交易前先 403。
        service.spreadsheets().values().update(
            spreadsheetId=spreadsheet_id,
            range=cell_range,
            valueInputOption="USER_ENTERED",
            body={"values": [[""]]},
        ).execute()
    print(f"已確認 {len(selected)} 筆 S 欄具備寫入權限。")


def write_payment_date(
    service,
    spreadsheet_id: str,
    sheet_title: str,
    row: int,
    payment_date: str,
) -> None:
    try:
        service.spreadsheets().values().update(
            spreadsheetId=spreadsheet_id,
            range=_writeback_range(sheet_title, row),
            valueInputOption="USER_ENTERED",
            body={"values": [[payment_date]]},
        ).execute()
    except HttpError as exc:
        raise RuntimeError(
            f"第 {row} 列退款已在富邦完成，但 S 欄日期回填失敗；"
            "已停止，不會執行下一筆，請先人工確認此筆避免重複退款。"
        ) from exc


def run(area: str, rows: set[int], accounts_file: Path, cdp_url: str) -> int:
    values = read_report_values(DEPOSIT_REFUND_TYPE, area)
    candidates = pending_deposit_refunds(values)
    selected = [item for item in candidates if int(item["sheet_row"]) in rows]
    if not selected:
        raise ValueError("勾選列中沒有符合 H>0 且 S 欄空白的資料")

    # 保持工作表原列順序，支援一次勾選多筆並逐筆完成。
    selected.sort(key=lambda item: int(item["sheet_row"]))

    destination_name = read_destination_name(DEPOSIT_REFUND_TYPE, area)
    account = load_account("fubon", area, accounts_file.expanduser())
    spreadsheet_id, sheet_title = resolve_report_location(DEPOSIT_REFUND_TYPE, area)
    service = get_sheets_service()

    # 先驗證每一筆 S 欄都能實際寫入。若權限不足，銀行端完全不會開始退款。
    verify_writeback_access(service, spreadsheet_id, sheet_title, selected)

    with sync_playwright() as playwright:
        _browser, context = connect_existing_chrome(playwright, cdp_url)
        page = ensure_login(context, account)
        try:
            for item in selected:
                row = int(item["sheet_row"])
                print(f"準備第 {row} 列：NT$ {item['amount']}／備註：{item['memo']}")
                fill_deposit_refund(page, area, account.bank_account, destination_name, item)

                # 每筆都要確認富邦完成頁，而且金額必須對應本筆；成功後立即回填
                # S 欄，回填成功才會進下一筆，避免多筆流程中發生重複退款。
                completed_at = wait_user_completed_transfer(
                    page,
                    require_completed_at=True,
                    expected_amount=str(item["amount"]),
                )
                if completed_at:
                    payment_date = datetime.strptime(
                        completed_at[:10], "%Y-%m-%d"
                    ).strftime("%Y/%m/%d")
                else:
                    payment_date = datetime.now(ZoneInfo("Asia/Taipei")).strftime("%Y/%m/%d")

                write_payment_date(
                    service,
                    spreadsheet_id,
                    sheet_title,
                    row,
                    payment_date,
                )
                print(f"已回填第 {row} 列 S 欄：{payment_date}")
                page = current_fubon_page(context, page) or page
            print("全部勾選資料均已完成付款並回填 S 欄。")
        except Exception:
            # 發生錯誤時保留銀行頁，方便人工確認；不登出、不關閉。
            raise
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="依工具包押金退款報表準備富邦臺幣轉帳")
    parser.add_argument("--area", required=True, choices=("台北", "台中"))
    parser.add_argument("--rows", required=True, help="勾選的工作表列號，以逗號分隔")
    parser.add_argument("--accounts-file", type=Path, default=DEFAULT_ACCOUNTS_FILE)
    parser.add_argument("--cdp-url", default=DEFAULT_CDP_URL)
    args = parser.parse_args()
    rows = {int(value) for value in args.rows.split(",") if value.strip().isdigit()}
    if not rows:
        raise ValueError("沒有勾選工具包押金退款資料")
    return run(args.area, rows, args.accounts_file, args.cdp_url)


if __name__ == "__main__":
    raise SystemExit(main())
