from __future__ import annotations

"""
逐筆下載鯨躍紙本發票的「B2B預覽(A5)」PDF，並依「訂單編號_買方統編_買方名稱.pdf」
歸檔到 Google Drive「財務根目錄／年份／雙月期別（如 07-08）／單月子資料夾（如 07）」。

選取器（點「功能」圖示、「B2B預覽(A5)」按鈕、發票方式 radio 的 id）是照畫面文字
與既有頁面（invoiceexport.jsp）的慣例寫的，還沒在真正登入的鯨躍畫面上跑過；
第一次執行如果卡住，把印出的錯誤訊息回報回來再調整選取器。
"""

import argparse
import base64
import re
import sys
from pathlib import Path

from playwright.sync_api import Page, TimeoutError as PlaywrightTimeoutError, sync_playwright

from tools.invoice_center.allowance_create import _login
from tools.invoice_center.cetustek_login_only import load_accounts
from tools.invoice_center.chrome_cdp import DEFAULT_CDP_URL, connect_existing_chrome
from tools.invoice_center.config import get_area_label, normalize_area
from tools.invoice_center.ei_export_all import month_range, normalize_month
from tools.invoice_center.invoice_archive import (
    AREA_FOLDER_NAMES,
    InvoiceArchiveProcessor,
    bi_month_period,
    get_or_create_finance_year_folder,
    get_or_create_folder,
    get_or_create_period_folder,
    upload_replacing,
)
from tools.invoice_center.query import to_roc_date


PROJECT_ROOT = Path(__file__).resolve().parents[2]
EI_QUERY_URL = "https://www.ei.com.tw/InvoiceRent/invoicequery.jsp"
HEADER_LABELS = {"訂單編號": "order_no", "買方統編": "buyer_id", "買方名稱": "buyer_name"}


def _set_readonly_value(locator, value: str) -> None:
    locator.evaluate(
        "(el, value) => { el.removeAttribute('readonly'); el.value = value; "
        "el.dispatchEvent(new Event('input', {bubbles:true})); "
        "el.dispatchEvent(new Event('change', {bubbles:true})); }",
        value,
    )


def _check_paper_only(page: Page) -> None:
    """勾選「發票方式：紙本」。優先沿用 invoiceexport.jsp 的 #searchway3；
    找不到時退回用可見文字精準比對（避免誤選「非紙本」）。"""
    radio = page.locator("#searchway3")
    if radio.count():
        radio.first.check(force=True)
        return
    clicked = page.evaluate(
        """() => {
          const radios = Array.from(document.querySelectorAll('input[type=radio]'));
          const hit = radios.find(r => {
            const label = (
              r.closest('label')?.innerText
              || r.nextElementSibling?.innerText
              || ''
            ).trim();
            return label === '紙本';
          });
          if (hit) { hit.checked = true; hit.dispatchEvent(new Event('change', {bubbles:true})); return true; }
          return false;
        }"""
    )
    if not clicked:
        raise RuntimeError("找不到「發票方式：紙本」選項，選取器可能需要更新")


def _search_paper_invoices(page: Page, start_roc: str, end_roc: str) -> None:
    page.goto(EI_QUERY_URL, wait_until="domcontentloaded")
    if page.locator("#userid").count() and page.locator("#userid").first.is_visible():
        raise RuntimeError("第二層登入已失效")
    for selector, value in (("#date1", start_roc), ("#date2", end_roc)):
        locator = page.locator(selector)
        if locator.count():
            _set_readonly_value(locator.first, value)
    _check_paper_only(page)
    _click_search(page)
    # networkidle 常常在結果表格真的插入 DOM 之前就先回來（AJAX 局部更新，
    # 不算是一次完整導覽），直接接著讀表格容易撈到 0 筆。改成明確等到結果
    # 表格（含「訂單編號」表頭）真的出現，撈不到再補按一次搜尋重試。
    _wait_for_results_table(page)


def _click_search(page: Page) -> None:
    # 實測畫面文字比對容易點到別的「搜尋」（例如瀏覽器書籤列），
    # 搜尋按鈕實際是 <a onclick="queryInvoiceList();">，直接用 onclick 屬性精準定位。
    search_button = page.locator("a[onclick='queryInvoiceList();']")
    if search_button.count():
        search_button.first.wait_for(state="visible", timeout=10_000)
        search_button.first.click()
    else:
        search_button = page.get_by_text("搜尋", exact=False).first
        search_button.wait_for(state="visible", timeout=10_000)
        search_button.click()
    page.wait_for_load_state("networkidle", timeout=15_000)


