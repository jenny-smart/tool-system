# 鯨躍電子發票加值中心：電子發票字軌號碼匯入／配號
#
# 兩個功能都假設鯨躍第二層（ei.com.tw/InvoiceRent）已經登入（跟其他鯨躍
# 功能共用同一個 Chrome session，登入請先跑「鯨躍登入」）。
#
# 「匯入」：把財政部字軌下載存到 Google Drive 的 CSV 抓回本機，上傳到鯨躍
# 「發票號碼匯入」頁。
#
# 「配號」：目前只做到「查詢年份／月份 → 搜尋」這一步（唯讀）。查詢結果
# 後面還有勾選／確認／送出等會實際異動鯨躍會員資料的操作，畫面結構還沒
# 拿到，本功能不會自動做這些——查完之後留在畫面上，請自行手動完成。
from __future__ import annotations

import argparse
import sys
import tempfile
from pathlib import Path
from typing import Any

from playwright.sync_api import Page, sync_playwright

if __package__ in {None, ""}:
    sys.path.append(str(Path(__file__).resolve().parents[1]))
    from invoice_center.invoice_archive import (
        _escape,
        _master_spreadsheet_id,
        ensure_config_sheet,
        get_google_services,
        resolve_archive_folders,
    )
    from invoice_center.mof_serial import parse_period_arg, period_filename
else:
    from .invoice_archive import (
        _escape,
        _master_spreadsheet_id,
        ensure_config_sheet,
        get_google_services,
        resolve_archive_folders,
    )
    from .mof_serial import parse_period_arg, period_filename

from tools.invoice_center.chrome_cdp import DEFAULT_CDP_URL, connect_existing_chrome, find_existing_page

EI_IMPORT_URL = "https://www.ei.com.tw/InvoiceRent/invoicenumberimport.jsp"
EI_SECTION_URL = "https://www.ei.com.tw/InvoiceRent/invoicesection.jsp"


def find_ei_page(context) -> Page | None:
    return find_existing_page(context, ("ei.com.tw/InvoiceRent",))


def _download_from_drive_by_name(drive: Any, folder_id: str, name: str, destination: Path) -> Path:
    query = f"'{_escape(folder_id)}' in parents and name='{_escape(name)}' and trashed=false"
    files = drive.files().list(
        q=query, fields="files(id,name)", pageSize=10,
        supportsAllDrives=True, includeItemsFromAllDrives=True,
    ).execute().get("files", [])
    if not files:
        raise RuntimeError(f"Google Drive 找不到檔案：{name}（可能還沒跑過財政部字軌下載）")
    request = drive.files().get_media(fileId=files[0]["id"], supportsAllDrives=True)
    destination.write_bytes(request.execute())
    return destination


def fetch_mof_serial_csv(area: str, year: int, label: str, destination: Path) -> Path:
    """抓回財政部字軌下載存到 Google Drive「紙本發票／期別」資料夾的 CSV。"""
    drive, sheets = get_google_services()
    spreadsheet_id = _master_spreadsheet_id()
    configs = ensure_config_sheet(sheets, spreadsheet_id)
    config = configs.get(area)
    if not config:
        raise RuntimeError(f"「鯨躍發票根目錄設定」找不到已啟用的 {area} 設定")
    start_month = int(label.split("-")[0])
    yyyymm = f"{year}{start_month:02d}"
    folders = resolve_archive_folders(drive, config, yyyymm)
    filename = period_filename(year, label, area)
    return _download_from_drive_by_name(drive, folders.paper_invoice, filename, destination)


