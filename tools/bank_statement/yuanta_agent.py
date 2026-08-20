from __future__ import annotations

import argparse
import csv
from datetime import datetime
from pathlib import Path

from playwright.sync_api import sync_playwright

from tools.bank_statement.accounts import DEFAULT_ACCOUNTS_FILE, load_account
from tools.bank_statement.native_yuanta import (
    YUANTA_HOST,
    YUANTA_URL,
    bind_yuanta_page,
    configure_yuanta_query,
    logout_yuanta,
    open_yuanta_statement_menu,
    wait_for_yuanta_login,
    wait_for_yuanta_statement,
)
from tools.bank_statement.sheet_filter import (
    read_and_filter,
    sync_financial_report,
    sync_yuanta_master_sheet,
)
from tools.invoice_center.chrome_cdp import connect_existing_chrome


YUANTA_ACCOUNT_OVERRIDES = {
    "台北": "21022000300985",
}


def parse_date(value: str):
    return datetime.strptime(value.replace("/", "-"), "%Y-%m-%d").date()


def save_csv(table, target: Path) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    with target.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(table.headers)
        writer.writerows(table.rows)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="沿用既有 Chrome 執行元大登入與明細查詢")
    parser.add_argument("mode", choices=("login", "download"))
    parser.add_argument("--area", required=True, choices=("台北", "台中", "桃園", "新竹", "高雄"))
    parser.add_argument("--start", type=parse_date)
    parser.add_argument("--end", type=parse_date)
    parser.add_argument("--accounts-file", type=Path, default=DEFAULT_ACCOUNTS_FILE)
    parser.add_argument("--cdp-url", default="http://127.0.0.1:9222")
    parser.add_argument("--output", type=Path)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    account = load_account("yuanta", args.area, args.accounts_file.expanduser())
    print(f"區域：{args.area}")
    with sync_playwright() as playwright:
        _browser, context = connect_existing_chrome(playwright, args.cdp_url)
        pages = [page for page in context.pages if not page.is_closed() and YUANTA_HOST in page.url]
        page = pages[-1] if pages else context.new_page()
        if YUANTA_HOST not in page.url:
            page.goto(YUANTA_URL, wait_until="domcontentloaded", timeout=60_000)
        bind_yuanta_page(page)
        print(f"瀏覽器：沿用 Agent Chrome（{args.cdp_url}）")
        wait_for_yuanta_login(account)
        if args.mode == "login":
            print(f"元大／{args.area} 登入完成；沿用 Agent Chrome 工作階段。")
            return 0
        try:
            if not args.start or not args.end:
                raise ValueError("元大明細查詢缺少開始日期或結束日期")
            account_number = YUANTA_ACCOUNT_OVERRIDES.get(args.area) or account.bank_account
            if not account_number:
                raise ValueError(f"bank_accounts.json 尚未設定元大／{args.area} 的 bank_account")
            open_yuanta_statement_menu()
            configure_yuanta_query(account_number, args.start, args.end)
            table = wait_for_yuanta_statement()
            if args.output:
                save_csv(table, args.output.expanduser())
                print(f"RESULT_FILE:{args.output.expanduser().resolve()}")
            new_table = read_and_filter(table, args.area, "yuanta")
            written = sync_yuanta_master_sheet(new_table, args.area)
            report_written = sync_financial_report(new_table, args.area, "yuanta")
            print(
                f"元大明細完成：區域 {args.area}；日期 {args.start:%Y/%m/%d}～{args.end:%Y/%m/%d}；"
                f"查詢 {len(table.rows)} 筆；新增 {len(new_table.rows)} 筆；"
                f"工作表寫入 {written} 筆；財報寫入 {report_written} 筆。"
            )
        finally:
            try:
                logout_yuanta()
            finally:
                if not page.is_closed():
                    page.close()
                    print("元大視窗已關閉。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