def _wait_for_results_table(page: Page, *, retry: bool = True) -> None:
    try:
        page.wait_for_function(
            "() => Array.from(document.querySelectorAll('table'))"
            ".some(t => t.innerText.includes('訂單編號'))",
            timeout=10_000,
        )
    except PlaywrightTimeoutError:
        if not retry:
            return
        _click_search(page)
        _wait_for_results_table(page, retry=False)


def _row_texts(page: Page) -> tuple[list[str] | None, list[list[str]]]:
    rows_locator = page.locator("table tr")
    count = rows_locator.count()
    header: list[str] | None = None
    data: list[list[str]] = []
    for i in range(count):
        cells = rows_locator.nth(i).locator("td, th")
        cell_count = cells.count()
        if cell_count == 0:
            continue
        texts = [cells.nth(j).inner_text().strip() for j in range(cell_count)]
        if header is None:
            if "訂單編號" in texts:
                header = texts
            continue
        row_text = " ".join(texts)
        if not any(texts) or "無資料" in row_text:
            continue
        data.append(texts)
    return header, data


def _rows_from_page(page: Page) -> list[dict[str, str]]:
    header, data = _row_texts(page)
    if not header:
        return []
    col_index: dict[str, int] = {}
    for idx, text in enumerate(header):
        for label, key in HEADER_LABELS.items():
            if label in text:
                col_index[key] = idx
    results = []
    for texts in data:
        row = {key: (texts[idx] if idx < len(texts) else "") for key, idx in col_index.items()}
        if row.get("order_no"):
            results.append(row)
    return results


def _go_next_page(page: Page) -> bool:
    next_link = page.get_by_text("下一頁", exact=False)
    if not next_link.count():
        return False
    link = next_link.first
    if not link.is_visible():
        return False
    if "disabled" in (link.get_attribute("class") or ""):
        return False
    link.click()
    page.wait_for_load_state("networkidle", timeout=15_000)
    return True


def _list_all_rows(page: Page) -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    seen: set[str] = set()
    for _ in range(50):
        for row in _rows_from_page(page):
            order_no = row["order_no"]
            if order_no not in seen:
                seen.add(order_no)
                rows.append(row)
        if not _go_next_page(page):
            break
    return rows


def _click_function_icon(page: Page, order_no: str) -> bool:
    rows_locator = page.locator("table tr")
    count = rows_locator.count()
    for i in range(count):
        row = rows_locator.nth(i)
        cells = row.locator("td")
        cell_count = cells.count()
        if cell_count == 0:
            continue
        if order_no not in row.inner_text():
            continue
        function_cell = cells.nth(cell_count - 1)
        clickable = function_cell.locator("a, img, button")
        target = clickable.first if clickable.count() else function_cell
        target.click()
        return True
    return False


def _capture_b2b_a5_pdf(context, page: Page) -> bytes:
    b2b_button = page.get_by_text("B2B預覽(A5)", exact=False)
    b2b_button.wait_for(state="visible", timeout=15_000)
    new_page = None
    try:
        with context.expect_page(timeout=5_000) as popup_info:
            b2b_button.click()
        new_page = popup_info.value
        new_page.wait_for_load_state("domcontentloaded")
        target_page = new_page
    except PlaywrightTimeoutError:
        page.wait_for_url("**/InvoiceCarrier/**", timeout=15_000)
        target_page = page
    target_page.wait_for_timeout(500)
    try:
        pdf_bytes = target_page.pdf(format="A5", print_background=True)
    except Exception:
        cdp = context.new_cdp_session(target_page)
        result = cdp.send(
            "Page.printToPDF",
            {"printBackground": True, "paperWidth": 5.83, "paperHeight": 8.27},
        )
        pdf_bytes = base64.b64decode(result["data"])
    if new_page is not None:
        new_page.close()
    else:
        page.go_back(wait_until="domcontentloaded")
    return pdf_bytes


def _sanitize(text: str) -> str:
    return re.sub(r'[\\/:*?"<>|]+', "", str(text or "").strip())


def _build_filename(order_no: str, buyer_id: str, buyer_name: str) -> str:
    parts = [part for part in (_sanitize(order_no), _sanitize(buyer_id), _sanitize(buyer_name)) if part]
    return "_".join(parts) + ".pdf"


