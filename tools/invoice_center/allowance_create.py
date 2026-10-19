from __future__ import annotations

import argparse
import re
import time
from datetime import datetime
from decimal import Decimal
from zoneinfo import ZoneInfo

from playwright.sync_api import Locator, Page, sync_playwright

from tools.invoice_center.allowance_filter import pending_allowances
from tools.invoice_center.cetustek_login_only import load_accounts
from tools.invoice_center.chrome_cdp import DEFAULT_CDP_URL, connect_existing_chrome
from tools.lemon_backend.stored_value_sheet import get_worksheet


ALLOWANCE_ADD_URL = "https://www.ei.com.tw/InvoiceRent/allowanceadd.jsp"
ALLOWANCE_QUERY_URL = "https://www.ei.com.tw/InvoiceRent/allowancequery.jsp"


def _visible(locator: Locator) -> Locator | None:
    for index in range(locator.count()):
        item = locator.nth(index)
        if item.is_visible():
            return item
    return None


def _login(context: object, area: str, accounts: dict) -> Page:
    """接續目前畫面登入這區鯨躍第二層（共用 ei_session.open_ei_page）。"""
    from tools.invoice_center.ei_session import open_ei_page

    return open_ei_page(context, area, accounts)[0]


def _open_allowance(page: Page) -> None:
    # 登入後直接進折讓單開立頁，不經「折讓單作業 > 折讓單開立」選單。
    if "allowanceadd.jsp" not in page.url:
        page.goto(ALLOWANCE_ADD_URL)
    page.wait_for_load_state("domcontentloaded")
    page.locator("#qyear").wait_for(state="visible")


def _money(value: str) -> Decimal:
    return Decimal(str(value).replace(",", "").replace("$", "").replace("NT", "").strip())


def _select_allowance_reason(page: Page, full_refund: bool) -> None:
    select = page.locator("#s_remark")
    options = select.locator("option")
    available: list[tuple[str, str]] = []
    for index in range(options.count()):
        option = options.nth(index)
        label = option.inner_text().strip()
        value = str(option.get_attribute("value") or "").strip()
        available.append((value, label))

    for value, label in available:
        normalized = re.sub(r"[\s,，、。．]+", "", label)
        is_match = "全退" in normalized if full_refund else ("部分退" in normalized or "部份退" in normalized)
        if is_match:
            select.select_option(value=value)
            return

    labels = "、".join(label for _value, label in available if label) or "無"
    expected = "全退" if full_refund else "部分退"
    raise RuntimeError(f"鯨躍折讓原因找不到「{expected}」選項；實際選項：{labels}")


def _finish_allowance_save(page: Page) -> str:
    from playwright.sync_api import TimeoutError as PlaywrightTimeoutError
    notice = page.locator("#msg").filter(has_text=re.compile("折讓開立成功"))
    confirmation_attempted = False
    deadline = time.monotonic() + 30
    while time.monotonic() < deadline:
        # Saving may finish before the alert's entrance/exit animation settles.
        # Success wins; never require clicking an already dismissed confirmation.
        if notice.is_visible():
            text = notice.inner_text()
            matches = re.findall(r"[A-Z]{2}\d{8,}", text)
            if matches:
                return matches[-1]
            raise RuntimeError("折讓已成功，但訊息缺少折讓單號，請填入已開立單號接續")
        confirm = page.locator("#alertify-ok")
        if not confirmation_attempted and confirm.is_visible():
            confirmation_attempted = True
            try:
                confirm.click(timeout=1000)
            except PlaywrightTimeoutError:
                pass  # It can disappear because saving already completed.
        page.wait_for_timeout(200)
    raise RuntimeError("未讀到折讓成功單號；如已開立，請填入已開立折讓單號接續新發票")


def _create_one(page: Page, invoice_no: str, untaxed: str, *, invoice_year: int | None = None,
                require_full: bool = False, before_save=None) -> str:
    year = page.locator("#qyear")
    if invoice_year is None:
        year.select_option(index=1)
    else:
        choices = []
        for option in year.locator("option").all():
            value = str(option.get_attribute("value") or "")
            label = option.inner_text().strip()
            if value in {str(invoice_year), str(invoice_year - 1911)} or label in {
                str(invoice_year), str(invoice_year - 1911), f"{invoice_year}年", f"{invoice_year - 1911}年"
            }:
                choices.append(value)
        if len(choices) != 1:
            raise RuntimeError("找不到原發票年度，禁止折讓")
        year.select_option(value=choices[0])
    page.locator("#invoicenumber").fill(invoice_no)
    page.locator("a[onclick='SearchInvoice();']").click()
    page.locator("#s_remark").wait_for(state="visible")

    available_text = page.locator("body").inner_text()
    sales_match = re.search(r"銷售額[\s\S]*?應稅:\$\s*([\d,]+(?:\.\d+)?)", available_text)
    sales_amount = _money(sales_match.group(1)) if sales_match else Decimal("-1")
    if require_full and (sales_amount <= 0 or _money(untaxed) != sales_amount):
        raise RuntimeError("原票可折讓銷售額與全額折讓金額不符，禁止重複或部分折讓")
    full_refund = _money(untaxed) == sales_amount
    _select_allowance_reason(page, full_refund=full_refund)

    page.locator("img[title='發票明細查詢']").click()
    products = page.locator("#processresult a[onclick^='setInvoiceDetail']")
    select_product = products.first
    select_product.wait_for(state="visible")
    if require_full and products.count() != 1:
        raise RuntimeError("原票有多筆明細，請先人工核對全額折讓，未送出")
    select_product.click()
    page.locator("#unitprice").wait_for(state="visible")

    # Full allowance uses the invoice detail exactly as EI populated it.
    # Editing unit price triggers recalculation of amount/tax on old two-copy invoices.
    if not full_refund:
        page.locator("#unitprice").fill(untaxed)
    page.locator("a[onclick='goDetail();']").click()
    confirm = page.get_by_text("確定", exact=True)
    confirm.wait_for(state="visible")
    confirm.click()
    page.locator("#save2").wait_for(state="visible")
    if before_save is not None:
        before_save()
    page.locator("#save2").click()
    return _finish_allowance_save(page)


