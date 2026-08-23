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


def _all_rows(service: Any, spreadsheet_id: str) -> list[dict[str, Any]]:
    rows = service.spreadsheets().values().get(
        spreadsheetId=spreadsheet_id,
        range=f"'{SHEET_NAME}'!A2:G",
    ).execute().get("values", [])
    result: list[dict[str, Any]] = []
    for row_no, row in enumerate(rows, start=2):
        values = list(row) + [""] * (len(HEADERS) - len(row))
        item = dict(zip(HEADERS, values[: len(HEADERS)]))
        item["_row"] = row_no
        result.append(item)
    return result


def enqueue_payload(area: str, order_no: str, payload_json: str, created_by: str = "Tool System") -> int:
    service = get_sheets_service()
    spreadsheet_id = get_master_spreadsheet_id()
    _ensure_sheet(service, spreadsheet_id)

    # 同一地區／訂單只保留一筆 pending。若之前失敗或重複送出，直接覆蓋最新 Payload，
    # 避免 Agent 一次撈到歷史 pending 而顯示 9 筆、重複開立。
    normalized_area = str(area).strip()
    normalized_order = str(order_no).strip()
    for item in reversed(_all_rows(service, spreadsheet_id)):
        if str(item.get("area", "")).strip() != normalized_area:
            continue
        if str(item.get("order_no", "")).strip() != normalized_order:
            continue
        if str(item.get("status", "")).strip() != "pending":
            continue
        row_no = int(item.get("_row") or 0)
        service.spreadsheets().values().update(
            spreadsheetId=spreadsheet_id,
            range=f"'{SHEET_NAME}'!A{row_no}:G{row_no}",
            valueInputOption="RAW",
            body={"values": [[now_text(), created_by, normalized_area, normalized_order, payload_json, "pending", "等待鯨躍貼入"]]},
        ).execute()
        return row_no

    result = service.spreadsheets().values().append(
        spreadsheetId=spreadsheet_id,
        range=f"'{SHEET_NAME}'!A:G",
        valueInputOption="RAW",
        insertDataOption="INSERT_ROWS",
        body={"values": [[now_text(), created_by, normalized_area, normalized_order, payload_json, "pending", "等待鯨躍貼入"]]},
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
    rows = _all_rows(service, spreadsheet_id)

    # 只回傳每個訂單最新一筆 pending；舊的重複 pending 自動標記 superseded。
    latest_by_order: dict[str, dict[str, Any]] = {}
    duplicates: list[int] = []
    for item in rows:
        if str(item.get("area", "")).strip() != area:
            continue
        if str(item.get("status", "")).strip() != "pending":
            continue
        order_no = str(item.get("order_no", "")).strip()
        if not order_no:
            continue
        previous = latest_by_order.get(order_no)
        if previous is not None:
            duplicates.append(int(previous.get("_row") or 0))
        latest_by_order[order_no] = item

    for row_no in duplicates:
        if row_no:
            update_payload_status(row_no, "superseded", "同訂單有較新的待處理 Payload，已略過")

    return sorted(latest_by_order.values(), key=lambda item: int(item.get("_row") or 0))


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
