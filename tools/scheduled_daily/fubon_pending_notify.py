"""富邦待辦通知（GitHub 排程，每週二、五 10:00）：只檢查、建提醒，不執行富邦轉帳。

檢查台北／台中三種內部請款的待付款筆數：請款記錄、工具包押金退款、清潔用品採購
（盤點規則與【富邦銀行】內部請款整合執行共用 tools/finance_management/internal_payment_batch.py）。
異動 ATM 退款改由【清潔異動】已備妥整合執行處理，不在這裡提醒。
有待處理時在 jenny@lemonclean.com.tw 行事曆建提醒（30 分鐘後開始，提前 20、10 分鐘跳窗）。
"""
from __future__ import annotations

from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from tools.finance_management import internal_payment_batch as batch
from tools.orders_system import calendar_notify

TZ = ZoneInfo("Asia/Taipei")
REMIND_CALENDAR_ID = "jenny@lemonclean.com.tw"


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
    lines.append("請到 Tools App【富邦銀行】內部請款整合執行勾選後執行。")
    return "\n".join(lines).strip()


def main() -> None:
    items = batch.scan()
    report = build_report(items)
    print(report, flush=True)
    if not items:
        print("沒有待處理資料，不建立日曆提醒。", flush=True)
        return
    subject = f"【富邦銀行】待處理提醒：請款／工具包押金退款／清潔用品採購 共 {len(items)} 筆"
    # 開始時間留 30 分鐘前導，Google 的提醒推播才排得進去
    calendar_notify.create_notify_event(summary=subject, description=report,
                                        start=datetime.now(TZ) + timedelta(minutes=30),
                                        calendar_id=REMIND_CALENDAR_ID, attendee="", reminder_minutes=[20, 10])
    print(f"已在 {REMIND_CALENDAR_ID} 建立日曆提醒。", flush=True)


if __name__ == "__main__":
    main()
