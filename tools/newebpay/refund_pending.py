from __future__ import annotations

import argparse
import re
import time
from datetime import datetime, timedelta
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Iterable
from zoneinfo import ZoneInfo

from playwright.sync_api import Locator, Page, sync_playwright

from tools.invoice_center.chrome_cdp import DEFAULT_CDP_URL, connect_existing_chrome, find_existing_page
from tools.memo_system.change_order import get_worksheet
from tools.newebpay.download_reports import (
    DEFAULT_ACCOUNTS_FILE,
    LOGIN_URL,
    PAYMENT_URL,
    first_visible,
    load_accounts,
    login,
    logout,
    set_date,
    write_date,
)


def _cell(row: list[str], column: int) -> str:
    return str(row[column] if len(row) > column else "").strip()


def pending_credit_card_refunds(values: list[list[str]]) -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    for row_number, row in enumerate(values[1:], start=2):
        status, order_no, payway, amount = _cell(row, 1), _cell(row, 6), _cell(row, 17), _cell(row, 18)
        if status != "待退款" or payway != "信用卡" or not order_no or not amount:
            continue
        normalized = re.sub(r"[^0-9.]", "", amount)
        try:
            parsed_amount = Decimal(normalized)
        except InvalidOperation:
            continue
        if parsed_amount <= 0:
            continue
        rows.append({"sheet_row": row_number, "order_no": order_no, "amount": normalized})
    return rows


def _visible(locator: Locator) -> Locator | None:
    for index in range(locator.count()):
        item = locator.nth(index)
        if item.is_visible():
            return item
    return None


def _click_text(page: Page, text: str, *, exact: bool = True, timeout: int = 15_000) -> None:
    deadline = time.monotonic() + timeout / 1000
    while time.monotonic() < deadline:
        for context in [page, *page.frames]:
            try:
                item = _visible(context.get_by_text(text, exact=exact))
                if item is not None:
                    item.click(timeout=3_000)
                    return
            except Exception:
                continue
        page.wait_for_timeout(200)
    raise RuntimeError(f"找不到「{text}」")


def _set_order_query(page: Page, order_no: str, start: str, end: str) -> None:
    if "/sale/Sell_center/search_transaction" not in page.url:
        page.goto(PAYMENT_URL, wait_until="domcontentloaded", timeout=60_000)
    _click_text(page, "信用卡交易專用查詢", exact=True)
    set_date(page, ("PeriodStartDate", "StartDate", "start_date"), start, 0)
    set_date(page, ("PeriodEndDate", "EndDate", "end_date"), end, 1)

    labels = page.locator("label").filter(has_text="商店訂單編號")
    label = _visible(labels)
    if label is not None:
        radio = label.locator('input[type="radio"]')
        if radio.count():
            radio.first.check(force=True)
        else:
            label.click()
    else:
        radios = page.locator('input[type="radio"]')
        matched = False
        for index in range(radios.count()):
            radio = radios.nth(index)
            parent_text = radio.locator("xpath=ancestor::*[self::label or self::td or self::div][1]").inner_text()
            if "商店訂單編號" in parent_text:
                radio.check(force=True)
                matched = True
                break
        if not matched:
            raise RuntimeError("找不到商店訂單編號查詢選項")

    candidates: list[Locator] = []
    inputs = page.locator('input[type="text"], input[type="search"]')
    for index in range(inputs.count()):
        item = inputs.nth(index)
        if not item.is_visible():
            continue
        hints = " ".join(filter(None, (item.get_attribute("name"), item.get_attribute("id"), item.get_attribute("placeholder")))).lower()
        if not re.search(r"date|period|start|end|日期", hints) and not re.fullmatch(r"\d{4}-\d{2}-\d{2}", item.input_value().strip()):
            candidates.append(item)
    if not candidates:
        raise RuntimeError("找不到訂單編號輸入欄位")
    candidates[-1].fill(order_no)
    _click_text(page, "開始查詢", exact=True)
    page.get_by_text(order_no, exact=True).first.wait_for(state="visible", timeout=30_000)


