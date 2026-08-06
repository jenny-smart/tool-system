from __future__ import annotations

import json
import os
import socket
import uuid
from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo

from tools.common.config_loader import get_master_spreadsheet_id, get_sheets_service


__all__ = [
    "LOG_SHEET_NAME",
    "STATUS_SHEET_NAME",
    "SHEET_NAME",
    "append_task_log",
    "claim_next_task",
    "create_task",
    "default_agent_id",
    "ensure_task_sheet",
    "list_tasks",
    "list_agent_status",
    "now_text",
    "read_task_log",
    "update_task",
    "write_agent_heartbeat",
]


SHEET_NAME = "本機Agent任務"
LOG_SHEET_NAME = "本機Agent任務Log"
STATUS_SHEET_NAME = "本機Agent狀態"
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
LOG_HEADERS = ["task_id", "seq", "logged_at", "content"]
STATUS_HEADERS = ["agent_id", "status", "last_seen", "pid", "actions", "version"]
TZ = ZoneInfo("Asia/Taipei")
_ENSURED_SPREADSHEETS: set[str] = set()


def now_text() -> str:
    return datetime.now(TZ).strftime("%Y-%m-%d %H:%M:%S")


def default_agent_id() -> str:
    return os.getenv("TOOL_LOCAL_AGENT_ID", "").strip() or socket.gethostname()


def _sheet_id(service: Any, spreadsheet_id: str, sheet_name: str) -> int | None:
    meta = service.spreadsheets().get(
        spreadsheetId=spreadsheet_id,
        fields="sheets(properties(sheetId,title))",
    ).execute()
    for sheet in meta.get("sheets", []):
        props = sheet.get("properties", {})
        if props.get("title") == sheet_name:
            return int(props["sheetId"])
    return None


def _ensure_sheet(
    service: Any,
    spreadsheet_id: str,
    sheet_name: str,
    headers: list[str],
) -> None:
    if _sheet_id(service, spreadsheet_id, sheet_name) is None:
        service.spreadsheets().batchUpdate(
            spreadsheetId=spreadsheet_id,
            body={
                "requests": [{
                    "addSheet": {
                        "properties": {
                            "title": sheet_name,
                            "gridProperties": {"frozenRowCount": 1},
                        }
                    }
                }]
            },
        ).execute()
    current = service.spreadsheets().values().get(
        spreadsheetId=spreadsheet_id,
        range=f"'{sheet_name}'!A1:Z1",
    ).execute().get("values", [])
    if not current or current[0][: len(headers)] != headers:
        service.spreadsheets().values().update(
            spreadsheetId=spreadsheet_id,
            range=f"'{sheet_name}'!A1",
            valueInputOption="RAW",
            body={"values": [headers]},
        ).execute()


def ensure_task_sheet(service: Any | None = None, spreadsheet_id: str = "") -> tuple[Any, str]:
    service = service or get_sheets_service()
    spreadsheet_id = spreadsheet_id or get_master_spreadsheet_id()
    if spreadsheet_id not in _ENSURED_SPREADSHEETS:
        _ensure_sheet(service, spreadsheet_id, SHEET_NAME, HEADERS)
        _ensure_sheet(service, spreadsheet_id, LOG_SHEET_NAME, LOG_HEADERS)
        _ensure_sheet(service, spreadsheet_id, STATUS_SHEET_NAME, STATUS_HEADERS)
        _ENSURED_SPREADSHEETS.add(spreadsheet_id)
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
        "status": "pending",
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


def append_task_log(
    task_id: str,
    content: str,
    *,
    start_seq: int = 1,
    service: Any | None = None,
    spreadsheet_id: str = "",
) -> int:
    service, spreadsheet_id = ensure_task_sheet(service, spreadsheet_id)
    text = str(content or "")
    if not text:
        return start_seq
    chunks = [text[index:index + 40000] for index in range(0, len(text), 40000)]
    rows = [
        [task_id, start_seq + index, now_text(), chunk]
        for index, chunk in enumerate(chunks)
    ]
    service.spreadsheets().values().append(
        spreadsheetId=spreadsheet_id,
        range=f"'{LOG_SHEET_NAME}'!A:D",
        valueInputOption="RAW",
        insertDataOption="INSERT_ROWS",
        body={"values": rows},
    ).execute()
    return start_seq + len(rows)


