# 鯨躍電子發票加值中心：電子發票字軌號碼匯入／配號
#
# 三個功能都假設鯨躍第二層（ei.com.tw/InvoiceRent）已經登入（跟其他鯨躍
# 功能共用同一個 Chrome session，登入請先跑「鯨躍登入」）。
#
# 「匯入」：把財政部字軌下載存到 Google Drive 的 CSV 抓回本機，上傳到鯨躍
# 「發票號碼匯入」頁。
#
# 「配號」：查詢年份／月份 → 搜尋 → 對查到的那筆按「配號」（扳手圖示）→
# 依「鯨躍字軌配號設定」分頁的保留本數，把總本數拆成「線上單張開立」＋
# 「API串接開立(虛)」→ 儲存。這一步會實際異動鯨躍的發票號碼配置，且畫面
# 明白寫「一旦發票已有使用情況，即無法回復」，執行結果一律寫進
# 「鯨躍字軌匯入配號Log」。
from __future__ import annotations

import argparse
import sys
import tempfile
from datetime import datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from playwright.sync_api import Page, sync_playwright

if __package__ in {None, ""}:
    sys.path.append(str(Path(__file__).resolve().parents[1]))
    from invoice_center.invoice_archive import (
        _ensure_sheet,
        _escape,
        _master_spreadsheet_id,
        ensure_config_sheet,
        get_google_services,
        resolve_archive_folders,
    )
    from invoice_center.mof_serial import parse_period_arg, period_filename
