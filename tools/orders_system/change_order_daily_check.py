# change_order_daily_check.py
# -*- coding: utf-8 -*-
"""
清潔異動每日檢查：台北／台中／桃園／新竹／高雄，工作日台北時間 10:00 跑一次
（.github/workflows/scheduled_change_order_check.yml，由 scripts/cloud_schedule.py 派送）。

只做兩件事，不回寫系統（回寫改由【清潔異動】已備妥整合執行處理）：
  1. 補 T（匯款金額）、Z（未稅金額）公式：待退款且 S 有值、T／Z 空白的格子。
  2. 列出登記日期 2026/09/01 以後「未備妥」的列（還要開折讓單、退款、開發票、
     異動儲值金或需人工的列），在 jenny@lemonclean.com.tw 行事曆建一筆 11:00 的提醒，
     10:30 跳窗；沒有未備妥、非工作日就不建。
盤點規則與【清潔異動】已備妥整合執行共用 tools/finance_management/change_order_batch.py。
"""
from __future__ import annotations

import sys
from datetime import date, datetime, time
from zoneinfo import ZoneInfo

from tools.finance_management import change_order_batch as batch
from tools.finance_management.execution_log import log_execution
from tools.orders_system import calendar_notify
from tools.common import tw_holidays

REGIONS = batch.REGIONS
TW_TZ = ZoneInfo("Asia/Taipei")

# 未備妥提醒：工作日 11:00 一筆，建在 Jenny 的行事曆，提前 30 分鐘（10:30）跳窗
REMIND_CALENDAR_ID = "jenny@lemonclean.com.tw"
REMIND_SLOT = time(11, 0)
REMIND_BEFORE_MINUTES = 30


def next_remind_slot(now: datetime, is_workday=tw_holidays.is_workday):
    """今天 11:00；非工作日或已過 11:00 回傳 None。"""
    now = now.astimezone(TW_TZ)
    if not is_workday(now.date()):
        return None
    start = datetime.combine(now.date(), REMIND_SLOT, tzinfo=TW_TZ)
    return start if start > now else None


def todo_items(items: list) -> list:
    """未備妥＝還沒完成的列（只差回寫系統的已完成列不算）。"""
    return [item for item in items if item["actions"] != ["sync"]]


def _todo_text(item: dict) -> str:
    steps = [batch.ACTIONS[a][0] for a in item["actions"] if a != "sync"] + item["manual"]
    return "；".join(steps)


def build_notify_text(todo: list, today: date) -> tuple:
    lines = [f"{today:%Y/%m/%d} 清潔異動未備妥 {len(todo)} 筆", ""]
    for item in todo:
        lines.append(
            f"・[{item['region']}] {item['status']} {item['order_no']}"
            f"（{item.get('customer', '')}）待處理：{_todo_text(item)}（第{item['row']}列）"
        )
    lines += ["", "可到 Tools App【清潔異動】已備妥整合執行一次處理。"]
    return f"清潔異動未備妥：{len(todo)} 筆", "\n".join(lines)


def _notify(todo: list, now: datetime = None, log=print):
    now = now or datetime.now(TW_TZ)
    if not todo:
        log("沒有未備妥的列，不建提醒")
        return None
    start = next_remind_slot(now)
    if start is None:
        log("非工作日或已過 11:00，不建提醒")
        return None
    title, body = build_notify_text(todo, now.date())
    kwargs = dict(summary=title, description=body, start=start, reminder_minutes=[REMIND_BEFORE_MINUTES])
    try:
        event = calendar_notify.create_notify_event(calendar_id=REMIND_CALENDAR_ID, attendee="", **kwargs)
        log(f"已建立 {start:%H:%M} 提醒（{REMIND_CALENDAR_ID}）")
        return event
    except Exception as exc:  # 只建在 Jenny 的行事曆，不改建到共用日曆
        log(f"⚠️ 建立 {REMIND_CALENDAR_ID} 行事曆提醒失敗：{exc}")
        return None


def run(regions=None, ui_logger=None) -> dict:
    def log(msg):
        print(msg, flush=True)
        if ui_logger:
            ui_logger(msg)

    todo = []
    failed = []
    for region in regions or REGIONS:
        try:
            region_todo = todo_items(batch.scan_region(region, fix_formulas=True, log=log))
        except Exception as exc:
            failed.append(region)
            log(f"❌ 【{region}】讀取清潔異動失敗：{exc}")
            log_execution("清潔異動每日檢查", region, "失敗", str(exc))
            continue
        todo.extend(region_todo)
        log(f"【{region}】未備妥 {len(region_todo)} 筆")
        log_execution("清潔異動每日檢查", region, "成功", f"未備妥 {len(region_todo)} 筆")
    _notify(todo, log=log)
    return {"todo": todo, "failed": failed}


if __name__ == "__main__":
    outcome = run()
    print(outcome)
    sys.exit(1 if outcome["failed"] else 0)
