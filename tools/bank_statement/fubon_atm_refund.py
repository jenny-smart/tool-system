from __future__ import annotations

import argparse
import re
import time
from pathlib import Path
from typing import Union

from playwright.sync_api import Frame, Locator, Page, sync_playwright

from tools.bank_statement.accounts import DEFAULT_ACCOUNTS_FILE, load_account
from tools.bank_statement.fubon_agent import ensure_login
from tools.bank_statement.fubon_refund_filter import pending_atm_refunds
from tools.bank_statement.open_login import (
    click_nearest_control,
    current_fubon_page,
    dismiss_fubon_idle_dialog,
    visible_in_viewport,
)
from tools.invoice_center.chrome_cdp import DEFAULT_CDP_URL, connect_existing_chrome
from tools.memo_system.change_order import get_worksheet


Context = Union[Frame, Page]


def _contexts(page: Page) -> list[Context]:
    return [page, *page.frames]


def _all_fubon_contexts(page: Page) -> list[Context]:
    contexts: list[Context] = []
    for candidate_page in page.context.pages:
        if candidate_page.is_closed() or "ebank.taipeifubon.com.tw" not in candidate_page.url:
            continue
        contexts.extend([candidate_page, *candidate_page.frames])
    return contexts or _contexts(page)


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
        for context in _all_fubon_contexts(page):
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
        print("點擊首頁『臺幣轉帳』功能磚。")
        try:
            page.keyboard.press("Escape")
        except Exception:
            pass
        try:
            # 與明細下載點「我的存款」相同：排除輪播中畫面外的複本，
            # 再直接觸發真正位於視窗內的功能磚連結。
            tile = visible_in_viewport(
                page, 'a:has-text("臺幣轉帳")', timeout_ms=8_000
            )
            tile.evaluate("el => el.click()")
        except Exception as exc:
            raise RuntimeError(f"找不到可點擊的『臺幣轉帳』：{exc}") from exc
        _click_text(page, "立即/預約轉帳")
    _row_for_label(page, "轉出帳號", timeout=30_000)
    return current_fubon_page(page.context, page) or page


def _choose_source_account(page: Page, area: str, source_account: str) -> None:
    if area != "台北":
        return
    row = _row_for_label(page, "轉出帳號")
    if "松高分行" in row.inner_text() and "==請選擇==" not in row.inner_text():
        print("轉出帳號已選擇：松高分行。")
        return

    # 富邦轉出帳號在有多筆帳號時，會跳出「共找到 N 筆帳號」的卡片選擇視窗；
    # 呼叫內部 onclick 函式在改版後已不可靠，改為真的點開再點卡片。
    trigger = _visible(row.get_by_text("==請選擇==", exact=False))
    if trigger is None:
        raise RuntimeError("轉出帳號找不到可點開的下拉框")
    click_nearest_control(trigger)

    card = None
    deadline = time.monotonic() + 15
    while time.monotonic() < deadline and card is None:
        for context in _all_fubon_contexts(page):
            try:
                node = _visible(context.get_by_text("松高分行", exact=False))
            except Exception:
                node = None
            if node is not None:
                card = node
                break
        if card is None:
            page.wait_for_timeout(200)
    if card is None:
        raise RuntimeError("轉出帳號的帳號選擇視窗找不到「松高分行」")
    click_nearest_control(card)

    # 選完帳號卡片後富邦會 AJAX 重建轉入帳號列；尖峰時可能超過 20 秒。
    deadline = time.monotonic() + 45
    while time.monotonic() < deadline:
        if "松高分行" in row.inner_text() and "==請選擇==" not in row.inner_text():
            print("轉出帳號已選擇：松高分行。")
            # 轉出帳號欄本身的文字先更新，轉入帳號的「自行輸入」是另一個
            # AJAX 才會重建；先停一下再找，避免點到重建前的殘留舊元素。
            page.wait_for_timeout(1_500)
            return
        page.wait_for_timeout(200)
    raise RuntimeError("已點選松高分行帳號卡片，但轉出帳號畫面未更新")


def _choose_manual_destination(page: Page) -> None:
    # 內部 id（如 inAcctTypeInput）在改版後可能變動，改用畫面上唯一穩定的
    # 「自行輸入」文字；選完轉出帳號後富邦會以 AJAX 延遲重建這個控制項，
    # _click_text 內建輪詢會等到它出現再點。
    _click_text(page, "自行輸入", exact=True, timeout=45_000)

    # 點「自行輸入」會送出 doInAccountChanged AJAX，再產生銀行與帳號欄位。
    ready_deadline = time.monotonic() + 45
    while time.monotonic() < ready_deadline:
        for context in _all_fubon_contexts(page):
            try:
                bank_select = context.locator('select[id="form1:bankList"]')
                if bank_select.count() and bank_select.is_enabled():
                    return
            except Exception:
                continue
        page.wait_for_timeout(200)
    raise RuntimeError("已點選「自行輸入」，但銀行選單仍為停用")


def _option_has_bank_code(option_text: str, bank_code: str) -> bool:
    return bool(re.search(rf"(^|\D){re.escape(bank_code)}(\D|$)", option_text.strip()))