else:
    from .invoice_archive import (
        _ensure_sheet,
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

ALLOCATION_CONFIG_SHEET = "鯨躍字軌配號設定"
ALLOCATION_CONFIG_HEADERS = ["地區", "線上單張開立保留本數", "備註"]
DEFAULT_ALLOCATION_CONFIG = [
    ["台北", "3", ""],
    ["台中", "3", ""],
    ["桃園", "3", ""],
    ["新竹", "3", ""],
    ["高雄", "3", ""],
]
SERIAL_LOG_SHEET = "鯨躍字軌匯入配號Log"
SERIAL_LOG_HEADERS = ["執行時間", "功能", "地區", "期別", "狀態", "訊息"]
TAIPEI_TZ = ZoneInfo("Asia/Taipei")


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


def ensure_allocation_config(sheets: Any, spreadsheet_id: str) -> dict[str, int]:
    """讀取（必要時建立）「鯨躍字軌配號設定」分頁：地區 -> 線上單張開立保留本數。"""
    _ensure_sheet(sheets, spreadsheet_id, ALLOCATION_CONFIG_SHEET)
    values = sheets.spreadsheets().values().get(
        spreadsheetId=spreadsheet_id, range=f"'{ALLOCATION_CONFIG_SHEET}'!A:C"
    ).execute().get("values", [])
    if not values:
        sheets.spreadsheets().values().update(
            spreadsheetId=spreadsheet_id,
            range=f"'{ALLOCATION_CONFIG_SHEET}'!A1",
            valueInputOption="RAW",
            body={"values": [ALLOCATION_CONFIG_HEADERS, *DEFAULT_ALLOCATION_CONFIG]},
        ).execute()
        values = [ALLOCATION_CONFIG_HEADERS, *DEFAULT_ALLOCATION_CONFIG]
    else:
        existing = {str(row[0]).strip() for row in values[1:] if row}
        missing = [row for row in DEFAULT_ALLOCATION_CONFIG if row[0] not in existing]
        if missing:
            sheets.spreadsheets().values().append(
                spreadsheetId=spreadsheet_id,
                range=f"'{ALLOCATION_CONFIG_SHEET}'!A:C",
                valueInputOption="RAW",
                insertDataOption="INSERT_ROWS",
                body={"values": missing},
            ).execute()
            values.extend(missing)
    result: dict[str, int] = {}
    for row in values[1:]:
        if not row:
            continue
        area = str(row[0]).strip()
        try:
            reserve = int(str(row[1]).strip()) if len(row) > 1 and str(row[1]).strip() else 3
        except ValueError:
            reserve = 3
        if area:
            result[area] = reserve
    return result


def _ensure_serial_log_sheet(sheets: Any, spreadsheet_id: str) -> None:
    _ensure_sheet(sheets, spreadsheet_id, SERIAL_LOG_SHEET)
    current = sheets.spreadsheets().values().get(
        spreadsheetId=spreadsheet_id, range=f"'{SERIAL_LOG_SHEET}'!A1:F1"
    ).execute().get("values", [])
    if not current:
        sheets.spreadsheets().values().update(
            spreadsheetId=spreadsheet_id,
            range=f"'{SERIAL_LOG_SHEET}'!A1",
            valueInputOption="RAW",
            body={"values": [SERIAL_LOG_HEADERS]},
        ).execute()


def write_serial_log(sheets: Any, spreadsheet_id: str, *, function_name: str, area: str, period: str, status: str, message: str) -> None:
    _ensure_serial_log_sheet(sheets, spreadsheet_id)
    sheets.spreadsheets().values().append(
        spreadsheetId=spreadsheet_id,
        range=f"'{SERIAL_LOG_SHEET}'!A:F",
        valueInputOption="RAW",
        insertDataOption="INSERT_ROWS",
        body={"values": [[
            datetime.now(TAIPEI_TZ).strftime("%Y-%m-%d %H:%M:%S"),
            function_name, area, period, status, message,
        ]]},
    ).execute()


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


def _search_serial_section(page: Page, qyear_label: str, qmonth_label: str) -> None:
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

    # 按鈕裡除了「搜尋」文字，還有一個 <span class="icon">›</span>，整個元素
    # 的文字其實是「搜尋›」，exact=True 反而比對不到，改用寬鬆比對。
    try:
        page.get_by_text("搜尋", exact=False).first.click(timeout=10_000, force=True)
    except Exception as e:
        raise RuntimeError(f"找不到「搜尋」按鈕；目前頁面：{page.url}") from e
    page.wait_for_timeout(2000)


def open_serial_section_query(page: Page, qyear_label: str, qmonth_label: str) -> None:
    """只查詢，不配號（唯讀，供人工檢視）。"""
    _search_serial_section(page, qyear_label, qmonth_label)
    print("[鯨躍發票] 已查詢配號清單，請在畫面上確認結果。")


def allocate_serial_numbers(page: Page, area: str, qyear_label: str, qmonth_label: str, reserve_count: int) -> tuple[int, int, int]:
    """查詢後對結果列按「配號」，依保留本數拆成線上單張開立／API串接開立(虛)並儲存。
    回傳 (總本數, 線上單張開立本數, API串接開立本數)。"""
    _search_serial_section(page, qyear_label, qmonth_label)

    row = page.locator("tr").filter(has_text=qyear_label).filter(has_text=qmonth_label)
    try:
        row.first.wait_for(state="visible", timeout=10_000)
    except Exception as e:
        raise RuntimeError(f"查詢結果找不到「{qyear_label} {qmonth_label}」這一列；目前頁面：{page.url}") from e

    cells = row.first.locator("td")
    cell_count = cells.count()
    if cell_count < 7:
        raise RuntimeError(f"查詢結果列欄位數不符預期（{cell_count} 欄），請截圖回報以便調整。")
    total_text = cells.nth(6).inner_text().strip()
    try:
        total = int(total_text)
    except ValueError as e:
        raise RuntimeError(f"讀不到總本數（讀到「{total_text}」）；請截圖回報以便調整。") from e

    if reserve_count > total:
        raise RuntimeError(f"保留本數（{reserve_count}）大於總本數（{total}），無法配號，請確認「{ALLOCATION_CONFIG_SHEET}」設定。")
    api_count = total - reserve_count

    try:
        row.first.locator("img[title='配號']").click(timeout=10_000, force=True)
    except Exception as e:
        raise RuntimeError(f"找不到「配號」（扳手）圖示；目前頁面：{page.url}") from e
    page.wait_for_timeout(1000)

    def _fill_amount(label_text: str, amount: int) -> None:
        label = page.get_by_text(label_text, exact=False)
        try:
            label.first.wait_for(state="visible", timeout=8000)
        except Exception as e:
            raise RuntimeError(f"配號視窗找不到「{label_text}」欄位；目前頁面：{page.url}") from e
        input_box = label.first.locator("xpath=following::input[1]")
        try:
            input_box.first.fill(str(amount), timeout=8000)
        except Exception as e:
            raise RuntimeError(f"「{label_text}」本數欄位填不進去；目前頁面：{page.url}") from e

    _fill_amount("線上單張開立", reserve_count)
    _fill_amount("API串接開立", api_count)

    try:
        page.locator("#tdsave2").click(timeout=10_000, force=True)
    except Exception as e:
        raise RuntimeError(f"找不到「儲存」按鈕；目前頁面：{page.url}") from e
    page.wait_for_timeout(2000)

    error_banner = page.get_by_text("本數不符合此區間配號", exact=False)
    if error_banner.count() and error_banner.first.is_visible():
        raise RuntimeError(
            f"儲存後畫面仍顯示「本數不符合此區間配號」（總本數 {total}＝線上單張開立 {reserve_count}"
            f"＋API串接開立 {api_count} 應該要相符，請截圖回報）"
        )

    print(f"[鯨躍發票] 已配號：{area} {qyear_label} {qmonth_label}，總本數 {total} = 線上單張開立 {reserve_count} + API串接開立 {api_count}")
    return total, reserve_count, api_count


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="鯨躍電子發票字軌號碼匯入／配號")
    parser.add_argument("action", choices=["import", "section-query", "allocate"])
    parser.add_argument("--area", required=True, help="區域，例如：台北、台中")
    parser.add_argument("--period", default="", help="匯入用：財政部字軌期別 YYYYMM（起始月），未輸入預設下一期")
    parser.add_argument("--qyear", default="", help="配號用：查詢年份下拉選單上的可見文字")
    parser.add_argument("--qmonth", default="", help="配號用：查詢月份下拉選單上的可見文字")
    parser.add_argument("--cdp-url", default=DEFAULT_CDP_URL)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    period_display = ""
    sheets = None
    spreadsheet_id = ""
    try:
        with sync_playwright() as playwright:
            _browser, context = connect_existing_chrome(playwright, args.cdp_url)
            print(f"瀏覽器：已連接現有 Chrome（{args.cdp_url}）")
            page = find_ei_page(context)
            if page is None:
                page = context.new_page()

            if args.action == "import":
                year, label = parse_period_arg(args.period)
                period_display = f"{year}年{label}期"
                with tempfile.TemporaryDirectory(prefix="mof_serial_fetch_") as temp_dir:
                    csv_path = fetch_mof_serial_csv(
                        args.area, year, label, Path(temp_dir) / period_filename(year, label, args.area)
                    )
                    print(f"已從 Google Drive 抓回：{csv_path.name}")
                    import_serial_numbers(page, csv_path)
                _, sheets = get_google_services()
                spreadsheet_id = _master_spreadsheet_id()
                write_serial_log(
                    sheets, spreadsheet_id, function_name="電子發票字軌號碼匯入",
                    area=args.area, period=period_display, status="成功", message=f"已上傳 {csv_path.name}",
                )
            elif args.action == "section-query":
                if not args.qyear or not args.qmonth:
                    raise ValueError("配號查詢需要 --qyear 與 --qmonth")
                period_display = f"{args.qyear} {args.qmonth}"
                open_serial_section_query(page, args.qyear, args.qmonth)
            else:
                if not args.qyear or not args.qmonth:
                    raise ValueError("配號需要 --qyear 與 --qmonth")
                period_display = f"{args.qyear} {args.qmonth}"
                _, sheets = get_google_services()
                spreadsheet_id = _master_spreadsheet_id()
                reserve_map = ensure_allocation_config(sheets, spreadsheet_id)
                reserve_count = reserve_map.get(args.area, 3)
                total, reserve, api_count = allocate_serial_numbers(
                    page, args.area, args.qyear, args.qmonth, reserve_count
                )
                write_serial_log(
                    sheets, spreadsheet_id, function_name="電子發票字軌號碼配號",
                    area=args.area, period=period_display, status="成功",
                    message=f"總本數 {total} = 線上單張開立 {reserve} + API串接開立 {api_count}",
                )
    except Exception as e:
        if args.action in ("import", "allocate"):
            try:
                if sheets is None:
                    _, sheets = get_google_services()
                    spreadsheet_id = _master_spreadsheet_id()
                write_serial_log(
                    sheets, spreadsheet_id,
                    function_name="電子發票字軌號碼匯入" if args.action == "import" else "電子發票字軌號碼配號",
                    area=args.area, period=period_display, status="失敗", message=str(e),
                )
            except Exception:
                pass
        raise
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
