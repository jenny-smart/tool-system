"""藍新月結：每區只登入一次（輸入一次驗證碼），依序下載收款／退款 CSV、查手續費發票金額、查藍新帳務（帳戶明細本次剩餘金額），再登出。

原本【藍新金流】藍新收退款下載、藍新手續費發票金額是兩支程式，各自登入／登出，
每區要輸入兩次驗證碼。【整合執行】月初藍新／鯨躍／現金缺口兩個都勾時改跑這支。
藍新每月 1 日 08:00 才開手續費發票，所以排程 09:00 才排入。

結束代碼（可相加）：0＝全部成功；1＝收退款下載失敗；2＝發票金額查詢失敗；4＝藍新帳務查詢失敗。
"""
from __future__ import annotations

import argparse
import calendar
import sys
from pathlib import Path

DOWNLOAD_FAILED = 1
INVOICE_FAILED = 2
BALANCE_FAILED = 4
# 高雄跟新竹用同一個藍新帳號：只登入新竹，下載完複製一份、地區改成高雄（2026-11 與 Jenny 確認）
SKIP_AREAS = ("高雄",)
COPY_AREAS = {"新竹": ("高雄",)}  # 收退款檔複製一份給高雄
# 高雄不用算藍新手續費發票金額（2026-11 Jenny）：發票不複製給高雄
NO_INVOICE_AREAS = ("高雄",)
PARTS = ("download", "invoice", "balance")  # 收退款、手續費發票、帳務查詢（帳戶明細本次剩餘金額）


class _Skip(Exception):
    """這次沒勾的那一半（只做收退款或只做發票）。"""


def copy_for_area(path: Path, source: str, target: str) -> Path:
    import shutil

    copy = path.parent.parent / target / path.name.replace(source, target)
    copy.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(path, copy)
    return copy


def exit_code(download_failures: list[str], invoice_failures: list[str], balance_failures=()) -> int:
    return ((DOWNLOAD_FAILED if download_failures else 0) | (INVOICE_FAILED if invoice_failures else 0)
            | (BALANCE_FAILED if balance_failures else 0))


