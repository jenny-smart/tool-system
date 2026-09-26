from __future__ import annotations

import argparse
import re
import tempfile
from pathlib import Path

from playwright.sync_api import sync_playwright

from tools.invoice_center.cetustek_login_only import ensure_expected_ei_login
from tools.invoice_center.chrome_cdp import (
    DEFAULT_CDP_URL,
    connect_existing_chrome,
    find_invoice_pages,
)
from tools.invoice_center.ei_export_all import (
    credentials_for,
    export_prize_invoices,
    load_accounts,
    login_portal,
    open_second_login,
)
from tools.invoice_center.invoice_archive import InvoiceArchiveProcessor


def _validate_period(period8: str) -> str:
    value = str(period8 or "").strip()
    if not re.fullmatch(r"\d{8}", value):
        raise ValueError("中獎期別請輸入 8 碼，例如 20260708")
    start_month, end_month = int(value[4:6]), int(value[6:8])
    if start_month not in {1, 3, 5, 7, 9, 11} or end_month != start_month + 1:
        raise ValueError("中獎期別需為 0102、0304、0506、0708、0910 或 1112")
    return value


def _is_missing_archive(exc: Exception, area: str, period8: str) -> bool:
    expected = f"Google Drive 找不到檔案：{period8}中獎發票-{area}.zip"
    return expected in str(exc)


def run(area: str, period8: str, cdp_url: str = DEFAULT_CDP_URL) -> int:
    if not area or area == "全區":
        raise ValueError("中獎發票更新請選擇單一區域")
    period8 = _validate_period(period8)
    processor = InvoiceArchiveProcessor()

    try:
        count = processor.update_prize_period(area=area, period8=period8)
        print(f"[{area}] Drive 已有 {period8} 中獎發票檔，直接更新完成：{count} 筆")
        return count
    except Exception as exc:
        if not _is_missing_archive(exc, area, period8):
            raise
        print(f"[{area}] Drive 缺少 {period8} 中獎發票檔，開始檢查鯨躍登入狀態並下載")

    accounts = load_accounts(None)
    credentials = credentials_for(area, accounts)

    with tempfile.TemporaryDirectory(prefix="ei_prize_update_") as temp_dir, sync_playwright() as playwright:
        _browser, context = connect_existing_chrome(playwright, cdp_url)
        portal_page, ei_page = find_invoice_pages(context)

        if ei_page is not None:
            active_page = ei_page
            reused = ensure_expected_ei_login(active_page, credentials)
            if reused:
                print(f"[{area}] 已登入正確鯨躍帳號，沿用目前登入狀態")
            else:
                print(f"[{area}] 已切換並登入正確鯨躍帳號")
        else:
            if portal_page is None:
                portal_page = context.new_page()
            login_portal(portal_page, accounts)
            active_page = open_second_login(context, portal_page)
            ensure_expected_ei_login(active_page, credentials)
            print(f"[{area}] 鯨躍登入完成")

        target = Path(temp_dir) / f"{period8}中獎發票-{area}.zip"
        prize_path = export_prize_invoices(active_page, period8, target)
        if not prize_path.exists() or prize_path.stat().st_size == 0:
            raise RuntimeError(f"{area}：中獎發票下載後檔案不存在或為空")
        if "prizeexport.jsp" not in active_page.url:
            raise RuntimeError(f"{area}：中獎發票下載後頁面不在中獎清冊頁；目前網址：{active_page.url}")

        processor.archive_prize_period(area=area, period8=period8, prize_path=prize_path)
        print(f"[{area}] 已下載並存入 Drive：{prize_path.name}")

    count = processor.update_prize_period(area=area, period8=period8)
    print(f"[{area}] 中獎發票更新完成：{count} 筆")
    return count


def main() -> int:
    parser = argparse.ArgumentParser(description="中獎發票更新；Drive 缺檔時自動登入鯨躍下載")
    parser.add_argument("--area", required=True)
    parser.add_argument("--period", required=True)
    parser.add_argument("--cdp-url", default=DEFAULT_CDP_URL)
    args = parser.parse_args()
    run(args.area, args.period, args.cdp_url)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
