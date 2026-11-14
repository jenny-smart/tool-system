"""清潔異動藍新信用卡待退款。

防重複退款（2026-11-14 Jenny：LC00220455 13 日已退款、AC 沒寫到，14 日又被退一次）：
  - 退款前先開藍新交易明細，有退款紀錄（退款申請中／退款成功…）就不退，提示人工確認；
    讀不到明細也不退（寧可漏退讓人補，不重複退）。
  - 送出退款後立刻寫 AC（先寫本機時間）及 B 欄，之後讀到藍新退款申請時間再覆蓋；
    避免讀時間失敗時 AC 空白，下次又排入退款。
  - 查詢結果列用完整訂單編號比對，LC00220455 不會對到 LC002204551。
"""
from __future__ import annotations

import argparse
import re
import time
from datetime import datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Iterable
from zoneinfo import ZoneInfo

from playwright.sync_api import Locator, Page, sync_playwright

from tools.invoice_center.chrome_cdp import DEFAULT_CDP_URL, connect_existing_chrome, find_existing_page
from tools.lemon_backend.stored_value_sheet import get_worksheet
from tools.finance_management.refund_guard import ready_for_refund, ready_items, refunded_status
from tools.newebpay.refund_filter import pending_credit_card_refunds
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


TIMESTAMP = re.compile(r"\d{4}-\d{2}-\d{2}\s+\d{2}:\d{2}:\d{2}")
DETAIL_TIMEOUT_SECONDS = 20


def _now_text() -> str:
    return datetime.now(ZoneInfo("Asia/Taipei")).strftime("%Y-%m-%d %H:%M:%S")


def order_pattern(order_no: str) -> re.Pattern:
    """完整訂單編號（前後不能再接英數字），避免 LC00220455 對到 LC002204551。"""
    return re.compile(rf"(?<![0-9A-Za-z]){re.escape(order_no)}(?![0-9A-Za-z])")


def refund_history(row_texts: Iterable[str]) -> list[tuple[str, str]]:
    """交易歷程記錄各列文字 → 退款紀錄 [(時間, 該列文字)]。

    執行狀態 退款(成功)／退款(申請中)／退款 都算；已取消（列上有「(取消)」）、失敗、駁回不算；請款列不算。"""
    found = []
    for text in row_texts:
        flat = " ".join(str(text or "").split())
        if "退款" not in flat or re.search(r"取消|失敗|駁回", flat):
            continue
        match = TIMESTAMP.search(flat)
        if match:
            found.append((match.group(0), flat))
    return found


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
    deadline = time.monotonic() + 30
    while _find_order_row(page, order_no) is None:
        if time.monotonic() > deadline:
            raise RuntimeError(f"藍新查無訂單 {order_no}")
        page.wait_for_timeout(250)


def _find_order_row(page: Page, order_no: str) -> Locator | None:
    """查詢結果中訂單編號完全相同的那一列（inner_text 有欄位分隔，才能排除 LC002204551 這類較長編號）。"""
    candidates = page.locator("tr", has_text=order_no).filter(
        has=page.locator("button[onclick*='change_card_status'], a[onclick*='search_card_detail']")
    )
    pattern = order_pattern(order_no)
    for index in range(candidates.count()):
        row = candidates.nth(index)
        try:
            if row.is_visible() and pattern.search(row.inner_text()):
                return row
        except Exception:
            continue
    return None


def _order_row(page: Page, order_no: str) -> Locator:
    row = _find_order_row(page, order_no)
    if row is None:
        raise RuntimeError(f"藍新查無訂單 {order_no}")
    return row


