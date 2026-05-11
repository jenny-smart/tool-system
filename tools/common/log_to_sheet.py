from __future__ import annotations

import argparse
import json
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

try:
    import streamlit as st
except Exception:
    st = None

from google.oauth2 import service_account
from googleapiclient.discovery import build

TZ = timezone(timedelta(hours=8))

SCOPES = [
    "https://www.googleapis.com/auth/spreadsheets",
]

LOG_SHEETS = {
    "daily": "日排程執行Log",
    "monthly": "月排程執行Log",
    "field": "外場排程執行Log",
    "notify": "通知紀錄Log",
}

ERROR_SHEET_NAME = "錯誤追蹤Log"

HEADERS = [
    "執行時間",
    "系統",
    "功能",
    "執行方式",
    "區域",
    "期別",
    "日期",
    "目標位置",
    "來源檔名",
    "結果",
    "訊息",
    "GitHub Run",
]

ERROR_HEADERS = [
    "執行時間",
    "系統",
    "功能",
    "區域",
    "期別",
    "日期",
    "錯誤內容",
    "Traceback",
    "GitHub Run",
]


def now_text() -> str:
    return datetime.now(TZ).strftime("%Y-%m-%d %H:%M:%S")


def get_service_account_info() -> dict[str, Any]:
    raw = os.getenv("GOOGLE_SERVICE_ACCOUNT", "").strip()
    if raw:
        return json.loads(raw)

    raw_json = os.getenv("GOOGLE_SERVICE_ACCOUNT_JSON", "").strip()
    if raw_json:
        return json.loads(raw_json)

    path = os.getenv("GOOGLE_SERVICE_ACCOUNT_FILE", "").strip()
    if path and Path(path).exists():
        return json.loads(Path(path).read_text(encoding="utf-8"))

    if st is not None:
        try:
            return dict(st.secrets["GOOGLE_SERVICE_ACCOUNT"])
        except Exception:
            pass

    raise RuntimeError("找不到 GOOGLE_SERVICE_ACCOUNT 設定")


def get_spreadsheet_id(explicit_id: str = "") -> str:
    value = (
        explicit_id.strip()
        or os.getenv("TOOLS_APP_LOG_SPREADSHEET_ID", "").strip()
        or os.getenv("MASTER_LOG_SPREADSHEET_ID", "").strip()
    )

    if value:
        return value

    if st is not None:
        for key in ["TOOLS_APP_LOG_SPREADSHEET_ID", "MASTER_LOG_SPREADSHEET_ID"]:
            try:
                value = str(st.secrets[key]).strip()
                if value:
                    return value
            except Exception:
                pass

    raise RuntimeError(
        "找不到主控 Log 試算表 ID，請設定 TOOLS_APP_LOG_SPREADSHEET_ID"
    )


def get_sheets_service():
    creds = service_account.Credentials.from_service_account_info(
        get_service_account_info(),
        scopes=SCOPES,
    )
    return build("sheets", "v4", credentials=creds, cache_discovery=False)


def get_spreadsheet_metadata(service, spreadsheet_id: str) -> dict[str, Any]:
    return service.spreadsheets().get(
        spreadsheetId=spreadsheet_id,
        fields="sheets(properties(sheetId,title,gridProperties))",
    ).execute()


def get_sheet_id(service, spreadsheet_id: str, sheet_name: str) -> int | None:
    meta = get_spreadsheet_metadata(service, spreadsheet_id)

    for sheet in meta.get("sheets", []):
        props = sheet.get("properties", {})
        if props.get("title") == sheet_name:
            return int(props.get("sheetId"))

    return None


def ensure_sheet(
    service,
    spreadsheet_id: str,
    sheet_name: str,
    headers: list[str],
) -> None:
    sheet_id = get_sheet_id(service, spreadsheet_id, sheet_name)

    if sheet_id is None:
        body = {
            "requests": [
                {
                    "addSheet": {
                        "properties": {
                            "title": sheet_name,
                            "gridProperties": {
                                "frozenRowCount": 1,
                            },
                        }
                    }
                }
            ]
        }

        res = service.spreadsheets().batchUpdate(
            spreadsheetId=spreadsheet_id,
            body=body,
        ).execute()

        sheet_id = int(
            res["replies"][0]["addSheet"]["properties"]["sheetId"]
        )

    current = service.spreadsheets().values().get(
        spreadsheetId=spreadsheet_id,
        range=f"{sheet_name}!A1:L1",
    ).execute().get("values", [])

    if not current or current[0] != headers:
        service.spreadsheets().values().update(
            spreadsheetId=spreadsheet_id,
            range=f"{sheet_name}!A1",
            valueInputOption="USER_ENTERED",
            body={"values": [headers]},
        ).execute()

    format_requests = [
        {
            "repeatCell": {
                "range": {
                    "sheetId": sheet_id,
                    "startRowIndex": 0,
                    "endRowIndex": 1,
                },
                "cell": {
                    "userEnteredFormat": {
                        "textFormat": {"bold": True},
                        "backgroundColor": {
                            "red": 0.86,
                            "green": 0.90,
                            "blue": 0.98,
                        },
                    }
                },
                "fields": "userEnteredFormat(textFormat,backgroundColor)",
            }
        },
        {
            "updateSheetProperties": {
                "properties": {
                    "sheetId": sheet_id,
                    "gridProperties": {
                        "frozenRowCount": 1,
                    },
                },
                "fields": "gridProperties.frozenRowCount",
            }
        },
        {
            "autoResizeDimensions": {
                "dimensions": {
                    "sheetId": sheet_id,
                    "dimension": "COLUMNS",
                    "startIndex": 0,
                    "endIndex": len(headers),
                }
            }
        },
    ]

    service.spreadsheets().batchUpdate(
        spreadsheetId=spreadsheet_id,
        body={"requests": format_requests},
    ).execute()


