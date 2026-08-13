from __future__ import annotations

import argparse
import re
import time
from decimal import Decimal
from pathlib import Path

from playwright.sync_api import Locator, Page, sync_playwright

from tools.invoice_center.allowance_filter import pending_allowances
from tools.invoice_center.cetustek_login_only import (
    configured_areas, credentials_for, load_accounts, login_portal, login_second,
    open_second_login, portal_values,
)
from tools.invoice_center.chrome_cdp import DEFAULT_CDP_URL, connect_existing_chrome, find_invoice_pages
from tools.memo_system.change_order import get_worksheet


def _visible(locator: Locator) -> Locator | None:
    for index in range(locator.count()):
        item = locator.nth(index)
        if item.is_visible():
            return item
    return None


def _click(page: Page, text: str) -> None:
    for context in [page, *page.frames]:
        item = _visible(context.get_by_text(text, exact=True))
        if item is not None:
            item.click()
            page.wait_for_timeout(500)
            return
    raise RuntimeError(f"找不到「{text}」")


def _field_near(page: Page, label: str) -> Locator:
    label_node = _visible(page.get_by_text(label, exact=False))
    if label_node is not None:
        cell = label_node.locator("xpath=ancestor::*[self::td or self::tr or self::div][1]")
        field = _visible(cell.locator("input,select,textarea"))
        if field is not None:
            return field
        field = _visible(label_node.locator("xpath=following::input[1] | xpath=following::select[1]"))
        if field is not None:
            return field
    raise RuntimeError(f"找不到「{label}」欄位")


def _login(context: object, area: str, accounts: dict) -> Page:
    credentials = credentials_for(area, accounts)
    portal_page, ei_page = find_invoice_pages(context)
    if ei_page is not None:
        try:
            login_second(ei_page, credentials)
        except Exception:
            pass
        return ei_page
    if portal_page is None:
        portal_page = context.new_page()
    login_portal(portal_page, accounts)
    page = open_second_login(context, portal_page)
    login_second(page, credentials)
    return page


def _open_allowance(page: Page) -> None:
    for text in ("折讓單作業", "折讓單開立"):
        _click(page, text)


def _create_one(page: Page, invoice_no: str, untaxed: str) -> str:
    year = _field_near(page, "發票年份")
    if year.evaluate("e => e.tagName") == "SELECT":
        year.select_option(index=year.locator("option").count() - 1)
    _field_near(page, "發票號碼").fill(invoice_no)
    query = _visible(page.get_by_text(re.compile("查詢|搜尋")))
    if query is not None:
        query.click()
    page.wait_for_timeout(800)

    available_text = page.locator("body").inner_text()
    amounts = [Decimal(value.replace(",", "")) for value in re.findall(r"未稅單價\s*([\d,]+(?:\.\d+)?)", available_text)]
    reason = "全退" if amounts and Decimal(untaxed) == amounts[0] else "部份退"
    reason_field = _field_near(page, "折讓原因")
    if reason_field.evaluate("e => e.tagName") == "SELECT":
        reason_field.select_option(label=reason)
    else:
        reason_field.fill(reason)

    picker = _visible(page.locator("img,button,a").filter(has=page.get_by_text(re.compile("產品代碼|明細"))))
    if picker is None:
        picker = _visible(page.locator("input[type=image], img[onclick], button[onclick]"))
    if picker is not None:
        picker.click()
        page.wait_for_timeout(500)
    radio = _visible(page.locator('input[type="radio"]'))
    if radio is not None:
        radio.check(force=True)
        close = _visible(page.get_by_text("關閉", exact=True))
        if close is not None:
            close.click()

    _field_near(page, "折讓未稅價").fill(untaxed)
    _click(page, "加入明細")
    _click(page, "儲存")
    page.wait_for_timeout(800)
    text = page.locator("body").inner_text()
    matches = re.findall(r"[A-Z]{2}\d{8,}", text)
    if not matches:
        raise RuntimeError(f"{invoice_no} 儲存後找不到折讓單號")
    return matches[-1]


def run(area: str, cdp_url: str, selected_rows: set[int]) -> int:
    worksheet = get_worksheet(area)
    rows = [item for item in pending_allowances(worksheet.get_all_values()) if item["sheet_row"] in selected_rows]
    if not rows:
        raise ValueError("沒有可執行的待退款折讓資料")
    accounts = load_accounts(None)
    with sync_playwright() as playwright:
        _browser, context = connect_existing_chrome(playwright, cdp_url)
        page = _login(context, area, accounts)
        _open_allowance(page)
        for item in rows:
            number = _create_one(page, str(item["invoice_no"]), str(item["untaxed_amount"]))
            worksheet.update(range_name=f"AB{item['sheet_row']}", values=[[number]], value_input_option="RAW")
            print(f"第 {item['sheet_row']} 列：{item['invoice_no']} → {number}")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--area", required=True)
    parser.add_argument("--cdp-url", default=DEFAULT_CDP_URL)
    parser.add_argument("--rows", required=True)
    args = parser.parse_args()
    return run(args.area, args.cdp_url, {int(value) for value in args.rows.split(",") if value.strip().isdigit()})


if __name__ == "__main__":
    raise SystemExit(main())