def _submit_refund(page: Page, order_no: str, amount: str) -> None:
    row = _order_row(page, order_no)
    refund = _visible(row.locator("button[onclick*='change_card_status']"))
    if refund is None:
        raise RuntimeError(f"{order_no} 找不到退款按鈕")
    refund.click()
    modal = page.locator("#card_process_modal")
    modal.wait_for(state="visible", timeout=15_000)
    amount_field = modal.locator("#apply_amt")
    parsed_amount = Decimal(amount)
    amount_field.fill(str(int(parsed_amount)) if parsed_amount == parsed_amount.to_integral() else str(parsed_amount))
    submitted = False
    dialog_messages: list[str] = []

    def accept_dialog(dialog: object) -> None:
        dialog_messages.append(str(getattr(dialog, "message", "")))
        getattr(dialog, "accept")()

    page.on("dialog", accept_dialog)
    try:
        button = _visible(modal.locator('input[type="button"][value="送出"], button:has-text("送出")'))
        if button is not None:
            button.click()
            submitted = True
        if not submitted:
            raise RuntimeError("找不到退款送出按鈕")
        page.wait_for_timeout(1_500)
        confirm = _visible(page.get_by_text("確定", exact=True))
        if confirm is not None:
            confirm.click()
        page.wait_for_timeout(1_000)
    finally:
        page.remove_listener("dialog", accept_dialog)


def _history_rows(context) -> list[str] | None:
    """明細頁「交易歷程記錄」表格各列文字；表格還沒出現回傳 None。"""
    title = _visible(context.get_by_text("交易歷程記錄", exact=True))
    if title is None:
        return None
    rows = title.locator("xpath=ancestor::table[1]//tr")
    texts = [rows.nth(i).inner_text() for i in range(rows.count())]
    after = texts[next((i for i, t in enumerate(texts) if "交易歷程記錄" in t), 0):]  # 只看歷程區
    if not any(TIMESTAMP.search(text) for text in after):
        return None  # 還在載入
    return after


def _detail_rows(page: Page, order_no: str) -> list[str]:
    """開藍新交易明細，回傳「交易歷程記錄」各列文字；讀不到就報錯（不能確認有沒有退過）。"""
    links = _order_row(page, order_no).locator("a[onclick*='search_card_detail']")
    if not links.count():
        raise RuntimeError(f"{order_no} 找不到藍新金流交易序號")
    links.first.click()
    deadline = time.monotonic() + DETAIL_TIMEOUT_SECONDS
    while time.monotonic() < deadline:
        for context in [page, *page.frames]:
            try:
                rows = _history_rows(context)
                if rows:
                    return rows
            except Exception:
                continue
        page.wait_for_timeout(250)
    raise RuntimeError(f"{order_no} 讀不到藍新交易歷程記錄")


def _existing_refunds(page: Page, order_no: str) -> list[tuple[str, str]]:
    try:
        return refund_history(_detail_rows(page, order_no))
    finally:
        _close_detail_by_backdrop(page)


