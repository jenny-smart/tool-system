"""藍新月結：每區只登入一次（輸入一次驗證碼），依序下載收款／退款 CSV、查手續費發票金額，再登出。

原本【藍新金流】藍新收退款下載、藍新手續費發票金額是兩支程式，各自登入／登出，
每區要輸入兩次驗證碼。【整合執行】月初藍新／鯨躍／現金缺口兩個都勾時改跑這支。
藍新每月 1 日 08:00 才開手續費發票，所以排程 09:00 才排入。

結束代碼：0＝全部成功；1＝有地區收退款下載失敗；2＝有地區發票金額查詢失敗；3＝兩種都有失敗。
"""
from __future__ import annotations

import argparse
import calendar
import sys
from pathlib import Path

DOWNLOAD_FAILED = 1
INVOICE_FAILED = 2


def exit_code(download_failures: list[str], invoice_failures: list[str]) -> int:
    return (DOWNLOAD_FAILED if download_failures else 0) | (INVOICE_FAILED if invoice_failures else 0)


def main(argv=None) -> int:
    from playwright.sync_api import sync_playwright

    from tools.common.contractor_upload import upload_period_files
    from tools.invoice_center.chrome_cdp import DEFAULT_CDP_URL, connect_existing_chrome, find_existing_page
    from tools.newebpay import download_reports as dr
    from tools.newebpay import invoice_amounts as ia

    parser = argparse.ArgumentParser(description="藍新月結：每區登入一次，下載收退款＋查手續費發票金額")
    parser.add_argument("period", help="YYYYMM，例如 202610")
    parser.add_argument("--area", help="只做指定區域，例如 台北")
    parser.add_argument("--accounts-file", type=Path, default=dr.DEFAULT_ACCOUNTS_FILE)
    parser.add_argument("--cdp-url", default=DEFAULT_CDP_URL)
    args = parser.parse_args(argv)

    period = ia.normalize_month(args.period)
    year, month = int(period[:4]), int(period[4:])
    start, end, label = dr.explicit_date_range(f"{year}-{month:02d}-01",
                                               f"{year}-{month:02d}-{calendar.monthrange(year, month)[1]:02d}")
    invoice_date = ia.invoice_date_for(period)
    accounts = dr.load_accounts(args.accounts_file.expanduser(), args.area)
    print(f"期別：{period}（收退款 {start}～{end}；手續費發票日 {invoice_date}）")
    print(f"區域：{', '.join(a.area for a in accounts)}；每區只登入一次")

    results = []
    download_failures: list[str] = []
    invoice_failures: list[str] = []
    with sync_playwright() as playwright:
        _browser, context = connect_existing_chrome(playwright, args.cdp_url)
        page = find_existing_page(context, ("newebpay.com",)) or context.new_page()
        try:
            for account in accounts:
                print(f"\n===== {account.area} =====", flush=True)
                try:
                    messages = dr.login(page, account)
                except Exception as exc:
                    download_failures.append(f"{account.area}: 登入失敗 {exc}")
                    invoice_failures.append(f"{account.area}: 登入失敗")
                    print(f"[{account.area}] 登入失敗：{exc}", file=sys.stderr)
                    continue
                try:
                    try:
                        output = dr.area_output_dir(account.area, label)
                        output.mkdir(parents=True, exist_ok=True)
                        payment = output / f"{label}藍新收款-{account.area}.csv"
                        refund = output / f"{label}藍新退款-{account.area}.csv"
                        dr.download_payments(page, account, start, end, payment, messages)
                        dr.download_refunds(page, account, start, end, refund, messages)
                        upload_period_files([payment, refund], account.area, label)
                        for file_path in (payment, refund):
                            print(f"RESULT_FILE: {file_path}")
                    except Exception as exc:
                        download_failures.append(f"{account.area}: {exc}")
                        print(f"[{account.area}] 收退款下載失敗：{exc}", file=sys.stderr)
                    try:
                        rows = {r["invoice_date"]: r for r in ia.query_invoice_rows(page, [invoice_date])}
                        row = rows.get(invoice_date, {})
                        results.append(ia.InvoiceAmount(
                            area=account.area, source_month=period, invoice_date=invoice_date,
                            amount=ia.parse_amount(row["amount"]) if row else None,
                            invoice_number=row.get("invoice_number", ""), status=row.get("status", "查無發票")))
                        print(f"[{account.area}] 手續費發票：" + (f"NT$ {results[-1].amount:,}" if row else "查無發票"))
                    except Exception as exc:
                        invoice_failures.append(f"{account.area}: {exc}")
                        print(f"[{account.area}] 發票金額查詢失敗：{exc}", file=sys.stderr)
                finally:
                    try:
                        dr.logout(page, account.area)
                    except Exception as exc:
                        print(f"[{account.area}] 登出失敗：{exc}", file=sys.stderr)
        finally:
            if not page.is_closed():
                page.close()
                print("藍新視窗已關閉。")

    if results:
        written, titles = ia.sync_fee_sheets(results)
        print(f"藍新發票獨立工作表寫入 {written} 筆：{', '.join(titles)}")
    for title, failures in (("收退款下載未完成", download_failures), ("發票金額未完成", invoice_failures)):
        if failures:
            print(f"\n{title}：", file=sys.stderr)
            for failure in failures:
                print(f"- {failure}", file=sys.stderr)
    return exit_code(download_failures, invoice_failures)


if __name__ == "__main__":
    raise SystemExit(main())
