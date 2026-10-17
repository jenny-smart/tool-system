# calendar_notify.py
# -*- coding: utf-8 -*-
"""
共用：把排程結果寫成 Google Calendar 事件通知 Jenny。

比照 tools/orders_system/orders.py 的 build_gcal_service()（Service Account +
calendar scope，不需要另外的 OAuth 密鑰），但改用 tools.common.config_loader
的共用服務帳號設定，避免載入 orders.py 整個大檔（含 accounts/env 等仰賴
tools/orders_system 套件 sys.path 技巧的絕對匯入）。事件建立在既有的台北
日曆上，並把 Jenny 加為與會者（sendUpdates="all"），讓她同時收到日曆通知信。
"""
from __future__ import annotations

from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from google.oauth2.service_account import Credentials
from googleapiclient.discovery import build

from tools.common.config_loader import get_service_account_info
from tools.orders_system.env import GOOGLE_CALENDAR_MAP

TAIPEI_TZ = ZoneInfo("Asia/Taipei")

NOTIFY_ATTENDEE_EMAIL = "jenny@hers.com.tw"
NOTIFY_CALENDAR_REGION = "台北"
EVENT_DURATION_MINUTES = 30
REMINDER_MINUTES = [10, 20]

_CALENDAR_SCOPES = ["https://www.googleapis.com/auth/calendar"]


def _calendar_service():
    credentials = Credentials.from_service_account_info(
        get_service_account_info(), scopes=_CALENDAR_SCOPES
    )
    return build("calendar", "v3", credentials=credentials, cache_discovery=False)


def create_notify_event(summary: str, description: str = "", start: datetime = None,
                         calendar_id: str = None):
    """建立一個通知用日曆事件：30 分鐘、提前 20 分與 10 分跳窗提醒，並邀請 Jenny。"""
    service = _calendar_service()
    calendar_id = calendar_id or GOOGLE_CALENDAR_MAP.get(NOTIFY_CALENDAR_REGION)
    if not calendar_id:
        raise ValueError(f"{NOTIFY_CALENDAR_REGION} 尚未設定 Google Calendar ID")

    start = start or datetime.now(TAIPEI_TZ)
    end = start + timedelta(minutes=EVENT_DURATION_MINUTES)

    body = {
        "summary": summary,
        "description": description,
        "start": {"dateTime": start.isoformat(), "timeZone": "Asia/Taipei"},
        "end": {"dateTime": end.isoformat(), "timeZone": "Asia/Taipei"},
        "attendees": [{"email": NOTIFY_ATTENDEE_EMAIL}],
        "reminders": {
            "useDefault": False,
            "overrides": [{"method": "popup", "minutes": m} for m in REMINDER_MINUTES],
        },
    }

    return service.events().insert(
        calendarId=calendar_id, body=body, sendUpdates="all"
    ).execute()