def main(argv=None) -> int:
    from playwright.sync_api import sync_playwright

    from tools.common.contractor_upload import upload_period_files
    from tools.invoice_center.chrome_cdp import DEFAULT_CDP_URL, connect_existing_chrome, find_existing_page
    from tools.newebpay import download_reports as dr
    from tools.newebpay import invoice_amounts as ia

    parser = argparse.ArgumentParser(description="藍新月結：每區登入一次，下載收退款＋查手續費發票金額")
    parser.add_argument("period", help="YYYYMM，例如 202610")
    parser.add_argument("--area", action="append", default=None,
                        help="只做指定區域（可重複），例如 --area 桃園；高雄會改做新竹再複製一份（同一個帳號）")
    parser.add_argument("--skip-area", action="append", default=None,
                        help=f"略過的區域（可重複）；預設略過 {'、'.join(SKIP_AREAS)}（跟新竹同一個帳號）")
    parser.add_argument("--part", action="append", choices=("all",) + PARTS, default=None,
                        help="只做收退款（download）／發票金額（invoice）／帳務查詢（balance），可重複；預設全部")
    parser.add_argument("--accounts-file", type=Path, default=dr.DEFAULT_ACCOUNTS_FILE)
    parser.add_argument("--cdp-url", default=DEFAULT_CDP_URL)
    args = parser.parse_args(argv)

    period = ia.normalize_month(args.period)
    year, month = int(period[:4]), int(period[4:])
    start, end, label = dr.explicit_date_range(f"{year}-{month:02d}-01",
                                               f"{year}-{month:02d}-{calendar.monthrange(year, month)[1]:02d}")
    invoice_date = ia.invoice_date_for(period)
    wanted = set(args.area or [])
    for source, copies in COPY_AREAS.items():  # 高雄＝新竹的複本：要高雄就做新竹
        if wanted & set(copies):
            wanted = (wanted - set(copies)) | {source}
    accounts = [a for a in dr.load_accounts(args.accounts_file.expanduser(), None)
                if not wanted or a.area in wanted]
    skip = set(SKIP_AREAS if args.skip_area is None else args.skip_area)
    if skip:
        accounts = [a for a in accounts if a.area not in skip]
        print(f"略過：{'、'.join(sorted(skip))}（跟新竹同一個帳號，跑新竹再複製一份）")
    print(f"期別：{period}（收退款 {start}～{end}；手續費發票日 {invoice_date}）")
    parts = set(PARTS) if not args.part or "all" in args.part else set(args.part)
    do_download, do_invoice, do_balance = ("download" in parts), ("invoice" in parts), ("balance" in parts)
    print(f"區域：{', '.join(a.area for a in accounts)}；每區只登入一次；做：" + "、".join(
        n for n, on in (("收退款", do_download), ("發票金額", do_invoice), ("帳務查詢", do_balance)) if on))

    from tools.newebpay import account_balance as ab

    results = []
    balances: dict[str, tuple[str, int]] = {}
    download_failures: list[str] = []
    invoice_failures: list[str] = []
    balance_failures: list[str] = []
    with sync_playwright() as playwright:
        _browser, context = connect_existing_chrome(playwright, args.cdp_url)
        page = find_existing_page(context, ("newebpay.com",)) or context.new_page()
        try:
            for account in accounts:
                print(f"\n===== {account.area} =====", flush=True)
                try:
                    messages = dr.login(page, account)
                except Exception as exc:
                    if do_download:
                        download_failures.append(f"{account.area}: 登入失敗 {exc}")
                    if do_invoice:
                        invoice_failures.append(f"{account.area}: 登入失敗")
                    if do_balance and account.area not in ab.NO_BALANCE_AREAS:
                        balance_failures.append(f"{account.area}: 登入失敗")
                    print(f"[{account.area}] 登入失敗：{exc}", file=sys.stderr)
                    continue
                try:
                    try:
                        if not do_download:
                            raise _Skip
                        output = dr.area_output_dir(account.area, label)
                        output.mkdir(parents=True, exist_ok=True)
                        payment = output / f"{label}藍新收款-{account.area}.csv"
                        refund = output / f"{label}藍新退款-{account.area}.csv"
                        dr.download_payments(page, account, start, end, payment, messages)
                        dr.download_refunds(page, account, start, end, refund, messages)
                        upload_period_files([payment, refund], account.area, label)
                        for file_path in (payment, refund):
                            print(f"RESULT_FILE: {file_path}")
                        for copy_area in COPY_AREAS.get(account.area, ()):
                            copies = [copy_for_area(p, account.area, copy_area) for p in (payment, refund)]
                            upload_period_files(copies, copy_area, label)
                            print(f"[{account.area}] 已複製一份收退款檔為 {copy_area}")
                    except _Skip:
                        pass
                    except Exception as exc:
                        download_failures.append(f"{account.area}: {exc}")
                        print(f"[{account.area}] 收退款下載失敗：{exc}", file=sys.stderr)
                    try:
                        if not do_invoice:
                            raise _Skip
                        before = len(messages)
                        try:
                            row = ia.query_fee_invoice(page, invoice_date)
                        except Exception:
                            # 藍新跳「查無資料」＝還沒開發票：記成查無發票，不當程式錯誤
                            if not any("查無資料" in m for m in messages[before:]):
                                raise
                            row = {}
                        if row and row["invoice_date"] != invoice_date:
                            print(f"[{account.area}] 手續費發票不是 {invoice_date} 開，是 {row['invoice_date']}")
                        results.append(ia.InvoiceAmount(
                            area=account.area, source_month=period, invoice_date=row.get("invoice_date") or invoice_date,
                            amount=ia.parse_amount(row["amount"]) if row else None,
                            invoice_number=row.get("invoice_number", ""), status=row.get("status", "查無發票")))
                        print(f"[{account.area}] 手續費發票：" + (f"NT$ {results[-1].amount:,}" if row else "查無發票"))
                        for copy_area in (a for a in COPY_AREAS.get(account.area, ()) if a not in NO_INVOICE_AREAS):
                            results.append(ia.InvoiceAmount(
                                area=copy_area, source_month=period, invoice_date=invoice_date,
                                amount=results[-1].amount, invoice_number=results[-1].invoice_number,
                                status=results[-1].status))
                            print(f"[{account.area}] 發票金額同步記一筆給 {copy_area}")
                    except _Skip:
                        pass
                    except Exception as exc:
                        invoice_failures.append(f"{account.area}: {exc}")
                        print(f"[{account.area}] 發票金額查詢失敗：{exc}", file=sys.stderr)
                    if do_balance and account.area not in ab.NO_BALANCE_AREAS:
                        try:
                            found = ab.query_balance(page, period)
                            if found:
                                balances[account.area] = found
                                print(f"[{account.area}] 藍新帳務：{found[0]} 本次剩餘金額 NT$ {found[1]:,}")
                            else:
                                print(f"[{account.area}] 藍新帳務：查無資料")
                        except Exception as exc:
                            balance_failures.append(f"{account.area}: {exc}")
                            print(f"[{account.area}] 藍新帳務查詢失敗：{exc}", file=sys.stderr)
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
    if balances:
        ab.write_balances(balances)
        print(f"藍新帳務寫入「現金缺口試算」：{'、'.join(balances)}")
        try:  # 現金缺口已算到這個月底的話，一起貼到 review「現金缺口」39～40 列
            from tools.finance_management.review_monthly import paste_to_review

            paste_to_review(period, "cash", log=print)
        except Exception as exc:
            print(f"⚠️ review 沒更新：{exc}")
    for title, failures in (("收退款下載未完成", download_failures), ("發票金額未完成", invoice_failures),
                            ("藍新帳務未完成", balance_failures)):
        if failures:
            print(f"\n{title}：", file=sys.stderr)
            for failure in failures:
                print(f"- {failure}", file=sys.stderr)
    return exit_code(download_failures, invoice_failures, balance_failures)


if __name__ == "__main__":
    raise SystemExit(main())
