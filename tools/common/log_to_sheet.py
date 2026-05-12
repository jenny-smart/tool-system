from __future__ import annotations
import json, os
from datetime import datetime, timedelta, timezone
from google.oauth2 import service_account
from googleapiclient.discovery import build

TZ = timezone(timedelta(hours=8))
SCOPES = ["https://www.googleapis.com/auth/spreadsheets"]

SHEET_BY_SYSTEM = {
    "日排程系統": "日排程執行Log",
    "月排程系統": "月排程執行Log",
    "外場排程系統": "外場排程執行Log",
    "notify": "通知紀錄Log",
}

def now_text():
    return datetime.now(TZ).strftime("%Y-%m-%d %H:%M:%S")

def get_service():
    info = json.loads(os.environ["GOOGLE_SERVICE_ACCOUNT"])
    creds = service_account.Credentials.from_service_account_info(
        info,
        scopes=SCOPES,
    )
    return build("sheets", "v4", credentials=creds, cache_discovery=False)

def append_row(service, spreadsheet_id, sheet_name, values):
    result = service.spreadsheets().values().get(
        spreadsheetId=spreadsheet_id,
        range=f"{sheet_name}!A:A",
    ).execute()

    next_row = len(result.get("values", [])) + 1

    service.spreadsheets().values().update(
        spreadsheetId=spreadsheet_id,
        range=f"{sheet_name}!A{next_row}",
        valueInputOption="USER_ENTERED",
        body={"values": [values]},
    ).execute()

def log_to_sheet(
    *,
    system,
    function,
    run_type="",
    area="",
    period="",
    target="",
    source_file="",
    status="",
    message="",
):
    spreadsheet_id = os.environ["TOOLS_APP_LOG_SPREADSHEET_ID"]
    service = get_service()

    row = [
        now_text(),
        system,
        function,
        run_type,
        area,
        period,
        target,
        source_file,
        status,
        message,
    ]

    append_row(
        service,
        spreadsheet_id,
        SHEET_BY_SYSTEM[system],
        row,
    )