def import_serial_numbers(page: Page, csv_path: Path) -> None:
    page.goto(EI_IMPORT_URL, wait_until="domcontentloaded")
    if "invoicenumberimport" not in page.url:
        raise RuntimeError(f"導到「發票號碼匯入」頁失敗；目前頁面：{page.url}（可能尚未登入鯨躍第二層）")

    try:
        page.locator("input#upload").set_input_files(str(csv_path), timeout=10_000)
    except Exception as e:
        raise RuntimeError(f"找不到檔案上傳欄位；目前頁面：{page.url}") from e

    try:
        page.locator("#btnupload").click(timeout=10_000, force=True)
    except Exception as e:
        raise RuntimeError(f"找不到「匯入」按鈕；目前頁面：{page.url}") from e

    # 匯入後畫面會顯示什麼（成功/失敗訊息）目前還沒實測過，先等一下讓畫面
    # 有機會反應；實際結果請在 Chrome 視窗確認，有錯誤訊息請截圖回報以便
    # 補強判斷邏輯。
    page.wait_for_timeout(3000)
    print("[鯨躍發票] 已送出匯入，請在畫面上確認結果。")


def open_serial_section_query(page: Page, qyear_label: str, qmonth_label: str) -> None:
    page.goto(EI_SECTION_URL, wait_until="domcontentloaded")
    if "invoicesection" not in page.url:
        raise RuntimeError(f"導到「發票號碼配號」頁失敗；目前頁面：{page.url}（可能尚未登入鯨躍第二層）")

    # 查詢年份／月份是 select2 包裝的下拉選單，底層原生 <select> id 是
    # qyear／qmonth（select2 產生的顯示層 span id 是 select2-qyear-container
    # / select2-qmonth-container，照 select2 命名慣例反推）。用 select_option
    # 直接對原生 select 設值並觸發 change，select2 會自己同步顯示。
    try:
        page.locator("#qyear").select_option(label=qyear_label, timeout=10_000)
    except Exception as e:
        raise RuntimeError(f"「查詢年份」選不到「{qyear_label}」；目前頁面：{page.url}") from e
    try:
        page.locator("#qmonth").select_option(label=qmonth_label, timeout=10_000)
    except Exception as e:
        raise RuntimeError(f"「查詢月份」選不到「{qmonth_label}」；目前頁面：{page.url}") from e

    try:
        page.get_by_text("搜尋", exact=True).first.click(timeout=10_000, force=True)
    except Exception as e:
        raise RuntimeError(f"找不到「搜尋」按鈕；目前頁面：{page.url}") from e
    page.wait_for_timeout(2000)
    print(
        "[鯨躍發票] 已查詢配號清單；後續勾選／確認／送出等會異動鯨躍會員資料的"
        "操作請在畫面上手動完成，本功能不會自動送出。"
    )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="鯨躍電子發票字軌號碼匯入／配號")
    parser.add_argument("action", choices=["import", "section-query"])
    parser.add_argument("--area", required=True, help="區域，例如：台北、台中")
    parser.add_argument("--period", default="", help="匯入用：財政部字軌期別 YYYYMM（起始月），未輸入預設下一期")
    parser.add_argument("--qyear", default="", help="配號用：查詢年份下拉選單上的可見文字")
    parser.add_argument("--qmonth", default="", help="配號用：查詢月份下拉選單上的可見文字")
    parser.add_argument("--cdp-url", default=DEFAULT_CDP_URL)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    with sync_playwright() as playwright:
        _browser, context = connect_existing_chrome(playwright, args.cdp_url)
        print(f"瀏覽器：已連接現有 Chrome（{args.cdp_url}）")
        page = find_ei_page(context)
        if page is None:
            page = context.new_page()

        if args.action == "import":
            year, label = parse_period_arg(args.period)
            with tempfile.TemporaryDirectory(prefix="mof_serial_fetch_") as temp_dir:
                csv_path = fetch_mof_serial_csv(
                    args.area, year, label, Path(temp_dir) / period_filename(year, label, args.area)
                )
                print(f"已從 Google Drive 抓回：{csv_path.name}")
                import_serial_numbers(page, csv_path)
        else:
            if not args.qyear or not args.qmonth:
                raise ValueError("配號查詢需要 --qyear 與 --qmonth")
            open_serial_section_query(page, args.qyear, args.qmonth)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
