# change_order_daily_check.py
# -*- coding: utf-8 -*-
"""
清潔異動每日檢查：台北／台中／桃園／新竹，每天台北時間 10:00 跑一次
（.github/workflows/scheduled_change_order_check.yml）。

掃描各地區「清潔異動」工作表 B 欄狀態為「待收款」／「待退款」的列：
  - 待退款：AB（折讓單號碼）與 AC（退款時間）都有值 → 視為已備妥，
    自動回填後台，成功後才把 B 欄改成「已退款」。
  - 待收款：M（收款時間）與 O（收款發票號碼）都有值 → 視為已備妥，
    自動回填後台，成功後才把 B 欄改成「已收款」。
  - 條件還沒補齊（或自動回填失敗）的列，列進待辦清單寄信＋寫一筆行事曆
    提醒，完全不動 Sheet 資料。
"""
from __future__ import annotations

import os
import sys
from datetime import datetime
from zoneinfo import ZoneInfo

from tools.memo_system import memo, change_order
from tools.finance_management.execution_log import log_execution
from tools.notify.send_daily_result import send_email
from tools.orders_system import calendar_notify

REGIONS = ["台北", "台中", "桃園", "新竹"]
TW_TZ = ZoneInfo("Asia/Taipei")


def _login_credentials() -> tuple[str, str]:
    email = os.environ.get("LEMON_EMAIL", "").strip()
    password = os.environ.get("LEMON_PASSWORD", "").strip()
    if not email or not password:
        raise RuntimeError("缺少 LEMON_EMAIL / LEMON_PASSWORD，無法登入後台")
    return email, password


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


def _notify(all_todo: list, summary: dict):
    today = datetime.now(TW_TZ).strftime("%Y/%m/%d")
    total_synced = sum(s["synced"] for s in summary.values())
    total_failed = sum(s["failed"] for s in summary.values())

    lines = [f"{today} 清潔異動每日檢查",
             f"已自動回填：{total_synced} 筆；失敗：{total_failed} 筆；待辦：{len(all_todo)} 筆", ""]
    if all_todo:
        lines.append("待辦事項：")
        for item in all_todo:
            lines.append(
                f"・[{item['region']}] {item['kind']} {item['order_no']}"
                f"（{item.get('customer_name', '')}）缺：{item['missing']}（第{item['sheet_row']}列）"
            )
    else:
        lines.append("今天沒有待辦事項。")
    plain_body = "\n".join(lines)

    if all_todo:
        html_rows = "".join(
            f"<tr><td>{item['region']}</td><td>{item['kind']}</td><td>{item['order_no']}</td>"
            f"<td>{item.get('customer_name', '')}</td><td>{item['missing']}</td><td>{item['sheet_row']}</td></tr>"
            for item in all_todo
        )
        todo_html = (
            "<table border='1' cellpadding='4' cellspacing='0'>"
            "<tr><th>地區</th><th>類型</th><th>訂單編號</th><th>客人</th><th>缺少</th><th>列號</th></tr>"
            + html_rows + "</table>"
        )
    else:
        todo_html = "<p>今天沒有待辦事項。</p>"

    html_body = (
        f"<p>{today} 清潔異動每日檢查</p>"
        f"<p>已自動回填：{total_synced} 筆；失敗：{total_failed} 筆；待辦：{len(all_todo)} 筆</p>"
        f"{todo_html}"
    )

    try:
        send_email(f"【清潔異動每日檢查】{today}", plain_body, html_body)
    except Exception as exc:
        print(f"⚠️ 寄信失敗：{exc}", flush=True)

    try:
        calendar_notify.create_notify_event(
            summary=f"清潔異動待辦：{len(all_todo)} 筆（回填{total_synced}／失敗{total_failed}）",
            description=plain_body,
        )
    except Exception as exc:
        print(f"⚠️ 建立行事曆通知失敗：{exc}", flush=True)


def run(regions=None, ui_logger=None) -> dict:
    def log(msg):
        print(msg, flush=True)
        if ui_logger:
            ui_logger(msg)

    requested = regions or REGIONS
    enabled = change_order.supported_regions()
    active_regions = [r for r in requested if r in enabled]
    skipped_regions = [r for r in requested if r not in enabled]

    email, password = _login_credentials()
    memo.set_env("prod")  # tools/memo_system/env.py 預設 ENV="dev"，排程一定要強制切到正式站
    memo.set_runtime_credentials(email, password)
    session = memo.login(ui_logger=ui_logger)

    all_todo = []
    summary = {}

    for region in active_regions:
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

    _notify(all_todo, summary)
    return {"summary": summary, "todo": all_todo}


if __name__ == "__main__":
    outcome = run()
    total_failed = sum(s["failed"] for s in outcome["summary"].values())
    print(outcome)
    sys.exit(1 if total_failed else 0)
