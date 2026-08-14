from __future__ import annotations

import argparse
import time
from pathlib import Path

from playwright.sync_api import Frame, Locator, Page, sync_playwright

from tools.bank_statement.accounts import DEFAULT_ACCOUNTS_FILE, load_account
from tools.bank_statement.fubon_agent import ensure_login
from tools.bank_statement.fubon_refund_filter import pending_atm_refunds
from tools.bank_statement.open_login import current_fubon_page, dismiss_fubon_idle_dialog
from tools.invoice_center.chrome_cdp import DEFAULT_CDP_URL, connect_existing_chrome
from tools.memo_system.change_order import get_worksheet


Context = Frame | Page


def _contexts(page: Page) -> list[Context]:
    return [page, *page.frames]


def _visible(locator: Locator) -> Locator | None:
    for index in range(locator.count()):
        item = locator.nth(index)
        if item.is_visible():
            return item
    return None


def _click_text(page: Page, text: str, *, exact: bool = True, timeout: int = 15_000) -> None:
    deadline = time.monotonic() + timeout / 1000
    while time.monotonic() < deadline:
        dismiss_fubon_idle_dialog(page)
        for context in _contexts(page):
            try:
                item = _visible(context.get_by_text(text, exact=exact))
                if item is None:
                    continue
                try:
                    item.click(timeout=2_000)
                except Exception:
                    item.evaluate("element => element.click()")
                return
            except Exception:
                continue
        page.wait_for_timeout(200)
    raise RuntimeError(f"富邦頁面找不到「{text}」")


def _row_for_label(page: Page, label: str, timeout: int = 15_000) -> Locator:
    deadline = time.monotonic() + timeout / 1000
    while time.monotonic() < deadline:
        for context in _contexts(page):
            try:
                node = _visible(context.get_by_text(label, exact=True))
                if node is None:
                    continue
                for xpath in ("ancestor::tr[1]", "ancestor::div[contains(@class,'row')][1]", "parent::*"):
                    row = node.locator(f"xpath={xpath}")
                    if row.count():
                        return row.first
            except Exception:
                continue
        page.wait_for_timeout(200)
    raise RuntimeError(f"富邦頁面找不到欄位「{label}」")


def _fill_row_inputs(page: Page, label: str, values: list[str]) -> None:
    row = _row_for_label(page, label)
    inputs = row.locator(
        'input:visible:not([type="hidden"]):not([type="radio"]):not([type="button"]):not([type="submit"])'
    )
    if inputs.count() < len(values):
        raise RuntimeError(f"「{label}」預期 {len(values)} 個輸入欄位，實際 {inputs.count()} 個")
    for index, value in enumerate(values):
        field = inputs.nth(index)
        field.click()
        field.press("Meta+A")
        field.fill(value)


def _open_transfer_form(page: Page) -> Page:
    form_is_open = False
    for context in _contexts(page):
        try:
            if "轉出帳號" in context.locator("body").inner_text(timeout=1_000):
                form_is_open = True
                break
        except Exception:
            continue
    if not form_is_open:
        _click_text(page, "台幣轉帳")
        _click_text(page, "立即/預約轉帳")
    _row_for_label(page, "轉出帳號", timeout=30_000)
    return current_fubon_page(page.context, page) or page


def _choose_source_account(page: Page, area: str) -> None:
    if area != "台北":
        return
    # 台北登入有兩個轉出帳號；點選轉出帳號欄位後指定松高分行。
    row = _row_for_label(page, "轉出帳號")
    if "松高分行" in row.inner_text():
        return
    control = _visible(row.locator("select, button, input, [role='button'], [onclick]"))
    if control is None:
        raise RuntimeError("台北轉出帳號找不到選擇按鈕")
    try:
        control.click()
    except Exception:
        control.evaluate("element => element.click()")
    _click_text(page, "松高分行", exact=False)


def _choose_manual_destination(page: Page) -> None:
    row = _row_for_label(page, "轉入帳號")
    manual = _visible(row.get_by_text("自行輸入", exact=True))
    if manual is None:
        manual = _visible(page.get_by_text("自行輸入", exact=True))
    if manual is None:
        raise RuntimeError("轉入帳號找不到「自行輸入」")
    try:
        manual.click()
    except Exception:
        manual.evaluate("element => element.click()")


