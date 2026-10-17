# change_order_daily_check.py
# -*- coding: utf-8 -*-
"""
清潔異動每日檢查：台北／台中／桃園／新竹，每天台北時間 10:00、14:00 各跑一次
（.github/workflows/scheduled_change_order_check.yml，由 scripts/cloud_schedule.py 派送）。

掃描各地區「清潔異動」工作表 B 欄狀態為「待收款」／「待退款」的列：
  - 待退款：AB（折讓單號碼）與 AC（退款時間）都有值 → 視為已備妥，
    自動回填後台，成功後才把 B 欄改成「已退款」。
  - 待收款：M（收款時間）與 O（收款發票號碼）都有值 → 視為已備妥，
    自動回填後台，成功後才把 B 欄改成「已收款」。
  - 條件還沒補齊（或自動回填失敗）的列，列進待辦清單，在工作日 11:00／15:00
    （下一個時段）於 jenny@lemonclean.com.tw 行事曆建一筆提醒、提前 30 分鐘跳窗，
    完全不動 Sheet 資料；沒有待辦、非工作日就不建。

登入後台採「各區帳號密碼」（比照 tools/lemon_backend/config.py 的 AREA_ENV：
台北→TAIPEI_EMAIL/TAIPEI_PASSWORD、台中→TAICHUNG_EMAIL/TAICHUNG_PASSWORD、
桃園→TAOYUAN_EMAIL/TAOYUAN_PASSWORD、新竹→HSINCHU_EMAIL/HSINCHU_PASSWORD），
每個地區各自用自己的帳密登入、拿各自的 session，不共用單一帳號。
"""
from __future__ import annotations

import sys
from datetime import date, datetime, time
from zoneinfo import ZoneInfo

from tools.memo_system import memo, change_order
from tools.lemon_backend.config import get_credentials
from tools.finance_management.execution_log import log_execution
from tools.orders_system import calendar_notify
from tools.common import tw_holidays

REGIONS = ["台北", "台中", "桃園", "新竹"]
TW_TZ = ZoneInfo("Asia/Taipei")

# 未備妥提醒：工作日 11:00、15:00 各一筆，建在 Jenny 的行事曆，提前 30 分鐘跳窗
REMIND_CALENDAR_ID = "jenny@lemonclean.com.tw"
REMIND_SLOTS = (time(11, 0), time(15, 0))
REMIND_BEFORE_MINUTES = 30


def _login_session(region: str, ui_logger=None):
    """用該地區自己的帳密登入後台，回傳該地區專屬的 session。"""
    creds = get_credentials(region, required=False)
    if creds is None:
        raise RuntimeError(f"{region} 後台帳密未設定（需要對應的 *_EMAIL / *_PASSWORD Secret）")
    memo.set_env("prod")  # tools/memo_system/env.py 預設 ENV="dev"，排程一定要強制切到正式站
    memo.set_runtime_credentials(creds.email, creds.password)
    return memo.login(ui_logger=ui_logger)


def _col_index(letter: str) -> int:
    idx = 0
    for ch in letter:
        idx = idx * 26 + (ord(ch) - ord("A") + 1)
    return idx - 1


def _cell(row: list, letter: str) -> str:
    idx = _col_index(letter)
    return row[idx].strip() if idx < len(row) else ""


def scan_region(region: str) -> dict:
    """回傳 {"ready": [...], "todo": [...]}；ready 為已備妥可回填的列，
    todo 為條件還沒補齊、需要人工處理的列。"""
    ws = change_order.get_worksheet(region)
    rows = ws.get_all_values()

    ready = []
    todo = []

    for row_no, row in enumerate(rows[1:], start=2):
        status = _cell(row, "B")
        order_no = _cell(row, "G")
        if not order_no:
            continue

        if status == change_order.STATUS_PENDING_REFUND:
            allowance_no = _cell(row, "AB")
            refund_time = _cell(row, "AC")
            if allowance_no and refund_time:
                ready.append({"kind": "refund", "sheet_row": row_no, "order_no": order_no, "raw": row})
            else:
                missing = []
                if not allowance_no:
                    missing.append("折讓單")
                if not refund_time:
                    missing.append("退款時間")
                todo.append({
                    "region": region, "kind": "退款", "sheet_row": row_no,
                    "order_no": order_no, "customer_name": _cell(row, "H"),
                    "missing": "、".join(missing),
                })
        elif status == change_order.STATUS_PENDING_CHARGE:
            charge_time = _cell(row, "M")
            invoice_no = _cell(row, "O")
            if charge_time and invoice_no:
                ready.append({"kind": "charge", "sheet_row": row_no, "order_no": order_no, "raw": row})
            else:
                missing = []
                if not charge_time:
                    missing.append("收款")
                if not invoice_no:
                    missing.append("發票")
                todo.append({
                    "region": region, "kind": "收款", "sheet_row": row_no,
                    "order_no": order_no, "customer_name": _cell(row, "H"),
                    "missing": "、".join(missing),
                })

    return {"ready": ready, "todo": todo}


def _sync_ready_item(region: str, item: dict, session, ui_logger=None) -> tuple:
    """把已備妥的列回填後台；成功才把 B 欄改成已完成狀態。回傳 (成功?, 錯誤訊息)。"""
    kind = item["kind"]
    done_status = change_order.STATUS_DONE_REFUND if kind == "refund" else change_order.STATUS_DONE_CHARGE
    sync_item = {
        "sheet_row": item["sheet_row"],
        "kind": kind,
        "status": done_status,
        "order_no": item["order_no"],
        "raw": item["raw"],
    }
    try:
        change_order.sync_one_to_purchase_edit(sync_item, session=session, ui_logger=ui_logger)
    except Exception as exc:
        return False, str(exc)

    ws = change_order.get_worksheet(region)
    ws.update_acell(f"B{item['sheet_row']}", done_status)
    try:
        change_order.mark_sheet_row_done(region, item["sheet_row"], kind, ui_logger=ui_logger)
    except Exception:
        pass
    return True, ""


