from __future__ import annotations

import argparse
import tempfile
from pathlib import Path

from playwright.sync_api import sync_playwright
from tools.invoice_center.chrome_cdp import DEFAULT_CDP_URL, connect_existing_chrome
from tools.invoice_center.ei_export_all import (
    archive_as_zip, export_invoices, load_accounts, month_range, normalize_month,
)
from tools.invoice_center.invoice_archive import InvoiceArchiveProcessor


def download_paper(processor, area: str, month: str, cdp_url: str) -> None:
    accounts = load_accounts(None)
    start, end = month_range(month)
    with tempfile.TemporaryDirectory(prefix="ei_paper_") as temp_dir, sync_playwright() as playwright:
        _browser, context = connect_existing_chrome(playwright, cdp_url)
        from tools.invoice_center.ei_session import open_ei_page

        page, _reused = open_ei_page(context, area, accounts)
        name = f"{month}紙本發票-{area}"
        downloaded = export_invoices(
            page, start, end, paper_only=True, csv=True, detail=False,
            target=Path(temp_dir) / f"{name}.csv",
        )
        archive = archive_as_zip(downloaded, Path(temp_dir) / f"{name}.zip")
        processor.archive_paper_month(area=area, yyyymm=month, paper_path=archive)
        print(f"[{area}] 已下載並歸檔：{archive.name}")


def update_area(processor, area: str, month: str, cdp_url: str) -> int:
    try:
        return processor.update_paper(area=area, yyyymm=month)
    except RuntimeError as exc:
        expected = f"Google Drive 找不到檔案：{month}紙本發票-{area}.zip"
        if str(exc) != expected:
            raise
    print(f"[{area}] Drive 缺少紙本發票檔，開始登入鯨躍下載 {month}")
    download_paper(processor, area, month, cdp_url)
    return processor.update_paper(area=area, yyyymm=month)


def run(area: str, month: str, cdp_url: str = DEFAULT_CDP_URL) -> int:
    month = normalize_month(month)
    processor = InvoiceArchiveProcessor()
    areas = list(processor.configs) if area in {"", "全區", "all"} else [area]
    if not areas or any(name not in processor.configs for name in areas):
        raise ValueError("紙本發票更新找不到已啟用的區域設定")
    total, failures = 0, []
    for name in areas:
        try:
            count = update_area(processor, name, month, cdp_url)
            total += count
            processor.write_update_log(function_name="紙本發票更新", area=name, period=month,
                                       status="成功", count=count)
            print(f"[{name}] 紙本發票更新完成：{count} 筆")
        except Exception as exc:
            failures.append(f"{name}: {exc}")
            processor.write_update_log(function_name="紙本發票更新", area=name, period=month,
                                       status="失敗", message=str(exc))
    if failures:
        raise RuntimeError("；".join(failures))
    return total


def main() -> int:
    parser = argparse.ArgumentParser(description="紙本發票更新；Drive 缺檔時自動下載並歸檔")
    parser.add_argument("--area", required=True)
    parser.add_argument("--month", required=True)
    parser.add_argument("--cdp-url", default=DEFAULT_CDP_URL)
    args = parser.parse_args()
    run(args.area, args.month, args.cdp_url)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
