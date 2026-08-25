from __future__ import annotations

import argparse
import re
import time
from pathlib import Path
from typing import Iterable

from playwright.sync_api import Locator, Page, sync_playwright

from tools.bank_statement.internal_payment_registry import (
    NEWEBPAY_EXPRESS_TYPE,
    resolve_report_location,
)
from tools.common.config_loader import get_sheets_service
from tools.invoice_center.chrome_cdp import (
    DEFAULT_CDP_URL,
    connect_existing_chrome,
    find_existing_page,
)
from tools.newebpay.download_reports import (
    DEFAULT_ACCOUNTS_FILE,
    LOGIN_URL,
    click_search,
    load_accounts,
    login,
    select_merchant,
)
from tools.newebpay.express_payment_data import (
    ExpressPayment,
    amount_value as _amount_value,
    new_rows as _new_rows,
)


QUERY_URL = "https://www.newebpay.com/express_payment/order_list/epg_transaction_search"
TRANSACTION_RE = re.compile(r"^\d{17,20}$")


def _clean_lines(value: str) -> list[str]:
    return [re.sub(r"\s+", " ", line).strip() for line in value.splitlines() if line.strip()]


def _visible(locator: Locator) -> Locator | None:
    for index in range(locator.count()):
        item = locator.nth(index)
        if item.is_visible():
            return item
    return None


def _transaction_links(page: Page) -> list[Locator]:
    result: list[Locator] = []
    links = page.locator("a")
    for index in range(links.count()):
        link = links.nth(index)
        if link.is_visible() and TRANSACTION_RE.fullmatch(link.inner_text().strip()):
            result.append(link)
    return result


def _transaction_link(page: Page, transaction_no: str) -> Locator | None:
    for link in _transaction_links(page):
        if link.inner_text().strip() == transaction_no:
            return link
    return None


def _detail_value(page: Page, labels: Iterable[str]) -> str:
    for context in [page, *page.frames]:
        for label in labels:
            matches = context.get_by_text(label, exact=True)
            for index in range(matches.count()):
                item = matches.nth(index)
                if not item.is_visible():
                    continue
                cell = item.locator("xpath=ancestor::*[self::th or self::td][1]")
                if not cell.count():
                    continue
                value = cell.locator("xpath=following-sibling::*[self::th or self::td][1]")
                if value.count() and value.first.is_visible():
                    return " ".join(_clean_lines(value.first.inner_text()))
    raise RuntimeError(f"交易明細找不到欄位：{'／'.join(labels)}")


def _close_detail(page: Page) -> None:
    for selector in (
        ".modal:visible button.close",
        ".modal:visible [data-dismiss='modal']",
        ".fancybox-close:visible",
        ".ui-dialog-titlebar-close:visible",
    ):
        close = _visible(page.locator(selector))
        if close is not None:
            close.click(force=True)
            page.wait_for_timeout(300)
            return
    page.keyboard.press("Escape")
    page.wait_for_timeout(300)


def _read_current_page(page: Page) -> list[ExpressPayment]:
    payments: list[ExpressPayment] = []
    transaction_numbers = [link.inner_text().strip() for link in _transaction_links(page)]
    for transaction_no in transaction_numbers:
        visible_link = _transaction_link(page, transaction_no)
        if visible_link is None:
            raise RuntimeError(f"查詢結果找不到藍新金流交易序號：{transaction_no}")
        row = visible_link.locator("xpath=ancestor::tr[1]")
        cells = row.locator("td")
        if cells.count() < 5:
            raise RuntimeError(f"{transaction_no} 查詢結果欄位不足")
        merchant_lines = _clean_lines(cells.nth(0).inner_text())
        transaction_lines = _clean_lines(cells.nth(1).inner_text())
        product_lines = _clean_lines(cells.nth(2).inner_text())
        method_lines = _clean_lines(cells.nth(3).inner_text())
        status_lines = _clean_lines(cells.nth(4).inner_text())

        visible_link.click()
        payer = _detail_value(page, ("付款人",))
        phone = _detail_value(page, ("聯絡電話",))
        amount = _amount_value(_detail_value(page, ("金額",)))
        payments.append(
            ExpressPayment(
                merchant="\n".join(merchant_lines),
                transaction_no=transaction_no,
                merchant_order_no="\n".join(
                    line for line in transaction_lines if line != transaction_no
                ),
                product="\n".join(product_lines),
                payment_method="\n".join(method_lines),
                status_and_time="\n".join(status_lines),
                payer=payer,
                phone=phone,
                amount=amount,
            )
        )
        _close_detail(page)
    return payments


