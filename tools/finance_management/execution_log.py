"""財務工具共用的執行記錄：寫進主控試算表（Jenny's Lemonhometools）的
「財務工具執行記錄」分頁，讓每次跑財報富邦更新規則、現金缺口試算…等工具
留下永久紀錄（不像 Streamlit 畫面上的執行日誌，重新整理就沒了）。
"""

from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

from tools.common.config_loader import get_master_spreadsheet_id, get_sheets_service

EXECUTION_LOG_SHEET = "財務工具執行記錄"
TW_TZ = ZoneInfo("Asia/Taipei")

_HEADER = ["時間", "工具", "地區", "狀態", "訊息"]


def _ensure_log_sheet(service, spreadsheet_id: str) -> None:
    meta = service.spreadsheets().get(
        spreadsheetId=spreadsheet_id, fields="sheets.properties.title"
    ).execute()
    titles = {s["properties"]["title"] for s in meta.get("sheets", [])}
    if EXECUTION_LOG_SHEET in titles:
        return
    service.spreadsheets().batchUpdate(
        spreadsheetId=spreadsheet_id,
        body={"requests": [{"addSheet": {"properties": {"title": EXECUTION_LOG_SHEET}}}]},
    ).execute()
    service.spreadsheets().values().update(
        spreadsheetId=spreadsheet_id,
        range=f"'{EXECUTION_LOG_SHEET}'!A1:E1",
        valueInputOption="RAW",
        body={"values": [_HEADER]},
    ).execute()


def log_execution(tool: str, area: str, status: str, message: str = "") -> None:
    """打卡一筆執行記錄。任何失敗都吞掉（記錄本身不該讓主要功能跟著失敗）。"""
    try:
        service = get_sheets_service()
        spreadsheet_id = get_master_spreadsheet_id()
        _ensure_log_sheet(service, spreadsheet_id)
        now_text = datetime.now(TW_TZ).strftime("%Y/%m/%d %H:%M:%S")
        service.spreadsheets().values().append(
            spreadsheetId=spreadsheet_id,
            range=f"'{EXECUTION_LOG_SHEET}'!A:E",
            valueInputOption="USER_ENTERED",
            insertDataOption="INSERT_ROWS",
            body={"values": [[now_text, tool, area, status, message]]},
        ).execute()
    except Exception:
        pass
