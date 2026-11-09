"""各區固定費用：富邦匯款給台北後，同步明細並完成財報分錄。

更新紀錄：
  2026-11-09 Jenny：台中匯款完成後，逐科目記到請款記錄／台中（已付款＋付款日期，收款對象檸檬台北；
                    D＝富邦更新 I 欄、E＝L 欄）。寫入失敗只警告，不影響已完成的匯款與分錄。
  2026-11-09 Jenny：--allow-posted：分錄已先用「新增分錄」寫好時也能匯款（只靠第74列擋重複匯款）；
                    匯款後補寫分錄照樣只補缺的列。
"""
from __future__ import annotations

from contextlib import ExitStack

import argparse
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from playwright.sync_api import Page, sync_playwright

from tools.bank_statement.accounts import DEFAULT_ACCOUNTS_FILE, load_account
from tools.bank_statement.fubon_agent import ensure_login, logout_quietly, run_download, report_query_start
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
SUPPORTED_AREAS = ("台中", "桃園", "新竹", "高雄")  # 學院自己轉帳給台北，不經富邦匯款


def load_transfer_plan(
    period: str, area: str, selected_rows: set[int] | None = None
) -> dict[str, object]:
    if area not in SUPPORTED_AREAS:
        raise ValueError("固定費用富邦匯款請選擇台中／桃園／新竹／高雄（學院自己轉帳，只要寫台北分錄）")
    # 2026-11-09：有勾選科目時照勾選列重算（盤點列出的 41～71 列都能選）
    preview = preview_fixed_expenses(period, area, lenient=selected_rows is not None)
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
    allow_posted: bool = False,
) -> int:
    plan = load_transfer_plan(period, area, selected_rows)
    amount = int(plan["total"])
    require_fubon_not_transferred(plan, period, area)
    if allow_posted:
        print("分錄可能已先新增，只靠第74列（匯款日期）擋重複匯款。")
    else:
        assert_plan_not_posted(plan)
    bank_area = REPORT_AREA_NAMES.get(area, area)
    account = load_account("fubon", bank_area, accounts_file.expanduser())

    with sync_playwright() as playwright, ExitStack() as cleanup:
        _browser, context = connect_existing_chrome(playwright, cdp_url)
        page = ensure_login(context, account)
        cleanup.callback(logout_quietly, context, page)  # 轉帳／下載做完（成功或失敗）一律登出
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
            from tools.finance_management.fixed_expense_transfer import FIXED_AMOUNT_AREAS

            if area in FIXED_AMOUNT_AREAS and not FIXED_AMOUNT_AREAS[area][2]:
                # 2026-11-05 Jenny：高雄不跑富邦更新，只寫台北分錄
                print(f"{bank_area} 不跑富邦更新，直接寫入台北固定費用分錄。")
            else:
                print(f"開始同步 {bank_area} 富邦明細。")
                today = datetime.now(TW_TZ).date()
                run_download(context, page, account, report_query_start(account, today), today)
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

        try:
            if plan.get("fixed_amount"):
                source_cell = ""
                print(f"{area} 是每月固定金額，沒有行銷費用總表第74列可回填。")
            else:
                source_cell = write_transfer_date(period, area, paid_on)
                print(f"已回填來源總表 {source_cell}：{paid_on}。")
        except Exception as exc:
            # 匯款、明細及財報已完成時，不可因來源表權限問題讓使用者誤以為
            # 整批失敗而再次匯款；既有財報分錄仍會在下次銀行預檢時擋重複。
            print(f"警告：匯款及後續分錄已完成，但第74列日期回填失敗：{exc}")

        try:
            from tools.finance_management.fixed_expense_transfer import REQUEST_AREAS, write_request_rows

            if area in REQUEST_AREAS:
                print(f"請款記錄／{area} 新增 {write_request_rows(plan, paid_on)} 筆（已付款 {paid_on}）。")
        except Exception as exc:
            print(f"警告：匯款及分錄已完成，但請款記錄／{area} 寫入失敗，請手動補：{exc}")

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
    parser.add_argument("--allow-posted", action="store_true", help="分錄已先新增時仍可匯款（只看第74列擋重複）")
    args = parser.parse_args()
    selected_rows = None
    if args.rows is not None:
        selected_rows = {
            int(value) for value in args.rows.split(",") if value.strip().isdigit()
        }
        if not selected_rows:
            raise ValueError("請至少勾選一個固定費用科目")
    return run(args.area, args.month, args.accounts_file, args.cdp_url, selected_rows, args.allow_posted)


if __name__ == "__main__":
    raise SystemExit(main())