def _close_detail_by_backdrop(page: Page) -> None:
    for selector in ('.modal-backdrop:visible', '.ui-widget-overlay:visible', '.fancybox-overlay:visible'):
        backdrop = _visible(page.locator(selector))
        if backdrop is not None:
            backdrop.click(position={"x": 5, "y": 5}, force=True)
            page.wait_for_timeout(400)
            return
    page.mouse.click(5, max(5, page.viewport_size["height"] // 2 if page.viewport_size else 300))
    page.wait_for_timeout(400)


def run(area: str, accounts_file: Path, cdp_url: str, selected_rows: set[int] | None = None) -> int:
    worksheet = get_worksheet(area)
    values = worksheet.get_all_values()
    pending = pending_credit_card_refunds(values)
    if selected_rows is not None:
        pending = [item for item in pending if int(item["sheet_row"]) in selected_rows]
    print(f"{area}：待退款／VIP待退券＋信用卡共 {len(pending)} 筆。")
    total = len(pending)
    pending = ready_items(values, pending)  # 登入藍新前先排除缺折讓單號的列
    if not pending:
        if total:
            print("沒有可退款的列（缺折讓單或 VIP 尚未歸零），不登入藍新。")
        return 0
    account = load_accounts(accounts_file.expanduser(), area)[0]
    today = datetime.now(ZoneInfo("Asia/Taipei")).date()
    start, end = (today - timedelta(days=85)).isoformat(), today.isoformat()
    completed = 0
    skipped: list[int] = []
    with sync_playwright() as playwright:
        _browser, context = connect_existing_chrome(playwright, cdp_url)
        page = find_existing_page(context, ("newebpay.com",)) or context.new_page()
        if "newebpay.com" not in page.url:
            page.goto(LOGIN_URL, wait_until="domcontentloaded")
        login(page, account)
        try:
            for item in pending:
                order_no, amount, sheet_row = str(item["order_no"]), str(item["amount"]), int(item["sheet_row"])
                if not ready_for_refund(worksheet, sheet_row):
                    continue  # 有原發票但折讓單未開成功／未回填：不退款
                print(f"處理第 {sheet_row} 列：{order_no}／NT$ {amount}")
                _set_order_query(page, order_no, start, end)
                try:
                    existing = _existing_refunds(page, order_no)
                except Exception as exc:
                    print(f"⚠️ 第 {sheet_row} 列 {order_no} 無法確認藍新退款紀錄（{exc}），為避免重複退款先不退，請人工確認")
                    skipped.append(sheet_row)
                    continue
                if existing:
                    history = "；".join(text for _when, text in existing)
                    print(f"⚠️ 第 {sheet_row} 列 {order_no} 藍新已有退款紀錄，未再退款：{history}")
                    print(f"　　確認是這筆異動的退款，請把 AC 填上退款時間（{existing[0][0]}）、B 欄改為已退款；"
                          "若同訂單確實要再退一次，請到藍新手動退款")
                    skipped.append(sheet_row)
                    continue
                _set_order_query(page, order_no, start, end)
                _submit_refund(page, order_no, amount)
                # 送出就先寫 AC／B，之後讀時間失敗也不會被當成沒退、下次再退
                done_status = refunded_status(str(item.get("status", "")))
                worksheet.update(range_name=f"AC{sheet_row}", values=[[_now_text()]], value_input_option="RAW")
                worksheet.update(range_name=f"B{sheet_row}", values=[[done_status]], value_input_option="USER_ENTERED")
                completed += 1
                try:
                    _set_order_query(page, order_no, start, end)
                    refunds = _existing_refunds(page, order_no)
                    if not refunds:
                        raise RuntimeError("明細查無退款紀錄")
                    requested_at = max(when for when, _text in refunds)
                    worksheet.update(range_name=f"AC{sheet_row}", values=[[requested_at]], value_input_option="RAW")
                    print(f"完成：{order_no}；退款申請時間 {requested_at}")
                except Exception as exc:
                    print(f"⚠️ 完成：{order_no} 已送出退款，但讀不到藍新退款時間（{exc}），AC 先填本機時間，請到藍新確認")
        finally:
            try:
                logout(page, area)
            finally:
                if not page.is_closed():
                    page.close()
    print(f"藍新信用卡退款完成：{completed}/{total} 筆。")
    if skipped:
        print(f"⚠️ 未退款、需人工確認：第 {'、'.join(map(str, skipped))} 列")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="處理清潔異動工作表中的藍新信用卡待退款")
    parser.add_argument("--area", required=True)
    parser.add_argument("--accounts-file", type=Path, default=DEFAULT_ACCOUNTS_FILE)
    parser.add_argument("--cdp-url", default=DEFAULT_CDP_URL)
    parser.add_argument("--rows", help="勾選的工作表列號，以逗號分隔")
    args = parser.parse_args()
    selected_rows = (
        {int(value) for value in args.rows.split(",") if value.strip().isdigit()}
        if args.rows is not None
        else None
    )
    if selected_rows == set():
        raise ValueError("沒有勾選退款資料")
    return run(args.area, args.accounts_file, args.cdp_url, selected_rows)


if __name__ == "__main__":
    raise SystemExit(main())
