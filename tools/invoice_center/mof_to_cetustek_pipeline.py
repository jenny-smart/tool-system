# 財政部字軌下載 → 鯨躍匯入 → 鯨躍配號，一次跑完的整合流程。
#
# 三個步驟各自沿用原本模組的邏輯，執行記錄共用同一份「鯨躍字軌匯入配號
# Log」（靠「功能」欄分辨是哪個步驟），這裡不重複判斷或記錄，純粹依序
# 呼叫。任何一步
# 失敗（例如財政部這期還沒有字軌可下載）就整個停止、不會繼續跑後面的
# 步驟——這是每個步驟原本就有的行為（丟出例外），這裡沒有另外加防護，
# 是原本设计的自然结果。
#
# 財政部登入（含圖形驗證碼）不包含在這個流程裡，請先手動跑過
# 「財政部登入」、在瀏覽器完成登入，這個流程開始時假設 session 還有效。
from __future__ import annotations

import argparse
import sys
import tempfile
from pathlib import Path

from playwright.sync_api import sync_playwright

if __package__ in {None, ""}:
    sys.path.append(str(Path(__file__).resolve().parents[1]))
    from invoice_center import cetustek_serial, mof_serial
    from invoice_center.ei_export_all import (
        credentials_for as ei_credentials_for,
        load_accounts as load_ei_accounts,
        login_portal,
        login_second,
        open_second_login,
    )
    from invoice_center.invoice_archive import get_google_services, _master_spreadsheet_id
    from invoice_center.mof_config import credentials_for, load_accounts
else:
    from . import cetustek_serial, mof_serial
    from .ei_export_all import (
        credentials_for as ei_credentials_for,
        load_accounts as load_ei_accounts,
        login_portal,
        login_second,
        open_second_login,
    )
    from .invoice_archive import get_google_services, _master_spreadsheet_id
    from .mof_config import credentials_for, load_accounts

from tools.invoice_center.chrome_cdp import (
    DEFAULT_CDP_URL,
    connect_existing_chrome,
    find_invoice_pages,
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="財政部字軌下載→鯨躍匯入→鯨躍配號 一次跑完")
    parser.add_argument("--area", required=True, help="區域，例如：台北、台中")
    parser.add_argument("--period", default="", help="財政部字軌期別 YYYYMM（起始月），未輸入預設下一期")
    parser.add_argument("--qyear", required=True, help="鯨躍配號查詢年份下拉選單上的可見文字，例如 115")
    parser.add_argument("--qmonth", required=True, help="鯨躍配號查詢月份下拉選單上的可見文字，例如 09-10")
    parser.add_argument("--cdp-url", default=DEFAULT_CDP_URL)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    year, label = mof_serial.parse_period_arg(args.period)
    period_text = f"{year}年{label}期"
    filename = mof_serial.period_filename(year, label, args.area)

    with sync_playwright() as playwright:
        _browser, context = connect_existing_chrome(playwright, args.cdp_url)
        print(f"瀏覽器：已連接現有 Chrome（{args.cdp_url}）")

        # 1/3 財政部字軌下載
        mof_page = mof_serial.find_mof_page(context)
        if mof_page is None:
            mof_page = context.new_page()
        credentials = credentials_for(args.area, load_accounts())
        mof_serial.login_mof(mof_page, credentials)

        downloads_dir = Path.home() / "MOF account" / "downloads"
        destination = downloads_dir / filename
        mof_serial.download_serial_csv(mof_page, year, label, destination)
        print(f"[1/3] 已下載：{destination}")
        mof_serial.archive_and_log(destination, args.area, year, label)
        print("[1/3] 已歸檔到 Google Drive，並寫入鯨躍字軌匯入配號Log")

        # 2/3 鯨躍匯入
        # Follow the same complete two-layer flow as 「鯨躍登入」.
        ei_accounts = load_ei_accounts()
        ei_credentials = ei_credentials_for(args.area, ei_accounts)
        portal_page, ei_page = find_invoice_pages(context)
        if portal_page is not None:
            login_portal(portal_page, ei_accounts)
            ei_page = open_second_login(context, portal_page)
            login_second(ei_page, ei_credentials)
        elif ei_page is not None:
            login_second(ei_page, ei_credentials)
        else:
            portal_page = context.new_page()
            login_portal(portal_page, ei_accounts)
            ei_page = open_second_login(context, portal_page)
            login_second(ei_page, ei_credentials)
        with tempfile.TemporaryDirectory(prefix="mof_serial_fetch_") as temp_dir:
            csv_path = cetustek_serial.fetch_mof_serial_csv(
                args.area, year, label, Path(temp_dir) / filename
            )
            cetustek_serial.import_serial_numbers(ei_page, csv_path)
        print("[2/3] 已上傳匯入鯨躍")

        _, sheets = get_google_services()
        spreadsheet_id = _master_spreadsheet_id()
        cetustek_serial.write_serial_log(
            sheets, spreadsheet_id, function_name="電子發票字軌號碼匯入",
            area=args.area, period=period_text, status="成功", message=f"已上傳 {filename}",
        )
        print("[2/3] 已寫入鯨躍字軌匯入配號Log")

        # 3/3 鯨躍配號
        reserve_map = cetustek_serial.ensure_allocation_config(sheets, spreadsheet_id)
        reserve_count = reserve_map.get(args.area, 3)
        total, reserve, api_count = cetustek_serial.allocate_serial_numbers(
            ei_page, args.area, args.qyear, args.qmonth, reserve_count
        )
        cetustek_serial.write_serial_log(
            sheets, spreadsheet_id, function_name="電子發票字軌號碼配號",
            area=args.area, period=f"{args.qyear} {args.qmonth}", status="成功",
            message=f"總本數 {total} = 線上單張開立 {reserve} + API串接開立 {api_count}",
        )
        print(f"[3/3] 已配號：總本數 {total} = 線上單張開立 {reserve} + API串接開立 {api_count}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