def _wait_confirmation(page: Page, timeout: int = 30_000) -> None:
    deadline = time.monotonic() + timeout / 1000
    while time.monotonic() < deadline:
        for context in _contexts(page):
            try:
                text = context.locator("body").inner_text(timeout=800)
            except Exception:
                continue
            if "STEP 2" in text or "確認資料" in text or "交易密碼" in text:
                return
        page.wait_for_timeout(250)
    raise RuntimeError("已按確認，但未進入富邦確認資料頁")


def _wait_user_completed_transfer(page: Page, timeout: int = 600_000) -> None:
    """Wait for the user to finish the bank's final verification before next row."""
    print("請人工核對並完成本筆最終交易；完成後會自動準備下一筆。")
    deadline = time.monotonic() + timeout / 1000
    while time.monotonic() < deadline:
        dismiss_fubon_idle_dialog(page)
        for context in _contexts(page):
            try:
                text = context.locator("body").inner_text(timeout=800)
            except Exception:
                continue
            if any(marker in text for marker in ("交易成功", "轉帳成功", "交易已完成")):
                return
        page.wait_for_timeout(500)
    raise RuntimeError("等待人工完成富邦交易逾時；尚未準備下一筆")


def fill_refund(page: Page, area: str, item: dict[str, object]) -> None:
    page = _open_transfer_form(page)
    _choose_source_account(page, area)
    _choose_manual_destination(page)
    _fill_row_inputs(page, "轉入帳號", [str(item["bank_code"]), str(item["account_number"])])
    _fill_row_inputs(page, "轉帳金額", [str(item["amount"])])
    _click_text(page, "立即", exact=True)
    _fill_row_inputs(page, "給自己", [f"清潔{item['customer']}退款"])
    _fill_row_inputs(page, "給對方", ["檸檬家事"])
    _click_text(page, "確認", exact=True)
    _wait_confirmation(page)


def run(area: str, rows: set[int], accounts_file: Path, cdp_url: str) -> int:
    worksheet = get_worksheet(area)
    candidates = pending_atm_refunds(worksheet.get_all_values())
    selected = [item for item in candidates if int(item["sheet_row"]) in rows]
    if not selected:
        raise ValueError("勾選列中沒有符合 B=待退款、R=ATM 且 P/Q/T 完整的資料")
    account = load_account("fubon", area, accounts_file.expanduser())
    with sync_playwright() as playwright:
        _browser, context = connect_existing_chrome(playwright, cdp_url)
        page = ensure_login(context, account)
        try:
            for index, item in enumerate(selected):
                print(
                    f"準備第 {item['sheet_row']} 列：{item['customer']}／"
                    f"{item['bank_code']}-{str(item['account_number'])[-5:]}／NT$ {item['amount']}"
                )
                fill_refund(page, area, item)
                print("已進入富邦確認資料頁。")
                if index + 1 < len(selected):
                    _wait_user_completed_transfer(page)
                    page = current_fubon_page(context, page) or page
            print("全部勾選資料均已準備；最後一筆請人工核對並完成最終交易。")
        except Exception:
            # 發生錯誤時保留銀行頁，方便人工確認；不登出、不關閉。
            raise
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="依清潔異動工作表準備富邦 ATM 退款")
    parser.add_argument("--area", required=True, choices=("台北", "台中"))
    parser.add_argument("--rows", required=True, help="勾選的工作表列號，以逗號分隔")
    parser.add_argument("--accounts-file", type=Path, default=DEFAULT_ACCOUNTS_FILE)
    parser.add_argument("--cdp-url", default=DEFAULT_CDP_URL)
    args = parser.parse_args()
    rows = {int(value) for value in args.rows.split(",") if value.strip().isdigit()}
    if not rows:
        raise ValueError("沒有勾選 ATM 退款資料")
    return run(args.area, rows, args.accounts_file, args.cdp_url)


if __name__ == "__main__":
    raise SystemExit(main())