def _submit_refund(page: Page, order_no: str, amount: str) -> None:
    row = page.get_by_text(order_no, exact=True).first.locator("xpath=ancestor::tr[1]")
    refund = _visible(row.get_by_text("退款", exact=True))
    if refund is None:
        raise RuntimeError(f"{order_no} 找不到退款按鈕")
    refund.click()
    modal = page.locator('.modal:visible, [role="dialog"]:visible').last
    modal.wait_for(state="visible", timeout=15_000)
    amount_field = first_visible(modal, ('input[name*="amount" i]', 'input[id*="amount" i]', 'input[type="text"]'))
    parsed_amount = Decimal(amount)
    amount_field.fill(str(int(parsed_amount)) if parsed_amount == parsed_amount.to_integral() else str(parsed_amount))
    submitted = False
    dialog_messages: list[str] = []

    def accept_dialog(dialog: object) -> None:
        dialog_messages.append(str(getattr(dialog, "message", "")))
        getattr(dialog, "accept")()

    page.on("dialog", accept_dialog)
    try:
        for text in ("送出", "確定"):
            button = _visible(modal.get_by_text(text, exact=True))
            if button is not None:
                button.click()
                submitted = True
                break
        if not submitted:
            raise RuntimeError("找不到退款送出按鈕")
        page.wait_for_timeout(1_500)
        confirm = _visible(page.get_by_text("確定", exact=True))
        if confirm is not None:
            confirm.click()
        page.wait_for_timeout(1_000)
    finally:
        page.remove_listener("dialog", accept_dialog)


def _refund_requested_at(page: Page, order_no: str) -> str:
    row = page.get_by_text(order_no, exact=True).first.locator("xpath=ancestor::tr[1]")
    links = row.locator("a")
    transaction_link = links.first if links.count() else None
    if transaction_link is None:
        raise RuntimeError(f"{order_no} 找不到藍新金流交易序號")
    transaction_link.click()
    deadline = time.monotonic() + 20
    while time.monotonic() < deadline:
        for context in [page, *page.frames]:
            try:
                status = _visible(context.get_by_text("退款(申請中)", exact=True))
                if status is None:
                    continue
                history_row = status.locator("xpath=ancestor::tr[1]")
                text = history_row.inner_text()
                match = re.search(r"\d{4}-\d{2}-\d{2}\s+\d{2}:\d{2}:\d{2}", text)
                if match:
                    return match.group(0)
            except Exception:
                continue
        page.wait_for_timeout(250)
    raise RuntimeError(f"{order_no} 查無退款(申請中)時間")


def _close_detail_by_backdrop(page: Page) -> None:
    for selector in ('.modal-backdrop:visible', '.ui-widget-overlay:visible', '.fancybox-overlay:visible'):
        backdrop = _visible(page.locator(selector))
        if backdrop is not None:
            backdrop.click(position={"x": 5, "y": 5}, force=True)
            page.wait_for_timeout(400)
            return
    page.mouse.click(5, max(5, page.viewport_size["height"] // 2 if page.viewport_size else 300))
    page.wait_for_timeout(400)


def run(area: str, accounts_file: Path, cdp_url: str) -> int:
    worksheet = get_worksheet(area)
    pending = pending_credit_card_refunds(worksheet.get_all_values())
    print(f"{area}：待退款＋信用卡共 {len(pending)} 筆。")
    if not pending:
        return 0
    account = load_accounts(accounts_file.expanduser(), area)[0]
    today = datetime.now(ZoneInfo("Asia/Taipei")).date()
    start, end = (today - timedelta(days=85)).isoformat(), today.isoformat()
    completed = 0
    with sync_playwright() as playwright:
        _browser, context = connect_existing_chrome(playwright, cdp_url)
        page = find_existing_page(context, ("newebpay.com",)) or context.new_page()
        if "newebpay.com" not in page.url:
            page.goto(LOGIN_URL, wait_until="domcontentloaded")
        login(page, account)
        try:
            for item in pending:
                order_no, amount, sheet_row = str(item["order_no"]), str(item["amount"]), int(item["sheet_row"])
                print(f"處理第 {sheet_row} 列：{order_no}／NT$ {amount}")
                _set_order_query(page, order_no, start, end)
                _submit_refund(page, order_no, amount)
                _set_order_query(page, order_no, start, end)
                requested_at = _refund_requested_at(page, order_no)
                worksheet.update(range_name=f"AC{sheet_row}", values=[[requested_at]], value_input_option="RAW")
                worksheet.update(range_name=f"B{sheet_row}", values=[["已退款"]], value_input_option="USER_ENTERED")
                print(f"完成：{order_no}；退款申請時間 {requested_at}")
                completed += 1
                _close_detail_by_backdrop(page)
        finally:
            try:
                logout(page, area)
            finally:
                if not page.is_closed():
                    page.close()
    print(f"藍新信用卡退款完成：{completed}/{len(pending)} 筆。")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="處理清潔異動工作表中的藍新信用卡待退款")
    parser.add_argument("--area", required=True)
    parser.add_argument("--accounts-file", type=Path, default=DEFAULT_ACCOUNTS_FILE)
    parser.add_argument("--cdp-url", default=DEFAULT_CDP_URL)
    args = parser.parse_args()
    return run(args.area, args.accounts_file, args.cdp_url)


if __name__ == "__main__":
    raise SystemExit(main())
