# calendar_notify.py
# -*- coding: utf-8 -*-
"""
共用：把排程結果寫成 Google Calendar 事件通知 Jenny。

優先用 Jenny 本人的 OAuth 授權（GOOGLE_CALENDAR_REFRESH_TOKEN，與 Drive 共用
GOOGLE_OAUTH_CLIENT_ID／SECRET）；尚未設定時改用 tools.common.config_loader 的
共用服務帳號（不載入 orders.py 整個大檔）。事件一律建在 Jenny 的
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


# Jenny 本人（jenny@lemonclean.com.tw）授權的行事曆 refresh token；
# 由 tools/common/google_oauth_setup_lemonclean_calendar.py 產生；
# 該腳本用的 OAuth 用戶端與 GOOGLE_OAUTH_CLIENT_ID 不同時，另設 GOOGLE_CALENDAR_CLIENT_ID／SECRET
CALENDAR_REFRESH_TOKEN_KEY = "GOOGLE_CALENDAR_REFRESH_TOKEN"


def _oauth_credentials():
    """有 Jenny 行事曆授權時回傳 OAuth 憑證，否則回傳 None。"""
    from google.oauth2.credentials import Credentials as OAuthCredentials

    from tools.common.google_auth import _oauth_value

    refresh_token = _oauth_value(CALENDAR_REFRESH_TOKEN_KEY)
    if not refresh_token:
        return None
    return OAuthCredentials(
        token=None,
        refresh_token=refresh_token,
        token_uri="https://oauth2.googleapis.com/token",
        # 行事曆授權若用了不同的 OAuth 用戶端（jenny_tool-oauth.json），要配同一組 ID／SECRET，
        # 否則 Google 回 unauthorized_client；未設定時沿用 Drive 那組
        client_id=_oauth_value("GOOGLE_CALENDAR_CLIENT_ID") or _oauth_value("GOOGLE_OAUTH_CLIENT_ID"),
        client_secret=_oauth_value("GOOGLE_CALENDAR_CLIENT_SECRET") or _oauth_value("GOOGLE_OAUTH_CLIENT_SECRET"),
        scopes=["https://www.googleapis.com/auth/calendar.events"],
    )


def _calendar_service():
    # 優先用 Jenny 本人授權（服務帳號沒有她行事曆的寫入權限，會回 404）；
    # 尚未設定時沿用服務帳號
    credentials = _oauth_credentials() or Credentials.from_service_account_info(
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