def next_remind_slot(now: datetime, is_workday=tw_holidays.is_workday):
    """下一個提醒時段（今天 11:00 或 15:00）；非工作日或已過 15:00 回傳 None。"""
    now = now.astimezone(TW_TZ)
    if not is_workday(now.date()):
        return None
    for slot in REMIND_SLOTS:
        start = datetime.combine(now.date(), slot, tzinfo=TW_TZ)
        if start > now:
            return start
    return None


def build_notify_text(all_todo: list, summary: dict, today: date) -> tuple:
    total_synced = sum(s["synced"] for s in summary.values())
    total_failed = sum(s["failed"] for s in summary.values())
    lines = [f"{today:%Y/%m/%d} 清潔異動未備妥，請補齊後下次檢查會自動回填系統",
             f"已自動回填：{total_synced} 筆；失敗：{total_failed} 筆；待補齊：{len(all_todo)} 筆", ""]
    for item in all_todo:
        lines.append(
            f"・[{item['region']}] {item['kind']} {item['order_no']}"
            f"（{item.get('customer_name', '')}）缺：{item['missing']}（第{item['sheet_row']}列）"
        )
    lines += ["", "待退款請補 AB 折讓單號碼、AC 退款時間；待收款請補 M 收款時間、O 發票號碼。"]
    title = f"清潔異動未備妥：{len(all_todo)} 筆待補齊（回填{total_synced}／失敗{total_failed}）"
    return title, "\n".join(lines)


def _notify(all_todo: list, summary: dict, now: datetime = None, log=print):
    """有未備妥的列才建提醒：工作日 11:00／15:00 中下一個時段，提前 30 分鐘跳窗。"""
    now = now or datetime.now(TW_TZ)
    if not all_todo:
        log("沒有未備妥的列，不建提醒")
        return None
    start = next_remind_slot(now)
    if start is None:
        log("非工作日或已過 15:00，不建提醒")
        return None
    title, body = build_notify_text(all_todo, summary, now.date())
    kwargs = dict(summary=title, description=body, start=start, reminder_minutes=[REMIND_BEFORE_MINUTES])
    try:
        event = calendar_notify.create_notify_event(calendar_id=REMIND_CALENDAR_ID, attendee="", **kwargs)
        log(f"已建立 {start:%H:%M} 提醒（{REMIND_CALENDAR_ID}）")
        return event
    except Exception as exc:  # 服務帳號尚未取得 Jenny 行事曆權限時，改建在共用日曆並邀請她
        log(f"⚠️ 無法寫入 {REMIND_CALENDAR_ID} 行事曆（{exc}），改用共用日曆邀請")
    try:
        return calendar_notify.create_notify_event(attendee=REMIND_CALENDAR_ID, **kwargs)
    except Exception as exc:
        log(f"⚠️ 建立行事曆通知失敗：{exc}")
        return None


def run(regions=None, ui_logger=None) -> dict:
    def log(msg):
        print(msg, flush=True)
        if ui_logger:
            ui_logger(msg)

    requested = regions or REGIONS
    enabled = change_order.supported_regions()
    active_regions = [r for r in requested if r in enabled]
    skipped_regions = [r for r in requested if r not in enabled]

    all_todo = []
    summary = {}

    for region in active_regions:
        try:
            session = _login_session(region, ui_logger=ui_logger)
        except Exception as exc:
            log(f"❌ 【{region}】登入失敗：{exc}")
            summary[region] = {"synced": 0, "failed": 1, "todo": 0, "errors": [str(exc)]}
            log_execution("清潔異動每日檢查", region, "失敗", str(exc))
            continue

        scanned = scan_region(region)
        ready, todo = scanned["ready"], scanned["todo"]
        all_todo.extend(todo)

        synced = failed = 0
        errors = []
        for item in ready:
            ok, err = _sync_ready_item(region, item, session, ui_logger=ui_logger)
            if ok:
                synced += 1
            else:
                failed += 1
                errors.append(f"{item['order_no']}：{err}")
                all_todo.append({
                    "region": region, "kind": "退款" if item["kind"] == "refund" else "收款",
                    "sheet_row": item["sheet_row"], "order_no": item["order_no"],
                    "customer_name": "", "missing": f"自動回填失敗：{err}",
                })

        summary[region] = {"synced": synced, "failed": failed, "todo": len(todo), "errors": errors}
        log(f"【{region}】已回填 {synced} 筆，失敗 {failed} 筆，待辦 {len(todo)} 筆")

        status = "成功" if failed == 0 else "部分失敗"
        message = f"回填 {synced} 筆，失敗 {failed} 筆，待辦 {len(todo)} 筆"
        if errors:
            message += "；" + "；".join(errors[:5])
        log_execution("清潔異動每日檢查", region, status, message)

    for region in skipped_regions:
        log(f"【{region}】清潔異動設定分頁未啟用或找不到，略過")

    _notify(all_todo, summary, log=log)
    return {"summary": summary, "todo": all_todo}


if __name__ == "__main__":
    outcome = run()
    total_failed = sum(s["failed"] for s in outcome["summary"].values())
    print(outcome)
    sys.exit(1 if total_failed else 0)
