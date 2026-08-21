# atm_reconcile.py
# -*- coding: utf-8 -*-
"""
ATM 對帳排程：台北／台中，每天台北時間 11:00、15:00 各跑一次
（.github/workflows/scheduled_atm_reconcile.yml）。

只挑「J 欄有訂單編號、且 T 欄（對帳狀態）還空白」的列視為要處理的新增列，
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

import sys

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


def find_new_rows(region: str) -> list:
    """掃描 ATM Sheet，回傳「J 欄有訂單編號、且 T 欄尚為空白」的列號（新增列）。"""
    ws = atm.get_atm_worksheet(region)
    rows = memo.with_retry(ws.get_all_values)
    new_rows = []
    for row_no, row in enumerate(rows, start=1):
        if row_no == 1:
            continue
        order_no = memo.safe_cell(row, atm.COL_ORDER_NO)
        recon_status = memo.safe_cell(row, atm.COL_RECON_STATUS)
        if order_no and not recon_status:
            new_rows.append(row_no)
    return new_rows


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