def _roc_to_ad(text: str) -> str:
    year, month, day = (int(part) for part in text.split("/"))
    return f"{year + 1911}/{month:02d}/{day:02d}"


def parse_allowance_rows(rows_text: list[str], invoice_no: str, *, untaxed: str = "",
                         not_before: str = "") -> tuple[str, str] | None:
    """從折讓單查詢結果各列文字，找這一筆的折讓單（日期、單號）。

    條件：原發票為 invoice_no、未作廢、折讓日期不早於登記日期（not_before，YYYY/MM/DD）、
    列中有這筆的未稅金額（四捨五入到整數，容許 ±1）。同一張發票以前別次的折讓不會被誤認。
    """
    invoice_no = invoice_no.strip().upper()
    target = _money(untaxed) if untaxed else None
    since = re.sub(r"\D", "", not_before)
    for text in rows_text:
        if invoice_no not in text.upper() or "作廢" in text:
            continue
        numbers = [n for n in re.findall(r"[A-Z]{2}\d{8,}", text.upper()) if n != invoice_no]
        if not numbers:
            continue
        dates = re.findall(r"(?<!\d)(\d{3}/\d{2}/\d{2})(?!\d)", text)
        allowance_date = _roc_to_ad(dates[0]) if dates else ""
        if since and allowance_date and re.sub(r"\D", "", allowance_date) < since[:8]:
            continue
        if target is not None:
            amounts = [_money(a) for a in re.findall(r"\d[\d,]*(?:\.\d+)?", text.replace(invoice_no, ""))]
            if not any(abs(a - target.quantize(Decimal("1"))) <= 1 for a in amounts):
                continue
        return allowance_date, numbers[0]
    return None


def _query_row_inputs(page: Page, label: str) -> Locator:
    row = page.locator("tr").filter(has=page.get_by_text(label, exact=True)).first
    return row.locator("input[type='text'], input:not([type])")


def _click_query_search(page: Page) -> None:
    """按查詢頁的「搜尋」。鯨躍按鈕是 <a>搜尋<span class="icon">›</span></a>，
    文字其實是「搜尋›」，比對時先去掉符號；只點看得到的那顆。"""
    controls = page.locator("a, button, input[type='button'], input[type='submit'], [role='button']")
    for index in range(controls.count()):
        control = controls.nth(index)
        if not control.is_visible():
            continue
        text = control.evaluate("el => el.tagName === 'INPUT' ? el.value : (el.textContent || '')")
        if re.sub(r"[\W_]+", "", text or "") == "搜尋":
            control.click()
            return
    texts = page.get_by_text("搜尋", exact=False)
    for index in range(texts.count()):
        if texts.nth(index).is_visible():
            texts.nth(index).click()
            return
    raise RuntimeError(f"鯨躍折讓單查詢頁找不到「搜尋」按鈕，無法確認是否已開立折讓單；目前頁面：{page.url}")


