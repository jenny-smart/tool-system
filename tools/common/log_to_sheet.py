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
SCOPES = ["https://www.googleapis.com/auth/spreadsheets"]

SHEET_BY_SYSTEM = {
    "daily": "日排程執行Log",
    "日排程系統": "日排程執行Log",
    "monthly": "月排程執行Log",
    "月排程系統": "月排程執行Log",
    "field": "外場排程執行Log",
    "外場排程系統": "外場排程執行Log",
    "外場日排程系統": "外場排程執行Log",
    "notify": "通知紀錄Log",
    "通知": "通知紀錄Log",
}

ERROR_SHEET_NAME = "錯誤追蹤Log"
LOG_HEADERS = ["執行時間","系統","功能","執行方式","區域","期別/日期","目標位置","來源檔名","結果","訊息"]
ERROR_HEADERS = ["執行時間","系統","功能","區域","期別/日期","結果","錯誤內容","Traceback","GitHub Run"]

def now_text() -> str:
    return datetime.now(TZ).strftime("%Y-%m-%d %H:%M:%S")

def normalize_dt(value: Any) -> str:
    if not value:
        return ""
    if isinstance(value, datetime):
        if value.tzinfo:
            return value.astimezone(TZ).strftime("%Y-%m-%d %H:%M:%S")
        return value.strftime("%Y-%m-%d %H:%M:%S")
    return str(value)

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
        for key in ["GOOGLE_SERVICE_ACCOUNT", "gcp_service_account"]:
            try:
                info = dict(st.secrets[key])
                if info:
                    return info
            except Exception:
                pass
    raise RuntimeError("找不到 GOOGLE_SERVICE_ACCOUNT 設定")

def get_log_spreadsheet_id(explicit_id: str = "") -> str:
    value = explicit_id.strip() or os.getenv("TOOLS_APP_LOG_SPREADSHEET_ID","").strip() or os.getenv("MASTER_LOG_SPREADSHEET_ID","").strip() or os.getenv("LOG_SPREADSHEET_ID","").strip()
    if value:
        return value
    if st is not None:
        for key in ["TOOLS_APP_LOG_SPREADSHEET_ID","MASTER_LOG_SPREADSHEET_ID","LOG_SPREADSHEET_ID"]:
            try:
                value = str(st.secrets[key]).strip()
                if value:
                    return value
            except Exception:
                pass
    raise RuntimeError("找不到主控 Log 試算表 ID，請設定 TOOLS_APP_LOG_SPREADSHEET_ID")

def get_sheets_service():
    creds = service_account.Credentials.from_service_account_info(get_service_account_info(), scopes=SCOPES)
    return build("sheets", "v4", credentials=creds, cache_discovery=False)

def get_sheet_id(service, spreadsheet_id: str, sheet_name: str) -> int | None:
    meta = service.spreadsheets().get(spreadsheetId=spreadsheet_id, fields="sheets(properties(sheetId,title))").execute()
    for sheet in meta.get("sheets", []):
        props = sheet.get("properties", {})
        if props.get("title") == sheet_name:
            return int(props.get("sheetId"))
    return None

def ensure_sheet(service, spreadsheet_id: str, sheet_name: str, headers: list[str]) -> None:
    sheet_id = get_sheet_id(service, spreadsheet_id, sheet_name)
    if sheet_id is None:
        res = service.spreadsheets().batchUpdate(spreadsheetId=spreadsheet_id, body={"requests":[{"addSheet":{"properties":{"title":sheet_name,"gridProperties":{"frozenRowCount":1}}}}]}).execute()
        sheet_id = int(res["replies"][0]["addSheet"]["properties"]["sheetId"])
    current = service.spreadsheets().values().get(spreadsheetId=spreadsheet_id, range=f"{sheet_name}!A1:Z1").execute().get("values", [])
    if not current or current[0][:len(headers)] != headers:
        service.spreadsheets().values().update(spreadsheetId=spreadsheet_id, range=f"{sheet_name}!A1", valueInputOption="USER_ENTERED", body={"values":[headers]}).execute()

