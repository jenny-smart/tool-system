# 財政部電子發票整合服務平台（einvoice.nat.gov.tw）
# 「電子發票字軌號碼取號」半自動下載＋歸檔。
#
# 登入頁有圖形驗證碼，一律留給使用者在畫面上手動輸入密碼與驗證碼、按「登入」；
# 本程式只負責預填統一編號／帳號，以及登入後的選單導覽、查詢、下載、
# 歸檔到 Google Drive「紙本發票／期別」資料夾（跟鯨躍紙本發票共用同一套
# 地區設定與資料夾規則），並寫入「財政部電子發票取號Log」。
from __future__ import annotations

import argparse
import re
import sys
import time
from datetime import date, datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from playwright.sync_api import Page, sync_playwright

if __package__ in {None, ""}:
    sys.path.append(str(Path(__file__).resolve().parents[1]))
    from invoice_center.mof_config import (
        MOF_LOGIN_URL,
        MOFCredentials,
        configured_areas,
        credentials_for,
        load_accounts,
    )
    from invoice_center.invoice_archive import (
        ensure_config_sheet,
        get_google_services,
        resolve_archive_folders,
        upload_replacing,
        _ensure_sheet,
        _master_spreadsheet_id,
    )
else:
    from .mof_config import (
        MOF_LOGIN_URL,
        MOFCredentials,
        configured_areas,
        credentials_for,
        load_accounts,
    )
    from .invoice_archive import (
        ensure_config_sheet,
        get_google_services,
        resolve_archive_folders,
        upload_replacing,
        _ensure_sheet,
        _master_spreadsheet_id,
    )

from tools.invoice_center.chrome_cdp import (
    DEFAULT_CDP_URL,
    connect_existing_chrome,
    find_existing_page,
)

MOF_LOG_SHEET = "財政部電子發票取號Log"
MOF_LOG_HEADERS = ["執行時間", "功能", "地區", "期別", "狀態", "訊息"]
TAIPEI_TZ = ZoneInfo("Asia/Taipei")

_PERIOD_LABELS = ["01-02", "03-04", "05-06", "07-08", "09-10", "11-12"]


