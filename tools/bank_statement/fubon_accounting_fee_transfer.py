"""【整合執行】會計事務所費用轉帳：台北富邦合併一筆轉給勤信，轉完寫請款記錄。

更新紀錄：
  2026-11-09 Jenny：新增。會計事務所費用確認勾選的項目合併成一筆，自行輸入勤信帳號
                    （013 國泰世華 0000240035015567），人工核對、手機核准後：
                    1. 請款記錄／台北逐項新增 A＝已付款、B＝付款日期（收款對象勤信）
                    2. 台中的費用另記請款記錄／台中 A＝待付款（收款對象台北，富邦尾數 3320）
                    3. 下載台北富邦明細（財報最後一列日期～今天）、登出
                    進銀行前再檢查一次請款記錄／台北，已有的項目就整筆不轉（避免重複付款）。
"""
from __future__ import annotations

import argparse
import json
from contextlib import ExitStack
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from playwright.sync_api import Page, sync_playwright

from tools.bank_statement.accounts import DEFAULT_ACCOUNTS_FILE, load_account
from tools.bank_statement.fubon_agent import ensure_login, logout_quietly, run_download, report_query_start
from tools.bank_statement.fubon_transfer_common import (
    choose_immediate_date,
    choose_manual_destination,
    choose_source_account,
    fill_manual_destination,
    fill_row_inputs,
    open_transfer_form,
    wait_user_completed_transfer,
)
from tools.finance_management import accounting_fee_batch as batch
from tools.invoice_center.chrome_cdp import DEFAULT_CDP_URL, connect_existing_chrome

TW_TZ = ZoneInfo("Asia/Taipei")


def load_items(text: str) -> list[dict]:
    items = [{"memo": str(i["memo"]).strip(), "category": str(i.get("category", "")),
              "amount": int(i["amount"])} for i in json.loads(text)]
    items = [i for i in items if i["memo"] and i["amount"] > 0]
    if not items:
        raise ValueError("沒有可轉帳的會計事務所費用")
    return items


def fill_transfer(page: Page, source_account: str, period: str, total: int) -> None:
    page = open_transfer_form(page)
    choose_source_account(page, batch.AREA, source_account)
    choose_manual_destination(page)
    # 點「自行輸入」後銀行代碼下拉框是另外產生的元件，要等一下才出現（同請款記錄轉帳）
    page.wait_for_timeout(800)
    fill_manual_destination(page, batch.PAYEE_BANK_CODE, batch.PAYEE_ACCOUNT)
    page.wait_for_timeout(800)
    fill_row_inputs(page, "轉帳金額", [str(total)])
    choose_immediate_date(page)
    note = batch.transfer_note(period)
    fill_row_inputs(page, "給自己", [note])
    fill_row_inputs(page, "給對方", [note])
    print(f"已填入：轉給勤信 {batch.PAYEE_BANK_CODE}-{batch.PAYEE_ACCOUNT}，NT$ {total:,}（{note}）。")
    print("請人工核對後自行按『確認』送出（系統不會自動送出）。")


def _paid_on(completed_at: str | None) -> str:
    if completed_at:
        return datetime.strptime(completed_at[:10], "%Y-%m-%d").strftime("%Y/%m/%d")
    return datetime.now(TW_TZ).strftime("%Y/%m/%d")


def run(period: str, items: list[dict], accounts_file: Path, cdp_url: str) -> int:
    batch.assert_not_paid(items)  # 進銀行前先擋重複
    total = sum(i["amount"] for i in items)
    for i in items:
        print(f"・{i['memo']}：{i['amount']:,}")
    print(f"合計 {len(items)} 項，合併一筆 NT$ {total:,}")
    account = load_account("fubon", batch.AREA, accounts_file.expanduser())

    with sync_playwright() as playwright, ExitStack() as cleanup:
        _browser, context = connect_existing_chrome(playwright, cdp_url)
        page = ensure_login(context, account)
        cleanup.callback(logout_quietly, context, page)  # 轉帳／下載做完（成功或失敗）一律登出
        fill_transfer(page, account.bank_account, period, total)
        completed_at = wait_user_completed_transfer(page, require_completed_at=True,
                                                    expected_amount=str(total),
                                                    expected_account=batch.PAYEE_ACCOUNT)
        paid_on = _paid_on(completed_at)
        try:
            batch.record_paid(period, items, paid_on, log=print)
        except Exception as exc:
            raise RuntimeError("富邦轉帳已完成，但請款記錄寫入失敗；請勿重複轉帳，"
                               f"請手動在請款記錄／台北補已付款（{paid_on}）、台中補待付款：{exc}") from exc
        print("開始下載台北富邦明細（財報最後一列日期～今天）。")
        today = datetime.now(TW_TZ).date()
        run_download(context, page, account, report_query_start(account, today), today)
    print("會計事務所費用轉帳、請款記錄與富邦明細均已完成。")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="會計事務所費用：台北富邦合併一筆轉給勤信")
    parser.add_argument("--period", required=True, help="YYYYMM")
    parser.add_argument("--items", required=True, help='JSON：[{"memo": "...", "amount": 8800}]')
    parser.add_argument("--accounts-file", type=Path, default=DEFAULT_ACCOUNTS_FILE)
    parser.add_argument("--cdp-url", default=DEFAULT_CDP_URL)
    args = parser.parse_args()
    return run(args.period, load_items(args.items), args.accounts_file, args.cdp_url)


if __name__ == "__main__":
    raise SystemExit(main())
