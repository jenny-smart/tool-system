"""各區固定費用：富邦匯款給台北後，同步明細並完成財報分錄。"""
from __future__ import annotations

import argparse
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from playwright.sync_api import Page, sync_playwright

from tools.bank_statement.accounts import DEFAULT_ACCOUNTS_FILE, load_account
from tools.bank_statement.fubon_agent import ensure_login, run_download
from tools.bank_statement.fubon_transfer_common import (
    choose_immediate_date,
    choose_saved_destination,
    choose_source_account,
    fill_row_inputs,
    open_transfer_form,
    wait_user_completed_transfer,
)
from tools.finance_management.fixed_expense_transfer import (
    REPORT_AREA_NAMES,
    assert_plan_not_posted,
    preview_fixed_expenses,
    require_fubon_not_transferred,
    select_plan_rows,
    transfer_fixed_expenses,
    write_transfer_date,
)
from tools.invoice_center.chrome_cdp import DEFAULT_CDP_URL, connect_existing_chrome


TW_TZ = ZoneInfo("Asia/Taipei")
DESTINATION_NAME = "台北"
SUPPORTED_AREAS = ("台中", "桃園", "新竹", "高雄", "家電")


def load_transfer_plan(
    period: str, area: str, selected_rows: set[int] | None = None
) -> dict[str, object]:
    if area not in SUPPORTED_AREAS:
        raise ValueError("固定費用富邦匯款請選擇台中／桃園／新竹／高雄／家電")
    preview = preview_fixed_expenses(period, area)
    plans = preview.get("plans") or []
    if not plans:
        raise ValueError(f"{period}／{area} 第72列沒有可匯款金額")
    plan = plans[0]
    return select_plan_rows(plan, selected_rows) if selected_rows is not None else plan


def fill_fixed_expense_transfer(
    page: Page,
    bank_area: str,
    source_account: str,
    period: str,
    source_area: str,
    amount: int,
) -> None:
    page = open_transfer_form(page)
    choose_source_account(page, bank_area, source_account)
    choose_saved_destination(page, DESTINATION_NAME)
    page.wait_for_timeout(800)
    fill_row_inputs(page, "轉帳金額", [str(amount)])
    choose_immediate_date(page)
    memo = f"{period[:4]}/{period[4:]}固定費用{source_area}"
    fill_row_inputs(page, "給自己", [memo])
    fill_row_inputs(page, "給對方", [memo])
    print(f"已填入：{source_area}固定費用 NT$ {amount:,}，轉入 {DESTINATION_NAME}。")
    print("請人工核對後自行按『確認』送出（系統不會自動送出）。")


def _payment_date(completed_at: str | None) -> str:
    if completed_at:
        return datetime.strptime(completed_at[:10], "%Y-%m-%d").strftime("%Y/%m/%d")
    return datetime.now(TW_TZ).strftime("%Y/%m/%d")


def run(
    area: str,
    period: str,
    accounts_file: Path,
    cdp_url: str,
    selected_rows: set[int] | None = None,
) -> int:
    plan = load_transfer_plan(period, area, selected_rows)
    amount = int(plan["total"])
    require_fubon_not_transferred(plan, period, area)
    assert_plan_not_posted(plan)
    bank_area = REPORT_AREA_NAMES.get(area, area)
    account = load_account("fubon", bank_area, accounts_file.expanduser())

    with sync_playwright() as playwright:
        _browser, context = connect_existing_chrome(playwright, cdp_url)
        page = ensure_login(context, account)
        fill_fixed_expense_transfer(
            page, bank_area, account.bank_account, period, area, amount
        )
        completed_at = wait_user_completed_transfer(
            page,
            require_completed_at=True,
            expected_amount=str(amount),
        )
        paid_on = _payment_date(completed_at)
        try:
            source_cell = write_transfer_date(period, area, paid_on)
            print(f"富邦匯款已完成；已回填來源總表 {source_cell}：{paid_on}。")
            print(f"開始同步 {bank_area} 富邦明細。")
            today = datetime.now(TW_TZ).date()
            run_download(context, page, account, today, today)
            print(f"{bank_area} 富邦更新同步完成，開始寫入原訂固定費用分錄。")
            result = transfer_fixed_expenses(
                period,
                area=area,
                allow_unmarked=True,
                payment_date=paid_on,
                selected_rows=selected_rows,
            )
        except Exception as exc:
            raise RuntimeError(
                "富邦匯款已完成，但後續明細或財報同步失敗；請勿重複匯款，"
                f"請依日誌續處理：{exc}"
            ) from exc

    for summary in result.get("summaries") or []:
        print(summary)
    print("固定費用匯款、富邦明細與原訂分錄均已完成。")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="各區固定費用富邦匯款給台北")
    parser.add_argument("--area", required=True, choices=SUPPORTED_AREAS)
    parser.add_argument("--month", required=True)
    parser.add_argument("--accounts-file", type=Path, default=DEFAULT_ACCOUNTS_FILE)
    parser.add_argument("--cdp-url", default=DEFAULT_CDP_URL)
    parser.add_argument("--rows", help="勾選的來源列號，以逗號分隔；未提供時全選")
    args = parser.parse_args()
    selected_rows = None
    if args.rows is not None:
        selected_rows = {
            int(value) for value in args.rows.split(",") if value.strip().isdigit()
        }
        if not selected_rows:
            raise ValueError("請至少勾選一個固定費用科目")
    return run(args.area, args.month, args.accounts_file, args.cdp_url, selected_rows)


if __name__ == "__main__":
    raise SystemExit(main())