def read_task_log(
    task_id: str,
    *,
    service: Any | None = None,
    spreadsheet_id: str = "",
) -> str:
    service, spreadsheet_id = ensure_task_sheet(service, spreadsheet_id)
    rows = service.spreadsheets().values().get(
        spreadsheetId=spreadsheet_id,
        range=f"'{LOG_SHEET_NAME}'!A2:D",
    ).execute().get("values", [])
    matches = []
    for row in rows:
        values = list(row) + [""] * (len(LOG_HEADERS) - len(row))
        if str(values[0]) == task_id:
            try:
                seq = int(values[1])
            except (TypeError, ValueError):
                seq = 0
            matches.append((seq, str(values[3])))
    return "".join(content for _seq, content in sorted(matches))


def write_agent_heartbeat(
    agent_id: str,
    *,
    status: str = "online",
    pid: int | str = "",
    actions: list[str] | tuple[str, ...] | None = None,
    version: str = "1",
    service: Any | None = None,
    spreadsheet_id: str = "",
) -> None:
    service, spreadsheet_id = ensure_task_sheet(service, spreadsheet_id)
    rows = service.spreadsheets().values().get(
        spreadsheetId=spreadsheet_id,
        range=f"'{STATUS_SHEET_NAME}'!A2:F",
    ).execute().get("values", [])
    row_number = None
    for index, row in enumerate(rows, start=2):
        if row and str(row[0]) == agent_id:
            row_number = index
            break
    values = [
        agent_id,
        status,
        now_text(),
        str(pid),
        ",".join(actions or ()),
        version,
    ]
    if row_number is None:
        service.spreadsheets().values().append(
            spreadsheetId=spreadsheet_id,
            range=f"'{STATUS_SHEET_NAME}'!A:F",
            valueInputOption="RAW",
            insertDataOption="INSERT_ROWS",
            body={"values": [values]},
        ).execute()
    else:
        service.spreadsheets().values().update(
            spreadsheetId=spreadsheet_id,
            range=f"'{STATUS_SHEET_NAME}'!A{row_number}:F{row_number}",
            valueInputOption="RAW",
            body={"values": [values]},
        ).execute()


def list_agent_status(
    *,
    max_age_seconds: int = 30,
    service: Any | None = None,
    spreadsheet_id: str = "",
) -> list[dict[str, Any]]:
    service, spreadsheet_id = ensure_task_sheet(service, spreadsheet_id)
    rows = service.spreadsheets().values().get(
        spreadsheetId=spreadsheet_id,
        range=f"'{STATUS_SHEET_NAME}'!A2:F",
    ).execute().get("values", [])
    now = datetime.now(TZ)
    result: list[dict[str, Any]] = []
    for row in rows:
        values = list(row) + [""] * (len(STATUS_HEADERS) - len(row))
        item = dict(zip(STATUS_HEADERS, values[: len(STATUS_HEADERS)]))
        try:
            last_seen = datetime.strptime(item["last_seen"], "%Y-%m-%d %H:%M:%S").replace(tzinfo=TZ)
            fresh = (now - last_seen).total_seconds() <= max_age_seconds
        except (TypeError, ValueError):
            fresh = False
        item["online"] = item.get("status") == "online" and fresh
        result.append(item)
    return result


def claim_next_task(
    *,
    agent_id: str = "",
    service: Any | None = None,
    spreadsheet_id: str = "",
) -> dict[str, str] | None:
    service, spreadsheet_id = ensure_task_sheet(service, spreadsheet_id)
    agent_id = agent_id or default_agent_id()
    pending = [
        task
        for task in reversed(list_tasks(limit=500, service=service, spreadsheet_id=spreadsheet_id))
        if task["status"] in {"pending", "queued"}
    ]
    if not pending:
        return None
    task = pending[0]
    row_number = int(task["_row"])
    update_task(
        row_number,
        {"status": "running", "agent_id": agent_id, "started_at": now_text(), "message": "本機 Agent 執行中"},
        service=service,
        spreadsheet_id=spreadsheet_id,
    )
    task.update({"status": "running", "agent_id": agent_id, "started_at": now_text()})
    return task
