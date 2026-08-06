from __future__ import annotations

import argparse
import csv
import time
from pathlib import Path

from playwright.sync_api import BrowserContext, Page, sync_playwright

from tools.bank_statement.accounts import DEFAULT_ACCOUNTS_FILE, BankAccount, load_account
from tools.bank_statement.capture import CapturedTable, copy_to_clipboard, find_fubon_statement
from tools.bank_statement.open_login import (
    LOGIN_URLS,
    current_fubon_page,
    fill_fubon,
    has_visible_text,
    is_fubon_logged_in,
    open_fubon_statement,
    parse_date,
    wait_fubon_login,
)
from tools.bank_statement.sheet_filter import read_and_filter, sync_fubon_master_sheet
from tools.invoice_center.chrome_cdp import DEFAULT_CDP_URL, connect_existing_chrome


def existing_page(context: BrowserContext) -> Page | None:
    pages = [page for page in context.pages if "ebank.taipeifubon.com.tw" in page.url]
    logged_in = next((page for page in reversed(pages) if is_fubon_logged_in(page)), None)
    return logged_in or (pages[-1] if pages else None)


def ensure_login(context: BrowserContext, account: BankAccount) -> Page:
    page = existing_page(context) or context.new_page()
    if is_fubon_logged_in(page):
        print("偵測到富邦已登入，沿用 Agent Chrome 工作階段。")
        return page
    last_error: Exception | None = None
    for attempt in range(2):
        page.goto(LOGIN_URLS["fubon"], wait_until="domcontentloaded", timeout=30_000)
        try:
            fill_fubon(page, account)
            last_error = None
            break
        except Exception as exc:
            last_error = exc
            print(f"富邦登入預填重試 {attempt + 1}/2：{exc}")
            page.reload(wait_until="domcontentloaded", timeout=30_000)
            page.wait_for_timeout(500)
    if last_error is not None:
        raise last_error
    print("富邦帳密已預填，請在 Agent Chrome 輸入驗證碼並登入。")
    return wait_fubon_login(context, page)


def wait_statement(page: Page, timeout_seconds: int = 60) -> CapturedTable:
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        table = find_fubon_statement(page)
        if table is not None:
            return table
        page.wait_for_timeout(500)
    raise RuntimeError("等待富邦交易明細表逾時")


def save_csv(table: CapturedTable, target: Path) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    with target.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(table.headers)
        writer.writerows(table.rows)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="共用 Agent Chrome 執行富邦登入與明細下載")
    parser.add_argument("mode", choices=("login", "download"))
    parser.add_argument("--area", required=True, choices=("台北", "台中", "桃園", "新竹", "高雄"))
    parser.add_argument("--start", type=parse_date)
    parser.add_argument("--end", type=parse_date)
    parser.add_argument("--accounts-file", type=Path, default=DEFAULT_ACCOUNTS_FILE)
    parser.add_argument("--cdp-url", default=DEFAULT_CDP_URL)
    parser.add_argument("--output", type=Path)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    account = load_account("fubon", args.area, args.accounts_file.expanduser())
    if args.mode == "download" and (not args.start or not args.end):
        raise ValueError("富邦明細下載缺少開始日期或結束日期")
    with sync_playwright() as playwright:
        _browser, context = connect_existing_chrome(playwright, args.cdp_url)
        print(f"瀏覽器：沿用 Agent Chrome（{args.cdp_url}）")
        page = ensure_login(context, account)
        if args.mode == "login":
            print(f"富邦／{args.area} 登入完成；Agent Chrome 將保持開啟。")
            return 0
        page = open_fubon_statement(context, page, account, args.start, args.end)
        page = current_fubon_page(context, page) or page
        table = wait_statement(page)
        if args.output:
            target = args.output.expanduser()
            save_csv(table, target)
            print(f"RESULT_FILE:{target.resolve()}")
        new_table = read_and_filter(table, args.area, "fubon")
        sheet_rows = sync_fubon_master_sheet(table, args.area)
        if new_table.rows:
            copy_to_clipboard(new_table)
        print(
            f"富邦明細完成：共 {len(table.rows)} 筆；新增 {len(new_table.rows)} 筆；"
            f"工作表新增 {sheet_rows} 筆。"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
