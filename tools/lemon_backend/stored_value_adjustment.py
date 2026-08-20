from __future__ import annotations

import argparse
import re
import time
from datetime import datetime
from urllib.parse import urljoin
from zoneinfo import ZoneInfo

from playwright.sync_api import Locator, Page, sync_playwright
from playwright.sync_api import TimeoutError as PlaywrightTimeoutError

from tools.invoice_center.chrome_cdp import DEFAULT_CDP_URL, connect_existing_chrome, find_existing_page
from tools.lemon_backend.config import get_credentials
from tools.lemon_backend.stored_value_filter import build_note, pending_stored_value_adjustments
from tools.lemon_backend.stored_value_sheet import get_worksheet


BASE_URL = "https://backend.lemonclean.com.tw"


def _visible(locator: Locator) -> Locator | None:
    for index in range(locator.count()):
        item = locator.nth(index)
        if item.is_visible():
            return item
    return None


def _first_visible(page: Page, selectors: tuple[str, ...]) -> Locator | None:
    for selector in selectors:
        item = _visible(page.locator(selector))
        if item is not None:
            return item
    return None


def _click_text(page: Page, *labels: str, timeout: int = 15_000) -> None:
    deadline = time.monotonic() + timeout / 1000
    while time.monotonic() < deadline:
        for label in labels:
            item = _visible(page.get_by_role("button", name=label, exact=True))
            if item is None:
                item = _visible(page.get_by_text(label, exact=True))
            if item is not None:
                item.click()
                return
        page.wait_for_timeout(200)
    raise RuntimeError(f"找不到按鈕：{'／'.join(labels)}")


def _login(page: Page, area: str) -> None:
    credentials = get_credentials(area)
    page.goto(f"{BASE_URL}/login", wait_until="domcontentloaded", timeout=60_000)
    if "/login" not in page.url:
        # 不沿用其他區域的登入狀態。
        logout_links = page.locator('a[href*="logout"]')
        logout_href = logout_links.first.get_attribute("href") if logout_links.count() else ""
        page.goto(logout_href or f"{BASE_URL}/logout", wait_until="domcontentloaded")
        page.goto(f"{BASE_URL}/login", wait_until="domcontentloaded")
    if "/login" not in page.url:
        raise RuntimeError("無法登出目前後台帳號，已停止以避免操作錯誤區域")
    email = _first_visible(page, ('input[name="email"]', 'input[type="email"]'))
    password = _first_visible(page, ('input[name="password"]', 'input[type="password"]'))
    if email is None or password is None:
        raise RuntimeError("後台登入頁缺少帳號或密碼欄位")
    email.fill(credentials.email)
    password.fill(credentials.password)
    submit = _first_visible(page, ('button[type="submit"]', 'input[type="submit"]'))
    if submit is None:
        raise RuntimeError("後台登入頁找不到登入按鈕")
    submit.click()
    page.wait_for_load_state("domcontentloaded")
    page.goto(f"{BASE_URL}/purchase", wait_until="domcontentloaded", timeout=60_000)
    if "/login" in page.url:
        raise RuntimeError(f"{area}後台登入失敗")


def _open_history(page: Page, order_no: str) -> Page:
    page.goto(f"{BASE_URL}/purchase?orderNo={order_no}", wait_until="domcontentloaded", timeout=60_000)
    search = _first_visible(page, ('button:has-text("搜尋")', 'input[value="搜尋"]'))
    if search is not None:
        search.click()
        page.wait_for_load_state("domcontentloaded")
    row = page.locator("tr", has_text=order_no).first
    row.wait_for(state="visible", timeout=30_000)
    history = _visible(row.get_by_text("儲值金歷程", exact=True))
    if history is None:
        history = _first_visible(page, ('a:has-text("儲值金歷程")', 'button:has-text("儲值金歷程")'))
    if history is None:
        raise RuntimeError(f"{order_no} 找不到儲值金歷程")
    href = history.get_attribute("href")
    if href:
        page.goto(urljoin(BASE_URL, href), wait_until="domcontentloaded", timeout=60_000)
    else:
        history.click()
        page.wait_for_timeout(800)
        if "/stored_value_histories" not in page.url:
            matching_pages = [candidate for candidate in page.context.pages if "/stored_value_histories" in candidate.url]
            if matching_pages:
                page = matching_pages[-1]
    page.wait_for_url("**/stored_value_histories", timeout=30_000)
    return page


def _select_option(select: Locator, text: str, *, contains: bool = False) -> None:
    options = select.locator("option")
    for index in range(options.count()):
        label = options.nth(index).inner_text().strip()
        if (text in label) if contains else (text == label):
            select.select_option(index=index)
            return
    raise RuntimeError(f"下拉選單找不到：{text}")


def _already_done(page: Page, order_no: str, amount: str, note: str) -> bool:
    compact = re.sub(r"[,$\s]", "", page.locator("body").inner_text())
    compact_note = re.sub(r"[,$\s]", "", note)
    return order_no in compact and compact_note in compact and amount in compact


def _logout(page: Page) -> None:
    links = page.locator('a[href*="logout"]')
    href = links.first.get_attribute("href") if links.count() else ""
    if href:
        page.goto(href, wait_until="domcontentloaded")


