"""【富邦銀行】內部請款整合執行：同一地區登入一次，轉帳全部做完後才下載明細（財報最後一列日期～今天）。

依序：請款記錄 → 工具包押金退款 → 清潔用品採購（依月份），最後下載一次富邦明細、登出。
進銀行前先把每一類的報表讀好、驗證好；某一類讀取失敗只跳過那一類（其他照做），
全部都失敗就不登入富邦。
"""
from __future__ import annotations

import argparse
import json
from contextlib import ExitStack
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from playwright.sync_api import sync_playwright

from tools.bank_statement import fubon_deposit_refund, fubon_payment_request, fubon_supply_purchase
from tools.bank_statement.accounts import DEFAULT_ACCOUNTS_FILE, load_account
from tools.bank_statement.fubon_agent import ensure_login, logout_quietly, run_download, report_query_start
from tools.invoice_center.chrome_cdp import DEFAULT_CDP_URL, connect_existing_chrome


def prepare_all(
    area: str,
    payment_rows: set[int],
    deposit_rows: set[int],
    supply: dict[str, set[int]],
    merge: bool | set[int] = False,
    supply_split: dict[str, set[int]] | None = None,
) -> tuple[list[tuple[str, object, dict]], list[str]]:
    """回傳 ([(說明, 模組, prepared)], [失敗訊息])，順序即轉帳順序。"""
    jobs = []
    if payment_rows:
        jobs.append(("請款記錄", fubon_payment_request, lambda: fubon_payment_request.prepare(area, payment_rows, merge)))
    if deposit_rows:
        jobs.append(("工具包押金退款", fubon_deposit_refund, lambda: fubon_deposit_refund.prepare(area, deposit_rows)))
    for month in sorted(supply):
        if supply[month]:
            jobs.append((f"清潔用品採購（{month}）", fubon_supply_purchase,
                         lambda month=month: fubon_supply_purchase.prepare(
                             area, month, supply[month], (supply_split or {}).get(month, set()))))
    ready, errors = [], []
    for label, module, load in jobs:
        try:
            ready.append((label, module, load()))
        except Exception as exc:
            errors.append(f"{label}：{exc}")
            print(f"❌ 【{area}】{label} 讀取／驗證失敗，略過：{exc}")
    return ready, errors


def run(
    area: str,
    payment_rows: set[int],
    deposit_rows: set[int],
    supply: dict[str, set[int]],
    accounts_file: Path,
    cdp_url: str,
    merge: bool | set[int] = False,
    supply_split: dict[str, set[int]] | None = None,
) -> int:
    ready, errors = prepare_all(area, payment_rows, deposit_rows, supply, merge, supply_split)
    if not ready:
        raise ValueError(f"【{area}】沒有可轉帳的資料：" + "；".join(errors))

    account = load_account("fubon", area, accounts_file.expanduser())
    with sync_playwright() as playwright, ExitStack() as cleanup:
        _browser, context = connect_existing_chrome(playwright, cdp_url)
        page = ensure_login(context, account)
        cleanup.callback(logout_quietly, context, page)  # 轉帳／下載做完（成功或失敗）一律登出
        for label, module, prepared in ready:
            print(f"── 【{area}】{label}：{len(prepared['selected'])} 筆 ──")
            page = module.pay(context, page, area, account, prepared)
        print(f"【{area}】轉帳全部完成，開始下載富邦明細（財報最後一列日期～今天）。")
        today = datetime.now(ZoneInfo("Asia/Taipei")).date()
        run_download(context, page, account, report_query_start(account, today), today)
    return 1 if errors else 0


def _rows(text: str) -> set[int]:
    return {int(value) for value in text.split(",") if value.strip().isdigit()}


def main() -> int:
    parser = argparse.ArgumentParser(description="富邦內部請款：同一地區轉帳全部做完後才下載明細")
    parser.add_argument("--area", required=True, choices=("台北", "台中"))
    parser.add_argument("--payment-rows", default="", help="請款記錄勾選列號，以逗號分隔")
    parser.add_argument("--deposit-rows", default="", help="工具包押金退款勾選列號，以逗號分隔")
    parser.add_argument("--supply", default="{}", help='清潔用品採購 JSON：{"202610": [4, 8]}')
    parser.add_argument("--merge", action="store_true", help="請款記錄同一收款對象合併成一筆轉帳（全部勾選列）")
    parser.add_argument("--merge-rows", default="", help="只有這些請款記錄列號參與合併，以逗號分隔；其他照單筆轉")
    parser.add_argument("--supply-split", default="{}", help='清潔用品採購取消合併的列 JSON：{"202610": [4]}')
    parser.add_argument("--accounts-file", type=Path, default=DEFAULT_ACCOUNTS_FILE)
    parser.add_argument("--cdp-url", default=DEFAULT_CDP_URL)
    args = parser.parse_args()
    supply = {str(month): {int(r) for r in rows} for month, rows in json.loads(args.supply).items()}
    supply_split = {str(month): {int(r) for r in rows} for month, rows in json.loads(args.supply_split).items()}
    return run(args.area, _rows(args.payment_rows), _rows(args.deposit_rows), supply,
               args.accounts_file, args.cdp_url, merge=args.merge or _rows(args.merge_rows),
               supply_split=supply_split)


if __name__ == "__main__":
    raise SystemExit(main())