def _page_select(page: Page) -> Locator | None:
    selects = page.locator("select")
    for index in range(selects.count()):
        select = selects.nth(index)
        if not select.is_visible():
            continue
        parent_text = select.locator("xpath=ancestor::*[self::div or self::td][1]").inner_text()
        if "目前頁次" in parent_text:
            return select
    return None


def read_results(page: Page) -> list[ExpressPayment]:
    deadline = time.monotonic() + 30
    while time.monotonic() < deadline:
        if _transaction_links(page) or "查無" in page.locator("body").inner_text():
            break
        page.wait_for_timeout(250)
    if QUERY_URL not in page.url:
        raise RuntimeError(f"藍新快速查帳未停留在查詢頁：{page.url}")

    selector = _page_select(page)
    pages = [""]
    if selector is not None:
        pages = [
            selector.locator("option").nth(index).get_attribute("value") or ""
            for index in range(selector.locator("option").count())
        ]
    results: list[ExpressPayment] = []
    for index, value in enumerate(pages):
        if index:
            selector = _page_select(page)
            if selector is None:
                raise RuntimeError("藍新查詢結果換頁選單消失")
            selector.select_option(value=value)
            page.wait_for_timeout(800)
        results.extend(_read_current_page(page))
    return results


def append_new_payments(area: str, payments: list[ExpressPayment]) -> int:
    spreadsheet_id, title = resolve_report_location(NEWEBPAY_EXPRESS_TYPE, area)
    service = get_sheets_service()
    response = service.spreadsheets().values().get(
        spreadsheetId=spreadsheet_id,
        range=f"'{title}'!A:H",
    ).execute()
    rows = response.get("values", [])
    existing = {
        str(row[1]).strip().splitlines()[0]
        for row in rows
        if len(row) > 1 and str(row[1]).strip()
    }
    new_rows = _new_rows(payments, existing)
    if not new_rows:
        return 0
    append_response = service.spreadsheets().values().append(
        spreadsheetId=spreadsheet_id,
        range=f"'{title}'!A:H",
        valueInputOption="RAW",
        insertDataOption="INSERT_ROWS",
        body={"values": new_rows},
    ).execute()
    updated_range = append_response.get("updates", {}).get("updatedRange", "")
    match = re.search(r"![A-Z]+(\d+):[A-Z]+(\d+)$", updated_range)
    if not match:
        raise RuntimeError(f"{area} 無法確認藍新快速查帳寫入範圍：{updated_range}")
    start_row, end_row = int(match.group(1)), int(match.group(2))
    verify = service.spreadsheets().values().get(
        spreadsheetId=spreadsheet_id,
        range=f"'{title}'!B{start_row}:B{end_row}",
    ).execute().get("values", [])
    written = [str(row[0]).strip().splitlines()[0] for row in verify if row]
    expected = [str(row[1]).splitlines()[0] for row in new_rows]
    if written != expected:
        raise RuntimeError(f"{area} 寫入後驗證失敗：預期 {expected}，實際 {written}")
    return len(new_rows)


def run(area: str, accounts_file: Path, cdp_url: str) -> int:
    account = load_accounts(accounts_file.expanduser(), area)[0]
    with sync_playwright() as playwright:
        _browser, context = connect_existing_chrome(playwright, cdp_url)
        page = find_existing_page(context, ("newebpay.com",)) or context.new_page()
        if "newebpay.com" not in page.url:
            page.goto(LOGIN_URL, wait_until="domcontentloaded")
        login(page, account)
        page.goto(QUERY_URL, wait_until="domcontentloaded", timeout=60_000)
        select_merchant(page, account)
        click_search(page)
        payments = read_results(page)
        written = append_new_payments(area, payments)
        print(f"{area}：藍新快速查帳 {len(payments)} 筆，新增 {written} 筆，略過 {len(payments) - written} 筆。")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="藍新快速收款查帳並追加未登記交易")
    parser.add_argument("--area", required=True)
    parser.add_argument("--accounts-file", type=Path, default=DEFAULT_ACCOUNTS_FILE)
    parser.add_argument("--cdp-url", default=DEFAULT_CDP_URL)
    args = parser.parse_args()
    return run(args.area, args.accounts_file, args.cdp_url)


if __name__ == "__main__":
    raise SystemExit(main())
