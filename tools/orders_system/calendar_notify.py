# calendar_notify.py
# -*- coding: utf-8 -*-
"""
共用：把排程結果寫成 Google Calendar 事件通知 Jenny。

比照 tools/orders_system/orders.py 的 build_gcal_service()（Service Account +
calendar scope，不需要另外的 OAuth 密鑰），但改用 tools.common.config_loader
的共用服務帳號設定，避免載入 orders.py 整個大檔（含 accounts/env 等仰賴
tools/orders_system 套件 sys.path 技巧的絕對匯入）。事件一律建在 Jenny 的
行事曆（jenny@lemonclean.com.tw），不用共用日曆（lemonclean925），也不另寄邀請信。
"""
from __future__ import annotations

from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from google.oauth2.service_account import Credentials
from googleapiclient.discovery import build

from tools.common.config_loader import get_service_account_info

TAIPEI_TZ = ZoneInfo("Asia/Taipei")

# 系統通知一律建在 Jenny 的行事曆，不用共用日曆（lemonclean925）
NOTIFY_CALENDAR_ID = "jenny@lemonclean.com.tw"
EVENT_DURATION_MINUTES = 30
REMINDER_MINUTES = [10, 20]

_CALENDAR_SCOPES = ["https://www.googleapis.com/auth/calendar"]


def _calendar_service():
    credentials = Credentials.from_service_account_info(
        get_service_account_info(), scopes=_CALENDAR_SCOPES
    )
    return build("calendar", "v3", credentials=credentials, cache_discovery=False)


def create_notify_event(summary: str, description: str = "", start: datetime = None,
                         calendar_id: str = None, reminder_minutes=None, attendee: str = ""):
    """建立一個通知用日曆事件：30 分鐘、預設提前 20 分與 10 分跳窗提醒，
    建在 Jenny 的行事曆；attendee 有給才另外邀請（會寄邀請信）。"""
    service = _calendar_service()
    calendar_id = calendar_id or NOTIFY_CALENDAR_ID

    start = start or datetime.now(TAIPEI_TZ)
    end = start + timedelta(minutes=EVENT_DURATION_MINUTES)

    body = {
        "summary": summary,
        "description": description,
        "start": {"dateTime": start.isoformat(), "timeZone": "Asia/Taipei"},
        "end": {"dateTime": end.isoformat(), "timeZone": "Asia/Taipei"},
        "reminders": {
            "useDefault": False,
            # reminder_minutes=[] 表示不跳提醒（例如只留紀錄的事件）
            "overrides": [{"method": "popup", "minutes": m}
                          for m in (REMINDER_MINUTES if reminder_minutes is None else reminder_minutes)],
        },
    }
    if attendee:
        body["attendees"] = [{"email": attendee}]

    return service.events().insert(
        calendarId=calendar_id, body=body, sendUpdates="all" if attendee else "none"
    ).execute()
