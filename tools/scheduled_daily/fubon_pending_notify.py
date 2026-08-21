from __future__ import annotations

import os
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build

from tools.bank_statement.fubon_deposit_refund_filter import pending_deposit_refunds
from tools.bank_statement.fubon_payment_request_filter import pending_payment_requests
from tools.bank_statement.fubon_refund_filter import pending_atm_refunds
from tools.bank_statement.internal_payment_registry import (
    DEPOSIT_REFUND_TYPE,
    PAYMENT_REQUEST_TYPE,
    read_report_values,
)
from tools.memo_system.change_order import get_worksheet

AREAS = ["台北", "台中"]
TZ = ZoneInfo("Asia/Taipei")
CALENDAR_SCOPES = ["https://www.googleapis.com/auth/calendar.events"]


def create_calendar_reminder(summary: str, description: str) -> None:
    """用 Google Calendar 事件當提醒，取代寄信。

    jenny@hers.com.tw 所屬的 Google Workspace 不允許把日曆共用「變更活動」
    權限授權給網域外帳號（jenny@lemonclean.com.tw），所以這裡不能沿用其他
    排程共用的那組 GOOGLE_OAUTH_REFRESH_TOKEN（那組是 lemonclean 身分）。
    改用一組獨立、直接以 jenny@hers.com.tw 本人身分授權的 refresh token
    （GOOGLE_HERS_CALENDAR_REFRESH_TOKEN），寫進她自己的主日曆
    （calendarId="primary"），不需要任何跨帳號共用設定。
    """
    client_id = os.getenv("GOOGLE_OAUTH_CLIENT_ID", "").strip()
    client_secret = os.getenv("GOOGLE_OAUTH_CLIENT_SECRET", "").strip()
    refresh_token = os.getenv("GOOGLE_HERS_CALENDAR_REFRESH_TOKEN", "").strip()

    missing = [name for name, value in [
        ("GOOGLE_OAUTH_CLIENT_ID", client_id),
        ("GOOGLE_OAUTH_CLIENT_SECRET", client_secret),
        ("GOOGLE_HERS_CALENDAR_REFRESH_TOKEN", refresh_token),
    ] if not value]
    if missing:
        raise RuntimeError("缺少日曆授權設定：" + "、".join(missing))

    creds = Credentials(
        token=None,
        refresh_token=refresh_token,
        token_uri="https://oauth2.googleapis.com/token",
        client_id=client_id,
        client_secret=client_secret,
        scopes=CALENDAR_SCOPES,
    )
    service = build("calendar", "v3", credentials=creds, cache_discovery=False)

    # 事件開始時間要留足夠前導時間，Google 的提醒推播才排得進去：開始時間訂在
    # 30 分鐘後，並在開始前 20 分鐘、10 分鐘各跳一次 popup 提醒。
    start = datetime.now(TZ) + timedelta(minutes=30)
    end = start + timedelta(minutes=30)
    body = {
        "summary": summary,
        "description": description,
        "start": {"dateTime": start.isoformat(), "timeZone": "Asia/Taipei"},
        "end": {"dateTime": end.isoformat(), "timeZone": "Asia/Taipei"},
        "reminders": {
            "useDefault": False,
            "overrides": [
                {"method": "popup", "minutes": 20},
                {"method": "popup", "minutes": 10},
            ],
        },
    }
    service.events().insert(calendarId="primary", body=body).execute()


def describe(label: str, candidates: list[dict[str, object]], detail_key: str = "") -> str:
    if not candidates:
        return f"　{label}：0 筆"
    if not detail_key:
        parts = [str(item["sheet_row"]) for item in candidates]
    else:
        parts = []
        for item in candidates:
            detail = str(item.get(detail_key, "")).strip()
            row_text = str(item["sheet_row"])
            parts.append(f"{row_text}（{detail}）" if detail else row_text)
    return f"　{label}：{len(candidates)} 筆（{'、'.join(parts)}）"


def main() -> None:
    lines: list[str] = []
    total = 0

    for area in AREAS:
        atm_candidates = pending_atm_refunds(get_worksheet(area).get_all_values())
        payment_candidates = pending_payment_requests(read_report_values(PAYMENT_REQUEST_TYPE, area))
        deposit_candidates = pending_deposit_refunds(read_report_values(DEPOSIT_REFUND_TYPE, area))

        total += len(atm_candidates) + len(payment_candidates) + len(deposit_candidates)

        lines.append(f"【{area}】")
        lines.append(describe("異動 ATM 退款", atm_candidates, "note"))       # K 欄（後台備註）
        lines.append(describe("請款記錄", payment_candidates, "memo"))         # E 欄（費用說明）
        lines.append(describe("工具包押金退款", deposit_candidates, "memo"))   # Q 欄（留言）
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
