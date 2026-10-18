# atm_reconcile.py
# -*- coding: utf-8 -*-
"""
ATM 對帳排程：台北／台中，每天台北時間 11:00、15:00 各跑一次
（.github/workflows/scheduled_atm_reconcile.yml）。

只挑「J 欄有訂單編號、P／T 欄還空白，且 B 欄入帳日期在最近 7 天內」的列視為新增列，
交給 tools.memo_system.atm.process_atm_rows() 去執行系統更新（按已付款／
開立發票／發確認信，並回寫 P～T 欄）。

process_atm_rows() 本身只會略過 J 欄空白或 T 欄為「需確認／非訂單／…」的列，
並不會主動排除「T 欄已經有值（已對過帳）」的列——所以絕不能把已處理過的
列號傳給它，否則會重新執行一次並覆蓋掉原本已經寫好的 ATM 對帳資料
（先前發生過的問題）。因此這支排程自己先掃描、只把 T 欄仍空白的列號組成
row_spec 再呼叫 process_atm_rows()，已經對過帳的原始資料列完全不會被讀進
待處理清單，從根本避免覆蓋。
登入後台採「各區帳號密碼」（比照 tools/lemon_backend/config.py 的 AREA_ENV：
台北→TAIPEI_EMAIL/TAIPEI_PASSWORD、台中→TAICHUNG_EMAIL/TAICHUNG_PASSWORD…），
每個地區各自用自己的帳密登入、拿各自的 session，不共用單一帳號。
"""
from __future__ import annotations

import re
import sys
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

from tools.memo_system import memo, atm
from tools.lemon_backend.config import get_credentials
from tools.finance_management.execution_log import log_execution
from tools.orders_system import calendar_notify

REGIONS = ["台北", "台中"]


def _login_session(region: str, ui_logger=None):
    """用該地區自己的帳密登入後台，回傳該地區專屬的 session。"""
    creds = get_credentials(region, required=False)
    if creds is None:
        raise RuntimeError(f"{region} 後台帳密未設定（需要對應的 *_EMAIL / *_PASSWORD Secret）")
    memo.set_env("prod")  # tools/memo_system/env.py 預設 ENV="dev"，排程一定要強制切到正式站
    memo.set_runtime_credentials(creds.email, creds.password)
    return memo.login(ui_logger=ui_logger)


# 只處理銀行入帳日期（B 欄）在最近幾天內的列：舊資料多半沒有填 T 欄，
# 不能只看 T 空白，否則會把整年舊訂單重跑一次並重發確認信（2026-10-17 發生過）。
RECENT_DAYS = 7
TW_TZ = ZoneInfo("Asia/Taipei")


def _bank_date(text: str):
    match = re.search(r"(\d{4})\D(\d{1,2})\D(\d{1,2})", str(text or ""))
    if not match:
        return None
    try:
        return date(*(int(part) for part in match.groups()))
    except ValueError:
        return None


def is_new_row(row: list, today: date) -> bool:
    """J 有訂單編號、P（系統對帳時間）與 T（對帳狀態）都空白，且 B 入帳日期在最近 RECENT_DAYS 天內。"""
    if not memo.safe_cell(row, atm.COL_ORDER_NO):
        return False
    if memo.safe_cell(row, atm.COL_RECON_STATUS) or memo.safe_cell(row, atm.COL_RECONCILED_AT):
        return False
    bank_date = _bank_date(memo.safe_cell(row, atm.COL_BANK_TIME))
    return bank_date is not None and today - timedelta(days=RECENT_DAYS) <= bank_date <= today


def find_new_rows(region: str, today: date = None) -> list:
    """掃描 ATM Sheet，回傳要處理的新增列號（條件見 is_new_row）。"""
    today = today or datetime.now(TW_TZ).date()
    ws = atm.get_atm_worksheet(region)
    rows = memo.with_retry(ws.get_all_values)
    return [row_no for row_no, row in enumerate(rows, start=1) if row_no > 1 and is_new_row(row, today)]


def _notify_calendar(summary: dict, total_success: int, total_failed: int, total_skipped: int):
    lines = [
        f"{region}：成功 {result['success']}／失敗 {result['failed']}／略過 {result['skipped']}"
        for region, result in summary.items()
    ]
    description = "\n".join(lines) if lines else "沒有任何地區有新增列"

    status_word = "完成" if total_failed == 0 else "完成（有失敗，請查看明細）"
    summary_title = f"ATM對帳{status_word}：成功{total_success}／失敗{total_failed}"

    try:
        calendar_notify.create_notify_event(summary=summary_title, description=description)
    except Exception as exc:
        print(f"⚠️ 建立行事曆通知失敗：{exc}", flush=True)


def run(regions=None, ui_logger=None) -> dict:
    def log(msg):
        print(msg, flush=True)
        if ui_logger:
            ui_logger(msg)

    regions = regions or REGIONS

    summary = {}
    total_success = total_failed = total_skipped = 0

    for region in regions:
        try:
            session = _login_session(region, ui_logger=ui_logger)
        except Exception as exc:
            log(f"❌ 【{region}】登入失敗：{exc}")
            summary[region] = {"processed": 0, "success": 0, "failed": 1, "skipped": 0, "errors": [str(exc)]}
            total_failed += 1
            log_execution("ATM對帳", region, "失敗", str(exc))
            continue

        new_rows = find_new_rows(region)
        log(f"【{region}】新增列（J有單號且T空白）：{len(new_rows)} 筆 → {new_rows}")

        if not new_rows:
            summary[region] = {"processed": 0, "success": 0, "failed": 0, "skipped": 0, "errors": []}
            log_execution("ATM對帳", region, "成功", "沒有新增列，略過")
            continue

        row_spec = ",".join(str(r) for r in new_rows)
        result = atm.process_atm_rows(region, row_spec, session=session, ui_logger=ui_logger)
        summary[region] = result
        total_success += result["success"]
        total_failed += result["failed"]
        total_skipped += result["skipped"]

        status = "成功" if result["failed"] == 0 else "部分失敗"
        message = f"處理 {result['processed']} 筆，成功 {result['success']}，失敗 {result['failed']}"
        if result["errors"]:
            message += "；" + "；".join(result["errors"][:5])
        log_execution("ATM對帳", region, status, message)

    _notify_calendar(summary, total_success, total_failed, total_skipped)
    return summary


if __name__ == "__main__":
    outcome = run()
    has_failure = any(r.get("failed") for r in outcome.values())
    print(outcome)
    sys.exit(1 if has_failure else 0)
