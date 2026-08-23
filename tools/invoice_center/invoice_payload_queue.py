from __future__ import annotations

from typing import Any

from tools.common.config_loader import get_master_spreadsheet_id, get_sheets_service
from tools.local_agent_queue import now_text


SHEET_NAME = "發票開立佇列"
HEADERS = ["created_at", "created_by", "area", "order_no", "payload_json", "status", "message"]


def _ensure_sheet(service: Any, spreadsheet_id: str) -> None:
    meta = service.spreadsheets().get(
        spreadsheetId=spreadsheet_id,
        fields="sheets(properties(sheetId,title))",
    ).execute()
    titles = {sheet.get("properties", {}).get("title") for sheet in meta.get("sheets", [])}
    if SHEET_NAME not in titles:
        service.spreadsheets().batchUpdate(
            spreadsheetId=spreadsheet_id,
            body={"requests": [{"addSheet": {"properties": {"title": SHEET_NAME, "gridProperties": {"frozenRowCount": 1}}}}]},
        ).execute()
    current = service.spreadsheets().values().get(
        spreadsheetId=spreadsheet_id,
        range=f"'{SHEET_NAME}'!A1:G1",
    ).execute().get("values", [])
    if not current or current[0][: len(HEADERS)] != HEADERS:
        service.spreadsheets().values().update(
            spreadsheetId=spreadsheet_id,
            range=f"'{SHEET_NAME}'!A1",
            valueInputOption="RAW",
            body={"values": [HEADERS]},
        ).execute()


def enqueue_payload(area: str, order_no: str, payload_json: str, created_by: str = "Tool System") -> int:
    service = get_sheets_service()
    spreadsheet_id = get_master_spreadsheet_id()
    _ensure_sheet(service, spreadsheet_id)
    result = service.spreadsheets().values().append(
        spreadsheetId=spreadsheet_id,
        range=f"'{SHEET_NAME}'!A:G",
        valueInputOption="RAW",
        insertDataOption="INSERT_ROWS",
        body={"values": [[now_text(), created_by, area, order_no, payload_json, "pending", "等待鯨躍貼入"]]},
    ).execute()
    updated = result.get("updates", {}).get("updatedRange", "")
    try:
        return int(updated.rsplit("!A", 1)[1].split(":", 1)[0])
    except Exception:
        return 0


def list_pending_payloads(area: str) -> list[dict[str, Any]]:
    service = get_sheets_service()
    spreadsheet_id = get_master_spreadsheet_id()
    _ensure_sheet(service, spreadsheet_id)
    rows = service.spreadsheets().values().get(
        spreadsheetId=spreadsheet_id,
        range=f"'{SHEET_NAME}'!A2:G",
    ).execute().get("values", [])
    result: list[dict[str, Any]] = []
    for row_no, row in enumerate(rows, start=2):
        values = list(row) + [""] * (len(HEADERS) - len(row))
        item = dict(zip(HEADERS, values[: len(HEADERS)]))
        if str(item.get("area", "")).strip() != area:
            continue
        if str(item.get("status", "")).strip() != "pending":
            continue
        item["_row"] = row_no
        result.append(item)
    return result


def update_payload_status(row_no: int, status: str, message: str = "") -> None:
    if not row_no:
        return
    service = get_sheets_service()
    spreadsheet_id = get_master_spreadsheet_id()
    _ensure_sheet(service, spreadsheet_id)
    service.spreadsheets().values().update(
        spreadsheetId=spreadsheet_id,
        range=f"'{SHEET_NAME}'!F{row_no}:G{row_no}",
        valueInputOption="RAW",
        body={"values": [[status, message]]},
    ).execute()
