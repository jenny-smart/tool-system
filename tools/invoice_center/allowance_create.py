from __future__ import annotations

import argparse
import re
from datetime import datetime
from decimal import Decimal
from zoneinfo import ZoneInfo

from playwright.sync_api import Locator, Page, sync_playwright

from tools.invoice_center.allowance_filter import pending_allowances
from tools.invoice_center.cetustek_login_only import (
    credentials_for, load_accounts, login_portal, login_second, open_second_login,
)
from tools.invoice_center.chrome_cdp import DEFAULT_CDP_URL, connect_existing_chrome, find_invoice_pages
from tools.lemon_backend.stored_value_sheet import get_worksheet


def _visible(locator: Locator) -> Locator | None:
    for index in range(locator.count()):
        item = locator.nth(index)
        if item.is_visible():
            return item
    return None


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
    if "allowanceadd.jsp" not in page.url:
        page.goto("https://www.ei.com.tw/InvoiceRent/allowanceadd.jsp")
    page.wait_for_load_state("domcontentloaded")
    page.locator("#qyear").wait_for(state="visible")


def _money(value: str) -> Decimal:
    return Decimal(str(value).replace(",", "").replace("$", "").replace("NT", "").strip())


def _create_one(page: Page, invoice_no: str, refund: str, untaxed: str) -> str:
    year = page.locator("#qyear")
    year.select_option(index=1)
    page.locator("#invoicenumber").fill(invoice_no)
    page.locator("a[onclick='SearchInvoice();']").click()
    page.locator("#s_remark").wait_for(state="visible")

    available_text = page.locator("body").inner_text()
    sales_match = re.search(r"銷售額[\s\S]*?應稅:\$\s*([\d,]+(?:\.\d+)?)", available_text)
    sales_amount = _money(sales_match.group(1)) if sales_match else Decimal("-1")
    reason = "取消，全退" if _money(refund) == sales_amount else "部份退"
    page.locator("#s_remark").select_option(label=reason)

    page.locator("img[title='發票明細查詢']").click()
    select_product = page.locator("#processresult a[onclick^='setInvoiceDetail']").first
    select_product.wait_for(state="visible")
    select_product.click()
    page.locator("#unitprice").wait_for(state="visible")

    page.locator("#unitprice").fill(untaxed)
    page.locator("a[onclick='goDetail();']").click()
    confirm = page.get_by_text("確定", exact=True)
    confirm.wait_for(state="visible")
    confirm.click()
    page.locator("#save2").wait_for(state="visible")
    page.locator("#save2").click()
    save_confirm = page.get_by_text("確定", exact=True)
    save_confirm.wait_for(state="visible")
    save_confirm.click()
    notice = page.locator("#msg").filter(has_text=re.compile("折讓開立成功"))
    notice.wait_for(state="visible")
    text = notice.inner_text()
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
            number = _create_one(
                page,
                str(item["invoice_no"]),
                str(item["refund_amount"]),
                str(item["untaxed_amount"]),
            )
            allowance_date = datetime.now(ZoneInfo("Asia/Taipei")).strftime("%Y/%m/%d")
            worksheet.update(
                range_name=f"AA{item['sheet_row']}",
                values=[[allowance_date]],
                value_input_option="USER_ENTERED",
            )
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