def _submit_adjustment(page: Page, item: dict[str, object], date_text: str) -> bool:
    order_no = str(item["order_no"])
    amount = str(item["amount"])
    note = build_note(str(item["note"]), date_text, str(item["suffix"]))
    if _already_done(page, order_no, amount, note):
        print(f"略過重複異動：{order_no}／{amount}／{note}")
        return False

    adjust_button = page.locator('button[data-target="#basicModal"]', has_text="異動儲值金")
    adjust_button.wait_for(state="visible", timeout=30_000)
    modal = page.locator("#basicModal")
    adjust_button.click()
    try:
        modal.wait_for(state="visible", timeout=15_000)
    except PlaywrightTimeoutError:
        # 頁面剛從上一筆訂單切換過來時，按鈕看起來可點但點擊事件有時候沒接
        # 上，Bootstrap modal 不會真的開啟（沒有錯誤，只是彈窗一直是 hidden）。
        # 重新點一次通常就能正常開啟，還是失敗才真的中止。
        print(f"{order_no} 第一次點擊「異動儲值金」後彈窗沒開啟，重試一次")
        adjust_button.click()
        modal.wait_for(state="visible", timeout=15_000)
    selects = modal.locator("select")
    if selects.count() < 3:
        raise RuntimeError(f"{order_no} 異動視窗只有 {selects.count()} 個下拉欄位")
    order_select, action_select, item_select = selects.nth(0), selects.nth(1), selects.nth(2)
    order_select.locator("option", has_text=order_no).first.wait_for(state="attached", timeout=15_000)
    item_select.locator("option", has_text="異動費").first.wait_for(state="attached", timeout=15_000)
    _select_option(order_select, order_no, contains=True)
    _select_option(action_select, str(item["action"]))
    _select_option(item_select, "異動費")

    amount_field = _first_visible(
        page,
        ('#basicModal input[type="number"]:visible',),
    )
    note_field = _first_visible(page, ('#basicModal textarea:visible',))
    if amount_field is None or note_field is None:
        raise RuntimeError(f"{order_no} 找不到異動金額或備註欄位")
    amount_field.fill(amount)
    note_field.fill(note)

    dialog_messages: list[str] = []
    page.once("dialog", lambda dialog: (dialog_messages.append(dialog.message), dialog.accept()))
    send = (
        _visible(modal.get_by_role("button", name="送出", exact=True))
        or _visible(modal.get_by_role("button", name="確定", exact=True))
        or _visible(modal.get_by_text("送出", exact=True))
        or _visible(modal.get_by_text("確定", exact=True))
    )
    if send is None:
        raise RuntimeError(f"{order_no} 找不到送出按鈕")
    with page.expect_navigation(wait_until="domcontentloaded", timeout=30_000):
        send.click()
    if not _already_done(page, order_no, amount, note):
        raise RuntimeError(f"{order_no} 送出後查無新增紀錄，請人工確認")
    return True


def run(area: str, cdp_url: str, selected_rows: set[int]) -> int:
    worksheet = get_worksheet(area)
    pending = pending_stored_value_adjustments(worksheet.get_all_values())
    pending = [item for item in pending if int(item["sheet_row"]) in selected_rows]
    if not pending:
        raise ValueError("勾選列已不是待扣／待退儲值金，或缺少訂單編號／金額")
    date_text = datetime.now(ZoneInfo("Asia/Taipei")).strftime("%m/%d")
    completed = 0
    with sync_playwright() as playwright:
        _browser, context = connect_existing_chrome(playwright, cdp_url)
        page = find_existing_page(context, ("backend.lemonclean.com.tw",)) or context.new_page()
        _login(page, area)
        try:
            for item in pending:
                print(f"處理第 {item['sheet_row']} 列：{item['order_no']}／{item['action']} NT$ {item['amount']}")
                page = _open_history(page, str(item["order_no"]))
                created = _submit_adjustment(page, item, date_text)
                if not str(item["completed_at"]).strip():
                    completed_at = datetime.now(ZoneInfo("Asia/Taipei")).strftime("%Y/%m/%d %H:%M:%S")
                    worksheet.update_cell(
                        int(item["sheet_row"]), int(item["time_column"]), completed_at
                    )
                    print(f"已回寫第 {item['sheet_row']} 列時間：{completed_at}")
                if created:
                    completed += 1
                    print(f"完成：{item['order_no']}／{date_text}{item['suffix']}")
        finally:
            _logout(page)
    print(f"檸檬後台儲值金異動完成：{completed}/{len(pending)} 筆（其餘為已存在而略過）")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="依清潔異動工作表異動檸檬後台儲值金")
    parser.add_argument("--area", required=True)
    parser.add_argument("--cdp-url", default=DEFAULT_CDP_URL)
    parser.add_argument("--rows", required=True, help="勾選的工作表列號，以逗號分隔")
    args = parser.parse_args()
    rows = {int(value) for value in args.rows.split(",") if value.strip().isdigit()}
    if not rows:
        raise ValueError("沒有勾選儲值金異動資料")
    return run(args.area, args.cdp_url, rows)


if __name__ == "__main__":
    raise SystemExit(main())
