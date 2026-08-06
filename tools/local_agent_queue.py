from __future__ import annotations

import json
import os
import socket
import uuid
from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo

from tools.common.config_loader import get_master_spreadsheet_id, get_sheets_service


SHEET_NAME = "本機Agent任務"
HEADERS = [
    "task_id",
    "created_at",
    "created_by",
    "action",
    "params_json",
    "status",
    "agent_id",
    "started_at",
    "finished_at",
    "message",
    "log",
    "result_json",
]
TZ = ZoneInfo("Asia/Taipei")


def now_text() -> str:
    return datetime.now(TZ).strftime("%Y-%m-%d %H:%M:%S")


def default_agent_id() -> str:
    return os.getenv("TOOL_LOCAL_AGENT_ID", "").strip() or socket.gethostname()


def _sheet_id(service: Any, spreadsheet_id: str) -> int | None:
    meta = service.spreadsheets().get(
        spreadsheetId=spreadsheet_id,
        fields="sheets(properties(sheetId,title))",
    ).execute()
    for sheet in meta.get("sheets", []):
        props = sheet.get("properties", {})
        if props.get("title") == SHEET_NAME:
            return int(props["sheetId"])
    return None


def ensure_task_sheet(service: Any | None = None, spreadsheet_id: str = "") -> tuple[Any, str]:
    service = service or get_sheets_service()
    spreadsheet_id = spreadsheet_id or get_master_spreadsheet_id()
    if _sheet_id(service, spreadsheet_id) is None:
        service.spreadsheets().batchUpdate(
            spreadsheetId=spreadsheet_id,
            body={
                "requests": [{
                    "addSheet": {
                        "properties": {
                            "title": SHEET_NAME,
                            "gridProperties": {"frozenRowCount": 1},
                        }
                    }
                }]
            },
        ).execute()
    current = service.spreadsheets().values().get(
        spreadsheetId=spreadsheet_id,
        range=f"'{SHEET_NAME}'!A1:L1",
    ).execute().get("values", [])
    if not current or current[0] != HEADERS:
        service.spreadsheets().values().update(
            spreadsheetId=spreadsheet_id,
            range=f"'{SHEET_NAME}'!A1",
            valueInputOption="RAW",
            body={"values": [HEADERS]},
        ).execute()
    return service, spreadsheet_id


def create_task(
    action: str,
    params: dict[str, Any] | None = None,
    *,
    created_by: str = "Tool System",
    service: Any | None = None,
    spreadsheet_id: str = "",
) -> dict[str, Any]:
    service, spreadsheet_id = ensure_task_sheet(service, spreadsheet_id)
    task = {
        "task_id": uuid.uuid4().hex,
        "created_at": now_text(),
        "created_by": created_by,
        "action": action,
        "params_json": json.dumps(params or {}, ensure_ascii=False, separators=(",", ":")),
        "status": "queued",
        "agent_id": "",
        "started_at": "",
        "finished_at": "",
        "message": "等待本機 Agent",
        "log": "",
        "result_json": "",
    }
    service.spreadsheets().values().append(
        spreadsheetId=spreadsheet_id,
        range=f"'{SHEET_NAME}'!A:L",
        valueInputOption="RAW",
        insertDataOption="INSERT_ROWS",
        body={"values": [[task[key] for key in HEADERS]]},
    ).execute()
    return task


def list_tasks(
    *,
    limit: int = 20,
    service: Any | None = None,
    spreadsheet_id: str = "",
) -> list[dict[str, str]]:
    service, spreadsheet_id = ensure_task_sheet(service, spreadsheet_id)
    rows = service.spreadsheets().values().get(
        spreadsheetId=spreadsheet_id,
        range=f"'{SHEET_NAME}'!A2:L",
    ).execute().get("values", [])
    result: list[dict[str, str]] = []
    for row_number, row in enumerate(rows, start=2):
        values = list(row) + [""] * (len(HEADERS) - len(row))
        item = dict(zip(HEADERS, values[: len(HEADERS)]))
        item["_row"] = str(row_number)
        result.append(item)
    return result[-max(1, limit):][::-1]


def update_task(
    row_number: int,
    changes: dict[str, Any],
    *,
    service: Any | None = None,
    spreadsheet_id: str = "",
) -> None:
    service, spreadsheet_id = ensure_task_sheet(service, spreadsheet_id)
    row = service.spreadsheets().values().get(
        spreadsheetId=spreadsheet_id,
        range=f"'{SHEET_NAME}'!A{row_number}:L{row_number}",
    ).execute().get("values", [[]])[0]
    values = list(row) + [""] * (len(HEADERS) - len(row))
    for key, value in changes.items():
        if key in HEADERS:
            values[HEADERS.index(key)] = value
    service.spreadsheets().values().update(
        spreadsheetId=spreadsheet_id,
        range=f"'{SHEET_NAME}'!A{row_number}:L{row_number}",
        valueInputOption="RAW",
        body={"values": [values[: len(HEADERS)]]},
    ).execute()


def claim_next_task(
    *,
    agent_id: str = "",
    service: Any | None = None,
    spreadsheet_id: str = "",
) -> dict[str, str] | None:
    service, spreadsheet_id = ensure_task_sheet(service, spreadsheet_id)
    agent_id = agent_id or default_agent_id()
    queued = [task for task in reversed(list_tasks(limit=500, service=service, spreadsheet_id=spreadsheet_id)) if task["status"] == "queued"]
    if not queued:
        return None
    task = queued[0]
    row_number = int(task["_row"])
    update_task(
        row_number,
        {"status": "running", "agent_id": agent_id, "started_at": now_text(), "message": "本機 Agent 執行中"},
        service=service,
        spreadsheet_id=spreadsheet_id,
    )
    task.update({"status": "running", "agent_id": agent_id, "started_at": now_text()})
    return task