def find_existing_allowance(page: Page, invoice_no: str, days: int = 120, *, untaxed: str = "",
                            not_before: str = "") -> tuple[str, str] | None:
    """到鯨躍「折讓單查詢」用原發票號碼查近 days 天的折讓單；有就回傳（日期, 單號）。

    用來確認「已開立但沒回填」：開立前先查，避免重複開立；開立後讀不到單號時再查一次補回。
    查詢本身失敗會丟例外（呼叫端停止，不在不確定時開立）。
    """
    page.goto(ALLOWANCE_QUERY_URL, wait_until="domcontentloaded")
    today = datetime.now(ZoneInfo("Asia/Taipei")).date()
    start = today.fromordinal(today.toordinal() - days)
    roc = lambda d: f"{d.year - 1911}/{d.month:02d}/{d.day:02d}"  # noqa: E731
    dates = _query_row_inputs(page, "折讓單日期")
    if dates.count() < 2:
        raise RuntimeError("鯨躍折讓單查詢頁找不到「折讓單日期」欄位，無法確認是否已開立折讓單")
    for locator, value in ((dates.nth(0), roc(start)), (dates.nth(1), roc(today))):
        locator.evaluate(
            "(el, v) => { el.removeAttribute('readonly'); el.value = v; "
            "el.dispatchEvent(new Event('input', {bubbles:true})); el.dispatchEvent(new Event('change', {bubbles:true})); }",
            value,
        )
    keyword = _query_row_inputs(page, "關鍵字")
    if not keyword.count():
        raise RuntimeError("鯨躍折讓單查詢頁找不到「關鍵字」欄位，無法確認是否已開立折讓單")
    keyword.first.fill(invoice_no)
    _click_query_search(page)
    page.wait_for_load_state("networkidle", timeout=20_000)
    page.wait_for_timeout(800)
    rows = page.locator("tr").filter(has_text=invoice_no)
    texts = [rows.nth(i).inner_text() for i in range(rows.count())]
    return parse_allowance_rows(texts, invoice_no, untaxed=untaxed, not_before=not_before)


def _existing_allowance(values: list[list[str]], sheet_row: int) -> tuple[str, str]:
    row = values[sheet_row - 1] if 0 < sheet_row <= len(values) else []
    allowance_date = str(row[26]).strip() if len(row) > 26 else ""
    allowance_no = str(row[27]).strip() if len(row) > 27 else ""
    return allowance_date, allowance_no


def run(area: str, cdp_url: str, selected_rows: set[int]) -> int:
    worksheet = get_worksheet(area)
    values = worksheet.get_all_values()
    rows = [item for item in pending_allowances(values) if item["sheet_row"] in selected_rows]
    if not rows:
        # selected rows may already have been completed manually or by an earlier run.
        completed = []
        for sheet_row in sorted(selected_rows):
            allowance_date, allowance_no = _existing_allowance(values, sheet_row)
            if allowance_date or allowance_no:
                completed.append(sheet_row)
        if completed:
            print(f"已略過既有折讓結果列：{', '.join(map(str, completed))}；AA／AB 不覆蓋")
            return 0
        raise ValueError("沒有可執行的待退款折讓資料")

    accounts = load_accounts(None)
    with sync_playwright() as playwright:
        _browser, context = connect_existing_chrome(playwright, cdp_url)
        page = _login(context, area, accounts)
        _open_allowance(page)
        for item in rows:
            sheet_row = int(item["sheet_row"])
            # Re-read the row immediately before creating the allowance. This protects
            # manual corrections made after the task was queued and prevents retries
            # from creating a second allowance.
            current_values = worksheet.get_all_values()
            allowance_date, allowance_no = _existing_allowance(current_values, sheet_row)
            if allowance_date or allowance_no:
                print(
                    f"第 {sheet_row} 列已有折讓結果"
                    f"（AA={allowance_date or '-'}／AB={allowance_no or '-'}），略過開立且不覆蓋"
                )
                continue

            invoice_no = str(item["invoice_no"])
            # 開立前：鯨躍已有這張發票的折讓單（上次開了但沒回填）就補回，不重開
            match_args = {"untaxed": str(item["untaxed_amount"]), "not_before": str(item.get("registered_on") or "")}
            found = find_existing_allowance(page, invoice_no, **match_args)
            if found:
                allowance_date, number = found
                print(f"第 {sheet_row} 列：鯨躍已有 {invoice_no} 的折讓單 {number}，不重開，補回 AA／AB")
            else:
                _open_allowance(page)
                try:
                    number = _create_one(page, invoice_no, str(item["untaxed_amount"]))
                except Exception as exc:
                    # 開立後讀不到單號：再查一次，有就補回；查不到才算開立失敗
                    found = find_existing_allowance(page, invoice_no, **match_args)
                    if not found:
                        raise RuntimeError(f"第 {sheet_row} 列 {invoice_no} 折讓單未開立成功：{exc}") from exc
                    allowance_date, number = found
                    print(f"第 {sheet_row} 列：存檔後未讀到單號，查詢確認已開立 {number}，補回 AA／AB")
                else:
                    allowance_date = datetime.now(ZoneInfo("Asia/Taipei")).strftime("%Y/%m/%d")
                _open_allowance(page)
            allowance_date = allowance_date or datetime.now(ZoneInfo("Asia/Taipei")).strftime("%Y/%m/%d")

            # One final read before writing, so a concurrent/manual update always wins.
            latest_values = worksheet.get_all_values()
            existing_date, existing_no = _existing_allowance(latest_values, sheet_row)
            updates = []
            if not existing_date:
                updates.append({"range": f"AA{sheet_row}", "values": [[allowance_date]]})
            if not existing_no:
                updates.append({"range": f"AB{sheet_row}", "values": [[number]]})
            if updates:
                worksheet.batch_update(updates, value_input_option="USER_ENTERED")
            print(
                f"第 {sheet_row} 列：{item['invoice_no']} → {number}；"
                f"AA／AB 僅補空白欄位，既有資料不覆蓋"
            )
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