def init_log_sheets(spreadsheet_id: str = "") -> None:
    spreadsheet_id = get_spreadsheet_id(spreadsheet_id)
    service = get_sheets_service()

    for sheet_name in LOG_SHEETS.values():
        ensure_sheet(service, spreadsheet_id, sheet_name, HEADERS)

    ensure_sheet(service, spreadsheet_id, ERROR_SHEET_NAME, ERROR_HEADERS)


def github_run_url() -> str:
    server_url = os.getenv("GITHUB_SERVER_URL", "").strip()
    repository = os.getenv("GITHUB_REPOSITORY", "").strip()
    run_id = os.getenv("GITHUB_RUN_ID", "").strip()

    if server_url and repository and run_id:
        return f"{server_url}/{repository}/actions/runs/{run_id}"

    return ""


def append_row(
    service,
    spreadsheet_id: str,
    sheet_name: str,
    values: list[Any],
) -> None:
    service.spreadsheets().values().append(
        spreadsheetId=spreadsheet_id,
        range=f"{sheet_name}!A:A",
        valueInputOption="USER_ENTERED",
        insertDataOption="INSERT_ROWS",
        body={"values": [values]},
    ).execute()


def log_to_sheet(
    *,
    system: str,
    function: str,
    run_type: str = "",
    area: str = "",
    period: str = "",
    date: str = "",
    target: str = "",
    source_file: str = "",
    status: str = "",
    message: str = "",
    traceback_text: str = "",
    github_run: str = "",
    spreadsheet_id: str = "",
) -> None:
    spreadsheet_id = get_spreadsheet_id(spreadsheet_id)
    service = get_sheets_service()

    system_key = system.strip().lower()
    sheet_name = LOG_SHEETS.get(system_key)

    if not sheet_name:
        raise RuntimeError(
            f"未知系統：{system}，可用值：{', '.join(LOG_SHEETS.keys())}"
        )

    init_log_sheets(spreadsheet_id)

    run_url = github_run or github_run_url()

    row = [
        now_text(),
        system,
        function,
        run_type,
        area,
        period,
        date,
        target,
        source_file,
        status,
        message,
        run_url,
    ]

    append_row(service, spreadsheet_id, sheet_name, row)

    if status not in ["成功", "success", "SUCCESS", "✅ 成功"]:
        error_row = [
            now_text(),
            system,
            function,
            area,
            period,
            date,
            message,
            traceback_text,
            run_url,
        ]

        append_row(service, spreadsheet_id, ERROR_SHEET_NAME, error_row)


def main() -> None:
    parser = argparse.ArgumentParser()

    parser.add_argument("--init", action="store_true")
    parser.add_argument("--spreadsheet-id", default="")

    parser.add_argument("--system", default="")
    parser.add_argument("--function", default="")
    parser.add_argument("--run-type", default="")
    parser.add_argument("--area", default="")
    parser.add_argument("--period", default="")
    parser.add_argument("--date", default="")
    parser.add_argument("--target", default="")
    parser.add_argument("--source-file", default="")
    parser.add_argument("--status", default="")
    parser.add_argument("--message", default="")
    parser.add_argument("--traceback", default="")
    parser.add_argument("--github-run", default="")

    args = parser.parse_args()

    if args.init:
        init_log_sheets(args.spreadsheet_id)
        print("✅ Log 工作表初始化完成", flush=True)
        return

    if not args.system or not args.function:
        raise RuntimeError("請提供 --system 與 --function，或使用 --init")

    log_to_sheet(
        system=args.system,
        function=args.function,
        run_type=args.run_type,
        area=args.area,
        period=args.period,
        date=args.date,
        target=args.target,
        source_file=args.source_file,
        status=args.status,
        message=args.message,
        traceback_text=args.traceback,
        github_run=args.github_run,
        spreadsheet_id=args.spreadsheet_id,
    )

    print("✅ 已寫入 Log 工作表", flush=True)


if __name__ == "__main__":
    main()
