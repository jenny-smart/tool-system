from __future__ import annotations

import argparse
from pathlib import Path

from playwright.sync_api import sync_playwright

from tools.bank_statement.accounts import DEFAULT_ACCOUNTS_FILE, load_account
from tools.bank_statement.native_yuanta import (
    YUANTA_HOST,
    YUANTA_URL,
    bind_yuanta_page,
    collect_yuanta_failed_salary_rows,
    logout_yuanta,
    open_yuanta_salary_status,
    query_yuanta_salary_last_week,
    wait_for_yuanta_login,
)
from tools.bank_statement.sheet_filter import sync_yuanta_salary_status
from tools.invoice_center.chrome_cdp import connect_existing_chrome


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="檢查元大最近一週薪資付款失敗明細")
    parser.add_argument("--area", required=True, choices=("台北", "台中", "桃園", "新竹", "高雄"))
    parser.add_argument("--accounts-file", default=str(DEFAULT_ACCOUNTS_FILE))
    parser.add_argument("--cdp-url", default="http://127.0.0.1:9222")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    account = load_account("yuanta", args.area, Path(args.accounts_file).expanduser())
    with sync_playwright() as playwright:
        _browser, context = connect_existing_chrome(playwright, args.cdp_url)
        pages = [page for page in context.pages if not page.is_closed() and YUANTA_HOST in page.url]
        page = pages[-1] if pages else context.new_page()
        if YUANTA_HOST not in page.url:
            page.goto(YUANTA_URL, wait_until="domcontentloaded", timeout=60_000)
        bind_yuanta_page(page)
        print(f"瀏覽器：沿用 Agent Chrome（{args.cdp_url}）")
        wait_for_yuanta_login(account)
        try:
            open_yuanta_salary_status()
            query_yuanta_salary_last_week()
            checked, failed = collect_yuanta_failed_salary_rows()
            output = [[args.area, *row] for row in failed]
            written = sync_yuanta_salary_status(output)
            print(f"元大薪資付款狀態完成：區域 {args.area}；檢查 {checked} 批；交易失敗 {len(failed)} 筆；寫入 {written} 筆。")
        finally:
            try:
                logout_yuanta()
            finally:
                if not page.is_closed():
                    page.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