def init_log_sheets(spreadsheet_id: str = "") -> None:
    spreadsheet_id = get_log_spreadsheet_id(spreadsheet_id)
    service = get_sheets_service()
    for sheet_name in sorted(set(SHEET_BY_SYSTEM.values())):
        ensure_sheet(service, spreadsheet_id, sheet_name, LOG_HEADERS)
    ensure_sheet(service, spreadsheet_id, ERROR_SHEET_NAME, ERROR_HEADERS)

def github_run_url() -> str:
    server_url = os.getenv("GITHUB_SERVER_URL","").strip()
    repository = os.getenv("GITHUB_REPOSITORY","").strip()
    run_id = os.getenv("GITHUB_RUN_ID","").strip()
    if server_url and repository and run_id:
        return f"{server_url}/{repository}/actions/runs/{run_id}"
    return ""

def append_row(service, spreadsheet_id: str, sheet_name: str, values: list[Any]) -> None:
    service.spreadsheets().values().append(spreadsheetId=spreadsheet_id, range=f"{sheet_name}!A:A", valueInputOption="USER_ENTERED", insertDataOption="INSERT_ROWS", body={"values":[values]}).execute()

def normalize_status(status: str) -> str:
    value = str(status or "").strip().lower()
    if value in ["running","執行中"]:
        return "執行中"
    if value in ["success","成功","ok","0","✅ 成功"]:
        return "成功"
    return "失敗"

def log_to_sheet(*, system: str, function: str, run_type: str = "", area: str = "", period: str = "", date: str = "", target: str = "", source_file: str = "", status: str = "", message: str = "", traceback_text: str = "", github_run: str = "", spreadsheet_id: str = "") -> None:
    spreadsheet_id = get_log_spreadsheet_id(spreadsheet_id)
    service = get_sheets_service()
    sheet_name = SHEET_BY_SYSTEM.get(str(system).strip(), SHEET_BY_SYSTEM.get(str(system).strip().lower()))
    if not sheet_name:
        raise RuntimeError(f"未知系統：{system}")
    init_log_sheets(spreadsheet_id)
    status_text = normalize_status(status)
    period_or_date = period or date
    append_row(service, spreadsheet_id, sheet_name, [now_text(), system, function, run_type, area, period_or_date, target, source_file, status_text, message])
    if status_text == "失敗":
        append_row(service, spreadsheet_id, ERROR_SHEET_NAME, [now_text(), system, function, area, period_or_date, status_text, message, traceback_text or message, github_run or github_run_url()])

def write_job_log(*, system_name: str, job_name: str, status: str, started_at: Any = "", finished_at: Any = "", message: str = "", area: str = "", period: str = "", date: str = "", target: str = "", source_file: str = "", run_type: str = "手動", traceback_text: str = "") -> None:
    msg_parts = []
    if started_at:
        msg_parts.append(f"開始時間：{normalize_dt(started_at)}")
    if finished_at:
        msg_parts.append(f"完成時間：{normalize_dt(finished_at)}")
    if message:
        msg_parts.append(str(message))
    log_to_sheet(system=system_name, function=job_name, run_type=run_type, area=area, period=period, date=date, target=target, source_file=source_file, status=status, message="｜".join(msg_parts), traceback_text=traceback_text or message)

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
    args = parser.parse_args()
    if args.init:
        init_log_sheets(args.spreadsheet_id)
        print("✅ Log 工作表初始化完成", flush=True)
        return
    if not args.system or not args.function:
        raise RuntimeError("請提供 --system 與 --function，或使用 --init")
    log_to_sheet(system=args.system, function=args.function, run_type=args.run_type, area=args.area, period=args.period, date=args.date, target=args.target, source_file=args.source_file, status=args.status, message=args.message, traceback_text=args.traceback, spreadsheet_id=args.spreadsheet_id)
    print("✅ 已寫入 Log 工作表", flush=True)

if __name__ == "__main__":
    main()