def _fill_manual_destination(page: Page, bank_code: str, account_number: str) -> None:
    row = _row_for_label(page, "轉入帳號")
    bank_select = row.locator('select[id="form1:bankList"]')
    if not bank_select.count():
        raise RuntimeError("自行輸入找不到銀行下拉框")
    options = bank_select.locator("option")
    target_value = ""
    for index in range(options.count()):
        option = options.nth(index)
        if _option_has_bank_code(option.inner_text(), bank_code):
            target_value = option.get_attribute("value") or ""
            break
    if not target_value:
        raise RuntimeError(f"銀行下拉框找不到代碼 {bank_code}")

    # 銀行代碼欄同樣是自製彈出視窗（點開後跳出「銀行代碼」方塊清單），
    # 不是可信賴的隱藏 select；呼叫內部函式在改版後已不可靠，改為真的
    # 點開再點代碼方塊，並用隱藏 select 的實際值（點擊後由網站自己寫入）
    # 來驗證是否真的選取成功，而不是驗證我們自己寫入的值。
    trigger = _visible(row.get_by_text("=請選擇=", exact=True))
    if trigger is None:
        raise RuntimeError("轉入帳號找不到可點開的銀行代碼下拉框")
    click_nearest_control(trigger)

    code_pattern = re.compile(rf"(^|\D){re.escape(bank_code)}(\D|$)")
    code_button = None
    deadline = time.monotonic() + 15
    while time.monotonic() < deadline and code_button is None:
        for context in _all_fubon_contexts(page):
            try:
                code_button = _visible(context.get_by_text(code_pattern, exact=False))
            except Exception:
                code_button = None
            if code_button is not None:
                break
        if code_button is None:
            page.wait_for_timeout(200)
    if code_button is None:
        raise RuntimeError(f"銀行代碼清單找不到代碼 {bank_code}")
    click_nearest_control(code_button)

    deadline = time.monotonic() + 15
    while time.monotonic() < deadline:
        if bank_select.input_value() == target_value:
            break
        page.wait_for_timeout(200)
    else:
        raise RuntimeError(f"已點選銀行代碼 {bank_code}，但畫面未更新")

    inputs = row.locator(
        'input:visible:not([type="hidden"]):not([type="radio"]):not([type="button"]):not([type="submit"])'
    )
    if not inputs.count():
        raise RuntimeError("自行輸入找不到轉入帳號欄")
    account_input = inputs.last
    account_input.click()
    account_input.press("Meta+A")
    account_input.fill(account_number)


def _choose_immediate_date(page: Page) -> None:
    # 「立即」與當天日期同屬一個文字節點（例如「立即 2026/08/15」），
    # exact 文字比對永遠找不到單獨的「立即」；交易日期第一個單選鈕
    # 就是立即，直接點它比對文字更穩定。
    row = _row_for_label(page, "交易日期")
    radio = _visible(row.locator('input[type="radio"]'))
    if radio is None:
        raise RuntimeError("交易日期找不到可選的立即選項")
    if not radio.is_checked():
        try:
            radio.check(timeout=2_000)
        except Exception:
            radio.evaluate("el => el.click()")
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        if radio.is_checked():
            return
        page.wait_for_timeout(100)
    raise RuntimeError("已點選交易日期的立即選項，但未成功勾選")


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


def fill_refund(
    page: Page, area: str, source_account: str, item: dict[str, object]
) -> None:
    page = _open_transfer_form(page)
    _choose_source_account(page, area, source_account)

    _choose_manual_destination(page)
    print("轉入帳號已切換為自行輸入。")
    page.wait_for_timeout(800)

    _fill_manual_destination(
        page, str(item["bank_code"]), str(item["account_number"])
    )
    print(f"轉入帳號已填入：P={item['bank_code']}／Q={item['account_number']}")
    page.wait_for_timeout(800)

    _fill_row_inputs(page, "轉帳金額", [str(item["amount"])])
    print(f"轉帳金額已填入：{item['amount']}")
    page.wait_for_timeout(800)

    _choose_immediate_date(page)
    print("交易日期已選擇：立即。")
    page.wait_for_timeout(800)

    _fill_row_inputs(page, "給自己", [f"清潔{item['customer']}退"])
    _fill_row_inputs(page, "給對方", ["檸檬家事"])
    print("留言欄位已填寫完成。")
    page.wait_for_timeout(800)

    _click_text(page, "確認", exact=True)
    print("已點擊確認，等待進入確認資料頁。")
    _wait_confirmation(page)


def run(area: str, rows: set[int], accounts_file: Path, cdp_url: str) -> int:
    worksheet = get_worksheet(area)
    candidates = pending_atm_refunds(worksheet.get_all_values())
    selected = [item for item in candidates if int(item["sheet_row"]) in rows]
    if not selected:
        raise ValueError("勾選列中沒有符合 B=待退款、R=ATM 且 P/Q/T 完整的資料")

    zero_amount = [item for item in selected if item["amount"] == "0"]
    if zero_amount:
        rows_desc = "、".join(
            f"第{item['sheet_row']}列（{item['customer']}）" for item in zero_amount
        )
        print(f"⚠️ 轉帳金額（T欄）為 0，已略過：{rows_desc}")
    selected = [item for item in selected if item["amount"] != "0"]
    if not selected:
        raise ValueError("勾選列的轉帳金額（T欄）皆為 0，請先確認金額後再執行")

    account = load_account("fubon", area, accounts_file.expanduser())
    with sync_playwright() as playwright:
        _browser, context = connect_existing_chrome(playwright, cdp_url)
        page = ensure_login(context, account)
        try:
            for index, item in enumerate(selected):
                print(
                    f"準備第 {item['sheet_row']} 列：{item['customer']}／"
                    f"P={item['bank_code']}／Q={item['account_number']}／NT$ {item['amount']}"
                )
                fill_refund(page, area, account.bank_account, item)
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
