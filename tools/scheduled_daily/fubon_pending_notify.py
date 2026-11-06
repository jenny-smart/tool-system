"""【整合執行】富邦內部請款盤點（排程時間見主控表「排程設定」fubon_pending_check，預設每週二、五 10:30）。

執行方式（排程設定 H 欄）：
  盤點後等確認（預設）：只盤點、建提醒，你確認後到 Tools App【整合執行】富邦內部請款執行。
  盤點後自動排入：盤點到的全部排入本機 Agent（富邦轉帳仍需手機核准）。

檢查台北／台中三種內部請款的待付款筆數：請款記錄、工具包押金退款、清潔用品採購
（盤點規則與【整合執行】富邦內部請款共用 tools/finance_management/internal_payment_batch.py）。
異動 ATM 退款改由【整合執行】清潔異動已備妥處理，不在這裡提醒。
有待處理時在 jenny@hers.com.tw 行事曆建提醒（30 分鐘後開始，提前 20、10 分鐘跳窗）。
"""
from __future__ import annotations

from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from tools.finance_management import internal_payment_batch as batch
from tools.orders_system import calendar_notify

TZ = ZoneInfo("Asia/Taipei")
REMIND_CALENDAR_ID = "jenny@hers.com.tw"


def build_report(items: list[dict]) -> str:
    lines = []
    for area in batch.AREAS:
        lines.append(f"【{area}】")
        for kind, (label, _action) in batch.KINDS.items():
            hit = [i for i in items if i["area"] == area and i["kind"] == kind]
            if not hit:
                lines.append(f"　{label}：0 筆")
                continue
            detail = "、".join(f"{i['row']}（{i['memo']}）" if i.get("memo") else str(i["row"]) for i in hit)
            lines.append(f"　{label}：{len(hit)} 筆（{detail}）")
        lines.append("")
    lines.append("打開 Tools App 會跳視窗，按「前往處理」直接停在這次盤點結果，勾選後執行（不用重新盤點）。")
    return "\n".join(lines).strip()


def main() -> None:
    from tools.scheduling.schedule_settings import MODE_CONFIRM, MODE_QUEUE, get_mode

    items = batch.scan()
    report = build_report(items)
    print(report, flush=True)
    if not items:
        print("沒有待處理資料，不建立日曆提醒。", flush=True)
        return
    if get_mode("fubon_pending_check", MODE_CONFIRM) != MODE_QUEUE:
        try:  # 2026-11-06 Jenny：存盤點結果，Tools App 跳視窗、停在這次盤點結果接著執行
            from tools.scheduling import scan_results

            scan_results.save("fubon_pending_check", items, f"待付款 {len(items)} 筆")
            print("已存盤點結果，Tools App 會跳視窗", flush=True)
        except Exception as exc:
            print(f"⚠️ 盤點結果存檔失敗：{exc}", flush=True)
    if get_mode("fubon_pending_check", MODE_CONFIRM) == MODE_QUEUE:
        from tools.local_agent_queue import create_task

        batch.queue_actions(items, create_task, created_by="排程系統", log=lambda t: print(t, flush=True))
        report += "\n\n已自動排入本機 Agent，富邦轉帳請用手機核准。"
    subject = f"【富邦銀行】待處理提醒：請款／工具包押金退款／清潔用品採購 共 {len(items)} 筆"
    # 開始時間留 30 分鐘前導，Google 的提醒推播才排得進去
    event = calendar_notify.create_notify_event(summary=subject, description=report,
                                                start=datetime.now(TZ) + timedelta(minutes=30),
                                                calendar_id=REMIND_CALENDAR_ID, attendee="", reminder_minutes=[20, 10],
                                                replace_prefix="【富邦銀行】待處理提醒")
    print(f"已在 {(event or {}).get('_calendar_id', REMIND_CALENDAR_ID)} 建立日曆提醒。", flush=True)
    try:  # 2026-11-06 Jenny：盤點完 Mac 跳視窗提醒去確認及執行
        from tools.local_agent_queue import queue_popup

        queue_popup(subject, report, system="財務管理", function="【整合執行】富邦內部請款", scan_key="fubon_pending_check")
        print("已排入 Mac 提醒視窗", flush=True)
    except Exception as exc:
        print(f"⚠️ Mac 提醒視窗排入失敗：{exc}", flush=True)


if __name__ == "__main__":
    main()