def period_of(d: date) -> tuple[int, str]:
    return d.year, _PERIOD_LABELS[(d.month - 1) // 2]


def next_period(year: int, label: str) -> tuple[int, str]:
    idx = _PERIOD_LABELS.index(label)
    if idx == len(_PERIOD_LABELS) - 1:
        return year + 1, _PERIOD_LABELS[0]
    return year, _PERIOD_LABELS[idx + 1]


def parse_period_arg(period_text: str) -> tuple[int, str]:
    """接受 YYYYMM（起始月，例如 202609）或 YYYY年MM-MM期 格式；未輸入時預設下一期。"""
    text = str(period_text or "").strip()
    if not text:
        today = date.today()
        year, label = period_of(today)
        return next_period(year, label)
    match = re.fullmatch(r"(\d{4})(\d{2})", text)
    if match:
        year, month = int(match.group(1)), int(match.group(2))
        label = _PERIOD_LABELS[(month - 1) // 2]
        return year, label
    match = re.fullmatch(r"(\d{4})[年-](\d{2}-\d{2})期?", text)
    if match:
        return int(match.group(1)), match.group(2)
    raise ValueError(f"看不懂的期別格式：{period_text}，請用 YYYYMM，例如 202609")


def period_filename(year: int, label: str, area: str) -> str:
    start_mm, end_mm = label.split("-")
    return f"{year}{start_mm}{end_mm}電子發票取號-{area}.csv"


def find_mof_page(context) -> Page | None:
    return find_existing_page(context, ("einvoice.nat.gov.tw",))


def fill_if_present(page: Page, selector: str, value: str) -> bool:
    locator = page.locator(selector)
    if value and locator.count() and locator.first.is_visible():
        locator.first.fill(value)
        return True
    return False


def login_mof(page: Page, credentials: MOFCredentials) -> None:
    """開啟登入頁並預填統一編號／帳號（與密碼，若帳密檔內有設定）。
    驗證碼與送出登入一律留給使用者手動操作。"""
    page.goto(MOF_LOGIN_URL, wait_until="domcontentloaded")

    # 登入頁預設身分別不保證是「營業人/扣繳單位」（實測會落在消費者/手機條碼），
    # 一定要先切過去，並且「確認真的切成功」（統一編號欄位真的出現）才繼續，
    # 不然會誤填到手機條碼登入表單的欄位。
    identity_tab = page.get_by_text("扣繳單位", exact=False)
    if not identity_tab.count():
        identity_tab = page.get_by_text("營業人", exact=False)
    if identity_tab.count() and identity_tab.first.is_visible():
        try:
            identity_tab.first.click(timeout=3000)
        except Exception:
            pass

    ubn_input = page.locator("input[name='LoginUBN'], input[placeholder*='統一編號']")
    try:
        ubn_input.first.wait_for(state="visible", timeout=8000)
    except Exception:
        raise RuntimeError(
            "已嘗試切換到「營業人/扣繳單位」身分，但還是找不到統一編號欄位；"
            f"目前頁面：{page.url}。可能是畫面預設身分或欄位結構跟預期不同，"
            "請截圖回報以便調整。"
        )

    # 身分別切成功後，登入方式預設就是「帳號」，這裡用文字點擊只是保險，
    # 萬一之前被切到「憑證」分頁。
    account_mode_tab = page.get_by_text("帳號", exact=True)
    if account_mode_tab.count() and account_mode_tab.first.is_visible():
        try:
            account_mode_tab.first.click(timeout=2000)
        except Exception:
            pass

    filled_ubn = fill_if_present(page, "input[name='LoginUBN'], input[placeholder*='統一編號']", credentials.ubn)
    filled_account = fill_if_present(page, "input[name='LoginAccount'], input[placeholder*='帳號']", credentials.account)
    if not filled_ubn and not filled_account:
        print("[財政部電子發票] 找不到統一編號／帳號欄位，請直接在網頁手動輸入。", file=sys.stderr)

    if credentials.password:
        fill_if_present(page, "input[type='password'], input[placeholder*='密碼']", credentials.password)
        print(f"[財政部電子發票] {credentials.area} 帳密已預填，請在網頁輸入圖形驗證碼並點擊「登入」。")
    else:
        print("[財政部電子發票] 帳密檔未設定密碼，請在網頁手動輸入密碼與圖形驗證碼並點擊「登入」。")

    deadline = time.monotonic() + 300
    while time.monotonic() < deadline:
        if "/accounts/login" not in page.url:
            print("[財政部電子發票] 登入成功")
            return
        page.wait_for_timeout(1000)
    raise RuntimeError(f"登入等待逾時（5 分鐘），目前頁面：{page.url}")


def _open_serial_query(page: Page, year: int, label: str) -> None:
    menu_item = page.get_by_text("電子發票字軌號碼取號", exact=False)
    count = menu_item.count()
    if count == 0:
        raise RuntimeError("左側選單找不到「電子發票字軌號碼取號」，請確認目前頁面／登入狀態。")
    # 這個選單常見是「大分類→子項目」都用同一段文字，逐一點擊展開到底。
    for index in range(count):
        try:
            item = menu_item.nth(index)
            if item.is_visible():
                item.click(timeout=3000)
                page.wait_for_timeout(500)
        except Exception:
            continue

    query_tab = page.get_by_role("tab", name="查詢")
    if not query_tab.count():
        query_tab = page.get_by_text("查詢", exact=True)
    if not query_tab.count():
        raise RuntimeError("找不到「查詢」頁籤，選單可能還沒導到「電子發票字軌號碼取號」頁面。")
    query_tab.first.click(timeout=5000)
    page.wait_for_timeout(500)

    period_text = f"{year}年{label}期"
    # 發票期別是一組「起～迄」的期別選擇器；起訖都選同一期，只查這一期。
    period_label = page.get_by_text("發票期別", exact=False)
    if not period_label.count():
        raise RuntimeError("找不到「發票期別」欄位，無法設定查詢期別。")
    pickers = period_label.first.locator("xpath=following::input[1] | following::*[contains(@class,'picker')][1]")
    opened_any = False
    for picker_index in range(min(pickers.count(), 2)):
        try:
            pickers.nth(picker_index).click(timeout=3000)
            option = page.get_by_text(period_text, exact=True)
            if option.count():
                option.first.click(timeout=3000)
                opened_any = True
            page.wait_for_timeout(300)
        except Exception:
            continue
    if not opened_any:
        raise RuntimeError(
            f"無法自動設定發票期別為「{period_text}」，請手動在畫面上選好起訖期別後，"
            "再重新執行下載任務（或直接手動查詢下載，改用系統的檔案上傳流程）。"
        )

    search_button = page.get_by_role("button", name="查詢")
    if not search_button.count():
        search_button = page.get_by_text("查詢", exact=True)
    search_button.last.click(timeout=5000)
    page.wait_for_timeout(1500)


def download_serial_csv(page: Page, year: int, label: str, destination: Path) -> Path:
    _open_serial_query(page, year, label)

    period_text = f"{year}年{label}期"
    row = page.get_by_text(period_text, exact=False).locator("xpath=ancestor::tr[1]")
    if not row.count():
        raise RuntimeError(f"查詢結果找不到「{period_text}」這一列，可能尚未取號或期別不存在。")
    checkbox = row.first.locator("input[type='checkbox']")
    if checkbox.count():
        checkbox.first.check(timeout=3000)
    else:
        row.first.click(timeout=3000)

    download_button = page.get_by_role("button", name="下載")
    if not download_button.count():
        download_button = page.get_by_text("下載", exact=True)
    with page.expect_download(timeout=60_000) as download_info:
        download_button.last.click(timeout=5000)
    download = download_info.value
    destination.parent.mkdir(parents=True, exist_ok=True)
    download.save_as(str(destination))
    return destination


def _ensure_log_sheet(sheets: Any, spreadsheet_id: str) -> None:
    _ensure_sheet(sheets, spreadsheet_id, MOF_LOG_SHEET)
    current = sheets.spreadsheets().values().get(
        spreadsheetId=spreadsheet_id, range=f"'{MOF_LOG_SHEET}'!A1:F1"
    ).execute().get("values", [])
    if not current:
        sheets.spreadsheets().values().update(
            spreadsheetId=spreadsheet_id,
            range=f"'{MOF_LOG_SHEET}'!A1",
            valueInputOption="RAW",
            body={"values": [MOF_LOG_HEADERS]},
        ).execute()


def write_mof_log(sheets: Any, spreadsheet_id: str, *, area: str, period: str, status: str, message: str) -> None:
    _ensure_log_sheet(sheets, spreadsheet_id)
    sheets.spreadsheets().values().append(
        spreadsheetId=spreadsheet_id,
        range=f"'{MOF_LOG_SHEET}'!A:F",
        valueInputOption="RAW",
        insertDataOption="INSERT_ROWS",
        body={"values": [[
            datetime.now(TAIPEI_TZ).strftime("%Y-%m-%d %H:%M:%S"),
            "財政部電子發票取號", area, period, status, message,
        ]]},
    ).execute()


def archive_and_log(local_path: Path, area: str, year: int, label: str) -> str:
    period_text = f"{year}年{label}期"
    drive, sheets = get_google_services()
    spreadsheet_id = _master_spreadsheet_id()
    configs = ensure_config_sheet(sheets, spreadsheet_id)
    config = configs.get(area)
    if not config:
        message = f"「鯨躍發票根目錄設定」找不到已啟用的 {area} 設定"
        write_mof_log(sheets, spreadsheet_id, area=area, period=period_text, status="失敗", message=message)
        raise RuntimeError(message)

    start_month = int(label.split("-")[0])
    yyyymm = f"{year}{start_month:02d}"
    folders = resolve_archive_folders(drive, config, yyyymm)
    upload_replacing(drive, local_path, folders.paper_invoice)

    message = f"已存為 {local_path.name}"
    write_mof_log(sheets, spreadsheet_id, area=area, period=period_text, status="成功", message=message)
    return message


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="財政部電子發票字軌號碼取號：查詢、下載、歸檔")
    parser.add_argument("--area", required=True, help="區域，例如：台北、台中")
    parser.add_argument("--period", default="", help="期別，格式 YYYYMM（起始月），例如 202609；未輸入時預設下一期")
    parser.add_argument("--accounts", type=Path, help="財政部電子發票帳密 JSON")
    parser.add_argument("--cdp-url", default=DEFAULT_CDP_URL)
    parser.add_argument("--login-only", action="store_true", help="只預填登入欄位，不下載")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    accounts = load_accounts(args.accounts)
    area = args.area or configured_areas(accounts)[0]
    credentials = credentials_for(area, accounts)
    year, label = parse_period_arg(args.period)

    with sync_playwright() as playwright:
        _browser, context = connect_existing_chrome(playwright, args.cdp_url)
        print(f"瀏覽器：已連接現有 Chrome（{args.cdp_url}）")
        page = find_mof_page(context)
        if page is None:
            page = context.new_page()
        # 一律導到登入頁：session 還有效的話網站本身會自動轉回登入後的頁面，
        # login_mof() 的輪詢會偵測到網址離開 /accounts/login 而視為登入成功；
        # 不在這裡自行判斷「已經登入」，避免誤判剛開出來的空白分頁而整個跳過導覽。
        login_mof(page, credentials)

        if args.login_only:
            return 0

        downloads_dir = Path.home() / "MOF account" / "downloads"
        filename = period_filename(year, label, area)
        destination = downloads_dir / filename
        download_serial_csv(page, year, label, destination)
        print(f"已下載：{destination}")

        message = archive_and_log(destination, area, year, label)
        print(message)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