def _resolve_month_folder(processor: InvoiceArchiveProcessor, area_label: str, yyyymm: str) -> str:
    config = processor.configs.get(area_label)
    if not config:
        raise RuntimeError(f"「鯨躍發票根目錄設定」找不到已啟用的 {area_label} 設定")
    year = yyyymm[:4]
    month2 = yyyymm[4:]
    pair = bi_month_period(yyyymm)[4:]  # 例如 "0708"
    if config.finance_root_id:
        finance_year = get_or_create_finance_year_folder(processor.drive, config.finance_root_id, year, area_label)
        bi_month_folder_id = get_or_create_period_folder(processor.drive, finance_year, pair)
    else:
        if not config.contractor_root_id:
            raise RuntimeError(f"{area_label} 尚未設定承攬費總根目錄ID")
        contractor_year = get_or_create_folder(processor.drive, config.contractor_root_id, f"{year}專員承攬服務費")
        area_parent = get_or_create_folder(processor.drive, contractor_year, AREA_FOLDER_NAMES[area_label])
        bi_month_folder_id = get_or_create_folder(processor.drive, area_parent, f"{pair[:2]}-{pair[2:]}")
    return get_or_create_folder(processor.drive, bi_month_folder_id, month2)


def run(area: str, cdp_url: str, month: str, *, upload: bool = True) -> int:
    area_key = normalize_area(area)
    area_label = get_area_label(area_key)
    yyyymm = normalize_month(month)
    start, end = month_range(yyyymm)
    start_roc, end_roc = to_roc_date(start), to_roc_date(end)
    accounts = load_accounts(None)

    processor = InvoiceArchiveProcessor() if upload else None
    folder_id = _resolve_month_folder(processor, area_label, yyyymm) if processor else None

    output_dir = PROJECT_ROOT / "outputs" / "paper_invoice_pdf" / area_label / yyyymm
    output_dir.mkdir(parents=True, exist_ok=True)

    with sync_playwright() as playwright:
        _browser, context = connect_existing_chrome(playwright, cdp_url)
        # 鯨躍的 B2B預覽(A5) 頁面 onload 會自己叫 window.print()；
        # 用 context 層級的 init script 蓋掉，避免擋住 headless PDF 產生。
        context.add_init_script("window.print = () => {};")
        print(f"瀏覽器：已連接現有 Chrome（{cdp_url}）")
        page = _login(context, area_key, accounts)

        _search_paper_invoices(page, start_roc, end_roc)
        rows = _list_all_rows(page)
        print(f"查到 {len(rows)} 筆紙本發票（{start} ～ {end}）")

        saved = 0
        for index, row in enumerate(rows, start=1):
            order_no = row.get("order_no", "")
            print(f"[{index}/{len(rows)}] {order_no}\t{row.get('buyer_name', '')}")
            if index > 1:
                _search_paper_invoices(page, start_roc, end_roc)
            if not _click_function_icon(page, order_no):
                print(f"  找不到列，略過：{order_no}", file=sys.stderr)
                continue
            try:
                pdf_bytes = _capture_b2b_a5_pdf(context, page)
            except Exception as exc:
                print(f"  產生 PDF 失敗：{exc}", file=sys.stderr)
                continue
            filename = _build_filename(order_no, row.get("buyer_id", ""), row.get("buyer_name", ""))
            local_path = output_dir / filename
            local_path.write_bytes(pdf_bytes)
            if processor and folder_id:
                upload_replacing(processor.drive, local_path, folder_id)
            print(f"RESULT_FILE:{local_path}")
            saved += 1

        if processor:
            processor.write_update_log(
                function_name="紙本發票PDF建檔",
                area=area_label,
                period=yyyymm,
                status="成功" if saved == len(rows) else "部分完成",
                count=saved,
                message=f"共 {len(rows)} 筆，成功 {saved} 筆",
            )

    print(f"完成：共 {len(rows)} 筆，成功產生 {saved} 份 PDF；已存至 {output_dir}")
    return 0


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="下載鯨躍紙本發票 B2B預覽(A5) PDF 並歸檔")
    parser.add_argument("month", help="年月 YYYYMM，例如 202607")
    parser.add_argument("--area", required=True, help="地區，例如：台北、台中、桃園、新竹、高雄")
    parser.add_argument("--cdp-url", default=DEFAULT_CDP_URL)
    parser.add_argument("--no-upload", action="store_true", help="只在本機存 PDF，不上傳 Google Drive")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    return run(args.area, args.cdp_url, args.month, upload=not args.no_upload)


if __name__ == "__main__":
    raise SystemExit(main())
