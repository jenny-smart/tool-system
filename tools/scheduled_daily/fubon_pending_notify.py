from __future__ import annotations

from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from google.oauth2 import service_account
from googleapiclient.discovery import build

from tools.bank_statement.fubon_deposit_refund_filter import pending_deposit_refunds
from tools.bank_statement.fubon_payment_request_filter import pending_payment_requests
from tools.bank_statement.fubon_refund_filter import pending_atm_refunds
from tools.bank_statement.internal_payment_registry import (
    DEPOSIT_REFUND_TYPE,
    PAYMENT_REQUEST_TYPE,
    read_report_values,
)
from tools.common.config_loader import get_service_account_info
from tools.memo_system.change_order import get_worksheet

AREAS = ["台北", "台中"]
TZ = ZoneInfo("Asia/Taipei")
CALENDAR_SCOPES = ["https://www.googleapis.com/auth/calendar.events"]


def create_calendar_reminder(summary: str, description: str) -> None:
    """用 Google Calendar 事件當提醒，取代寄信。

    跟其他排程共用同一套 Google 認證：sitecustomize 在 GOOGLE_OAUTH_* 就緒時，
    會把這裡建立的服務帳號憑證換成 Jenny 本人的 OAuth，事件才會建到她自己的
    日曆（calendarId="primary"）上，而不是服務帳號自己的日曆。
    """
    creds = service_account.Credentials.from_service_account_info(
        get_service_account_info(), scopes=CALENDAR_SCOPES
    )
    service = build("calendar", "v3", credentials=creds, cache_discovery=False)

    start = datetime.now(TZ)
    end = start + timedelta(minutes=30)
    body = {
        "summary": summary,
        "description": description,
        "start": {"dateTime": start.isoformat(), "timeZone": "Asia/Taipei"},
        "end": {"dateTime": end.isoformat(), "timeZone": "Asia/Taipei"},
        "reminders": {"useDefault": False, "overrides": [{"method": "popup", "minutes": 0}]},
    }
    service.events().insert(calendarId="primary", body=body).execute()


def describe(label: str, candidates: list[dict[str, object]]) -> str:
    if not candidates:
        return f"　{label}：0 筆"
    rows = "、".join(str(item["sheet_row"]) for item in candidates)
    return f"　{label}：{len(candidates)} 筆（列號：{rows}）"


def main() -> None:
    lines: list[str] = []
    total = 0

    for area in AREAS:
        atm_candidates = pending_atm_refunds(get_worksheet(area).get_all_values())
        payment_candidates = pending_payment_requests(read_report_values(PAYMENT_REQUEST_TYPE, area))
        deposit_candidates = pending_deposit_refunds(read_report_values(DEPOSIT_REFUND_TYPE, area))

        total += len(atm_candidates) + len(payment_candidates) + len(deposit_candidates)

        lines.append(f"【{area}】")
        lines.append(describe("異動 ATM 退款", atm_candidates))
        lines.append(describe("請款記錄", payment_candidates))
        lines.append(describe("工具包押金退款", deposit_candidates))
        lines.append("")

    report = "\n".join(lines).strip()
    print(report, flush=True)

    if total == 0:
        print("沒有新增待處理資料，不建立日曆提醒。", flush=True)
        return

    subject = f"【富邦銀行】待處理提醒：異動 ATM 退款／請款／工具包押金退款 共 {total} 筆"
    create_calendar_reminder(subject, report)
    print("已建立日曆提醒。", flush=True)


if __name__ == "__main__":
    main()
